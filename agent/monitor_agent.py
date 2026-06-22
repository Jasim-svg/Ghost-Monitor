#!/usr/bin/env python3
"""
Ghost Monitor Agent v2 — passive activity collector
Pushes snapshots to the commander dashboard over HTTP.
Per-device token auth. Consent gate at startup.
Local transparency page generated each cycle.

Install:  pip install psutil pystray plyer Pillow
Run:      (use the enrollment script — do not run directly)
"""

import os
import sys
import json
import time
import socket
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
    print("[MONITOR] Missing psutil. Run: pip install psutil")
    sys.exit(1)

# ── Paths ─────────────────────────────────────────────────────────────────────
SCRIPT_DIR    = Path(__file__).parent.resolve()
CONFIG_FILE   = SCRIPT_DIR / "local_config.json"
CONSENT_FILE  = SCRIPT_DIR / "consent_ack.json"    # written by enrollment script
LOG_FILE      = SCRIPT_DIR / "monitor_agent.log"
LOCAL_DATA    = SCRIPT_DIR / "local_data"

# ── Logging ───────────────────────────────────────────────────────────────────
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
#  Section 2 — Consent gate
# ═══════════════════════════════════════════════════════════════════════════

def _consent_acknowledged() -> bool:
    """Returns True only if the local consent record exists.
    This file is written by the enrollment script — it cannot be created
    any other way, so running monitor_agent.py without enrolling always exits."""
    return CONSENT_FILE.exists()


def _load_consent_ack() -> dict:
    try:
        return json.loads(CONSENT_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}

# ═══════════════════════════════════════════════════════════════════════════
#  Section 1 — Identity (reads token, not shared secret)
# ═══════════════════════════════════════════════════════════════════════════

def get_identity():
    """Return (device_id, display_name, commander_url, token).
    Exits immediately if local_config.json doesn't exist —
    the enrollment script must run first."""
    if not CONFIG_FILE.exists():
        log.error("local_config.json not found. Run the enrollment script first.")
        sys.exit(1)
    cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    return (
        cfg["device_id"],
        cfg["display_name"],
        cfg["commander_url"].rstrip("/"),
        cfg["token"],
    )

# ═══════════════════════════════════════════════════════════════════════════
#  Section 6 — Quiet hours / self-pause
# ═══════════════════════════════════════════════════════════════════════════

PAUSED_FILE        = LOCAL_DATA / "paused_until.json"
MAX_PAUSES_PER_DAY = 2   # tray icon enforces this; agent just respects the file

def _check_paused() -> tuple:
    """Returns (is_paused: bool, paused_until_iso: str)."""
    if not PAUSED_FILE.exists():
        return False, ""
    try:
        data  = json.loads(PAUSED_FILE.read_text(encoding="utf-8"))
        until_str = data.get("until", "")
        until = datetime.datetime.fromisoformat(until_str)
        now   = datetime.datetime.now(datetime.timezone.utc)
        if until.tzinfo is None:
            until = until.replace(tzinfo=datetime.timezone.utc)
        if now < until:
            return True, until_str
    except Exception:
        pass
    return False, ""

# ═══════════════════════════════════════════════════════════════════════════
#  Data collection — read-only observation
# ═══════════════════════════════════════════════════════════════════════════

def collect_processes():
    psutil.cpu_percent(interval=0.1)   # warm up — first call always returns 0.0
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
        log.warning("Network access denied — run as Administrator for full network data")
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
    """Full file listing for the dashboard's file browser.
    Returns partial results if scanning exceeds time_budget seconds."""
    home     = Path.home()
    files    = []
    deadline = time.time() + time_budget
    for dir_name in ("Documents", "Desktop", "Downloads"):
        scan = home / dir_name
        if not scan.exists():
            continue
        try:
            for f in scan.rglob("*"):
                if time.time() > deadline:
                    log.warning("collect_file_listing: time budget exceeded, returning partial")
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
#  Section 1 — HTTP helpers (X-Agent-Token replaces X-Agent-Secret)
# ═══════════════════════════════════════════════════════════════════════════

