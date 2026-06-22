#!/usr/bin/env python3
"""
Ghost Monitor v2 — Agency Security Dashboard Server
Per-device token auth. Consent gate. Access logging.

Install:  pip install flask flask-cors
Run:      python dashboard_server.py
"""

import json
import re
import uuid
import hashlib
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
CONSENT_DIR   = DATA_DIR / "consent"
SETTINGS_FILE = BASE_DIR / "monitor_settings.json"   # gitignored
TOKENS_FILE   = BASE_DIR / "tokens.json"              # gitignored

PORT = 8888
OFFLINE_THRESHOLD_MINUTES = 30
POLICY_VERSION = "1.0"
app.config["MAX_CONTENT_LENGTH"] = 150 * 1024 * 1024   # 150 MB upload limit

POLICY_TEXT = """GHOST MONITOR — MONITORING POLICY (v1.0)

This device is monitored by your agency administrator.
The following data is collected every 5–10 minutes:

  • Processes      — running program names, CPU%, memory usage
  • Network        — active connections, external IPs flagged
  • System metrics — CPU%, RAM%, disk usage
  • Active window  — title of the currently focused window
  • File listings  — names and sizes in Documents, Desktop, Downloads
                     (file contents are NOT read unless an admin
                      explicitly requests a download of a specific file)

Data is sent only to the agency commander PC.
Only your administrator can view it.

If a file is downloaded by an administrator, it is recorded in
your local transparency log (right-click the tray icon to view).

Questions? Contact your agency administrator.
"""

for _d in [DATA_DIR, DEVICES_DIR, LOGS_DIR, FILES_DIR, TASKS_DIR,
           FILE_CACHE, CONSENT_DIR]:
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


def _get_lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


# ── Per-device token management ─────────────────────────────────────────────

def _load_tokens() -> dict:
    return _load_json(TOKENS_FILE) or {}


def _save_tokens(tokens: dict):
    TOKENS_FILE.write_text(json.dumps(tokens, indent=2), encoding="utf-8")


def _generate_token() -> str:
    return secrets.token_hex(16)


def _save_token(device_id: str, token: str) -> None:
    tokens = _load_tokens()
    tokens[device_id] = token
    _save_tokens(tokens)


def _get_device_token(device_id: str):
    return _load_tokens().get(device_id)


def _auth(req, device_id: str) -> bool:
    """Per-device token auth. Compares X-Agent-Token header against stored token."""
    expected = _get_device_token(device_id)
    if not expected:
        return False
    provided = req.headers.get("X-Agent-Token", "")
    if not provided:
        return False
    return secrets.compare_digest(expected, provided)


def _get_or_create_enrollment(device_name: str) -> tuple:
    """Return (device_id, token) for a device name. Creates token on first call.
    Idempotent: same name always returns the same device_id, and reuses the token
    if one already exists (so re-downloading the enrollment link is safe)."""
    device_id = "dev_" + hashlib.md5(device_name.encode()).hexdigest()[:8]
    tokens = _load_tokens()
    if device_id in tokens:
        return device_id, tokens[device_id]
    token = _generate_token()
    _save_token(device_id, token)
    log.info(f"Enrollment created: {device_name} → {device_id}")
    return device_id, token


# ── Access log ───────────────────────────────────────────────────────────────

def _append_access_log(device_id: str, entry: dict, dedup_key: str = None):
    """Append an entry to the device's consent/access_log.json.
    If dedup_key is provided, skips the append if the last entry has the same key
    (prevents spamming the log with repeated 'monitoring paused' entries)."""
    log_path = CONSENT_DIR / device_id / "access_log.json"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    entries = []
    if log_path.exists():
        try:
            entries = json.loads(log_path.read_text(encoding="utf-8"))
        except Exception:
            entries = []
    if dedup_key and entries and entries[-1].get("_dedup") == dedup_key:
        return
    if dedup_key:
        entry["_dedup"] = dedup_key
    entries.append(entry)
    log_path.write_text(json.dumps(entries, indent=2), encoding="utf-8")

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


@app.route("/agent/tray_icon.py")
def serve_tray_script():
    """Enrollment scripts download the tray indicator from here."""
    return send_from_directory("agent", "tray_icon.py",
                               as_attachment=False, mimetype="text/plain")


