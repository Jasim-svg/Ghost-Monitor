#!/usr/bin/env python3
"""
Ghost Monitor v2 — System Tray Indicator
Runs independently of the agent. Reads local files only — never phones home
to check status. Stays visible even when the commander is offline.

Dependencies: pystray, Pillow  (pip install pystray Pillow)
"""

import json
import sys
import time
import webbrowser
import threading
from pathlib import Path
from datetime import datetime, timezone, timedelta

try:
    import pystray
    from PIL import Image, ImageDraw
except ImportError:
    print("Missing deps: pip install pystray Pillow")
    sys.exit(1)

# ── Paths (same install dir as monitor_agent.py) ──────────────────────────────
SCRIPT_DIR    = Path(__file__).parent.resolve()
CONFIG_FILE   = SCRIPT_DIR / "local_config.json"
CONSENT_FILE  = SCRIPT_DIR / "consent_ack.json"
LOCAL_DATA    = SCRIPT_DIR / "local_data"
SUMMARY_HTML  = LOCAL_DATA / "summary.html"
PAUSED_FILE   = LOCAL_DATA / "paused_until.json"
PAUSE_LOG     = LOCAL_DATA / "pause_log.json"

# ── Thresholds ────────────────────────────────────────────────────────────────
STALE_MINUTES      = 35   # show amber if agent hasn't synced within this window
MAX_PAUSES_PER_DAY = 2    # hard cap enforced before writing paused_until.json

# ── Icon colours ──────────────────────────────────────────────────────────────
COLOR_GREEN  = (34,  197,  94, 255)   # active + in sync
COLOR_AMBER  = (245, 158,  11, 255)   # paused or stale
COLOR_RED    = (239,  68,  68, 255)   # consent missing (shouldn't normally appear)

# ═══════════════════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════════════════

def _load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _get_last_sync_ts() -> str:
    """Return the ISO timestamp of the most recent local snapshot, or ''."""
    snap_root = LOCAL_DATA / "snapshots"
    if not snap_root.exists():
        return ""
    for day_dir in sorted(snap_root.iterdir(), reverse=True):
        if not day_dir.is_dir():
            continue
        for f in sorted(day_dir.iterdir(), reverse=True):
            if f.suffix == ".json":
                data = _load_json(f)
                if data and data.get("timestamp"):
                    return data["timestamp"]
    return ""


def _relative_time(iso: str) -> str:
    if not iso:
        return "never"
    try:
        ts = datetime.fromisoformat(iso)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        delta = (datetime.now(timezone.utc) - ts).total_seconds()
        if delta < 60:
            return f"{int(delta)}s ago"
        if delta < 3600:
            return f"{int(delta / 60)}m ago"
        return f"{delta / 3600:.1f}h ago"
    except Exception:
        return "?"


def _is_paused() -> tuple:
    """Returns (is_paused: bool, until_iso: str)."""
    data = _load_json(PAUSED_FILE)
    if not data:
        return False, ""
    try:
        until = datetime.fromisoformat(data.get("until", ""))
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) < until:
            return True, data["until"]
    except Exception:
        pass
    return False, ""


def _pauses_used_today() -> int:
    """Returns how many pauses the user has triggered today."""
    data  = _load_json(PAUSE_LOG) or {}
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if data.get("date") != today:
        return 0
    return data.get("count", 0)


def _record_pause(minutes: int):
    """Write paused_until.json and increment the daily pause counter."""
    until = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    _write_json(PAUSED_FILE, {
        "until":      until.isoformat(),
        "minutes":    minutes,
        "paused_at":  datetime.now(timezone.utc).isoformat(),
    })
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    data  = _load_json(PAUSE_LOG) or {}
    if data.get("date") != today:
        data = {"date": today, "count": 0}
    data["count"] += 1
    _write_json(PAUSE_LOG, data)

# ═══════════════════════════════════════════════════════════════════════════
#  Icon image builder
# ═══════════════════════════════════════════════════════════════════════════

def _make_icon(color: tuple) -> Image.Image:
    """Draw a simple solid-circle icon in the given RGBA color."""
    size = 64
    img  = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d    = ImageDraw.Draw(img)
    pad  = 6
    d.ellipse([pad, pad, size - pad, size - pad], fill=color)
    return img


def _current_icon_color() -> tuple:
    """Returns the color the icon should be right now."""
    is_p, _ = _is_paused()
    if is_p:
        return COLOR_AMBER

    last_ts = _get_last_sync_ts()
    if last_ts:
        try:
            ts    = datetime.fromisoformat(last_ts)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            delta = (datetime.now(timezone.utc) - ts).total_seconds() / 60
            if delta <= STALE_MINUTES:
                return COLOR_GREEN
        except Exception:
            pass
    return COLOR_AMBER   # stale or never synced