def http_post(url: str, data: dict, token: str, timeout: int = 20):
    try:
        body = json.dumps(data).encode("utf-8")
        req  = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type",  "application/json")
        req.add_header("X-Agent-Token", token)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as e:
        log.warning(f"POST {url} failed: {e}")
        return None


def http_get(url: str, token: str, timeout: int = 20):
    try:
        req = urllib.request.Request(url)
        req.add_header("X-Agent-Token", token)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
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

def handle_file_requests(tasks: list, commander_url: str, device_id: str, token: str):
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
                }, token)
                continue

            size = p.stat().st_size
            if size > FILE_SIZE_LIMIT:
                http_post(endpoint, {
                    "device_id": device_id, "request_id": request_id,
                    "error": f"File too large ({size // 1024 // 1024} MB > 100 MB limit)",
                }, token)
                continue

            content_b64 = base64.b64encode(p.read_bytes()).decode()
            http_post(endpoint, {
                "device_id":   device_id,
                "request_id":  request_id,
                "path":        file_path,
                "filename":    p.name,
                "content_b64": content_b64,
                "size":        size,
            }, token, timeout=300)
            log.info(f"File uploaded: {p.name} ({size} bytes)")

        except Exception as e:
            log.warning(f"File upload error: {e}")
            http_post(endpoint, {
                "device_id": device_id, "request_id": request_id,
                "error": str(e),
            }, token)

# ═══════════════════════════════════════════════════════════════════════════
#  Section 4 — Access log sync
# ═══════════════════════════════════════════════════════════════════════════

ACCESS_LOG_FILE = LOCAL_DATA / "access_log.json"

def sync_access_log(commander_url: str, device_id: str, token: str) -> list:
    """Fetches new access-log entries from the commander and merges them into
    local_data/access_log.json. Returns the full merged list."""
    existing = []
    since    = ""
    if ACCESS_LOG_FILE.exists():
        try:
            existing = json.loads(ACCESS_LOG_FILE.read_text(encoding="utf-8"))
            # Find the most recent entry's timestamp for incremental fetch
            ts_list = [e.get("ts", "") for e in existing if e.get("ts")]
            if ts_list:
                since = max(ts_list)
        except Exception:
            pass

    url = f"{commander_url}/api/agent/access-log/{device_id}"
    if since:
        url += f"?since={since}"

    new_entries = http_get(url, token) or []
    # Merge: append only entries not already present (by ts+type dedup)
    existing_keys = {(e.get("ts", ""), e.get("type", "")) for e in existing}
    for entry in new_entries:
        key = (entry.get("ts", ""), entry.get("type", ""))
        if key not in existing_keys:
            existing.append(entry)
            existing_keys.add(key)

    ACCESS_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    ACCESS_LOG_FILE.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    return existing

# ═══════════════════════════════════════════════════════════════════════════
#  Section 5 — Local transparency page
# ═══════════════════════════════════════════════════════════════════════════

def generate_transparency_page(device_id: str, display_name: str,
                                snapshot: dict, access_log: list):
    """Writes local_data/summary.html — opened by the tray icon's
    'What's being monitored?' menu item. Requires zero network access."""
    consent    = _load_consent_ack()
    enrolled   = consent.get("ts", "unknown")
    policy_ver = consent.get("policy_version", "1.0")

    ts  = snapshot.get("timestamp", "—")
    cpu = snapshot.get("metrics", {}).get("cpu_percent", "—")
    ram = snapshot.get("metrics", {}).get("ram_percent", "—")

    # Build access log rows (most recent first, hide internal _dedup key)
    visible_log = [e for e in reversed(access_log) if e.get("type") != "monitoring_paused"]
    pause_log   = [e for e in reversed(access_log) if e.get("type") == "monitoring_paused"]

    def _log_rows(entries, cols):
        if not entries:
            return '<tr><td colspan="{}" style="color:#94a3b8;font-style:italic;">None recorded.</td></tr>'.format(len(cols))
        rows = ""
        for e in entries[:30]:
            rows += "<tr>" + "".join(f"<td>{e.get(c,'—')}</td>" for c in cols) + "</tr>"
        return rows

    file_rows  = _log_rows(visible_log, ["ts", "file", "path"])
    pause_rows = _log_rows(pause_log,   ["ts", "paused_until"])

    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Ghost Monitor — What's Being Monitored?</title>
