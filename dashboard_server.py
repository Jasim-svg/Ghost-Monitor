#!/usr/bin/env python3
"""
Ghost Monitor — Agency Security Dashboard Server
No git, no GitHub. Agents push data over HTTP on your LAN.

Install:  pip install flask flask-cors
Run:      python dashboard_server.py
"""

import json
import re
import uuid
import secrets
import socket
import base64
import shutil
import threading
import time
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta
from flask import Flask, jsonify, request, send_from_directory, Response, send_file
from flask_cors import CORS

log = logging.getLogger("monitor.server")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [SERVER] %(message)s")

app = Flask(__name__, static_folder="dashboard")
CORS(app)

BASE_DIR      = Path(__file__).parent.resolve()
DATA_DIR      = BASE_DIR / "data"
DEVICES_DIR   = DATA_DIR / "devices"
LOGS_DIR      = DATA_DIR / "logs"
FILES_DIR     = DATA_DIR / "file_browser"
TASKS_DIR     = DATA_DIR / "agent_tasks"
FILE_CACHE    = DATA_DIR / "file_cache"
SETTINGS_FILE = BASE_DIR / "monitor_settings.json"   # gitignored, local only

PORT = 8888
OFFLINE_THRESHOLD_MINUTES = 30
app.config["MAX_CONTENT_LENGTH"] = 150 * 1024 * 1024   # 150 MB upload limit

for _d in [DATA_DIR, DEVICES_DIR, LOGS_DIR, FILES_DIR, TASKS_DIR, FILE_CACHE]:
    _d.mkdir(parents=True, exist_ok=True)

# ═══════════════════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════════════════

def _load_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _safe_id(raw: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_\-]", "", str(raw))[:40]


def _safe_name(raw: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_\-]", "_", str(raw))[:40]


def _is_online(last_seen_iso: str) -> bool:
    try:
        last   = datetime.fromisoformat(last_seen_iso.replace("Z", "+00:00"))
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=OFFLINE_THRESHOLD_MINUTES)
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        return last > cutoff
    except Exception:
        return False


def _load_settings() -> dict:
    return _load_json(SETTINGS_FILE) or {}


def _save_settings(data: dict):
    SETTINGS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _get_secret() -> str:
    s = _load_settings()
    if not s.get("agency_secret"):
        s["agency_secret"] = secrets.token_hex(16)
        _save_settings(s)
        log.info(f"Generated agency secret: {s['agency_secret']}")
    return s["agency_secret"]


def _auth(req) -> bool:
    expected = _get_secret()
    provided = req.headers.get("X-Agent-Secret", "")
    return secrets.compare_digest(expected, provided)


def _get_lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

# ═══════════════════════════════════════════════════════════════════════════
#  Static
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/")
def index():
    return send_from_directory("dashboard", "index.html")


@app.route("/agent/monitor_agent.py")
def serve_agent_script():
    """Enrollment scripts download the agent from here."""
    return send_from_directory("agent", "monitor_agent.py",
                               as_attachment=False, mimetype="text/plain")

# ═══════════════════════════════════════════════════════════════════════════
#  Agent API — agents POST data here
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/api/agent/checkin", methods=["POST"])
def agent_checkin():
    if not _auth(request):
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    sid  = _safe_id(data.get("device_id", ""))
    if not sid:
        return jsonify({"error": "no device_id"}), 400
    _write_json(DEVICES_DIR / sid / "status.json", data)
    return jsonify({"ok": True})


@app.route("/api/agent/snapshot", methods=["POST"])
def agent_snapshot():
    if not _auth(request):
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    sid  = _safe_id(data.get("device_id", ""))
    if not sid:
        return jsonify({"error": "no device_id"}), 400

    snap     = data.get("snapshot", {})
    now      = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    ts       = now.strftime("%Y-%m-%dT%H-%M-%S")

    snap_dir = LOGS_DIR / sid / "snapshots" / date_str
    snap_dir.mkdir(parents=True, exist_ok=True)
    _write_json(snap_dir / f"{ts}.json", snap)

    # Keep last 2 days only
    snap_root = LOGS_DIR / sid / "snapshots"
    days = sorted(d for d in snap_root.iterdir() if d.is_dir())
    for old in days[:-2]:
        shutil.rmtree(old, ignore_errors=True)

    return jsonify({"ok": True})


