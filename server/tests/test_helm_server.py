"""End to end test of helm_server.py against throwaway processes only.

Starts the real server on a test port with a config in a temp folder (path has a space and a
non-ASCII letter), dummy sleeper services, a fake job controller, a fake Telegram and a fake AHK
bridge. Every process it stops is one it started, by exact PID.

Run: python test_helm_server.py
"""
import json
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import psutil

SERVER = Path(__file__).resolve().parents[1] / "helm_server.py"
PORT, FAKE_PORT, DPORT = 18775, 18776, 18990
KEY = "test-key"
BASE = f"http://127.0.0.1:{PORT}"
PY = sys.executable
FAILS = []
SEEN = {"cmd_a": [], "cmd_b": [], "telegram": []}
STARTED = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


def call(path, method="GET", body=None, key=KEY):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(BASE + path, data, {"X-Key": key} if key else {}, method=method)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")


class Fake(BaseHTTPRequestHandler):
    def _send(self, payload):
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/api/status"):
            self._send({"ok": True, "entries": [{"id": "x.ahk", "running": True}]})
        elif self.path.startswith("/api/af/cfg"):
            self._send({"cfg": {"cleanTags": True}, "ver": 3, "connected": True})
        elif self.path.startswith("/api/af/chars"):
            self._send({"ok": True, "matches": ["a b"], "path": self.path})
        elif self.path.startswith("/api/af"):
            self._send({"state": True, "connected": False})
        elif self.path.startswith("/usage"):
            self._send({"usage": {"percent": 41}, "auth": self.headers.get("Authorization")})
        else:
            self._send({})

    def do_POST(self):
        size = int(self.headers.get("Content-Length") or 0)
        data = json.loads(self.rfile.read(size) or b"{}") if size else {}
        if self.path == "/a/cmd":
            SEEN["cmd_a"].append(data)
            self._send({"ok": True, "echo": data})
        elif self.path == "/b/cmd":
            SEEN["cmd_b"].append(data)
            self._send({"ok": True, "exclude": data.get("labels")})
        elif "/sendMessage" in self.path:
            SEEN["telegram"].append(data)
            self._send({"ok": True})
        elif self.path == "/api/af/cfg":
            self._send({"cfg": data, "ver": 4})
        elif self.path.startswith("/api/action"):
            self._send({"ok": True, "path": self.path})
        else:
            self._send({})

    def log_message(self, *_):
        pass


def svc(rows, name):
    return next((r for r in rows if r["name"] == name), None)


def overview():
    return call("/api/overview")[1]["services"]


