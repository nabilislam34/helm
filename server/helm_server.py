"""Helm: a small PC control service for a phone dashboard.

Shows and controls a fixed list of local services (exact PID only), reports system
health, forwards commands to local job controllers, and sends alerts to Telegram.

Everything specific to one machine (service list, paths, ports, keys) lives in
config.json next to this file. See config.example.json.

Cost rules this file keeps to:
  - one process scan at a time, cached, limited to a few process names
  - no WMI, no PowerShell, no polling of anything a person did not ask for
  - one background check per minute for alerts
  - slow lookups (folder counts, database counts, usage) only on request and cached

Run: pythonw helm_server.py [--config path]
"""
import argparse
import configparser
import json
import re
import secrets
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import util as importlib_util
from pathlib import Path

import psutil

HERE = Path(__file__).resolve().parent
NCPU = psutil.cpu_count() or 1
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

DEFAULTS = {
    "port": 8775,
    "key": "",
    "scan_names": ["python.exe", "pythonw.exe", "pwsh.exe", "powershell.exe", "cmd.exe", "py.exe"],
    "wrapper_names": ["cmd.exe", "py.exe"],
    "registry_module": "",        # optional .py file exposing REGISTRY = {name: {match, launch, cwd}}
    "services": {},               # name -> {match, launch, cwd, group, task, port, watch, hidden, ram_limit_mb}
    "discover": [],               # [{prefix, script, launch, cwd, group}] for one script on many ports
    "ahk_bridge": "",
    "ahk_mode_labels": [],        # button labels for a script that reports numbered modes
    "ahk_pins": [],               # [{label, id, restart_if_running}] one-tap scripts shown above the list
    # userscripts: folders are read one level deep, cheatsheet is a JSON file
    # [{category, scripts: [{name, keys: [...], desc}]}], toggle_labels names the settings keys
    "userscripts": {"folders": [], "cheatsheet": "", "exclude": ["backup", "-New userscript-"],
                    "toggle_labels": {}},
    "command_targets": {},        # name -> url of a local job controller taking {"cmd": ...}
    "commands_allowed": [],
    "command_mirror": {},         # cmd -> other target that must get the same command
    "command_toggles": [],        # names for {"cmd": "toggle", "name": ..., "value": true|false}
    "command_fields": [],         # names for {"cmd": "set", "name": ..., "value": "..."}
    "command_choices": {},        # toggle name -> list of values to pick from
    "quick": {},                  # name -> {label, launch, cwd, hidden}
    "usage": {},                  # {url, token_file, fields: {label: "dotted.path"}}
    "count_folders": {},          # label -> folder (counted with Everything's es.exe)
    "es": {"path": "", "instance": "", "ext": "png;jpg;jpeg;webp;gif;avif;bmp;tif;tiff;heic"},
    "sqlite_counts": [],          # [{label, path, table}]
    "crystaldiskinfo_dir": "",
    "telegram": {"enabled": False, "token_file": "", "chat_id": None, "chat_id_file": "",
                 "api_base": "https://api.telegram.org"},
    "thresholds": {"ram_percent": 92, "disk_free_gb": 10, "proc_ram_mb": 6000, "disk_temp_c": 70,
                   "cooldown_min": 30},
    "watch_interval_s": 60,
}

CFG = {}
STATE = {"muted": [], "thresholds": {}}
STATE_PATH = HERE / "state.json"
ALERTS_PATH = HERE / "alerts.jsonl"
LOG_PATH = HERE / "helm.log"
ALERTS = deque(maxlen=500)
STOP = threading.Event()

SCAN_TTL = 5
SCAN = {"at": 0.0, "rows": {}}
SCAN_LOCK = threading.Lock()
ACT_LOCK = threading.Lock()
CACHE_LOCK = threading.Lock()
CPU_PREV = {}                     # pid -> (wall time, cpu seconds)
PORT_OK = {}                      # service name -> bool, filled by the watcher
CACHE = {}                        # key -> (time, value)
IO_PREV = {}
QUICK_PROCS = {}


def log(text):
    try:
        if LOG_PATH.exists() and LOG_PATH.stat().st_size > 200_000:
            LOG_PATH.write_text(LOG_PATH.read_text(encoding="utf-8", errors="replace")[-100_000:],
                                encoding="utf-8")
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(time.strftime("%Y-%m-%d %H:%M:%S ") + text + "\n")
    except OSError:
        pass


def cached(key, ttl, make):
    with CACHE_LOCK:
        hit = CACHE.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
    value = make()
    with CACHE_LOCK:
        CACHE[key] = (time.time(), value)
    return value