@app.route("/api/policy")
def get_policy():
    """Returns the plain-text monitoring policy shown to staff at enrollment."""
    return Response(POLICY_TEXT, mimetype="text/plain")

# ═══════════════════════════════════════════════════════════════════════════
#  Consent endpoint (no auth required — called before token is used)
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/api/consent/ack", methods=["POST"])
def consent_ack():
    """Records that a staff member acknowledged the monitoring policy.
    No auth required — the device does not have a usable token until after this step.
    The device_id must already exist in tokens.json (created at link-generation time)
    so that only legitimately enrolled devices can record consent."""
    data      = request.get_json(silent=True) or {}
    device_id = _safe_id(data.get("device_id", ""))
    if not device_id:
        return jsonify({"error": "device_id required"}), 400
    if not _get_device_token(device_id):
        return jsonify({"error": "unknown device — generate an enrollment link first"}), 404

    ack = {
        "device_id":      device_id,
        "ack_by":         data.get("ack_by", "enrollment"),
        "ts":             datetime.now(timezone.utc).isoformat(),
        "policy_version": data.get("policy_version", POLICY_VERSION),
    }
    _write_json(CONSENT_DIR / device_id / "ack.json", ack)
    log.info(f"Consent ACK: {device_id} (policy v{ack['policy_version']})")
    return jsonify({"ok": True})

# ═══════════════════════════════════════════════════════════════════════════
#  Agent API — agents POST data here (all require per-device X-Agent-Token)
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/api/agent/checkin", methods=["POST"])
def agent_checkin():
    data = request.get_json(silent=True) or {}
    sid  = _safe_id(data.get("device_id", ""))
    if not sid or not _auth(request, sid):
        return jsonify({"error": "unauthorized"}), 401

    _write_json(DEVICES_DIR / sid / "status.json", data)

    # Log pause events to the access log (with dedup so we only log once per pause)
    if data.get("paused"):
        paused_until = data.get("paused_until", "")
        _append_access_log(sid, {
            "type":          "monitoring_paused",
            "paused_until":  paused_until,
            "ts":            datetime.now(timezone.utc).isoformat(),
        }, dedup_key=f"paused_{paused_until}")

    return jsonify({"ok": True})


@app.route("/api/agent/snapshot", methods=["POST"])
def agent_snapshot():
    data = request.get_json(silent=True) or {}
    sid  = _safe_id(data.get("device_id", ""))
    if not sid or not _auth(request, sid):
        return jsonify({"error": "unauthorized"}), 401

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
    data = request.get_json(silent=True) or {}
    sid  = _safe_id(data.get("device_id", ""))
    if not sid or not _auth(request, sid):
        return jsonify({"error": "unauthorized"}), 401

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
    data = request.get_json(silent=True) or {}
    sid  = _safe_id(data.get("device_id", ""))
    if not sid or not _auth(request, sid):
        return jsonify({"error": "unauthorized"}), 401

    lines    = data.get("lines", [])
    log_path = LOGS_DIR / sid / "agent.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("\n".join(lines), encoding="utf-8")
    return jsonify({"ok": True})


@app.route("/api/agent/files", methods=["POST"])
def agent_files():
    data = request.get_json(silent=True) or {}
    sid  = _safe_id(data.get("device_id", ""))
    if not sid or not _auth(request, sid):
        return jsonify({"error": "unauthorized"}), 401
    _write_json(FILES_DIR / sid / "listing.json", data)
    return jsonify({"ok": True})


@app.route("/api/agent/file-content", methods=["POST"])
def agent_file_content():
    data = request.get_json(silent=True) or {}
    sid  = _safe_id(data.get("device_id", ""))
    rid  = _safe_id(data.get("request_id", ""))
    if not sid or not _auth(request, sid):
        return jsonify({"error": "unauthorized"}), 401
    if not rid:
        return jsonify({"error": "missing request_id"}), 400

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

        # Section 4: Log file access in the consent access log
        _append_access_log(sid, {
            "type": "file_access",
            "file": filename,
            "path": data.get("path", ""),
            "ts":   datetime.now(timezone.utc).isoformat(),
        })
    except Exception as e:
        _write_json(cache_dir / "status.json", {"status": "error", "error": str(e)})

    return jsonify({"ok": True})