# ═══════════════════════════════════════════════════════════════════════════
#  Menu builder
# ═══════════════════════════════════════════════════════════════════════════

def _build_menu(icon_ref: list) -> pystray.Menu:
    """Builds the right-click menu. icon_ref is a one-element list so
    callbacks can reference the icon object without circular captures."""

    cfg          = _load_json(CONFIG_FILE) or {}
    display_name = cfg.get("display_name", "Unknown Device")
    commander    = cfg.get("commander_url", "")

    last_ts  = _get_last_sync_ts()
    is_p, _  = _is_paused()

    status_label = "PAUSED" if is_p else "Active"
    last_label   = f"Last sync: {_relative_time(last_ts)}"

    # ── Actions ──────────────────────────────────────────────────────────

    def open_transparency(_icon, _item):
        if SUMMARY_HTML.exists():
            webbrowser.open(SUMMARY_HTML.as_uri())
        else:
            webbrowser.open((LOCAL_DATA / "summary.html").as_uri())

    def open_policy(_icon, _item):
        # Open the policy from the commander if available, or show a local note
        if commander:
            webbrowser.open(f"{commander}/api/policy")

    def do_unpause(_icon, _item):
        PAUSED_FILE.unlink(missing_ok=True)
        _refresh_icon(icon_ref)

    def do_pause_1h(_icon, _item):
        _do_pause(icon_ref, 60)

    def do_pause_2h(_icon, _item):
        _do_pause(icon_ref, 120)

    # ── Pause submenu or resume ───────────────────────────────────────────
    used_today = _pauses_used_today()
    can_pause  = (not is_p) and (used_today < MAX_PAUSES_PER_DAY)

    if is_p:
        pause_item = pystray.MenuItem("Resume monitoring", do_unpause)
    elif can_pause:
        remaining = MAX_PAUSES_PER_DAY - used_today
        pause_item = pystray.MenuItem(
            f"Pause monitoring… ({remaining} left today)",
            pystray.Menu(
                pystray.MenuItem("Pause for 1 hour",  do_pause_1h),
                pystray.MenuItem("Pause for 2 hours", do_pause_2h),
            ),
        )
    else:
        pause_item = pystray.MenuItem(
            f"Pause limit reached ({MAX_PAUSES_PER_DAY}/day)", None, enabled=False
        )

    return pystray.Menu(
        pystray.MenuItem(f"Ghost Monitor — {status_label}", None, enabled=False),
        pystray.MenuItem(last_label,           None, enabled=False),
        pystray.MenuItem(f"Device: {display_name}", None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("What's being monitored?", open_transparency),
        pystray.MenuItem("View monitoring policy",  open_policy),
        pystray.Menu.SEPARATOR,
        pause_item,
    )


def _do_pause(icon_ref: list, minutes: int):
    used = _pauses_used_today()
    if used >= MAX_PAUSES_PER_DAY:
        return
    _record_pause(minutes)
    _refresh_icon(icon_ref)


def _refresh_icon(icon_ref: list):
    """Updates the icon image and menu to reflect current state."""
    icon = icon_ref[0]
    icon.icon = _make_icon(_current_icon_color())
    icon.menu = _build_menu(icon_ref)
    icon.update_menu()

# ═══════════════════════════════════════════════════════════════════════════
#  Background update loop
# ═══════════════════════════════════════════════════════════════════════════

def _update_loop(icon_ref: list):
    """Refreshes icon color and menu every 60 seconds."""
    while True:
        time.sleep(60)
        try:
            _refresh_icon(icon_ref)
        except Exception:
            pass   # never crash the update thread

# ═══════════════════════════════════════════════════════════════════════════
#  Entry point
# ═══════════════════════════════════════════════════════════════════════════

def main():
    color    = _current_icon_color()
    img      = _make_icon(color)
    icon_ref = [None]   # mutable reference so callbacks can call icon.update_menu()

    icon = pystray.Icon(
        name  = "GhostMonitor",
        icon  = img,
        title = "Ghost Monitor",
    )
    icon_ref[0] = icon
    icon.menu   = _build_menu(icon_ref)

    # Background thread keeps icon state fresh without blocking pystray
    threading.Thread(target=_update_loop, args=(icon_ref,), daemon=True).start()

    icon.run()


if __name__ == "__main__":
    main()
