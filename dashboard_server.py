#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════╗
║   GHOST SYNC — COMMANDER DASHBOARD SERVER v1.1      ║
║   Main PC only — http://localhost:8888              ║
╚══════════════════════════════════════════════════════╝

Install:  pip install flask flask-cors
Run:      python dashboard_server.py
          (auto-opens browser)
"""

import json
import uuid
import subprocess
import threading
import time
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

log = logging.getLogger("ghost.server")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [SERVER] %(message)s")

app = Flask(__name__, static_folder="dashboard")
CORS(app)

REPO_DIR      = Path(__file__).parent.resolve()
TASKS_QUEUE   = REPO_DIR / "tasks" / "queue"
TASKS_CLAIMED = REPO_DIR / "tasks" / "claimed"
TASKS_DONE    = REPO_DIR / "tasks" / "done"
RESULTS_DIR   = REPO_DIR / "results"
DEVICES_DIR   = REPO_DIR / "devices"
LOGS_DIR      = REPO_DIR / "logs"

# Device is considered offline if last_seen > this many minutes ago
OFFLINE_THRESHOLD_MINUTES = 15

_last_pull_time = None
_last_pull_result = "Never"


# ═══════════════════════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════════════════════
def _load_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _is_online(last_seen_iso: str) -> bool:
    """Return True if last_seen is within OFFLINE_THRESHOLD_MINUTES."""
    try:
        last = datetime.fromisoformat(last_seen_iso.replace("Z", "+00:00"))
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=OFFLINE_THRESHOLD_MINUTES)
        return last > cutoff
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════════════════════
#  ROUTES
# ═══════════════════════════════════════════════════════════════════════════
@app.route("/")
def index():
    return send_from_directory("dashboard", "index.html")


@app.route("/api/fleet")
def fleet():
    """All devices with live online/offline status."""
    devices = []
    if DEVICES_DIR.exists():
        for d in sorted(DEVICES_DIR.iterdir()):
            if not d.is_dir() or d.name.startswith("."):
                continue
            sf = d / "status.json"
            data = _load_json(sf)
            if data:
                # Override status based on last_seen freshness
                last = data.get("last_seen", "")
                data["status"] = "online" if _is_online(last) else "offline"
                devices.append(data)
    return jsonify(devices)


@app.route("/api/tasks")
def tasks():
    """All tasks across queue/claimed/done."""
    all_tasks = []
    for status_label, folder in [
        ("queued", TASKS_QUEUE),
        ("claimed", TASKS_CLAIMED),
        ("done", TASKS_DONE),
    ]:
        if not folder.exists():
            continue
        for f in sorted(folder.glob("task_*.json"), reverse=True)[:30]:
            data = _load_json(f)
            if data:
                data["_status"] = status_label
                all_tasks.append(data)

    # Sort: queued first, then claimed, then done; within each group by priority
    order = {"queued": 0, "claimed": 1, "done": 2}
    all_tasks.sort(key=lambda x: (order.get(x.get("_status", "done"), 9),
                                   x.get("priority", 99)))
    return jsonify(all_tasks)


@app.route("/api/results")
def results():
    """Latest results from all workers, sorted by completed_at desc."""
    all_results = []
    if RESULTS_DIR.exists():
        for device_dir in RESULTS_DIR.iterdir():
            if not device_dir.is_dir() or device_dir.name.startswith("."):
                continue
            for rf in sorted(device_dir.glob("*.json"), reverse=True)[:10]:
                data = _load_json(rf)
                if data:
                    all_results.append(data)

    all_results.sort(key=lambda x: x.get("completed_at") or "", reverse=True)
    return jsonify(all_results[:60])


@app.route("/api/dispatch", methods=["POST"])
def dispatch():
    """Create a task for workers. Main PC only."""
    data    = request.get_json(silent=True) or {}
    command = (data.get("cmd") or "").strip()

    if not command:
        return jsonify({"error": "cmd is required"}), 400

    task_id = str(uuid.uuid4())[:8]
    task = {
        "task_id":   task_id,
        "command":   command,
        "requires": {
            "power_class": data.get("power", "weak"),
            "tools": [t.strip() for t in data.get("tools", "").split(",") if t.strip()],
        },
        "priority":        int(data.get("priority", 5)),
        "timeout_seconds": int(data.get("timeout", 300)),
        "created_at":      datetime.now(timezone.utc).isoformat(),
        "dispatched_by":   "main-commander",
    }

    TASKS_QUEUE.mkdir(parents=True, exist_ok=True)
    out = TASKS_QUEUE / f"task_{task_id}.json"
    out.write_text(json.dumps(task, indent=2), encoding="utf-8")

    # Auto git push so workers pick it up on next cycle
    _git_push(f"[commander] dispatch {task_id}")

    return jsonify({"ok": True, "task_id": task_id})


@app.route("/api/pull", methods=["POST"])
def pull():
    """Trigger a git pull on the main PC repo."""
    ok, output = _git_pull()
    return jsonify({"ok": ok, "output": output})


@app.route("/api/logs/<device_id>")
def device_logs(device_id: str):
    """Last 100 log lines for a specific device."""
    # Sanitize device_id — no path traversal
    safe_id = device_id.replace("/", "").replace("..", "").replace("\\", "")
    log_file = LOGS_DIR / safe_id / "agent.log"
    if not log_file.exists():
        return jsonify({"device_id": safe_id, "lines": []})
    lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()[-100:]
    return jsonify({"device_id": safe_id, "lines": lines})


@app.route("/api/result/<device_id>/<task_id>")
def result_detail(device_id: str, task_id: str):
    """Full stdout for a specific result."""
    safe_did = device_id.replace("/", "").replace("..", "")
    safe_tid = task_id.replace("/", "").replace("..", "")
    out_dir  = RESULTS_DIR / safe_did

    if not out_dir.exists():
        return jsonify({"error": "not found"}), 404

    # Find the result file
    for f in out_dir.glob(f"*_{safe_tid}.json"):
        data = _load_json(f)
        if data:
            return jsonify(data)

    return jsonify({"error": "result not found"}), 404


@app.route("/api/stats")
def stats():
    """Summary counts for dashboard stat cards."""
    global _last_pull_result

    online = 0
    total  = 0
    if DEVICES_DIR.exists():
        for d in DEVICES_DIR.iterdir():
            if not d.is_dir() or d.name.startswith("."):
                continue
            sf   = d / "status.json"
            data = _load_json(sf)
            if data:
                total += 1
                if _is_online(data.get("last_seen", "")):
                    online += 1

    queued  = len(list(TASKS_QUEUE.glob("task_*.json")))  if TASKS_QUEUE.exists()  else 0
    claimed = len(list(TASKS_CLAIMED.glob("task_*.json"))) if TASKS_CLAIMED.exists() else 0
    done    = len(list(TASKS_DONE.glob("task_*.json")))   if TASKS_DONE.exists()   else 0

    return jsonify({
        "devices_online": online,
        "devices_total":  total,
        "tasks_queued":   queued,
        "tasks_running":  claimed,
        "tasks_done":     done,
        "last_pull":      _last_pull_result,
        "server_time":    datetime.now(timezone.utc).isoformat(),
    })


# ═══════════════════════════════════════════════════════════════════════════
#  GIT HELPERS
# ═══════════════════════════════════════════════════════════════════════════
def _git_pull() -> tuple[bool, str]:
    global _last_pull_result, _last_pull_time
    try:
        result = subprocess.run(
            ["git", "pull", "--rebase", "origin", "main"],
            cwd=REPO_DIR, capture_output=True, text=True, timeout=60
        )
        ok = result.returncode == 0
        out = (result.stdout + result.stderr).strip()
        _last_pull_time   = datetime.now(timezone.utc)
        _last_pull_result = datetime.now(timezone.utc).strftime("%H:%M:%S")
        log.info(f"git pull: {'OK' if ok else 'FAIL'} — {out[:80]}")
        return ok, out
    except Exception as e:
        return False, str(e)


def _git_push(message: str):
    try:
        subprocess.run(["git", "add", "-A"], cwd=REPO_DIR, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", message],
            cwd=REPO_DIR, capture_output=True
        )
        subprocess.run(["git", "push"], cwd=REPO_DIR, capture_output=True, timeout=60)
        log.info(f"git push: {message[:60]}")
    except Exception as e:
        log.warning(f"git push failed: {e}")


# ═══════════════════════════════════════════════════════════════════════════
#  AUTO-PULL BACKGROUND THREAD
# ═══════════════════════════════════════════════════════════════════════════
def auto_pull_worker():
    """Pull every 5 minutes in the background so dashboard stays fresh."""
    time.sleep(10)  # Let server start first
    while True:
        _git_pull()
        time.sleep(300)  # 5 minutes


# ═══════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import webbrowser

    print("\n" + "═" * 50)
    print("  ⚡ GHOST SYNC — Commander Dashboard")
    print("  URL:  http://localhost:8888")
    print("  Auto-pull: every 5 minutes")
    print("  Press Ctrl+C to stop")
    print("═" * 50 + "\n")

    # Start auto-pull thread
    t = threading.Thread(target=auto_pull_worker, daemon=True)
    t.start()

    # Open browser after 1.5 seconds
    threading.Timer(1.5, lambda: webbrowser.open("http://localhost:8888")).start()

    app.run(host="127.0.0.1", port=8888, debug=False, use_reloader=False)