@app.route("/api/agent/tasks/<device_id>")
def agent_tasks(device_id: str):
    """Agents poll this for pending file-download requests."""
    sid = _safe_id(device_id)
    if not _auth(request, sid):
        return jsonify({"error": "unauthorized"}), 401
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


@app.route("/api/agent/access-log/<device_id>")
def agent_access_log_sync(device_id: str):
    """Agent polls this to sync the access log to its local copy.
    Returns entries newer than the 'since' query param (ISO timestamp)."""
    sid = _safe_id(device_id)
    if not _auth(request, sid):
        return jsonify({"error": "unauthorized"}), 401

    since    = request.args.get("since", "")
    log_path = CONSENT_DIR / sid / "access_log.json"
    entries  = []
    if log_path.exists():
        try:
            entries = json.loads(log_path.read_text(encoding="utf-8"))
        except Exception:
            entries = []
    if since:
        entries = [e for e in entries if e.get("ts", "") > since]
    return jsonify(entries)

# ═══════════════════════════════════════════════════════════════════════════
#  Dashboard API — served to the browser
# ═══════════════════════════════════════════════════════════════════════════

def _consent_info(device_id: str) -> dict:
    ack = _load_json(CONSENT_DIR / device_id / "ack.json")
    if ack:
        return {"consented": True, "consent_ts": ack.get("ts"), "policy_version": ack.get("policy_version")}
    return {"consented": False, "consent_ts": None, "policy_version": None}


@app.route("/api/fleet")
def fleet():
    devices = []
    if DEVICES_DIR.exists():
        for d in sorted(DEVICES_DIR.iterdir()):
            if not d.is_dir() or d.name.startswith("."):
                continue
            data = _load_json(d / "status.json")
            if data:
                data["online"]  = _is_online(data.get("last_seen", ""))
                data.update(_consent_info(_safe_id(data.get("device_id", d.name))))
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

    online    = sum(1 for d in devices if _is_online(d.get("last_seen", "")))
    offline   = len(devices) - online
    consented = sum(1 for d in devices
                    if (CONSENT_DIR / _safe_id(d.get("device_id", "")) / "ack.json").exists())

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
        "devices_online":    online,
        "devices_offline":   offline,
        "devices_total":     len(devices),
        "devices_consented": consented,
        "alerts_1h":         alert_count,
        "server_time":       datetime.now(timezone.utc).isoformat(),
    })


@app.route("/api/device/<device_id>/status")
def device_status(device_id: str):
    sid  = _safe_id(device_id)
    data = _load_json(DEVICES_DIR / sid / "status.json")
    if not data:
        return jsonify({"error": "not found"}), 404
    data["online"] = _is_online(data.get("last_seen", ""))
    data.update(_consent_info(sid))
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
    sid  = _safe_id(device_id)
    data = _load_json(FILES_DIR / sid / "listing.json")
    if not data:
        return jsonify({"files": [], "ts": None})
    return jsonify(data)


@app.route("/api/device/<device_id>/request-file", methods=["POST"])
def request_file(device_id: str):
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
    filename  = status.get("filename", "download")
    file_path = cache / filename
    if not file_path.exists():
        return jsonify({"error": "file missing"}), 404
    return send_file(file_path, as_attachment=True, download_name=filename)


@app.route("/api/device/<device_id>/revoke", methods=["POST"])
def revoke_device(device_id: str):
    """Revoke a device's token. Clears consent record too.
    Historical logs are kept. Device must be re-enrolled to resume monitoring."""
    sid    = _safe_id(device_id)
    tokens = _load_tokens()
    if sid in tokens:
        del tokens[sid]
        _save_tokens(tokens)
        log.info(f"Device revoked: {sid}")
    # Clear consent so it shows as not-consented in dashboard
    ack_path = CONSENT_DIR / sid / "ack.json"
    ack_path.unlink(missing_ok=True)
    return jsonify({"ok": True})


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
    lan_ip = _get_lan_ip()
    s      = _load_settings()
    return jsonify({
        "lan_ip":                lan_ip,
        "port":                  PORT,
        "commander_url_override": s.get("commander_url_override", ""),
        "enrollment_base_url":   s.get("commander_url_override") or f"http://{lan_ip}:{PORT}",
    })


