"""Stand-in for helm_server.py used by the simulator tests. Same routes and answer shapes,
canned data, and it remembers what was posted (GET /_log).

Run: python3 mock_server.py [port]      key = test
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

KEY = "test"
LOG = []


def service(name, group, running, **extra):
    row = {"name": name, "group": group, "running": running, "pid": 4242 if running else None, "port": None,
           "port_ok": None, "task": False, "watch": False, "muted": False, "can_start": True}
    if running:
        row.update(ram_mb=120.5, in_ram_mb=20.0, handles=210, uptime_s=7300, cpu=1.5)
    row.update(extra)
    return row


STATE = {
    "services": {
        "svc_up": service("svc_up", "Bots", True, watch=True),
        "svc_down": service("svc_down", "Bots", False),
        "web_8080": service("web_8080", "Gallery", True, port=8080, port_ok=True, watch=True),
    },
    "ahk": [
        {"id": "C:\\ahk\\Alpha keys.ahk", "name": "Alpha keys", "state": "running", "detail_state": "running",
         "hotkeys": [{"hotkey": "Ctrl+Alt+A", "description": "Do the alpha thing", "context": "always"},
                     {"hotkey": "F13", "description": "Second key", "context": "Everything"}], "fkmodes": True},
        {"id": "C:\\ahk\\Beta.ahk", "name": "Beta", "state": "off", "detail_state": "off", "hotkeys": []},
    ],
    "af": True,
    "cfg": {"cleanTags": True, "lowercaseTags": False, "newChar": "", "newMix": ""},
    "muted": [],
    "thresholds": {"ram_percent": 92, "disk_free_gb": 10, "proc_ram_mb": 6000, "disk_temp_c": 70, "cooldown_min": 30},
    "alerts": [{"t": 2, "time": "2026-01-02 10:00", "key": "down:svc_x", "level": "bad",
                "text": "DOWN: svc_x is not running", "sent": True}],
    "running": False,
    "toggles": {},
}


def overview():
    rows = sorted(STATE["services"].values(), key=lambda r: (r["group"], r["name"]))
    for row in rows:
        row["muted"] = row["name"] in STATE["muted"]
    return {"ok": True, "services": rows,
            "system": {"cpu": 7.0, "ram_percent": 61.0, "ram_used_gb": 19.5, "ram_total_gb": 32.0},
            "last_alert": STATE["alerts"][0], "time": "10:00:00"}


def system(counts):
    return {"ok": True, "cpu": 7.0, "ram_percent": 61.0, "ram_used_gb": 19.5, "ram_total_gb": 32.0, "swap_percent": 12.0,
            "disks": [{"mount": "C:\\", "percent": 82.7, "used_gb": 3080.6, "free_gb": 644.2, "total_gb": 3724.8}],
            "disk_io": {"read_bytes": 2097152, "write_bytes": 1024}, "net_io": {"bytes_sent": 2048, "bytes_recv": 4096},
            "top": [{"pid": 1, "name": "big.exe", "ram_mb": 5292, "in_ram_mb": 300}],
            "boot": "2026-01-01 08:00",
            "smart": [{"name": "DriveGood", "date": "2026/01/02 09:00:00", "health_code": "1", "health": "Good",
                       "temp_c": 37, "life": 96, "reallocated": 0, "pending": None, "uncorrectable": None,
                       "power_on_hours": 100},
                      {"name": "DriveBad", "date": "2026/01/02 09:00:00", "health_code": "2", "health": "Caution",
                       "temp_c": 41, "life": None, "reallocated": 0, "pending": 1, "uncorrectable": 0,
                       "power_on_hours": None}],
            "counts": {"at": "2026-01-02 10:00", "items": [{"label": "Pictures", "count": 1234},
                                                           {"label": "Broken", "count": None, "error": "folder missing"}]}
            if counts else None}


def command(body):
    cmd = body.get("cmd")
    if cmd == "status":
        return {"ok": True, "running": STATE["running"], "chars": ["alpha"], "queue": 0, "log": ["line one", "Done. OK:1"]}
    if cmd == "run":
        STATE["running"] = True
        return {"ok": True, "running": True}
    if cmd == "stop":
        STATE["running"] = False
        return {"ok": True, "msg": "stopping after current gen"}
    if cmd == "toggle":
        name, value = body.get("name"), body.get("value")
        if name == "model":
            return {"ok": True, "model": value if value != "status" else "source"}
        STATE["toggles"][name] = bool(value)
        return {"ok": True, name: bool(value)}
    return {"ok": True, "echo": body}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def reply(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_any(self, method):
        parsed = urlparse(self.path)
        query = {k: v[0] for k, v in parse_qs(parsed.query, keep_blank_values=True).items()}
        size = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(size) or b"{}") if size else {}
        path = parsed.path
        if path == "/_log":
            return self.reply(LOG)
        if path == "/api/ping":
            return self.reply({"ok": True, "name": "helm"})
        if self.headers.get("X-Key") != KEY:
            return self.reply({"ok": False, "error": "bad or missing key"}, 401)
        if method == "POST":
            LOG.append({"path": path, "query": query, "body": body})
        route = (method, path)
        if route == ("GET", "/api/overview"):
            return self.reply(overview())
        if route == ("POST", "/api/service"):
            row = STATE["services"].get(query.get("name"))
            if not row:
                return self.reply({"ok": False, "error": "unknown service: " + str(query.get("name"))})
            up = query.get("action") in ("start", "restart")
            fresh = service(row["name"], row["group"], up, watch=row["watch"], port=row["port"],
                            port_ok=row["port_ok"] if up else None)
            STATE["services"][row["name"]] = fresh
            return self.reply({"ok": True, "pid": fresh["pid"]})
        if route == ("GET", "/api/system"):
            return self.reply(system(query.get("counts") == "1"))
        if route == ("GET", "/api/ahk"):
            return self.reply({"ok": True, "entries": STATE["ahk"], "autoformat": {"state": STATE["af"], "connected": True},
                               "mode_labels": ["One", "Two", "Three", "Four", "Five"],
                               "pins": [{"label": "Pin Beta", "id": "C:\\ahk\\Beta.ahk", "restart_if_running": True}]})
        if route == ("POST", "/api/ahk/action"):
            for entry in STATE["ahk"]:
                if entry["id"] == query.get("id"):
                    entry["state"] = "off" if query.get("action") == "stop" else "running"
            return self.reply({"ok": True, "entries": STATE["ahk"]})
        if route == ("POST", "/api/ahk/af"):
            STATE["af"] = query.get("state") == "on"
            return self.reply({"state": STATE["af"], "connected": True})
        if route == ("GET", "/api/userscripts"):
            return self.reply({"ok": True, "autoformat": {"state": STATE["af"], "connected": True},
                               "settings": {"cfg": STATE["cfg"], "ver": 1, "connected": True},
                               "toggle_labels": {"cleanTags": "Clean tags"},
                               "scripts": [{"name": "Tool A", "file": "Tool A.user.js", "version": "2.0",
                                            "description": "Does a thing", "sites": ["https://example.com/*"],
                                            "modified": "2026-01-01", "found_keys": ["Shift+F11"],
                                            "keys": ["Shift + F11 (Toggle)"], "note": "hand note", "category": "Editing"},
                                           {"name": "Tool B", "file": "Tool B.user.js", "version": "1", "description": "",
                                            "sites": [], "modified": "2026-01-01", "found_keys": [], "keys": [],
                                            "note": "", "category": ""}]})
        if route == ("POST", "/api/userscripts/settings"):
            STATE["cfg"].update(body)
            return self.reply({"cfg": STATE["cfg"], "ver": 2, "connected": True})
        if route == ("GET", "/api/userscripts/search"):
            return self.reply({"ok": True, "matches": ["found name"]})
        if route == ("GET", "/api/targets"):
            return self.reply({"ok": True, "targets": ["default", "alt"], "commands": ["status"],
                               "toggles": ["dryrun", "verbose"], "fields": ["count", "cap"],
                               "choices": {"model": ["source", "v5", "4.5"]},
                               "quick": [{"name": "nap", "label": "Take a nap"}]})
        if route == ("POST", "/api/cmd"):
            return self.reply(command(body))
        if route == ("GET", "/api/usage"):
            return self.reply({"ok": True, "fields": {"Plan left %": 100, "Credits": 2899}, "at": "10:00"})
        if route == ("POST", "/api/quick"):
            return self.reply({"ok": True, "pid": 999})
        if route == ("GET", "/api/alerts"):
            return self.reply({"ok": True, "alerts": STATE["alerts"], "muted": STATE["muted"],
                               "thresholds": STATE["thresholds"], "telegram": True})
        if route == ("POST", "/api/alerts/config"):
            if "muted" in body:
                STATE["muted"] = body["muted"]
            STATE["thresholds"].update(body.get("thresholds", {}))
            return self.reply({"ok": True, "muted": STATE["muted"], "thresholds": STATE["thresholds"]})
        if route == ("POST", "/api/alerts/test"):
            STATE["alerts"].insert(0, {"t": 3, "time": "2026-01-02 10:05", "key": "test", "level": "good",
                                       "text": "Helm test alert", "sent": True})
            return self.reply({"ok": True, "sent": True})
        return self.reply({"ok": False, "error": "not found"}, 404)

    def do_GET(self):
        self.handle_any("GET")

    def do_POST(self):
        self.handle_any("POST")

    def log_message(self, *_):
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8775
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