@app.route("/api/agent/events", methods=["POST"])
def agent_events():
    if not _auth(request):
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    sid  = _safe_id(data.get("device_id", ""))
    if not sid:
        return jsonify({"error": "no device_id"}), 400

    now       = datetime.now()
    ts        = now.strftime("%Y-%m-%dT%H-%M-%S")
    event_dir = LOGS_DIR / sid / "events"
    event_dir.mkdir(parents=True, exist_ok=True)
    _write_json(event_dir / f"{ts}_events.json", data)

    # Keep last 200 event files
    files = sorted(event_dir.iterdir())
    for old in files[:-200]:
        old.unlink(missing_ok=True)

    return jsonify({"ok": True})


@app.route("/api/agent/log", methods=["POST"])
def agent_log():
    if not _auth(request):
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    sid  = _safe_id(data.get("device_id", ""))
    if not sid:
        return jsonify({"error": "no device_id"}), 400

    lines    = data.get("lines", [])
    log_path = LOGS_DIR / sid / "agent.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("\n".join(lines), encoding="utf-8")
    return jsonify({"ok": True})


@app.route("/api/agent/files", methods=["POST"])
def agent_files():
    if not _auth(request):
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    sid  = _safe_id(data.get("device_id", ""))
    if not sid:
        return jsonify({"error": "no device_id"}), 400
    _write_json(FILES_DIR / sid / "listing.json", data)
    return jsonify({"ok": True})


@app.route("/api/agent/file-content", methods=["POST"])
def agent_file_content():
    if not _auth(request):
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    sid  = _safe_id(data.get("device_id", ""))
    rid  = _safe_id(data.get("request_id", ""))
    if not sid or not rid:
        return jsonify({"error": "missing fields"}), 400

    # Remove the pending task
    (TASKS_DIR / sid / f"{rid}.json").unlink(missing_ok=True)

    cache_dir = FILE_CACHE / sid / rid
    cache_dir.mkdir(parents=True, exist_ok=True)

    if data.get("error"):
        _write_json(cache_dir / "status.json", {"status": "error", "error": data["error"]})
        return jsonify({"ok": True})

    try:
        content  = base64.b64decode(data["content_b64"])
        filename = data.get("filename", "download")
        (cache_dir / filename).write_bytes(content)
        _write_json(cache_dir / "status.json", {
            "status":   "ready",
            "filename": filename,
            "size":     len(content),
            "path":     data.get("path", ""),
        })
        log.info(f"File cached: {sid}/{rid}/{filename} ({len(content)} bytes)")
    except Exception as e:
        _write_json(cache_dir / "status.json", {"status": "error", "error": str(e)})

    return jsonify({"ok": True})


@app.route("/api/agent/tasks/<device_id>")
def agent_tasks(device_id: str):
    """Agents poll this for pending file-download requests."""
    if not _auth(request):
        return jsonify({"error": "unauthorized"}), 401
    sid      = _safe_id(device_id)
    task_dir = TASKS_DIR / sid
    if not task_dir.exists():
        return jsonify([])
    tasks = []
    for f in task_dir.iterdir():
        if f.suffix == ".json":
            d = _load_json(f)
            if d:
                tasks.append(d)
    return jsonify(tasks)

# ═══════════════════════════════════════════════════════════════════════════
#  Dashboard API — served to the browser
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/api/fleet")
def fleet():
    devices = []
    if DEVICES_DIR.exists():
        for d in sorted(DEVICES_DIR.iterdir()):
            if not d.is_dir() or d.name.startswith("."):
                continue
            data = _load_json(d / "status.json")
            if data:
                data["online"] = _is_online(data.get("last_seen", ""))
                devices.append(data)
    devices.sort(key=lambda d: d.get("last_seen", ""), reverse=True)
    return jsonify(devices)