<style>
  body {{ font-family: Arial, sans-serif; max-width: 740px; margin: 40px auto;
          padding: 0 24px; color: #1e293b; line-height: 1.65; }}
  h1   {{ color: #0ea5e9; font-size: 22px; margin-bottom: 4px; }}
  h2   {{ color: #334155; font-size: 15px; margin-top: 28px;
          border-bottom: 1px solid #e2e8f0; padding-bottom: 5px; }}
  .info  {{ background: #f0f9ff; border: 1px solid #bae6fd;
             border-radius: 6px; padding: 13px 17px; margin: 14px 0; }}
  .box   {{ background: #f8fafc; border-left: 4px solid #0ea5e9;
             padding: 10px 16px; margin: 10px 0; }}
  .box li {{ margin: 5px 0; }}
  table  {{ width: 100%; border-collapse: collapse; font-size: 12.5px; }}
  th     {{ text-align: left; padding: 6px 10px; background: #f1f5f9;
             color: #64748b; font-size: 10px; text-transform: uppercase;
             letter-spacing: 1px; }}
  td     {{ padding: 5px 10px; border-bottom: 1px solid #f1f5f9;
             word-break: break-all; }}
  .none  {{ color: #94a3b8; font-style: italic; }}
  .meta  {{ color: #64748b; font-size: 12px; }}
  footer {{ color: #94a3b8; font-size: 11px; margin-top: 36px;
             border-top: 1px solid #f1f5f9; padding-top: 10px; }}
</style>
</head>
<body>

<h1>Ghost Monitor — Transparency Report</h1>
<p class="meta">Generated: {now_str} &nbsp;·&nbsp; This page is local — no internet connection needed.</p>

<div class="info">
  <strong>Device: {display_name}</strong> &nbsp;(ID: {device_id})<br>
  Enrolled: <strong>{enrolled}</strong> &nbsp;·&nbsp; Policy version: {policy_ver}
</div>

<h2>What Is Being Collected</h2>
<div class="box">
  <ul>
    <li><strong>Running processes</strong> — program names, CPU% and memory usage</li>
    <li><strong>Network connections</strong> — active connections; external IPs are flagged</li>
    <li><strong>System metrics</strong> — CPU%, RAM%, disk usage</li>
    <li><strong>Active window title</strong> — title of the currently focused window</li>
    <li><strong>File listings</strong> — file names and sizes in Documents, Desktop, Downloads<br>
        <em>(file contents are not read unless an administrator explicitly requests a download)</em></li>
  </ul>
</div>
<p>Data is collected every 5–10 minutes and sent to your agency's commander PC.
Only your administrator can view it.</p>

<h2>Files Downloaded by Administrator</h2>
<table>
  <tr><th>Time</th><th>Filename</th><th>Full Path</th></tr>
  {file_rows}
</table>

<h2>Monitoring Paused by You</h2>
<table>
  <tr><th>Paused At</th><th>Paused Until</th></tr>
  {pause_rows}
</table>

<h2>Current Agent Status</h2>
<p class="meta">Last sync: <strong>{ts}</strong></p>
<p class="meta">CPU: {cpu}% &nbsp;·&nbsp; RAM: {ram}%</p>

<h2>Questions or Concerns?</h2>
<p>Contact your agency administrator with any questions about what data is collected
or to request a copy of your monitoring records.</p>

<footer>
  Ghost Monitor v2 &nbsp;·&nbsp; Local transparency report &nbsp;·&nbsp;
  Device: {display_name} ({device_id})
</footer>
</body>
</html>"""

    summary_path = LOCAL_DATA / "summary.html"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(html, encoding="utf-8")

# ═══════════════════════════════════════════════════════════════════════════
#  Main cycle
# ═══════════════════════════════════════════════════════════════════════════

def run_cycle(device_id: str, display_name: str, commander_url: str,
              token: str, prev_snapshot):
    now      = datetime.datetime.now()
    ts       = now.strftime("%Y-%m-%dT%H-%M-%S")
    date_str = now.strftime("%Y-%m-%d")
    log.info(f"--- Cycle {ts} ---")

    is_paused, paused_until = _check_paused()
    base = commander_url

    if is_paused:
        # Section 6: Send lightweight paused checkin — never go fully dark
        http_post(f"{base}/api/agent/checkin", {
            "device_id":    device_id,
            "display_name": display_name,
            "last_seen":    now.isoformat(),
            "os":           platform.system(),
            "hostname":     socket.gethostname(),
            "paused":       True,
            "paused_until": paused_until,
        }, token)
        log.info(f"Monitoring paused until {paused_until} — sent lightweight checkin")

        # Still sync access log + refresh transparency page while paused
        access_log = sync_access_log(commander_url, device_id, token)
        generate_transparency_page(device_id, display_name, {}, access_log)
        return prev_snapshot   # skip full data collection

    # ── Full data collection cycle ────────────────────────────────────────
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

    # ── Always write locally first ────────────────────────────────────────
    write_local(f"snapshots/{date_str}", f"{ts}.json", snapshot)
    if events:
        write_local("events", f"{ts}_events.json", {
            "events": events, "device_id": device_id, "display_name": display_name,
        })
    snap_root = LOCAL_DATA / "snapshots"
    if snap_root.exists():
        days = sorted(d for d in snap_root.iterdir() if d.is_dir())
        for old in days[:-2]:
            shutil.rmtree(old, ignore_errors=True)

    # ── Push to commander ─────────────────────────────────────────────────
    http_post(f"{base}/api/agent/checkin", {
        "device_id":     device_id,
        "display_name":  display_name,
        "last_seen":     now.isoformat(),
        "os":            platform.system(),
        "hostname":      socket.gethostname(),
        "ip":            my_ip,
        "metrics":       metrics,
        "active_window": snapshot.get("active_window"),
        "paused":        False,
    }, token)

    http_post(f"{base}/api/agent/snapshot", {
        "device_id": device_id,
        "snapshot":  snapshot,
    }, token)

    if events:
        http_post(f"{base}/api/agent/events", {
            "device_id":    device_id,
            "display_name": display_name,
            "events":       events,
        }, token)
        log.info(f"{len(events)} events pushed")

    # Push log tail
    try:
        lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()[-200:]
        http_post(f"{base}/api/agent/log", {"device_id": device_id, "lines": lines}, token)
    except Exception:
        pass

    # Push file listing (~40% of cycles to avoid overhead)
    if random.random() < 0.4:
        files = collect_file_listing()
        http_post(f"{base}/api/agent/files", {
            "device_id": device_id,
            "files":     files,
            "ts":        now.isoformat(),
        }, token)

    # Poll for pending tasks (file download requests from dashboard)
    tasks = http_get(f"{base}/api/agent/tasks/{device_id}", token) or []
    if tasks:
        log.info(f"Handling {len(tasks)} pending task(s)")
        handle_file_requests(tasks, commander_url, device_id, token)

    # ── Section 4+5: Sync access log and regenerate transparency page ─────
    access_log = sync_access_log(commander_url, device_id, token)
    generate_transparency_page(device_id, display_name, snapshot, access_log)

    log.info(f"--- Done (CPU {metrics['cpu_percent']}%  RAM {metrics['ram_percent']}%) ---")
    return snapshot


def main():
    # Section 2: Hard consent gate — exits if no local consent record
    if not _consent_acknowledged():
        log.error(
            "No local consent record found. "
            "Ghost Monitor will not start without explicit consent. "
            "Run the enrollment script to set up this device properly."
        )
        sys.exit(1)

    device_id, display_name, commander_url, token = get_identity()
    log.info(f"Ghost Monitor v2: {display_name} ({device_id})")
    log.info(f"Commander: {commander_url}")

    LOCAL_DATA.mkdir(parents=True, exist_ok=True)
    prev_snapshot = None
    interval      = random.randint(300, 600)

    while True:
        try:
            prev_snapshot = run_cycle(device_id, display_name, commander_url,
                                      token, prev_snapshot)
        except Exception as e:
            log.error(f"Cycle error: {e}", exc_info=True)
        log.info(f"Sleeping {interval}s …")
        time.sleep(interval)


if __name__ == "__main__":
    main()