def main():
    tmp = Path(tempfile.mkdtemp(prefix="helm test ü "))
    sleeper = tmp / "helm dummy sleeper.py"
    sleeper.write_text("import time,sys\nwhile True: time.sleep(1)\n", encoding="utf-8")
    listener = tmp / "helm_dummy_port.py"
    listener.write_text("import socket,sys,time\ns=socket.socket();s.bind(('127.0.0.1',int(sys.argv[-1])));s.listen()\n"
                        "while True: time.sleep(1)\n", encoding="utf-8")
    cdi = tmp / "cdi" / "Smart"
    (cdi / "DriveGood").mkdir(parents=True)
    (cdi / "DriveBad").mkdir()
    ini = '[PC]\nComputerName="X"\n[{n}FIRST]\nHealthStatus="1"\nTemperature="20"\n[{n}]\nDate="2026/01/01 10:00:00"\n' \
          'HealthStatus="{h}"\nTemperature="{t}"\nLife="96"\nReallocatedSectorsCount="4"\n'
    (cdi / "DriveGood" / "Smart.ini").write_text(ini.format(n="DriveGood", h=1, t=37), encoding="utf-8")
    (cdi / "DriveBad" / "Smart.ini").write_text(ini.format(n="DriveBad", h=2, t=41), encoding="utf-16")
    db = tmp / "c.sqlite"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE image_hashes (x)")
    conn.executemany("INSERT INTO image_hashes VALUES (?)", [(i,) for i in range(7)])
    conn.commit()
    conn.close()
    token = tmp / "tok.txt"
    token.write_text("SECRETTOKEN", encoding="utf-8")
    registry = tmp / "reg.py"
    registry.write_text(f"REGISTRY = {{'svc_b': {{'match': ['helm dummy sleeper.py', 'bbb'], "
                        f"'launch': [{PY!r}, {str(sleeper)!r}, 'bbb'], 'cwd': {str(tmp)!r}}}}}\n", encoding="utf-8")
    us1, us2 = tmp / "scripts one", tmp / "scripts ü two"
    us1.mkdir()
    us2.mkdir()
    head = "// ==UserScript==\n// @name         {n}\n// @version      {v}\n// @description  Does a thing\n" \
           "// @match        https://example.com/*\n// ==/UserScript==\n{body}\n"
    (us1 / "Tool A.user.js").write_text(head.format(n="Tool A (v1.0)", v="1.0", body="// Alt + Q old"), encoding="utf-8")
    time.sleep(0.05)
    (us2 / "Tool A-2.user.js").write_text(head.format(n="Tool A (v2.0)", v="2.0",
                                          body="// press Shift+F11 to toggle, ctrl + shift + v pastes; Shift+F11 again"), encoding="utf-8")
    (us2 / "Tool B backup.user.js").write_text(head.format(n="Tool B", v="1", body=""), encoding="utf-8")
    (us2 / "plain.user.js").write_text("no header here, e.key === 'x'", encoding="utf-8")
    sheet = tmp / "cheats.json"
    sheet.write_text(json.dumps([{"category": "Editing", "scripts": [{"name": "Tool A", "keys": ["Shift + F11 (Toggle)"], "desc": "hand note"}]}]),
                     encoding="utf-8")
    fake = f"http://127.0.0.1:{FAKE_PORT}"
    config = {
        "userscripts": {"folders": [str(us1), str(us2), str(tmp / "missing")], "cheatsheet": str(sheet),
                        "toggle_labels": {"cleanTags": "Clean tags"}},
        "port": PORT, "key": KEY, "watch_interval_s": 2, "registry_module": str(registry),
        "services": {
            "svc_a": {"match": ["helm dummy sleeper.py", "aaa"], "launch": [PY, str(sleeper), "aaa"],
                      "cwd": str(tmp), "hidden": True, "group": "Test", "watch": True},
            "svc_b": {"hidden": True, "group": "Test"},
            "svc_nolaunch": {"match": ["no_such_script_zz.py"]},
        },
        "discover": [{"prefix": "dport_", "script": "helm_dummy_port.py", "group": "Ports",
                      "launch": [PY, str(listener), "{port}"], "cwd": str(tmp), "hidden": True}],
        "ahk_bridge": fake,
        "command_targets": {"a": fake + "/a/cmd", "b": fake + "/b/cmd"},
        "commands_allowed": ["status", "exclude", "toggle"],
        "command_mirror": {"exclude": "b"},
        "quick": {"nap": {"label": "Nap", "launch": [PY, str(sleeper), "quick"], "cwd": str(tmp)}},
        "usage": {"url": fake + "/usage", "token_file": str(token), "fields": {"Left %": "usage.percent", "none": "a.b"}},
        "sqlite_counts": [{"label": "hashes", "path": str(db), "table": "image_hashes"},
                          {"label": "gone", "path": str(tmp / "nope.sqlite"), "table": "t"}],
        "crystaldiskinfo_dir": str(tmp / "cdi"),
        "telegram": {"enabled": True, "token_file": str(token), "chat_id": 123, "api_base": fake},
        "thresholds": {"cooldown_min": 30},
    }
    cfg_path = tmp / "config.json"
    cfg_path.write_text(json.dumps(config), encoding="utf-8")

    fake_server = ThreadingHTTPServer(("127.0.0.1", FAKE_PORT), Fake)
    threading.Thread(target=fake_server.serve_forever, daemon=True).start()
    server = subprocess.Popen([PY, str(SERVER), "--config", str(cfg_path)], creationflags=subprocess.CREATE_NO_WINDOW)
    STARTED.append(server.pid)
    port_dummy = None
    try:
        for _ in range(50):
            try:
                if call("/api/ping", key=None)[0] == 200:
                    break
            except OSError:
                time.sleep(0.2)
        check("ping needs no key", call("/api/ping", key=None)[1].get("name") == "helm")
        check("no key is refused", call("/api/overview", key=None)[0] == 401)
        check("wrong key is refused", call("/api/overview", key="nope")[0] == 401)
        check("post without key is refused", call("/api/service?name=svc_a&action=start", "POST", key=None)[0] == 401)
        check("second copy cannot bind", subprocess.run([PY, str(SERVER), "--config", str(cfg_path)], timeout=30,
                                                         creationflags=subprocess.CREATE_NO_WINDOW).returncode == 1)

        sibling = tmp / "alt_helm dummy sleeper.py"      # name contains svc_a's script name
        sibling.write_text(sleeper.read_text(encoding="utf-8"), encoding="utf-8")
        sib = subprocess.Popen([PY, str(sibling), "aaa"], creationflags=subprocess.CREATE_NO_WINDOW)
        STARTED.append(sib.pid)
        time.sleep(5.5)
        rows = overview()
        check("sibling script with a longer name is not mistaken for svc_a", svc(rows, "svc_a")["running"] is False, str(svc(rows, "svc_a")))
        check("both services listed and down", svc(rows, "svc_a")["running"] is False and svc(rows, "svc_b")["running"] is False)
        check("registry module entry merged with config extras", svc(rows, "svc_b")["group"] == "Test")

        r = call("/api/service?name=svc_a&action=start", "POST")[1]
        pid_a = r.get("pid")
        STARTED.append(pid_a)
        check("start a", r.get("ok") and psutil.pid_exists(pid_a), str(r))
        check("b untouched by start a", svc(overview(), "svc_b")["running"] is False)
        check("start twice refused", call("/api/service?name=svc_a&action=start", "POST")[1].get("ok") is False)
        r = call("/api/service?name=svc_b&action=start", "POST")[1]
        pid_b = r.get("pid")
        STARTED.append(pid_b)
        check("start b", r.get("ok") and pid_b != pid_a, str(r))
        row = svc(overview(), "svc_a")
        check("metrics present", row["ram_mb"] > 0 and row["handles"] > 0 and row["uptime_s"] >= 0, str(row))

        r = call("/api/service?name=svc_a&action=stop", "POST")[1]
        check("stop a kills only a", r.get("ok") and not psutil.pid_exists(pid_a) and psutil.pid_exists(pid_b), str(r))
        check("sibling survives stop a", sib.poll() is None)
        check("stop when down refused", call("/api/service?name=svc_a&action=stop", "POST")[1].get("ok") is False)
        r = call("/api/service?name=svc_b&action=restart", "POST")[1]
        new_b = r.get("pid")
        STARTED.append(new_b)
        check("restart b gives a new pid", r.get("ok") and new_b and new_b != pid_b and not psutil.pid_exists(pid_b), str(r))
        check("a still down after restart b", svc(overview(), "svc_a")["running"] is False)
        check("unknown service refused", call("/api/service?name=explorer.exe&action=stop", "POST")[1].get("ok") is False)
        check("bad action refused", call("/api/service?name=svc_b&action=nuke", "POST")[1].get("ok") is False)
        check("no launch command refused", "no start command" in str(call("/api/service?name=svc_nolaunch&action=start", "POST")[1]))

        port_dummy = subprocess.Popen([PY, str(listener), str(DPORT)], creationflags=subprocess.CREATE_NO_WINDOW)
        STARTED.append(port_dummy.pid)
        time.sleep(10)      # scan cache (5 s) + two watcher checks (2 s each)
        row = svc(overview(), f"dport_{DPORT}")
        check("port copy discovered", bool(row) and row["port"] == DPORT and row["pid"] == port_dummy.pid, str(row))
        check("port answering after a watcher check", bool(row) and row["port_ok"] is True, str(row))
        r = call(f"/api/service?name=dport_{DPORT}&action=restart", "POST")[1]
        STARTED.append(r.get("pid"))
        check("restart a discovered port copy", r.get("ok") and r.get("pid") != port_dummy.pid, str(r))

        # alerts: svc_a is watched and down
        time.sleep(7)
        downs = [m for m in SEEN["telegram"] if "DOWN: svc_a" in m.get("text", "")]
        check("one down alert sent", len(downs) == 1 and downs[0]["chat_id"] == 123, str(SEEN["telegram"]))
        time.sleep(5)
        check("cooldown holds", len([m for m in SEEN["telegram"] if "DOWN: svc_a" in m.get("text", "")]) == 1)
        check("bad drive alert sent once", len([m for m in SEEN["telegram"] if "DriveBad" in m.get("text", "")]) == 1)
        check("good drive not alerted", not [m for m in SEEN["telegram"] if "DriveGood" in m.get("text", "")])
        r = call("/api/service?name=svc_a&action=start", "POST")[1]
        STARTED.append(r.get("pid"))
        time.sleep(5)
        check("back alert sent", any("BACK: svc_a" in m.get("text", "") for m in SEEN["telegram"]))
        alerts = call("/api/alerts")[1]
        check("alert history newest first", alerts["alerts"][0]["text"].startswith("BACK") and alerts["alerts"][0]["sent"] is True)
        check("token not in log", "SECRETTOKEN" not in (tmp / "helm.log").read_text(encoding="utf-8"))
        r = call("/api/alerts/config", "POST", {"muted": ["svc_a"], "thresholds": {"ram_percent": 80, "bogus": 1, "disk_free_gb": -5}})[1]
        state = json.loads((tmp / "state.json").read_text(encoding="utf-8"))
        check("mute and threshold saved", state["muted"] == ["svc_a"] and state["thresholds"] == {"ram_percent": 80}, str(state))
        check("muted flag shown", svc(overview(), "svc_a")["muted"] is True)
        check("test alert", call("/api/alerts/test", "POST")[1].get("sent") is True)

        # commands
        r = call("/api/cmd", "POST", {"target": "a", "cmd": "toggle", "name": "dryrun", "value": True})[1]
        check("command forwarded exactly", SEEN["cmd_a"][-1] == {"cmd": "toggle", "name": "dryrun", "value": True}, str(SEEN["cmd_a"]))
        check("command not allowed refused", call("/api/cmd", "POST", {"target": "a", "cmd": "launch"})[1].get("ok") is False
              and all(c.get("cmd") != "launch" for c in SEEN["cmd_a"]))
        check("unknown target refused", call("/api/cmd", "POST", {"target": "zz", "cmd": "status"})[1].get("ok") is False)
        r = call("/api/cmd", "POST", {"target": "a", "cmd": "exclude", "labels": "todd"})[1]
        check("set is mirrored", SEEN["cmd_b"] == [{"cmd": "exclude", "labels": "todd"}] and r.get("b mirror", {}).get("exclude") == "todd", str(r))
        call("/api/cmd", "POST", {"target": "a", "cmd": "exclude", "labels": ""})
        check("show is not mirrored", len(SEEN["cmd_b"]) == 1)
        check("default target is the first", call("/api/cmd", "POST", {"cmd": "status"})[1].get("echo") == {"cmd": "status"})

        # system, usage, quick, ahk
        system = call("/api/system?counts=1")[1]
        smart = {d["name"]: d for d in system["smart"]}
        check("system fields", system["ram_total_gb"] > 0 and len(system["disks"]) > 0 and len(system["top"]) == 10, str(system)[:300])
        check("smart latest section, both encodings", smart["DriveGood"]["temp_c"] == 37 and smart["DriveGood"]["health"] == "Good"
              and smart["DriveBad"]["health"] == "Caution" and smart["DriveBad"]["reallocated"] == 4, str(smart))
        counts = {c["label"]: c for c in system["counts"]["items"]}
        check("sqlite count and missing db", counts["hashes"]["count"] == 7 and counts["gone"]["count"] is None, str(counts))
        check("counts not run unless asked", "counts" in call("/api/system")[1])
        time.sleep(1.2)
        io = call("/api/system")[1]
        use = call("/api/usage")[1]
        check("usage fields", use.get("fields") == {"Left %": 41, "none": None}, str(use))
        r = call("/api/quick?name=nap", "POST")[1]
        STARTED.append(r.get("pid"))
        check("quick action starts", r.get("ok") and psutil.pid_exists(r["pid"]), str(r))
        check("quick action single instance", call("/api/quick?name=nap", "POST")[1].get("ok") is False)
        check("unknown quick refused", call("/api/quick?name=zz", "POST")[1].get("ok") is False)
        a = call("/api/ahk")[1]
        check("ahk list forwarded", a.get("entries") == [{"id": "x.ahk", "running": True}] and a["autoformat"]["state"] is True, str(a))
        r = call("/api/ahk/action?action=restart&id=C%3A%5Ca%20b%5C%C3%BC.ahk", "POST")[1]
        check("ahk action forwarded with odd path", "id=C%3A%5Ca+b%5C%C3%BC.ahk" in r.get("path", ""), str(r))
        check("unknown route 404", call("/api/nope")[0] == 404)
        us = call("/api/userscripts")[1]
        by = {s["name"]: s for s in us["scripts"]}
        check("userscripts: newest copy wins, backup skipped, headerless kept",
              sorted(by) == ["Tool A (v2.0)", "plain.user"], str(sorted(by)))
        a2 = by.get("Tool A (v2.0)", {})
        check("userscripts: header, found keys, cheatsheet note",
              a2.get("version") == "2.0" and a2.get("found_keys") == ["Shift+F11", "Ctrl+Shift+V"]
              and a2.get("keys") == ["Shift + F11 (Toggle)"] and a2.get("note") == "hand note"
              and a2.get("sites") == ["https://example.com/*"], str(a2))
        check("userscripts: settings and labels", us["settings"]["cfg"] == {"cleanTags": True}
              and us["toggle_labels"] == {"cleanTags": "Clean tags"} and us["autoformat"]["state"] is True, str(us)[:300])
        r = call("/api/userscripts/settings", "POST", {"cleanTags": False})[1]
        check("userscripts: setting forwarded", r == {"cfg": {"cleanTags": False}, "ver": 4}, str(r))
        r = call("/api/userscripts/search?kind=chars&q=a%20b%26c")[1]
        check("userscripts: search forwarded", r.get("matches") == ["a b"] and "q=a+b%26c" in r.get("path", ""), str(r))

        # resource check
        proc = psutil.Process(server.pid)
        for _ in range(20):
            call("/api/overview"); call("/api/system")
        h0, m0, c0 = proc.num_handles(), proc.memory_info().rss / 1048576, sum(proc.cpu_times()[:2])
        t0 = time.time()
        for _ in range(200):
            call("/api/overview"); call("/api/system")
        span = time.time() - t0
        h1, m1, c1 = proc.num_handles(), proc.memory_info().rss / 1048576, sum(proc.cpu_times()[:2])
        print(f"RESOURCE 400 calls in {span:.1f}s: handles {h0}->{h1}, RAM {m0:.1f}->{m1:.1f} MB, cpu {c1 - c0:.2f}s, threads {proc.num_threads()}")
        check("no handle leak", h1 - h0 <= 5, f"{h0}->{h1}")
        check("no memory climb", m1 - m0 <= 3, f"{m0:.1f}->{m1:.1f}")
        c2 = sum(proc.cpu_times()[:2])
        time.sleep(20)
        idle = sum(proc.cpu_times()[:2]) - c2
        print(f"RESOURCE idle 20s with a 2s watcher (10 checks): cpu {idle:.2f}s = {idle / 10 * 1000:.0f} ms per check")
    finally:
        for pid in [p for p in STARTED if p]:
            try:
                psutil.Process(pid).kill()
            except psutil.Error:
                pass
        fake_server.shutdown()
        fake_server.server_close()
    print(f"temp dir left in place: {tmp}")
    print("FAILED: " + ", ".join(FAILS) if FAILS else "ALL PASS")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