def cache_peek(key):
    hit = CACHE.get(key)
    return hit[1] if hit else None


def thresholds():
    return {**CFG["thresholds"], **STATE.get("thresholds", {})}


def save_state():
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(STATE, indent=1), encoding="utf-8")
    tmp.replace(STATE_PATH)


# ── config ─────────────────────────────────────────────────────────────────────
def load_config(path):
    global STATE_PATH, ALERTS_PATH, LOG_PATH
    cfg = json.loads(json.dumps(DEFAULTS))
    if path.exists():
        user = json.loads(path.read_text(encoding="utf-8"))
        for key, value in user.items():
            if isinstance(value, dict) and isinstance(cfg.get(key), dict) and key in ("es", "telegram", "thresholds", "userscripts"):
                cfg[key].update(value)
            else:
                cfg[key] = value
    else:
        cfg["key"] = secrets.token_urlsafe(18)
        path.write_text(json.dumps({"port": cfg["port"], "key": cfg["key"]}, indent=1), encoding="utf-8")
    base = path.parent
    STATE_PATH, ALERTS_PATH, LOG_PATH = base / "state.json", base / "alerts.jsonl", base / "helm.log"

    services = {}
    module_path = cfg.get("registry_module")
    if module_path and Path(module_path).is_file():
        spec = importlib_util.spec_from_file_location("helm_registry", module_path)
        module = importlib_util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for name, entry in getattr(module, "REGISTRY", {}).items():
            services[name] = {"match": list(entry["match"]), "launch": list(entry.get("launch") or []),
                              "cwd": entry.get("cwd")}
    for name, extra in cfg["services"].items():
        services.setdefault(name, {}).update(extra)
    cfg["services"] = services
    CFG.clear()
    CFG.update(cfg)
    if STATE_PATH.exists():
        try:
            STATE.update(json.loads(STATE_PATH.read_text(encoding="utf-8")))
        except ValueError:
            pass
    if ALERTS_PATH.exists():
        for line in ALERTS_PATH.read_text(encoding="utf-8", errors="replace").splitlines()[-500:]:
            try:
                ALERTS.append(json.loads(line))
            except ValueError:
                pass


# ── services ───────────────────────────────────────────────────────────────────
def _metrics(proc, now):
    with proc.oneshot():
        times = proc.cpu_times()
        cpu_s = times.user + times.system
        mem = proc.memory_info()
        # ram_mb = memory the process owns (Windows trims the in-RAM part of idle processes to
        # almost nothing, so that number alone hides a 1 GB server); in_ram_mb = that in-RAM part
        info = {"pid": proc.pid, "ram_mb": round(getattr(mem, "private", mem.rss) / 1048576, 1),
                "in_ram_mb": round(mem.rss / 1048576, 1),
                "handles": proc.num_handles() if hasattr(proc, "num_handles") else None,
                "uptime_s": int(now - proc.create_time()), "created": proc.create_time()}
    prev = CPU_PREV.get(proc.pid)
    info["cpu"] = round((cpu_s - prev[1]) / (now - prev[0]) / NCPU * 100, 1) if prev and now > prev[0] else None
    CPU_PREV[proc.pid] = (now, cpu_s)
    return info


def _has_terms(low, terms):
    """Every term is in the command line as a whole name: 'gen_dev.py' must not match
    'alt_gen_dev.py', or stopping one service could stop its sibling."""
    return all(re.search(r"(?<![\w.-])" + re.escape(t.lower()) + r"(?!\w)", low) for t in terms)


def _pick(procs, terms):
    """Process whose command line holds every term. A real interpreter wins over a cmd/py wrapper."""
    hits = [p for p in procs if _has_terms(p[2], terms)]
    real = [p for p in hits if p[1] not in CFG["wrapper_names"]]
    return (real or hits or [None])[0]


def _row(name, svc, hit, now):
    row = {"name": name, "group": svc.get("group") or "Other", "running": False, "pid": None,
           "port": svc.get("port"), "port_ok": None, "task": bool(svc.get("task")),
           "watch": bool(svc.get("watch")), "muted": name in STATE["muted"],
           "can_start": bool(svc.get("task") or svc.get("launch"))}
    if hit:
        try:
            row.update(_metrics(hit[0], now))
            row["running"] = True
            row["port_ok"] = PORT_OK.get(name)
        except psutil.Error:
            pass
    return row


