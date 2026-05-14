#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════╗
║    GHOST SYNC — TASK DISPATCHER (Main PC only)      ║
║    Queue commands for workers to execute            ║
╚══════════════════════════════════════════════════════╝

Usage:
  python dispatch.py --cmd "nmap -sV 192.168.1.1" --tools nmap --power weak
  python dispatch.py --cmd "hashcat -m 0 hash.txt rockyou.txt" --power strong
  python dispatch.py --list          # show all tasks
  python dispatch.py --results       # show all results from workers
"""

import json
import uuid
import argparse
import sys
from pathlib import Path
from datetime import datetime, timezone
from rich.console import Console
from rich.table import Table
from rich import box

REPO_DIR      = Path(__file__).parent
TASKS_QUEUE   = REPO_DIR / "tasks" / "queue"
TASKS_CLAIMED = REPO_DIR / "tasks" / "claimed"
TASKS_DONE    = REPO_DIR / "tasks" / "done"
RESULTS_DIR   = REPO_DIR / "results"
DEVICES_DIR   = REPO_DIR / "devices"

console = Console()


def dispatch_task(command: str, required_tools: list, power_class: str,
                  priority: int = 5, timeout: int = 300) -> str:
    """Create a task JSON file in tasks/queue/."""
    task_id = str(uuid.uuid4())[:8]
    task = {
        "task_id":    task_id,
        "command":    command,
        "requires": {
            "power_class": power_class,
            "tools":       required_tools,
        },
        "priority":         priority,
        "timeout_seconds":  timeout,
        "created_at":       datetime.now(timezone.utc).isoformat(),
        "dispatched_by":    "main-commander",
    }

    TASKS_QUEUE.mkdir(parents=True, exist_ok=True)
    out = TASKS_QUEUE / f"task_{task_id}.json"
    out.write_text(json.dumps(task, indent=2))
    console.print(f"[bold green]✓ Task dispatched:[/] {task_id} → [{command[:60]}]")
    return task_id


def list_tasks():
    """Pretty print all tasks across queue/claimed/done."""
    table = Table(title="Ghost Sync — Task Status", box=box.ROUNDED)
    table.add_column("Task ID",  style="cyan",   width=12)
    table.add_column("Status",   style="bold",   width=10)
    table.add_column("Command",  style="white",  width=40)
    table.add_column("Power",    style="yellow", width=8)
    table.add_column("Created",  style="dim",    width=20)
    table.add_column("Worker",   style="green",  width=18)

    for status, folder in [("QUEUED", TASKS_QUEUE), ("CLAIMED", TASKS_CLAIMED), ("DONE", TASKS_DONE)]:
        if not folder.exists():
            continue
        for f in sorted(folder.glob("task_*.json")):
            try:
                t = json.loads(f.read_text())
                color = {"QUEUED": "yellow", "CLAIMED": "blue", "DONE": "green"}.get(status, "white")
                table.add_row(
                    t.get("task_id", "?"),
                    f"[{color}]{status}[/{color}]",
                    t.get("command", "")[:38],
                    t.get("requires", {}).get("power_class", "any"),
                    t.get("created_at", "")[:16].replace("T", " "),
                    t.get("claimed_by", "—"),
                )
            except Exception:
                pass

    console.print(table)


def show_results():
    """Show latest results from all worker devices."""
    if not RESULTS_DIR.exists():
        console.print("[dim]No results yet.[/]")
        return

    table = Table(title="Ghost Sync — Worker Results", box=box.ROUNDED)
    table.add_column("Device",    style="cyan",  width=20)
    table.add_column("Task ID",   style="yellow", width=12)
    table.add_column("Status",    style="bold",  width=10)
    table.add_column("Command",   style="white", width=35)
    table.add_column("Completed", style="dim",   width=20)

    for device_dir in sorted(RESULTS_DIR.iterdir()):
        if not device_dir.is_dir():
            continue
        for result_file in sorted(device_dir.glob("*.json"), reverse=True)[:5]:
            try:
                r = json.loads(result_file.read_text())
                color = "green" if r.get("status") == "success" else "red"
                table.add_row(
                    r.get("device_id", "?"),
                    r.get("task_id", "?"),
                    f"[{color}]{r.get('status','?').upper()}[/{color}]",
                    r.get("command", "")[:33],
                    (r.get("completed_at") or "")[:16].replace("T", " "),
                )
            except Exception:
                pass

    console.print(table)


def fleet_status():
    """Show all registered devices and their status."""
    if not DEVICES_DIR.exists():
        console.print("[dim]No devices registered yet.[/]")
        return

    table = Table(title="Ghost Sync — Fleet Status", box=box.ROUNDED)
    table.add_column("Device ID",  style="cyan",   width=24)
    table.add_column("Status",     style="bold",   width=10)
    table.add_column("OS",         style="white",  width=10)
    table.add_column("RAM",        style="yellow", width=8)
    table.add_column("Power",      style="green",  width=8)
    table.add_column("Last Seen",  style="dim",    width=20)

    for device_dir in sorted(DEVICES_DIR.iterdir()):
        status_file = device_dir / "status.json"
        if not status_file.exists():
            continue
        try:
            s    = json.loads(status_file.read_text())
            caps = s.get("capabilities", {})
            last = s.get("last_seen", "")[:16].replace("T", " ")
            table.add_row(
                s.get("device_id", "?"),
                "[green]● ONLINE[/]" if s.get("status") == "online" else "[dim]○ OFFLINE[/]",
                caps.get("os", "?"),
                f"{caps.get('ram_gb', '?')}GB",
                caps.get("power_class", "?"),
                last,
            )
        except Exception:
            pass

    console.print(table)


# ── CLI ────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Ghost Sync — Task Dispatcher")
    parser.add_argument("--cmd",     type=str, help="Command to dispatch to workers")
    parser.add_argument("--tools",   nargs="*", default=[], help="Required tools (e.g. nmap ffuf)")
    parser.add_argument("--power",   type=str, default="weak",
                        choices=["weak", "medium", "strong"], help="Minimum power class required")
    parser.add_argument("--priority", type=int, default=5, help="Priority 1=high, 10=low")
    parser.add_argument("--timeout", type=int, default=300, help="Timeout in seconds")
    parser.add_argument("--list",    action="store_true", help="List all tasks")
    parser.add_argument("--results", action="store_true", help="Show worker results")
    parser.add_argument("--fleet",   action="store_true", help="Show fleet status")
    args = parser.parse_args()

    if args.cmd:
        dispatch_task(args.cmd, args.tools, args.power, args.priority, args.timeout)
    elif args.list:
        list_tasks()
    elif args.results:
        show_results()
    elif args.fleet:
        fleet_status()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