@app.route("/api/stats")
def stats():
    devices = []
    if DEVICES_DIR.exists():
        for d in DEVICES_DIR.iterdir():
            if not d.is_dir() or d.name.startswith("."):
                continue
            data = _load_json(d / "status.json")
            if data:
                devices.append(data)

    online  = sum(1 for d in devices if _is_online(d.get("last_seen", "")))
    offline = len(devices) - online

    cutoff      = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    alert_count = 0
    if LOGS_DIR.exists():
        for dev_dir in LOGS_DIR.iterdir():
            event_dir = dev_dir / "events"
            if not event_dir.exists():
                continue
            for f in sorted(event_dir.iterdir(), reverse=True)[:10]:
                data = _load_json(f)
                if data:
                    for evt in data.get("events", []):
                        if evt.get("ts", "") >= cutoff and evt.get("severity") in ("warning", "critical"):
                            alert_count += 1

    return jsonify({
        "devices_online":  online,
        "devices_offline": offline,
        "devices_total":   len(devices),
        "alerts_1h":       alert_count,
        "server_time":     datetime.now(timezone.utc).isoformat(),
    })


@app.route("/api/device/<device_id>/status")
def device_status(device_id: str):
    sid  = _safe_id(device_id)
    data = _load_json(DEVICES_DIR / sid / "status.json")
    if not data:
        return jsonify({"error": "not found"}), 404
    data["online"] = _is_online(data.get("last_seen", ""))
    return jsonify(data)


@app.route("/api/device/<device_id>/snapshot")
def device_snapshot(device_id: str):
    sid       = _safe_id(device_id)
    snap_root = LOGS_DIR / sid / "snapshots"
    if not snap_root.exists():
        return jsonify({"error": "no snapshots yet"}), 404
    for day_dir in sorted(snap_root.iterdir(), reverse=True):
        if not day_dir.is_dir():
            continue
        for snap_file in sorted(day_dir.iterdir(), reverse=True):
            if snap_file.suffix == ".json":
                data = _load_json(snap_file)
                if data:
                    return jsonify(data)
    return jsonify({"error": "no snapshots found"}), 404


@app.route("/api/device/<device_id>/events")
def device_events(device_id: str):
    sid       = _safe_id(device_id)
    limit     = min(int(request.args.get("limit", 100)), 500)
    event_dir = LOGS_DIR / sid / "events"
    if not event_dir.exists():
        return jsonify([])
    all_events = []
    for f in sorted(event_dir.iterdir(), reverse=True)[:30]:
        data = _load_json(f)
        if data:
            for evt in data.get("events", []):
                evt.setdefault("device_id",    data.get("device_id", sid))
                evt.setdefault("display_name", data.get("display_name", sid))
                all_events.append(evt)
        if len(all_events) >= limit:
            break
    all_events.sort(key=lambda e: e.get("ts", ""), reverse=True)
    return jsonify(all_events[:limit])


@app.route("/api/device/<device_id>/logs")
def device_logs(device_id: str):
    sid      = _safe_id(device_id)
    log_file = LOGS_DIR / sid / "agent.log"
    if not log_file.exists():
        return jsonify({"lines": []})
    lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
    return jsonify({"lines": lines[-100:]})


@app.route("/api/device/<device_id>/files")
def device_files(device_id: str):
    """Full file listing pushed by the agent."""
    sid  = _safe_id(device_id)
    data = _load_json(FILES_DIR / sid / "listing.json")
    if not data:
        return jsonify({"files": [], "ts": None})
    return jsonify(data)


@app.route("/api/device/<device_id>/request-file", methods=["POST"])
def request_file(device_id: str):
    """Dashboard requests a file from the agent."""
    sid  = _safe_id(device_id)
    body = request.get_json(silent=True) or {}
    file_path = (body.get("path") or "").strip()
    if not file_path:
        return jsonify({"error": "path required"}), 400

    request_id = str(uuid.uuid4())[:12]
    task       = {
        "type":         "get_file",
        "request_id":   request_id,
        "path":         file_path,
        "requested_at": datetime.now().isoformat(),
    }
    _write_json(TASKS_DIR / sid / f"{request_id}.json", task)

    cache_dir = FILE_CACHE / sid / request_id
    cache_dir.mkdir(parents=True, exist_ok=True)
    _write_json(cache_dir / "status.json", {"status": "pending", "path": file_path})

    return jsonify({"ok": True, "request_id": request_id})


@app.route("/api/device/<device_id>/file-status/<request_id>")
def file_status(device_id: str, request_id: str):
    sid    = _safe_id(device_id)
    rid    = _safe_id(request_id)
    status = _load_json(FILE_CACHE / sid / rid / "status.json")
    if not status:
        return jsonify({"status": "not_found"}), 404
    return jsonify(status)


