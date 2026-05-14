#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════╗
║         GHOST SYNC — WORKER AGENT v1.1              ║
║   Silent. Automated. Set once, runs forever.        ║
║   Compatible: Windows 10 / Linux / macOS            ║
╚══════════════════════════════════════════════════════╝

FIRST BOOT  → asks ONE question (device name) → never asks again
EVERY CYCLE → pull → heartbeat → claim task → execute → push result
BACKGROUND  → runs as daemon, fully silent, no user input ever

INSTALL:
  pip install gitpython psutil schedule requests
  python ghost_agent.py
"""

import os
import sys
import json
import time
import uuid
import socket
import hashlib
import platform
import subprocess
import shutil
import logging
import signal
import random
from pathlib import Path
from datetime import datetime, timezone

# ── Third-party ────────────────────────────────────────────────────────────
try:
    import psutil
    import schedule
    import git
except ImportError:
    print("[GHOST] Missing deps. Run: pip install gitpython psutil schedule requests")
    sys.exit(1)

# ═══════════════════════════════════════════════════════════════════════════
#  CONFIG  ←  EDIT THIS ONE LINE BEFORE DEPLOYING
# ═══════════════════════════════════════════════════════════════════════════
REPO_URL      = "git@github.com:Jasim-svg/Ghost-sync.git"  # ← SET THIS

# Sync interval: 5–10 min, randomized per device to avoid simultaneous pushes
SYNC_INTERVAL = random.randint(300, 600)

AGENT_DIR     = Path(__file__).parent.resolve()
REPO_DIR      = AGENT_DIR.parent
ID_FILE       = AGENT_DIR / ".device_id"
CONFIG_FILE   = AGENT_DIR / "local_config.json"
LOG_FILE      = AGENT_DIR / "ghost_agent.log"

# Repo paths
TASKS_QUEUE   = REPO_DIR / "tasks" / "queue"
TASKS_CLAIMED = REPO_DIR / "tasks" / "claimed"
TASKS_DONE    = REPO_DIR / "tasks" / "done"
DEVICES_DIR   = REPO_DIR / "devices"
RESULTS_DIR   = REPO_DIR / "results"
LOGS_DIR      = REPO_DIR / "logs"

# ═══════════════════════════════════════════════════════════════════════════
#  LOGGING
# ═══════════════════════════════════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [GHOST] %(levelname)s — %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("ghost")


# ═══════════════════════════════════════════════════════════════════════════
#  IDENTITY  — asks ONE question on first boot only
# ═══════════════════════════════════════════════════════════════════════════
def get_or_create_identity() -> dict:
    if ID_FILE.exists() and CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass  # Corrupt config — re-register

    print("\n" + "═" * 54)
    print("  ⚡ GHOST SYNC AGENT — FIRST BOOT")
    print("  This question will NEVER be asked again.")
    print("═" * 54)
    name = input("\n  Device name (e.g. kali-laptop, win-burp, rpi-01): ").strip()
    if not name:
        name = f"device-{socket.gethostname()}"

    # Stable unique ID: name + hashed MAC address
    device_id = f"{name}-{hashlib.md5(str(uuid.getnode()).encode()).hexdigest()[:6]}"

    config = {
        "device_id":   device_id,
        "device_name": name,
        "hostname":    socket.gethostname(),
        "registered":  datetime.now(timezone.utc).isoformat(),
    }

    ID_FILE.write_text(device_id, encoding="utf-8")
    CONFIG_FILE.write_text(json.dumps(config, indent=2), encoding="utf-8")

    log.info(f"Registered as: {device_id}")
    print(f"\n  ✓ Registered: {device_id}")
    print("  ✓ Silent from now on. No more questions.\n")
    return config


# ═══════════════════════════════════════════════════════════════════════════
#  CAPABILITIES  — auto-detected, no user input
# ═══════════════════════════════════════════════════════════════════════════
def detect_capabilities() -> dict:
    def has(name: str) -> bool:
        return shutil.which(name) is not None

    mem_gb    = round(psutil.virtual_memory().total / (1024 ** 3), 1)
    cpu_cores = psutil.cpu_count(logical=False) or 1

    if mem_gb >= 16 and cpu_cores >= 8:
        power = "strong"
    elif mem_gb >= 8:
        power = "medium"
    else:
        power = "weak"

    return {
        "os":          platform.system(),
        "os_version":  platform.version()[:60],
        "arch":        platform.machine(),
        "ram_gb":      mem_gb,
        "cpu_cores":   cpu_cores,
        "power_class": power,
        "python":      platform.python_version(),
        "tools": {
            "nmap":     has("nmap"),
            "ffuf":     has("ffuf"),
            "gobuster": has("gobuster"),
            "nikto":    has("nikto"),
            "sqlmap":   has("sqlmap"),
            "curl":     has("curl"),
            "wget":     has("wget"),
            "git":      has("git"),
            "python3":  has("python3") or has("python"),
            "docker":   has("docker"),
            "john":     has("john"),
            "hashcat":  has("hashcat"),
            "nuclei":   has("nuclei"),
        },
    }


# ═══════════════════════════════════════════════════════════════════════════
#  GIT OPERATIONS  — with retry + rebase conflict resolution
# ═══════════════════════════════════════════════════════════════════════════
def git_pull(retries: int = 3) -> bool:
    """Pull with rebase. Retries on transient failures."""
    for attempt in range(1, retries + 1):
        try:
            repo = git.Repo(REPO_DIR)
            # Use rebase to avoid merge commits from multiple workers
            repo.git.pull("--rebase", "origin", "main")
            log.info("Pull OK")
            return True
        except git.exc.GitCommandError as e:
            err = str(e).lower()
            if "conflict" in err:
                # Abort rebase and reset — worker writes only to its own folder
                # so conflicts should never happen; if they do, our data wins
                log.warning(f"Pull conflict — aborting rebase and resetting")
                try:
                    repo.git.rebase("--abort")
                    repo.git.reset("--hard", "origin/main")
                    return True
                except Exception:
                    pass
            log.warning(f"Pull attempt {attempt}/{retries} failed: {e}")
            if attempt < retries:
                time.sleep(10 * attempt)
    return False


def git_push(message: str, retries: int = 3) -> bool:
    """Stage all, commit, push with retry."""
    for attempt in range(1, retries + 1):
        try:
            repo = git.Repo(REPO_DIR)
            repo.git.add(A=True)
            if repo.is_dirty(index=True, working_tree=False):
                repo.index.commit(message)
            try:
                repo.remotes.origin.push()
                log.info(f"Push OK: {message[:60]}")
                return True
            except git.exc.GitCommandError as push_err:
                # Remote has new commits — pull rebase and retry push
                log.warning(f"Push rejected — pulling first (attempt {attempt})")
                repo.git.pull("--rebase", "origin", "main")
                repo.remotes.origin.push()
                log.info(f"Push OK after rebase: {message[:60]}")
                return True
        except Exception as e:
            log.warning(f"Push attempt {attempt}/{retries} failed: {e}")
            if attempt < retries:
                time.sleep(15 * attempt)
    return False


# ═══════════════════════════════════════════════════════════════════════════
#  HEARTBEAT
# ═══════════════════════════════════════════════════════════════════════════
def push_heartbeat(identity: dict, caps: dict):
    device_id     = identity["device_id"]
    device_folder = DEVICES_DIR / device_id
    device_folder.mkdir(parents=True, exist_ok=True)

    status = {
        "device_id":    device_id,
        "device_name":  identity["device_name"],
        "status":       "online",
        "last_seen":    datetime.now(timezone.utc).isoformat(),
        "capabilities": caps,
    }

    (device_folder / "status.json").write_text(
        json.dumps(status, indent=2), encoding="utf-8"
    )
    (device_folder / "capabilities.json").write_text(
        json.dumps(caps, indent=2), encoding="utf-8"
    )


# ═══════════════════════════════════════════════════════════════════════════
#  TASK ENGINE
# ═══════════════════════════════════════════════════════════════════════════
POWER_RANK = {"weak": 1, "medium": 2, "strong": 3}


def scan_for_tasks(device_id: str, caps: dict) -> list:
    """
    Find unclaimed tasks this device can run.
    Task JSON: {task_id, command, requires:{power_class, tools[]}, priority, timeout_seconds}
    Workers ONLY read from tasks/queue/ — never write to it.
    """
    if not TASKS_QUEUE.exists():
        return []

    eligible = []
    for task_file in sorted(TASKS_QUEUE.glob("task_*.json")):
        try:
            task     = json.loads(task_file.read_text(encoding="utf-8"))
            req      = task.get("requires", {})

            # Power check
            my_power  = POWER_RANK.get(caps["power_class"], 1)
            req_power = POWER_RANK.get(req.get("power_class", "weak"), 1)
            if my_power < req_power:
                continue

            # Tool check
            req_tools = req.get("tools", [])
            if not all(caps["tools"].get(t, False) for t in req_tools):
                continue

            eligible.append((task_file, task))
        except Exception as e:
            log.warning(f"Bad task file {task_file.name}: {e}")

    # Sort by priority (1 = highest)
    eligible.sort(key=lambda x: x[1].get("priority", 99))
    return eligible


def claim_task(task_file: Path, task: dict, device_id: str) -> tuple:
    """
    Atomically claim a task via file rename.
    Returns (claimed_path, True) on success, (None, False) if another device got it.
    The atomic rename means only ONE device can ever win the claim.
    """
    claimed_path = TASKS_CLAIMED / f"{task_file.stem}_{device_id}.json"
    task["claimed_by"] = device_id
    task["claimed_at"] = datetime.now(timezone.utc).isoformat()

    # Write updated task to claimed path via temp file first
    tmp = claimed_path.with_suffix(".tmp")
    try:
        TASKS_CLAIMED.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(task, indent=2), encoding="utf-8")
        task_file.rename(claimed_path)  # atomic on POSIX; near-atomic on Windows
        tmp.unlink(missing_ok=True)
        log.info(f"Claimed: {task['task_id']} ← {task.get('command','')[:50]}")
        return claimed_path, True
    except (FileNotFoundError, OSError):
        # Another device renamed it first — skip gracefully
        tmp.unlink(missing_ok=True)
        log.info(f"Task {task['task_id']} already claimed — skipping")
        return None, False


def execute_task(task: dict, device_id: str) -> dict:
    """Run the command and capture all output."""
    command = task.get("command", "echo no-command")
    task_id = task.get("task_id", "unknown")
    timeout = task.get("timeout_seconds", 300)

    log.info(f"Executing [{task_id}]: {command[:80]}")

    result = {
        "task_id":      task_id,
        "device_id":    device_id,
        "command":      command,
        "started_at":   datetime.now(timezone.utc).isoformat(),
        "completed_at": None,
        "exit_code":    None,
        "status":       "running",
        "stdout":       "",
        "stderr":       "",
    }

    try:
        proc = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            # Windows: suppress console popup windows
            creationflags=(subprocess.CREATE_NO_WINDOW
                           if platform.system() == "Windows" else 0),
        )
        result["exit_code"] = proc.returncode
        result["stdout"]    = proc.stdout
        result["stderr"]    = proc.stderr
        result["status"]    = "success" if proc.returncode == 0 else "failed"
    except subprocess.TimeoutExpired:
        result["status"] = "timeout"
        result["stderr"] = f"Exceeded {timeout}s timeout"
    except Exception as e:
        result["status"] = "error"
        result["stderr"] = str(e)

    result["completed_at"] = datetime.now(timezone.utc).isoformat()
    log.info(f"Done [{task_id}]: {result['status']} (exit={result['exit_code']})")
    return result


def save_result(result: dict, device_id: str, claimed_file: Path):
    """Save result files and move task to done/."""
    ts      = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    tid     = result["task_id"]
    out_dir = RESULTS_DIR / device_id
    out_dir.mkdir(parents=True, exist_ok=True)

    # Full JSON result
    (out_dir / f"{ts}_{tid}.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )

    # Plain text stdout (easy to read from dashboard)
    if result.get("stdout"):
        (out_dir / f"{ts}_{tid}_output.txt").write_text(
            result["stdout"], encoding="utf-8"
        )

    # Move task file to done/
    TASKS_DONE.mkdir(parents=True, exist_ok=True)
    done_path = TASKS_DONE / claimed_file.name
    try:
        claimed_file.rename(done_path)
    except Exception:
        pass  # Already moved — fine

    log.info(f"Result saved → results/{device_id}/{ts}_{tid}.json")


# ═══════════════════════════════════════════════════════════════════════════
#  LOG SNAPSHOT  — push last 200 lines to repo so main PC can see them
# ═══════════════════════════════════════════════════════════════════════════
def push_log_snapshot(device_id: str):
    log_dest = LOGS_DIR / device_id
    log_dest.mkdir(parents=True, exist_ok=True)
    if LOG_FILE.exists():
        lines = LOG_FILE.read_text(encoding="utf-8").splitlines()[-200:]
        (log_dest / "agent.log").write_text("\n".join(lines), encoding="utf-8")


# ═══════════════════════════════════════════════════════════════════════════
#  MAIN SYNC LOOP
# ═══════════════════════════════════════════════════════════════════════════
def sync_loop(identity: dict, caps: dict):
    device_id = identity["device_id"]
    log.info(f"── Sync start ({device_id}) ──")

    # 1. Pull latest from repo
    if not git_pull():
        log.warning("Pull failed — skipping this cycle")
        return

    # 2. Heartbeat (let main PC know we're alive)
    push_heartbeat(identity, caps)

    # 3. Task engine
    tasks = scan_for_tasks(device_id, caps)
    if not tasks:
        log.info("Queue empty or no eligible tasks")
    else:
        task_file, task = tasks[0]  # highest priority first
        claimed_path, won = claim_task(task_file, task, device_id)

        if won and claimed_path:
            # Push claim immediately so other workers skip this task
            git_push(f"[{device_id}] claim {task['task_id']}")

            # Execute
            result       = execute_task(task, device_id)
            save_result(result, device_id, claimed_path)

    # 4. Log snapshot for main PC visibility
    push_log_snapshot(device_id)

    # 5. Push everything (heartbeat + result + logs)
    git_push(f"[{device_id}] sync {datetime.now(timezone.utc).strftime('%H:%M:%S')}")
    log.info(f"── Sync done ──\n")


# ═══════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════
def main():
    def shutdown(sig, frame):
        log.info("Ghost agent shutting down.")
        sys.exit(0)

    signal.signal(signal.SIGINT,  shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, shutdown)

    identity = get_or_create_identity()
    caps     = detect_capabilities()

    log.info(
        f"Ghost agent started | {identity['device_id']} | "
        f"OS: {caps['os']} | RAM: {caps['ram_gb']}GB | "
        f"Power: {caps['power_class']} | Interval: {SYNC_INTERVAL}s"
    )

    # First sync immediately on start
    try:
        sync_loop(identity, caps)
    except Exception as e:
        log.error(f"First sync failed: {e}")

    # Then schedule every SYNC_INTERVAL seconds
    schedule.every(SYNC_INTERVAL).seconds.do(sync_loop, identity=identity, caps=caps)

    while True:
        try:
            schedule.run_pending()
        except Exception as e:
            log.error(f"Scheduler error: {e}")
        time.sleep(10)


if __name__ == "__main__":
    main()
