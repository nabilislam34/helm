# Helm

A phone dashboard for a Windows PC that runs a lot of long-lived scripts.

- **`server/`**: a small Python service on the PC. It lists a fixed set of services, starts and stops them by exact process ID, reports system health, forwards commands to local job controllers, and sends alerts to Telegram.
- **`ios/`**: a native SwiftUI iPhone app with five tabs: Services, Scripts (AutoHotkey and userscripts), System, Commands, Alerts.

Everything specific to one machine (service list, paths, ports, keys) lives in `server/config.json`, which is not committed. Start from `server/config.example.json`.

## Design rules

- **Exact PID only.** A service is matched by whole names in its command line, re-checked right before a stop. Nothing is ever stopped by process name.
- **Cheap by default.** One cached process scan limited to a few process names, no WMI, no PowerShell, one background check per minute. Folder counts, database counts and usage lookups run only when asked and are cached.
- **No windows.** Every process the service starts is windowless.
- **Local network only.** Every route except `/api/ping` needs the `X-Key` header.

## Server

    pip install psutil
    copy server\config.example.json server\config.json
    pythonw server\helm_server.py

`server\launch-helm-hidden.vbs` starts it with no window, for a logon scheduled task.

Tests start the real server on a test port with throwaway dummy services, a fake job controller and a fake Telegram:

    python server\tests\test_helm_server.py

### Routes

| Route | Does |
|---|---|
| `GET /api/overview` | services with pid, memory, handles, CPU, uptime, port state |
| `POST /api/service?name=&action=start\|stop\|restart` | control one service |
| `GET /api/system[?counts=1]` | CPU, RAM, disks, disk and network rates, drive health, top processes, counts |
| `GET /api/ahk`, `POST /api/ahk/action` | AutoHotkey scripts through a local bridge |
| `GET /api/userscripts`, `POST /api/userscripts/settings` | userscript list, hotkey cheatsheets, shared settings |
| `GET /api/targets`, `POST /api/cmd` | allow-listed commands to local job controllers |
| `GET /api/alerts`, `POST /api/alerts/config`, `POST /api/alerts/test` | alert history, mutes, limits |

Drive health is read from the status files CrystalDiskInfo already writes; Helm never queries drives itself.

## iOS app

Built with XcodeGen on GitHub Actions (`.github/workflows/build-ios.yml`), no Mac needed. The `build` job produces an unsigned `Helm.ipa` to sideload. The `uitest` job runs the app in an iOS Simulator against `ios/Tests/mock_server.py`; it runs when the commit message contains `[uitest]` or on a manual run.

On first launch the app asks for the PC address and key. A link of the form `helm://setup?server=192.168.1.10:8775&key=...` fills both in.