@app.route("/api/device/<device_id>/download/<request_id>")
def download_file(device_id: str, request_id: str):
    sid    = _safe_id(device_id)
    rid    = _safe_id(request_id)
    cache  = FILE_CACHE / sid / rid
    status = _load_json(cache / "status.json")
    if not status or status.get("status") != "ready":
        return jsonify({"error": "not ready yet"}), 404
    filename   = status.get("filename", "download")
    file_path  = cache / filename
    if not file_path.exists():
        return jsonify({"error": "file missing"}), 404
    return send_file(file_path, as_attachment=True, download_name=filename)


@app.route("/api/alerts")
def all_alerts():
    limit  = min(int(request.args.get("limit", 200)), 500)
    alerts = []
    if LOGS_DIR.exists():
        for dev_dir in LOGS_DIR.iterdir():
            if not dev_dir.is_dir():
                continue
            event_dir = dev_dir / "events"
            if not event_dir.exists():
                continue
            for f in sorted(event_dir.iterdir(), reverse=True)[:10]:
                data = _load_json(f)
                if data:
                    for evt in data.get("events", []):
                        if evt.get("severity") in ("warning", "critical"):
                            evt.setdefault("device_id",    data.get("device_id",    dev_dir.name))
                            evt.setdefault("display_name", data.get("display_name", dev_dir.name))
                            alerts.append(evt)
    alerts.sort(key=lambda a: a.get("ts", ""), reverse=True)
    return jsonify(alerts[:limit])


@app.route("/api/activity")
def activity_feed():
    limit  = min(int(request.args.get("limit", 200)), 500)
    events = []
    if LOGS_DIR.exists():
        for dev_dir in LOGS_DIR.iterdir():
            if not dev_dir.is_dir():
                continue
            event_dir = dev_dir / "events"
            if not event_dir.exists():
                continue
            for f in sorted(event_dir.iterdir(), reverse=True)[:10]:
                data = _load_json(f)
                if data:
                    for evt in data.get("events", []):
                        evt.setdefault("device_id",    data.get("device_id",    dev_dir.name))
                        evt.setdefault("display_name", data.get("display_name", dev_dir.name))
                        events.append(evt)
    events.sort(key=lambda e: e.get("ts", ""), reverse=True)
    return jsonify(events[:limit])

# ═══════════════════════════════════════════════════════════════════════════
#  Settings
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/api/settings", methods=["GET"])
def get_settings():
    secret  = _get_secret()
    lan_ip  = _get_lan_ip()
    s       = _load_settings()
    return jsonify({
        "agency_secret":         secret,
        "lan_ip":                lan_ip,
        "port":                  PORT,
        "commander_url_override": s.get("commander_url_override", ""),
        "enrollment_base_url":   s.get("commander_url_override") or f"http://{lan_ip}:{PORT}",
    })


@app.route("/api/settings", methods=["POST"])
def save_settings():
    body = request.get_json(silent=True) or {}
    s    = _load_settings()
    if body.get("regenerate_secret"):
        s["agency_secret"] = secrets.token_hex(16)
        log.warning("Agency secret regenerated — existing agents must be re-enrolled")
    if "commander_url_override" in body:
        val = (body["commander_url_override"] or "").strip()
        s["commander_url_override"] = val   # empty string = clear (back to LAN mode)
    _save_settings(s)
    return jsonify({"ok": True, "agency_secret": s.get("agency_secret", _get_secret())})

# ═══════════════════════════════════════════════════════════════════════════
#  Enrollment script generation
# ═══════════════════════════════════════════════════════════════════════════