@app.route("/api/settings", methods=["POST"])
def save_settings():
    body = request.get_json(silent=True) or {}
    s    = _load_settings()
    if "commander_url_override" in body:
        val = (body["commander_url_override"] or "").strip()
        s["commander_url_override"] = val
    _save_settings(s)
    return jsonify({"ok": True})

# ═══════════════════════════════════════════════════════════════════════════
#  Enrollment script generation (v2 — consent gate + per-device token + tray)
# ═══════════════════════════════════════════════════════════════════════════

def _make_windows_script(device_name: str, device_id: str,
                         commander_url: str, token: str) -> str:
    generated = datetime.now().strftime('%Y-%m-%d %H:%M')
    return f"""@echo off
setlocal EnableDelayedExpansion
:: ================================================================
::  Ghost Monitor v2 — Device Setup
::  Device:    {device_name}
::  Commander: {commander_url}
::  Generated: {generated}
:: ================================================================
set DEVICE_ID={device_id}
set DEVICE_NAME={device_name}
set COMMANDER_URL={commander_url}
set DEVICE_TOKEN={token}
set INSTALL_DIR=%USERPROFILE%\\GhostMonitor

:: ── Step 1: Consent Disclosure ───────────────────────────────────
echo.
echo ================================================================
echo  GHOST MONITOR ^| MONITORING DISCLOSURE
echo ================================================================
echo.
echo  This device will be monitored by your agency administrator.
echo  The following data is collected every 5-10 minutes:
echo.
echo    Processes      - running program names, CPU%%, memory
echo    Network        - connections ^(external IPs flagged^)
echo    System metrics - CPU%%, RAM%%, disk usage
echo    Active window  - title of the currently focused window
echo    File listings  - names/sizes in Documents, Desktop, Downloads
echo.
echo  File contents are NOT read unless an admin explicitly requests
echo  a specific file download. Every such access is logged locally
echo  and visible to you via the tray icon.
echo.
echo  Data goes only to your agency's commander PC.
echo  Contact your administrator with any questions.
echo.
echo ================================================================
echo.
set /p CONSENT=Type Y to accept and continue, anything else to cancel:
if /i not "%%CONSENT%%"=="Y" (
    echo.
    echo  Setup cancelled. No files written, nothing installed.
    echo.
    pause
    exit /b 0
)
echo.

:: ── Step 2: Check Python ─────────────────────────────────────────
python --version >nul 2>&1
if %%errorlevel%% neq 0 (
    echo ERROR: Python not found.
    echo Install Python 3.8+ from https://python.org  ^(check "Add to PATH"^)
    pause & exit /b 1
)

:: ── Step 3: Record consent with commander ────────────────────────
echo [1/6] Recording consent...
set GM_DID=%DEVICE_ID%
set GM_URL=%COMMANDER_URL%
python -c "
import urllib.request, json, os, sys
b = json.dumps({{'device_id': os.environ['GM_DID'], 'ack_by': 'enrollment', 'policy_version': '1.0'}}).encode()
r = urllib.request.Request(os.environ['GM_URL'] + '/api/consent/ack', data=b, method='POST')
r.add_header('Content-Type', 'application/json')
try:
    urllib.request.urlopen(r, timeout=15)
    print('Consent recorded with commander.')
except Exception as e:
    print('ERROR:', e)
    sys.exit(1)
"
if %%errorlevel%% neq 0 (
    echo ERROR: Could not reach commander to record consent.
    echo Make sure the commander PC is running and network-reachable.
    echo Setup cancelled — no files were written.
    pause & exit /b 1
)

:: ── Step 4: Create install directory ─────────────────────────────
if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%"

:: ── Step 5: Download agent and tray indicator ────────────────────
echo [2/6] Downloading monitoring agent...
powershell -Command "try {{ Invoke-WebRequest '%COMMANDER_URL%/agent/monitor_agent.py' -OutFile '%INSTALL_DIR%\\monitor_agent.py' -UseBasicParsing }} catch {{ Write-Host $_.Exception.Message; exit 1 }}"
if %%errorlevel%% neq 0 (
    echo ERROR: Could not download agent. Is the commander running?
    pause & exit /b 1
)

echo [2/6] Downloading tray indicator...
powershell -Command "try {{ Invoke-WebRequest '%COMMANDER_URL%/agent/tray_icon.py' -OutFile '%INSTALL_DIR%\\tray_icon.py' -UseBasicParsing }} catch {{ Write-Host $_.Exception.Message; exit 1 }}"
if %%errorlevel%% neq 0 (
    echo ERROR: Could not download tray indicator. Is the commander running?
    pause & exit /b 1
)

:: ── Step 6: Install dependencies ─────────────────────────────────
echo [3/6] Installing dependencies (psutil pystray Pillow)...
python -m pip install psutil pystray plyer Pillow --quiet

:: ── Step 7: Write device config + consent record ─────────────────
echo [4/6] Writing configuration...
set GM_NAME=%DEVICE_NAME%
set GM_TOKEN=%DEVICE_TOKEN%
python -c "
import json, os, datetime
d     = os.path.join(os.path.expanduser('~'), 'GhostMonitor')
did   = os.environ['GM_DID']
name  = os.environ['GM_NAME']
url   = os.environ['GM_URL']
tok   = os.environ['GM_TOKEN']
cfg   = {{'device_id': did, 'display_name': name, 'commander_url': url, 'token': tok}}
ack   = {{'device_id': did, 'ack_by': 'enrollment', 'ts': datetime.datetime.utcnow().isoformat()+'Z', 'policy_version': '1.0'}}
open(os.path.join(d, 'local_config.json'), 'w').write(json.dumps(cfg, indent=2))
open(os.path.join(d, 'consent_ack.json'), 'w').write(json.dumps(ack, indent=2))
print('Device ID:', did)
"
if %%errorlevel%% neq 0 (
    echo ERROR: Configuration failed.
    pause & exit /b 1
)

:: ── Step 8: Register two startup tasks ───────────────────────────
echo [5/6] Creating startup tasks...
set PYWIN=pythonw
pythonw --version >nul 2>&1
if %%errorlevel%% neq 0 set PYWIN=python

schtasks /create /tn "GhostMonitorAgent" /tr "%%PYWIN%% \\"%INSTALL_DIR%\\monitor_agent.py\\"" /sc onlogon /ru "%%USERNAME%%" /rl highest /f >nul 2>&1
if %%errorlevel%% neq 0 (
    echo WARNING: Could not create agent task ^(try running as Administrator^).
) else (
    echo Agent startup task created.
)
schtasks /create /tn "GhostMonitorTray" /tr "pythonw \\"%INSTALL_DIR%\\tray_icon.py\\"" /sc onlogon /ru "%%USERNAME%%" /f >nul 2>&1
if %%errorlevel%% neq 0 (
    echo WARNING: Could not create tray task ^(try running as Administrator^).
) else (
    echo Tray indicator startup task created.
)

:: ── Step 9: Start both processes now ─────────────────────────────
echo [6/6] Starting monitoring now...
start "" /min %%PYWIN%% "%INSTALL_DIR%\\monitor_agent.py"
start "" pythonw "%INSTALL_DIR%\\tray_icon.py"

echo.
echo ================================================================
echo  Setup complete!  "{device_name}" is now monitored.
echo.
echo  A shield icon will appear in your system tray shortly.
echo  Right-click it anytime to see what data is being collected
echo  and to view any files an administrator has downloaded.
echo ================================================================
echo.
pause
"""