def scan(force=False):
    with SCAN_LOCK:
        now = time.time()
        if not force and now - SCAN["at"] < SCAN_TTL:
            return SCAN["rows"]
        me = psutil.Process().pid
        procs = []
        for proc in psutil.process_iter(["pid", "name"]):
            pname = (proc.info["name"] or "").lower()
            if pname not in CFG["scan_names"] or proc.info["pid"] == me:
                continue
            try:
                cmd = proc.cmdline()
            except psutil.Error:
                continue
            procs.append((proc, pname, " ".join(cmd).lower(), cmd))
        rows = {}
        for name, svc in CFG["services"].items():
            if svc.get("match"):
                rows[name] = _row(name, svc, _pick(procs, svc["match"]), now)
        for disc in CFG["discover"]:
            script = disc["script"].lower()
            for entry in procs:
                if not _has_terms(entry[2], [script]) or entry[1] in CFG["wrapper_names"]:
                    continue
                port = next((c for c in reversed(entry[3]) if c.isdigit()), None)
                if port:
                    name = disc["prefix"] + port
                    svc = {**disc, **CFG["services"].get(name, {}), "port": int(port)}
                    rows[name] = _row(name, svc, entry, now)
        for name, svc in CFG["services"].items():      # a watched port copy that is down still gets a row
            if name not in rows:
                rows[name] = _row(name, svc, None, now)
        alive = {r["pid"] for r in rows.values() if r["pid"]}
        for pid in [p for p in CPU_PREV if p not in alive]:
            del CPU_PREV[pid]
        SCAN.update(at=now, rows=rows)
        return rows


def service_def(name):
    svc = dict(CFG["services"].get(name, {}))
    for disc in CFG["discover"]:
        if name.startswith(disc["prefix"]) and name[len(disc["prefix"]):].isdigit():
            port = name[len(disc["prefix"]):]
            base = {"match": [disc["script"], port], "cwd": disc.get("cwd"), "group": disc.get("group"),
                    "launch": [a.replace("{port}", port) for a in disc.get("launch") or []]}
            return {**base, **svc}
    return svc or None


def _kill(row, svc):
    """Stop exactly the scanned PID, after checking it is still the same process."""
    try:
        proc = psutil.Process(row["pid"])
        low = " ".join(proc.cmdline()).lower()
        if abs(proc.create_time() - row["created"]) > 1 or not _has_terms(low, svc["match"]):
            return False
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except psutil.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    except psutil.NoSuchProcess:
        pass
    except psutil.Error as error:
        log(f"stop {row['name']} pid {row['pid']} failed: {error}")
        return False
    return True