def _make_windows_script(device_name: str, commander_url: str, secret: str) -> str:
    return f"""@echo off
setlocal EnableDelayedExpansion
:: ============================================================
::  Ghost Monitor — Device Setup
::  Device:    {device_name}
::  Commander: {commander_url}
::  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}
:: ============================================================
set DEVICE_NAME={device_name}
set COMMANDER_URL={commander_url}
set AGENT_SECRET={secret}
set INSTALL_DIR=%USERPROFILE%\\GhostMonitor

echo.
echo [Ghost Monitor] Setting up: %DEVICE_NAME%
echo.

:: ── Check Python ─────────────────────────────────────────
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Python not found.
    echo Install Python 3.8+ from https://python.org  ^(check "Add to PATH"^)
    pause & exit /b 1
)

:: ── Create directory ─────────────────────────────────────
if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%"

:: ── Download agent from commander ────────────────────────
echo [1/4] Downloading agent...
powershell -Command "try {{ Invoke-WebRequest '%COMMANDER_URL%/agent/monitor_agent.py' -OutFile '%INSTALL_DIR%\\monitor_agent.py' -UseBasicParsing }} catch {{ Write-Host $_.Exception.Message; exit 1 }}"
if %errorlevel% neq 0 (
    echo ERROR: Could not download agent.
    echo Make sure the commander PC is running and reachable on the network.
    pause & exit /b 1
)

:: ── Install dependency ────────────────────────────────────
echo [2/4] Installing psutil...
python -m pip install psutil --quiet

:: ── Write device config (use expanduser so spaces in username work) ──────────
echo [3/4] Configuring device...
set GM_NAME=%DEVICE_NAME%
set GM_URL=%COMMANDER_URL%
set GM_SECRET=%AGENT_SECRET%
python -c "
import json, hashlib, socket, os, sys
install_dir = os.path.join(os.path.expanduser('~'), 'GhostMonitor')
n   = os.environ['GM_NAME']
url = os.environ['GM_URL']
sec = os.environ['GM_SECRET']
did = 'dev_' + hashlib.md5((socket.gethostname() + n).encode()).hexdigest()[:8]
cfg = {{'device_id': did, 'display_name': n, 'commander_url': url, 'secret': sec}}
open(os.path.join(install_dir, '.device_id'), 'w').write(did)
open(os.path.join(install_dir, 'local_config.json'), 'w').write(json.dumps(cfg, indent=2))
print('Device ID:', did)
"
if %errorlevel% neq 0 (
    echo ERROR: Configuration failed.
    pause & exit /b 1
)

:: ── Detect pythonw (silent) or fall back to python minimised ──────────────
set PYWIN=pythonw
pythonw --version >nul 2>&1
if %errorlevel% neq 0 set PYWIN=python

:: ── Create silent scheduled task ─────────────────────────
echo [4/4] Creating startup task...
schtasks /create /tn "GhostMonitorAgent" /tr "%PYWIN% \\"%INSTALL_DIR%\\monitor_agent.py\\"" /sc onlogon /ru "%USERNAME%" /rl highest /f >nul 2>&1
if %errorlevel% neq 0 (
    echo WARNING: Could not create scheduled task ^(try running as Administrator^).
    echo You can start manually: %PYWIN% "%INSTALL_DIR%\\monitor_agent.py"
) else (
    echo Startup task created — agent will start silently on every login.
)

:: ── Start immediately ─────────────────────────────────────
echo.
echo Starting agent now...
start "" /min %PYWIN% "%INSTALL_DIR%\\monitor_agent.py"

echo.
echo ============================================================
echo  Setup complete!  Device "%DEVICE_NAME%" is now monitored.
echo  The agent runs silently in the background.
echo ============================================================
echo.
pause
"""