def _make_linux_script(device_name: str, device_id: str,
                       commander_url: str, token: str) -> str:
    generated = datetime.now().strftime('%Y-%m-%d %H:%M')
    return f"""#!/bin/bash
# ================================================================
#  Ghost Monitor v2 — Device Setup
#  Device:    {device_name}
#  Commander: {commander_url}
#  Generated: {generated}
# ================================================================
DEVICE_ID="{device_id}"
DEVICE_NAME="{device_name}"
COMMANDER_URL="{commander_url}"
DEVICE_TOKEN="{token}"
INSTALL_DIR="$HOME/GhostMonitor"

# ── Step 1: Consent Disclosure ──────────────────────────────────
echo ""
echo "================================================================"
echo " GHOST MONITOR | MONITORING DISCLOSURE"
echo "================================================================"
echo ""
echo " This device will be monitored by your agency administrator."
echo " The following data is collected every 5-10 minutes:"
echo ""
echo "   Processes      - running program names, CPU%, memory"
echo "   Network        - connections (external IPs flagged)"
echo "   System metrics - CPU%, RAM%, disk usage"
echo "   Active window  - title of the currently focused window"
echo "   File listings  - names/sizes in Documents, Desktop, Downloads"
echo ""
echo " File contents are NOT read unless an admin requests a specific"
echo " file download. Every such access is logged and visible to you"
echo " via the tray icon."
echo ""
echo " Data goes only to your agency's commander PC."
echo " Contact your administrator with any questions."
echo ""
echo "================================================================"
echo ""
read -r -p "Type Y to accept and continue, anything else to cancel: " CONSENT
if [ "$CONSENT" != "Y" ] && [ "$CONSENT" != "y" ]; then
    echo ""
    echo " Setup cancelled. No files written, nothing installed."
    echo ""
    exit 0
fi
echo ""

# ── Step 2: Check Python ────────────────────────────────────────
command -v python3 >/dev/null 2>&1 || {{ echo "ERROR: python3 not found."; exit 1; }}
PIP=$(command -v pip3 2>/dev/null || command -v pip 2>/dev/null)
[ -z "$PIP" ] && {{ echo "ERROR: pip not found."; exit 1; }}

# ── Step 3: Record consent with commander ───────────────────────
echo "[1/6] Recording consent..."
export CONSENT_URL="$COMMANDER_URL/api/consent/ack"
export DEV_ID="$DEVICE_ID"
python3 -c "
import urllib.request, json, sys, os
url  = os.environ['CONSENT_URL']
body = json.dumps({{'device_id': os.environ['DEV_ID'], 'ack_by': 'enrollment', 'policy_version': '1.0'}}).encode()
req  = urllib.request.Request(url, data=body, method='POST')
req.add_header('Content-Type', 'application/json')
try:
    urllib.request.urlopen(req, timeout=15)
    print('Consent recorded with commander.')
except Exception as e:
    print('ERROR:', e); sys.exit(1)
" || {{
    echo "ERROR: Could not record consent. Setup cancelled — no files were written."
    exit 1
}}

mkdir -p "$INSTALL_DIR"

# ── Step 5: Download agent and tray indicator ───────────────────
echo "[2/6] Downloading monitoring agent..."
curl -sf "$COMMANDER_URL/agent/monitor_agent.py" -o "$INSTALL_DIR/monitor_agent.py" || \\
  wget -q "$COMMANDER_URL/agent/monitor_agent.py" -O "$INSTALL_DIR/monitor_agent.py" || {{
    echo "ERROR: Could not download agent."; exit 1;
}}

echo "[2/6] Downloading tray indicator..."
curl -sf "$COMMANDER_URL/agent/tray_icon.py" -o "$INSTALL_DIR/tray_icon.py" || \\
  wget -q "$COMMANDER_URL/agent/tray_icon.py" -O "$INSTALL_DIR/tray_icon.py" || {{
    echo "ERROR: Could not download tray indicator."; exit 1;
}}

# ── Step 6: Install dependencies ────────────────────────────────
echo "[3/6] Installing dependencies (psutil pystray Pillow)..."
$PIP install psutil pystray plyer Pillow --quiet

# ── Step 7: Write device config + consent record ────────────────
echo "[4/6] Writing configuration..."
export GM_DID="$DEVICE_ID"
export GM_NAME="$DEVICE_NAME"
export GM_URL="$COMMANDER_URL"
export GM_TOKEN="$DEVICE_TOKEN"
python3 -c "
import json, os, datetime
d     = os.path.join(os.path.expanduser('~'), 'GhostMonitor')
did   = os.environ['GM_DID']
name  = os.environ['GM_NAME']
url   = os.environ['GM_URL']
tok   = os.environ['GM_TOKEN']
cfg   = {{'device_id': did, 'display_name': name, 'commander_url': url, 'token': tok}}
ack   = {{'device_id': did, 'ack_by': 'enrollment', 'ts': datetime.datetime.utcnow().isoformat()+'Z', 'policy_version': '1.0'}}
open(os.path.join(d, 'local_config.json'), 'w').write(json.dumps(cfg, indent=2))
open(os.path.join(d, 'consent_ack.json'),  'w').write(json.dumps(ack, indent=2))
print('Device ID:', did)
" || {{ echo "ERROR: Configuration failed."; exit 1; }}

# ── Step 8: Register startup entries ────────────────────────────
echo "[5/6] Setting up startup..."

# Agent: cron wrapper with pidfile lock
cat > "$INSTALL_DIR/run_agent.sh" << 'RUNEOF'
#!/bin/bash
PIDFILE="$HOME/GhostMonitor/agent.pid"
if [ -f "$PIDFILE" ]; then
    OLD_PID=$(cat "$PIDFILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        exit 0
    fi
fi
echo $$ > "$PIDFILE"
python3 "$HOME/GhostMonitor/monitor_agent.py" >> "$HOME/GhostMonitor/agent.log" 2>&1
rm -f "$PIDFILE"
RUNEOF
chmod +x "$INSTALL_DIR/run_agent.sh"

CRON_AGENT="*/8 * * * * $INSTALL_DIR/run_agent.sh"
CRON_TRAY="@reboot python3 $INSTALL_DIR/tray_icon.py >> $INSTALL_DIR/tray.log 2>&1 &"
( crontab -l 2>/dev/null | grep -v 'GhostMonitor' ; echo "$CRON_AGENT" ; echo "$CRON_TRAY" ) | crontab -

# ── Step 9: Start both now ───────────────────────────────────────
echo "[6/6] Starting monitoring now..."
nohup python3 "$INSTALL_DIR/monitor_agent.py" >> "$INSTALL_DIR/agent.log" 2>&1 &
disown
nohup python3 "$INSTALL_DIR/tray_icon.py" >> "$INSTALL_DIR/tray.log" 2>&1 &
disown

echo ""
echo "================================================================"
echo " Setup complete!  '{device_name}' is now monitored."
echo ""
echo " The tray icon will appear in your desktop notification area."
echo " Click it to see what data is being collected."
echo "================================================================"
"""