def _launch(name, svc):
    if svc.get("task"):
        result = subprocess.run(["schtasks", "/Run", "/TN", svc["task"]], capture_output=True, text=True,
                                timeout=20, creationflags=NO_WINDOW)
        return None if result.returncode == 0 else (result.stderr or result.stdout).strip()[:300]
    if not svc.get("launch"):
        return "no start command registered"
    try:
        # never a console window: a blank window nobody can identify is worse than no output
        subprocess.Popen(svc["launch"], cwd=svc.get("cwd"), stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
    except OSError as error:
        return f"launch failed: {error}"
    return None


def _wait_running(name, seconds, old_pid=None):
    end = time.time() + seconds
    while time.time() < end:
        time.sleep(1)
        row = scan(True).get(name)
        if row and row["running"] and row["pid"] != old_pid:
            return row
    return None


def service_action(name, action):
    if action not in ("start", "stop", "restart"):
        return {"ok": False, "error": "unknown action"}
    svc = service_def(name)
    if not svc or not svc.get("match"):
        return {"ok": False, "error": f"unknown service: {name}"}
    with ACT_LOCK:
        row = scan(True).get(name) or {"running": False, "pid": None, "name": name}
        if action == "start":
            if row["running"]:
                return {"ok": False, "error": f"{name} is already running", "pid": row["pid"]}
            error = _launch(name, svc)
            if error:
                return {"ok": False, "error": error}
            new = _wait_running(name, 10)
            return {"ok": bool(new), "pid": new and new["pid"], **({} if new else {"error": "did not come up in 10 s"})}
        if not row["running"]:
            return {"ok": False, "error": f"{name} is not running"}
        old = row["pid"]
        if not _kill(row, svc):
            return {"ok": False, "error": "process changed or could not be stopped", "pid": old}
        log(f"{action} {name}: stopped pid {old}")
        if action == "stop":
            return {"ok": True, "stopped_pid": old}
        # a task-run service may be relaunched by its own watchdog; only start it if it stays down
        new = _wait_running(name, 20, old) if svc.get("task") else None
        if not new:
            error = _launch(name, svc)
            if error:
                return {"ok": False, "error": error, "stopped_pid": old}
            new = _wait_running(name, 10, old)
        return {"ok": bool(new), "stopped_pid": old, "pid": new and new["pid"],
                **({} if new else {"error": "stopped, but it did not come back"})}


# ── system ─────────────────────────────────────────────────────────────────────
def _rates(key, counters, fields):
    now = time.time()
    prev = IO_PREV.get(key)
    IO_PREV[key] = (now, counters)
    if not prev or now <= prev[0] or counters is None or prev[1] is None:
        return {f: None for f in fields}
    span = now - prev[0]
    return {f: round((getattr(counters, f) - getattr(prev[1], f)) / span) for f in fields}


def _top_procs():
    rows = []
    for proc in psutil.process_iter(["pid", "name", "memory_info"]):
        mem = proc.info["memory_info"]
        if mem:
            rows.append({"pid": proc.info["pid"], "name": proc.info["name"],
                         "ram_mb": round(getattr(mem, "private", mem.rss) / 1048576),
                         "in_ram_mb": round(mem.rss / 1048576)})
    rows.sort(key=lambda r: -r["ram_mb"])
    return rows[:10]


def _disks():
    out = []
    for part in psutil.disk_partitions(all=False):
        try:
            use = psutil.disk_usage(part.mountpoint)
        except OSError:
            continue
        out.append({"mount": part.mountpoint, "percent": use.percent,
                    "used_gb": round(use.used / 1073741824, 1), "free_gb": round(use.free / 1073741824, 1),
                    "total_gb": round(use.total / 1073741824, 1)})
    return out


SMART_HEALTH = {"1": "Good", "2": "Caution", "3": "Bad"}


def _smart():
    """Latest reading per drive from CrystalDiskInfo's own Smart.ini files (read only)."""
    root = Path(CFG["crystaldiskinfo_dir"]) / "Smart" if CFG["crystaldiskinfo_dir"] else None
    if not root or not root.is_dir():
        return []
    drives = []
    for folder in root.iterdir():
        ini = folder / "Smart.ini"
        if not ini.is_file():
            continue
        raw = ini.read_bytes()
        text = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8", "replace")
        parser = configparser.ConfigParser(interpolation=None, strict=False)
        try:
            parser.read_string(text)
        except configparser.Error:
            continue
        for section in parser.sections():
            if section == "PC" or section.endswith("FIRST") or not parser.has_option(section, "HealthStatus"):
                continue
            get = lambda k: parser.get(section, k, fallback="").strip('"') or None
            num = lambda k: int(get(k)) if (get(k) or "").lstrip("-").isdigit() else None
            drives.append({"name": folder.name, "date": get("Date"), "health_code": get("HealthStatus"),
                           "health": SMART_HEALTH.get(get("HealthStatus") or "", "Unknown"),
                           "temp_c": num("Temperature"), "life": num("Life"),
                           "reallocated": num("ReallocatedSectorsCount"),
                           "pending": num("CurrentPendingSectorCount"),
                           "uncorrectable": num("UncorrectableSectorCount"), "power_on_hours": num("PowerOnHours")})
    return drives


def _counts():
    out = []
    es = CFG["es"]
    for label, folder in CFG["count_folders"].items():
        if not Path(folder).is_dir() or not es.get("path"):
            out.append({"label": label, "count": None, "error": "folder or es.exe missing"})
            continue
        cmd = [es["path"]] + (["-instance", es["instance"]] if es.get("instance") else []) + \
              ["-path", folder, "-get-result-count", f"ext:{es['ext']}"]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=30,
                                    creationflags=NO_WINDOW)
            out.append({"label": label, "count": int(result.stdout.strip())})
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            out.append({"label": label, "count": None, "error": str(error)[:200]})
    for item in CFG["sqlite_counts"]:
        path = Path(item["path"])
        if not path.is_file() or not item["table"].replace("_", "").isalnum():
            out.append({"label": item["label"], "count": None, "error": "database missing"})
            continue
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=5)
        try:
            total = conn.execute(f"SELECT COUNT(*) FROM {item['table']}").fetchone()[0]
            out.append({"label": item["label"], "count": total,
                        "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(path.stat().st_mtime))})
        except sqlite3.Error as error:
            out.append({"label": item["label"], "count": None, "error": str(error)[:200]})
        finally:
            conn.close()
    return {"at": time.strftime("%Y-%m-%d %H:%M"), "items": out}


def system_brief():
    mem = psutil.virtual_memory()
    return {"cpu": psutil.cpu_percent(None), "ram_percent": mem.percent,
            "ram_used_gb": round(mem.used / 1073741824, 1), "ram_total_gb": round(mem.total / 1073741824, 1)}


def system_full(with_counts):
    def make():
        swap = psutil.swap_memory()
        try:
            disk_io = psutil.disk_io_counters()
        except (OSError, RuntimeError):
            disk_io = None
        return {**system_brief(), "swap_percent": swap.percent, "disks": _disks(),
                "disk_io": _rates("disk", disk_io, ("read_bytes", "write_bytes")),
                "net_io": _rates("net", psutil.net_io_counters(), ("bytes_sent", "bytes_recv")),
                "top": _top_procs(), "boot": time.strftime("%Y-%m-%d %H:%M", time.localtime(psutil.boot_time()))}
    out = dict(cached("system", 5, make))
    out["smart"] = cached("smart", 60, _smart)
    out["counts"] = cached("counts", 600, _counts) if with_counts else cache_peek("counts")
    return out


# ── forwarding to other local services ─────────────────────────────────────────
def http_json(url, payload=None, method=None, timeout=12, headers=None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    # some sites refuse urllib's default agent name
    request = urllib.request.Request(url, data, {"Content-Type": "application/json", "User-Agent": "helm/1.0",
                                                 "Accept": "application/json", **(headers or {})},
                                     method=method or ("POST" if data is not None else "GET"))
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


HEADER_RE = re.compile(r"^//\s*@(name|version|description|match)\s+(.+?)\s*$", re.M)
COMBO_RE = re.compile(r"\b(?:(?:Ctrl|Control|Alt|Shift|Win|Cmd)\s*\+\s*)+(?:F\d{1,2}|[A-Za-z0-9]\b|[`\[\];',./\\=-]|"
                      r"Enter|Space|Tab|Esc|Escape|Up|Down|Left|Right|Arrow\w+|Delete|Backspace|Home|End)", re.I)


def _norm(name):
    return re.sub(r"[^a-z0-9]+", " ", re.sub(r"\(.*?\)|v?\d+(\.\d+)+", " ", name.lower())).strip()


def _userscripts():
    """Script list from the configured folders: header fields, key combos named in the source,
    and the hand-kept cheatsheet entry when one matches by name."""
    cfg = CFG["userscripts"]
    sheet = {}
    if cfg.get("cheatsheet") and Path(cfg["cheatsheet"]).is_file():
        try:
            for group in json.loads(Path(cfg["cheatsheet"]).read_text(encoding="utf-8")):
                for item in group.get("scripts", []):
                    sheet[_norm(item["name"])] = {**item, "category": group.get("category", "")}
        except (ValueError, KeyError, TypeError) as error:
            log(f"cheatsheet unreadable: {error}")
    best = {}
    for folder in cfg["folders"]:
        root = Path(folder)
        if not root.is_dir():
            continue
        for path in root.glob("*.user.js"):
            if any(word.lower() in path.name.lower() for word in cfg["exclude"]):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
                mtime = path.stat().st_mtime
            except OSError:
                continue
            head = text[:4000]
            fields = {}
            for key, value in HEADER_RE.findall(head):
                fields.setdefault(key, []).append(value)
            name = (fields.get("name") or [path.stem])[0]
            combos = []
            for combo in COMBO_RE.findall(text):
                tidy = re.sub(r"\s*\+\s*", "+", combo).replace("Control", "Ctrl")
                tidy = "+".join(p[:1].upper() + p[1:] for p in tidy.split("+"))
                if tidy not in combos:
                    combos.append(tidy)
            key = _norm(name)
            note = sheet.get(key) or next((v for k, v in sheet.items() if k and (k in key or key in k)), None)
            entry = {"name": name, "file": path.name, "version": (fields.get("version") or [""])[0],
                     "description": (fields.get("description") or [""])[0], "sites": fields.get("match", [])[:6],
                     "modified": time.strftime("%Y-%m-%d", time.localtime(mtime)), "mtime": mtime,
                     "found_keys": combos[:40], "keys": (note or {}).get("keys", []),
                     "note": (note or {}).get("desc", ""), "category": (note or {}).get("category", "")}
            if key not in best or mtime > best[key]["mtime"]:      # same script saved twice: newest file wins
                best[key] = entry
    rows = sorted(best.values(), key=lambda e: e["name"].lower())
    for row in rows:
        del row["mtime"]
    return rows


def ahk(path, method="GET", payload=None):
    if not CFG["ahk_bridge"]:
        return {"ok": False, "message": "no AHK bridge configured", "entries": []}
    try:
        return http_json(CFG["ahk_bridge"] + path, payload, method=method, timeout=14)
    except urllib.error.HTTPError as error:
        try:
            return json.loads(error.read().decode("utf-8"))
        except ValueError:
            return {"ok": False, "message": f"AHK bridge error {error.code}", "entries": []}
    except (OSError, ValueError) as error:
        return {"ok": False, "message": f"AHK bridge not reachable: {error}", "entries": []}


def command(body):
    target = body.pop("target", None) or next(iter(CFG["command_targets"]), None)
    cmd = body.get("cmd")
    url = CFG["command_targets"].get(target)
    if not url:
        return {"ok": False, "error": f"unknown target: {target}"}
    if cmd not in CFG["commands_allowed"]:
        return {"ok": False, "error": f"command not allowed: {cmd}"}
    try:
        out = http_json(url, body, timeout=12)
    except (OSError, ValueError) as error:
        return {"ok": False, "error": f"{target} not reachable: {error}"}
    mirror = CFG["command_mirror"].get(cmd)
    # a setting that another controller must share is sent there too, only when a value is being set
    if mirror and mirror != target and len(body) > 1 and any(v not in (None, "") for k, v in body.items() if k != "cmd") \
            and isinstance(out, dict):
        try:
            out[f"{mirror} mirror"] = http_json(CFG["command_targets"][mirror], body, timeout=8)
        except (OSError, ValueError, KeyError) as error:
            out[f"{mirror} mirror"] = f"NOT SET, not reachable ({error})"
    return out


def usage():
    cfg = CFG["usage"]
    if not cfg.get("url"):
        return {"ok": False, "error": "usage is not configured"}

    def make():
        try:
            token = Path(cfg["token_file"]).read_text(encoding="utf-8").strip()
            data = http_json(cfg["url"], timeout=15, headers={"Authorization": f"Bearer {token}"})
        except (OSError, ValueError) as error:
            return {"ok": False, "error": str(error)[:200]}
        fields = {}
        for label, dotted in cfg.get("fields", {}).items():
            value = data
            for part in dotted.split("."):
                value = value.get(part) if isinstance(value, dict) else None
            fields[label] = value
        return {"ok": True, "fields": fields, "at": time.strftime("%H:%M")}
    return cached("usage", 60, make)


def quick(name):
    item = CFG["quick"].get(name)
    if not item:
        return {"ok": False, "error": f"unknown quick action: {name}"}
    old = QUICK_PROCS.get(name)
    if old and old.poll() is None:
        return {"ok": False, "error": f"{item.get('label', name)} is already running", "pid": old.pid}
    try:
        proc = subprocess.Popen(item["launch"], cwd=item.get("cwd"), stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                creationflags=NO_WINDOW)
    except OSError as error:
        return {"ok": False, "error": f"launch failed: {error}"}
    QUICK_PROCS[name] = proc
    return {"ok": True, "pid": proc.pid}


# ── alerts ─────────────────────────────────────────────────────────────────────
ALERT_LAST = {}
DOWN_COUNT = {}
DOWN_SENT = set()
PORT_BAD = {}
RAM_HIGH = {"n": 0}
ALERT_WRITES = {"n": 0}


def telegram_send(text):
    tg = CFG["telegram"]
    if not tg.get("enabled"):
        return False
    try:
        token = Path(tg["token_file"]).read_text(encoding="utf-8").strip()
        chat = tg.get("chat_id")
        if chat is None and tg.get("chat_id_file"):
            chat = json.loads(Path(tg["chat_id_file"]).read_text(encoding="utf-8"))["allowed_user_ids"][0]
        reply = http_json(f"{tg['api_base']}/bot{token}/sendMessage", {"chat_id": chat, "text": text}, timeout=10)
        return bool(reply.get("ok"))
    except urllib.error.HTTPError as error:      # the token must never reach the log
        try:
            why = json.loads(error.read().decode("utf-8")).get("description", "")
        except ValueError:
            why = ""
        log(f"telegram send failed: HTTP {error.code} {why}")
        return False
    except Exception as error:
        log(f"telegram send failed: {type(error).__name__}")
        return False


def alert(key, text, level="warn", cooldown_min=None):
    now = time.time()
    wait = (thresholds()["cooldown_min"] if cooldown_min is None else cooldown_min) * 60
    if now - ALERT_LAST.get(key, 0) < wait:
        return
    ALERT_LAST[key] = now
    entry = {"t": int(now), "time": time.strftime("%Y-%m-%d %H:%M"), "key": key, "level": level, "text": text,
             "sent": telegram_send(text)}
    ALERTS.append(entry)
    try:
        ALERT_WRITES["n"] += 1
        if ALERT_WRITES["n"] % 100 == 0:        # keep the file as short as the list in memory
            ALERTS_PATH.write_text("".join(json.dumps(a) + "\n" for a in ALERTS), encoding="utf-8")
        else:
            with ALERTS_PATH.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry) + "\n")
    except OSError:
        pass


def _port_open(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.4):
            return True
    except OSError:
        return False


def tick():
    limits = thresholds()
    rows = scan()
    for name, row in rows.items():
        if row["running"] and row["port"]:
            PORT_OK[name] = row["port_ok"] = _port_open(row["port"])   # also into the cached row
        else:
            PORT_OK.pop(name, None)
        if not row["watch"] or row["muted"]:
            DOWN_COUNT.pop(name, None)
            continue
        if not row["running"]:
            DOWN_COUNT[name] = DOWN_COUNT.get(name, 0) + 1
            if DOWN_COUNT[name] == 2:                       # down on two checks in a row
                DOWN_SENT.add(name)
                alert(f"down:{name}", f"DOWN: {name} is not running", "bad")
        else:
            DOWN_COUNT.pop(name, None)
            if name in DOWN_SENT:
                DOWN_SENT.discard(name)
                alert(f"up:{name}", f"BACK: {name} is running again (pid {row['pid']})", "good", 0)
            if PORT_OK.get(name) is False:
                PORT_BAD[name] = PORT_BAD.get(name, 0) + 1
                if PORT_BAD[name] == 4:
                    alert(f"port:{name}", f"NOT ANSWERING: {name} is running but port {row['port']} is closed", "bad")
            else:
                PORT_BAD.pop(name, None)
            limit = CFG["services"].get(name, {}).get("ram_limit_mb") or limits["proc_ram_mb"]
            if row.get("ram_mb") and row["ram_mb"] > limit:
                alert(f"ram:{name}", f"HIGH RAM: {name} is using {row['ram_mb']:.0f} MB (limit {limit})")
    mem = psutil.virtual_memory()
    RAM_HIGH["n"] = RAM_HIGH["n"] + 1 if mem.percent >= limits["ram_percent"] else 0
    if RAM_HIGH["n"] == 3:
        top = ", ".join(f"{p['name']} {p['ram_mb']} MB" for p in _top_procs()[:3])
        alert("ram", f"RAM {mem.percent:.0f}% for 3 minutes. Biggest: {top}", "bad")
    for disk in cached("disks", 300, _disks):
        if disk["free_gb"] < limits["disk_free_gb"]:
            alert(f"disk:{disk['mount']}", f"LOW DISK: {disk['mount']} has {disk['free_gb']} GB free", "warn", 360)
    for drive in cached("smart", 60, _smart):
        if drive["health_code"] not in (None, "1"):
            alert(f"smart:{drive['name']}", f"DRIVE HEALTH {drive['health']}: {drive['name']} "
                  f"(read {drive['date']})", "bad", 1440)
        if drive["temp_c"] and drive["temp_c"] >= limits["disk_temp_c"]:
            alert(f"temp:{drive['name']}", f"DRIVE HOT: {drive['name']} is {drive['temp_c']} C", "warn", 120)


def watcher():
    psutil.cpu_percent(None)
    while not STOP.wait(CFG["watch_interval_s"]):
        try:
            tick()
        except Exception as error:
            log(f"watcher error: {error!r}")


# ── http ───────────────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    timeout = 10
    protocol_version = "HTTP/1.1"

    def reply(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def authed(self, query):
        given = self.headers.get("X-Key") or query.get("key", [""])[0]
        return bool(CFG["key"]) and secrets.compare_digest(given.encode(), CFG["key"].encode())

    def body(self):
        try:
            size = int(self.headers.get("Content-Length") or 0)
            if not 0 < size <= 200_000:
                return {}
            data = json.loads(self.rfile.read(size).decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def handle_request(self, method):
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        arg = lambda k: query.get(k, [""])[0]
        path = parsed.path
        body = self.body() if method == "POST" else {}
        if path == "/api/ping":
            return self.reply({"ok": True, "name": "helm"})
        if not self.authed(query):
            return self.reply({"ok": False, "error": "bad or missing key"}, 401)
        route = (method, path)
        if route == ("GET", "/api/overview"):
            rows = sorted(scan().values(), key=lambda r: (r["group"], r["name"]))
            return self.reply({"ok": True, "services": rows, "system": system_brief(),
                               "last_alert": ALERTS[-1] if ALERTS else None, "time": time.strftime("%H:%M:%S")})
        if route == ("POST", "/api/service"):
            return self.reply(service_action(arg("name"), arg("action")))
        if route == ("GET", "/api/system"):
            return self.reply({"ok": True, **system_full(arg("counts") == "1")})
        if route == ("GET", "/api/ahk"):
            out = ahk("/api/status")
            out["autoformat"] = ahk("/api/af")
            out["pins"] = CFG["ahk_pins"]
            out["mode_labels"] = CFG["ahk_mode_labels"]
            return self.reply(out)
        if route == ("POST", "/api/ahk/action"):
            return self.reply(ahk("/api/action?" + urllib.parse.urlencode({"action": arg("action"), "id": arg("id")}),
                                  "POST"))
        if route == ("POST", "/api/ahk/af"):
            return self.reply(ahk("/api/af/set?" + urllib.parse.urlencode({"state": arg("state")})))
        if route == ("GET", "/api/userscripts"):
            return self.reply({"ok": True, "scripts": cached("userscripts", 600, _userscripts),
                               "autoformat": ahk("/api/af"), "settings": ahk("/api/af/cfg"),
                               "toggle_labels": CFG["userscripts"]["toggle_labels"]})
        if route == ("POST", "/api/userscripts/settings"):
            return self.reply(ahk("/api/af/cfg", "POST", body))
        if route == ("GET", "/api/userscripts/search"):
            kind = "mixes" if arg("kind") == "mixes" else "chars"
            return self.reply(ahk(f"/api/af/{kind}?" + urllib.parse.urlencode({"q": arg("q")[:200]})))
        if route == ("GET", "/api/targets"):
            return self.reply({"ok": True, "targets": list(CFG["command_targets"]),
                               "commands": CFG["commands_allowed"], "toggles": CFG["command_toggles"],
                               "fields": CFG["command_fields"], "choices": CFG["command_choices"],
                               "quick": [{"name": k, "label": v.get("label", k)} for k, v in CFG["quick"].items()]})
        if route == ("POST", "/api/cmd"):
            return self.reply(command(body))
        if route == ("GET", "/api/usage"):
            return self.reply(usage())
        if route == ("POST", "/api/quick"):
            return self.reply(quick(arg("name")))
        if route == ("GET", "/api/alerts"):
            return self.reply({"ok": True, "alerts": list(ALERTS)[::-1], "muted": STATE["muted"],
                               "thresholds": thresholds(), "telegram": bool(CFG["telegram"].get("enabled"))})
        if route == ("POST", "/api/alerts/config"):
            if isinstance(body.get("muted"), list):
                STATE["muted"] = [str(n) for n in body["muted"]][:200]
            if isinstance(body.get("thresholds"), dict):
                for key, value in body["thresholds"].items():
                    if key in CFG["thresholds"] and isinstance(value, (int, float)) and value >= 0:
                        STATE.setdefault("thresholds", {})[key] = value
            save_state()
            SCAN["at"] = 0
            return self.reply({"ok": True, "muted": STATE["muted"], "thresholds": thresholds()})
        if route == ("POST", "/api/alerts/test"):
            alert(f"test:{time.time()}", "Helm test alert", "good", 0)
            return self.reply({"ok": True, "sent": ALERTS[-1]["sent"]})
        return self.reply({"ok": False, "error": "not found"}, 404)

    def run(self, method):
        try:
            self.handle_request(method)
        except (BrokenPipeError, ConnectionError):
            pass
        except Exception as error:
            log(f"{method} {self.path.split('?')[0]} failed: {error!r}")
            try:
                self.reply({"ok": False, "error": str(error)[:300]}, 500)
            except OSError:
                pass

    def do_GET(self):
        self.run("GET")

    def do_POST(self):
        self.run("POST")

    def log_message(self, *_):
        pass


class Server(ThreadingHTTPServer):
    allow_reuse_address = False   # a second copy fails to bind and exits instead of doubling up
    daemon_threads = True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(HERE / "config.json"))
    args = parser.parse_args()
    load_config(Path(args.config))
    try:
        server = Server(("0.0.0.0", CFG["port"]), Handler)
    except OSError:
        sys.exit(1)
    threading.Thread(target=watcher, daemon=True).start()
    log(f"started on port {CFG['port']} with {len(CFG['services'])} services")
    try:
        server.serve_forever()
    finally:
        STOP.set()
        server.server_close()


if __name__ == "__main__":
    main()