def _make_linux_script(device_name: str, commander_url: str, secret: str) -> str:
    return f"""#!/bin/bash
# ============================================================
#  Ghost Monitor — Device Setup
#  Device:    {device_name}
#  Commander: {commander_url}
#  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}
# ============================================================
set -e
DEVICE_NAME="{device_name}"
COMMANDER_URL="{commander_url}"
AGENT_SECRET="{secret}"
INSTALL_DIR="$HOME/GhostMonitor"

echo ""
echo "[Ghost Monitor] Setting up: $DEVICE_NAME"
echo ""

command -v python3 >/dev/null 2>&1 || {{ echo "ERROR: python3 not found."; exit 1; }}
PIP=$(command -v pip3 2>/dev/null || command -v pip 2>/dev/null)
[ -z "$PIP" ] && {{ echo "ERROR: pip not found."; exit 1; }}

mkdir -p "$INSTALL_DIR"

echo "[1/4] Downloading agent..."
curl -sf "$COMMANDER_URL/agent/monitor_agent.py" -o "$INSTALL_DIR/monitor_agent.py" || {{
    wget -q "$COMMANDER_URL/agent/monitor_agent.py" -O "$INSTALL_DIR/monitor_agent.py" || {{
        echo "ERROR: Could not download agent. Is the commander running?"
        exit 1
    }}
}}

echo "[2/4] Installing psutil..."
$PIP install psutil --quiet

echo "[3/4] Configuring device..."
python3 -c "
import json, hashlib, socket
n = '$DEVICE_NAME'
did = 'dev_' + hashlib.md5((socket.gethostname() + n).encode()).hexdigest()[:8]
cfg = {{'device_id': did, 'display_name': n, 'commander_url': '$COMMANDER_URL', 'secret': '$AGENT_SECRET'}}
open('$INSTALL_DIR/.device_id', 'w').write(did)
open('$INSTALL_DIR/local_config.json', 'w').write(json.dumps(cfg, indent=2))
print('Device ID:', did)
"

echo "[4/4] Creating cron job..."
# Wrapper script checks pidfile so only one instance runs at a time
cat > "$INSTALL_DIR/run_agent.sh" << 'RUNEOF'
#!/bin/bash
PIDFILE="$HOME/GhostMonitor/agent.pid"
if [ -f "$PIDFILE" ]; then
    OLD_PID=$(cat "$PIDFILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        exit 0   # already running
    fi
fi
echo $$ > "$PIDFILE"
python3 "$HOME/GhostMonitor/monitor_agent.py" >> "$HOME/GhostMonitor/agent.log" 2>&1
rm -f "$PIDFILE"
RUNEOF
chmod +x "$INSTALL_DIR/run_agent.sh"

CRON_LINE="*/8 * * * * $INSTALL_DIR/run_agent.sh"
( crontab -l 2>/dev/null | grep -v 'GhostMonitor' ; echo "$CRON_LINE" ) | crontab -

echo ""
echo "Starting agent now..."
nohup python3 "$INSTALL_DIR/monitor_agent.py" >> "$INSTALL_DIR/agent.log" 2>&1 &
disown

echo ""
echo "============================================================"
echo " Setup complete!  Device '$DEVICE_NAME' is now monitored."
echo " Agent runs silently. Cron keeps it running."
echo "============================================================"
"""


@app.route("/enroll/<filename>")
def enroll_script(filename: str):
    """
    GET /enroll/john-laptop.bat  → Windows setup script
    GET /enroll/john-laptop.sh   → Linux / macOS setup script
    """
    if "." not in filename:
        return "Bad request", 400
    name, ext = filename.rsplit(".", 1)
    if ext not in ("bat", "sh"):
        return "Only .bat and .sh supported", 400

    device_name = _safe_name(name)
    if not device_name:
        return "Invalid name", 400

    secret       = _get_secret()
    s            = _load_settings()
    lan_ip       = _get_lan_ip()
    commander_url = s.get("commander_url_override") or f"http://{lan_ip}:{PORT}"

    if ext == "bat":
        script  = _make_windows_script(device_name, commander_url, secret)
        dl_name = f"ghost_monitor_{device_name}.bat"
        mime    = "application/octet-stream"
    else:
        script  = _make_linux_script(device_name, commander_url, secret)
        dl_name = f"ghost_monitor_{device_name}.sh"
        mime    = "application/x-sh"

    log.info(f"Enrollment script generated: {device_name} ({ext}) → {commander_url}")
    return Response(
        script,
        mimetype=mime,
        headers={"Content-Disposition": f'attachment; filename="{dl_name}"'},
    )

# ═══════════════════════════════════════════════════════════════════════════
#  Entry point
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import webbrowser

    lan_ip = _get_lan_ip()
    secret = _get_secret()

    print("\n" + "═" * 54)
    print("  Ghost Monitor — Agency Security Dashboard")
    print(f"  Your dashboard:  http://localhost:{PORT}")
    print(f"  Network access:  http://{lan_ip}:{PORT}")
    print(f"  Agency secret:   {secret}")
    print(f"  Enrollment URL:  http://{lan_ip}:{PORT}/enroll/<name>.bat")
    print("  Press Ctrl+C to stop")
    print("═" * 54 + "\n")

    threading.Timer(1.5, lambda: webbrowser.open(f"http://localhost:{PORT}")).start()
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)