@app.route("/enroll/<filename>")
def enroll_script(filename: str):
    """
    GET /enroll/john-laptop.bat  → Windows setup script (v2)
    GET /enroll/john-laptop.sh   → Linux / macOS setup script (v2)

    Generates (or retrieves existing) per-device token at link-generation time.
    Token is embedded in the script — no shared secret needed.
    """
    if "." not in filename:
        return "Bad request", 400
    name, ext = filename.rsplit(".", 1)
    if ext not in ("bat", "sh"):
        return "Only .bat and .sh supported", 400

    device_name = _safe_name(name)
    if not device_name:
        return "Invalid name", 400

    s             = _load_settings()
    lan_ip        = _get_lan_ip()
    commander_url = s.get("commander_url_override") or f"http://{lan_ip}:{PORT}"

    # Generate or retrieve the per-device token (idempotent per device name)
    device_id, token = _get_or_create_enrollment(device_name)

    if ext == "bat":
        script  = _make_windows_script(device_name, device_id, commander_url, token)
        dl_name = f"ghost_monitor_{device_name}.bat"
        mime    = "application/octet-stream"
    else:
        script  = _make_linux_script(device_name, device_id, commander_url, token)
        dl_name = f"ghost_monitor_{device_name}.sh"
        mime    = "application/x-sh"

    log.info(f"Enrollment script: {device_name} ({device_id}) [{ext}] → {commander_url}")
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

    print("\n" + "═" * 58)
    print("  Ghost Monitor v2 — Agency Security Dashboard")
    print(f"  Dashboard:     http://localhost:{PORT}")
    print(f"  Network:       http://{lan_ip}:{PORT}")
    print(f"  Auth:          per-device tokens  (tokens.json)")
    print(f"  Enrollment:    http://{lan_ip}:{PORT}/enroll/<name>.bat")
    print("  Press Ctrl+C to stop")
    print("═" * 58 + "\n")

    threading.Timer(1.5, lambda: webbrowser.open(f"http://localhost:{PORT}")).start()
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)
