#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════╗
║        GHOST SYNC — SETUP WIZARD v1.1               ║
║  Run ONCE on each device. Never run again.          ║
║  Works on Windows 10 / Linux / macOS               ║
╚══════════════════════════════════════════════════════╝

Usage:
  python setup.py           (asks: main or worker?)
  python setup.py --main    (force main PC setup)
  python setup.py --worker  (force worker setup)
"""

import os
import sys
import json
import shutil
import subprocess
import platform
import argparse
from pathlib import Path

IS_WINDOWS = platform.system() == "Windows"
IS_LINUX   = platform.system() == "Linux"
IS_MAC     = platform.system() == "Darwin"

REPO_DIR  = Path(__file__).parent.resolve()
AGENT_DIR = REPO_DIR / "agent"


# ═══════════════════════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════════════════════
def run(cmd: str, check: bool = False, capture: bool = False):
    return subprocess.run(
        cmd, shell=True, check=check,
        capture_output=capture, text=True
    )


def banner(text: str):
    print("\n" + "═" * 54)
    print(f"  {text}")
    print("═" * 54)


def ok(msg: str):
    print(f"  [✓] {msg}")


def warn(msg: str):
    print(f"  [!] {msg}")


def info(msg: str):
    print(f"      {msg}")


# ═══════════════════════════════════════════════════════════════════════════
#  PYTHON DEPS
# ═══════════════════════════════════════════════════════════════════════════
def install_python_deps(mode: str):
    banner("Installing Python dependencies")
    py = sys.executable
    base = ["gitpython", "psutil", "schedule", "requests"]
    if mode == "main":
        base += ["flask", "flask-cors", "rich"]

    result = run(f'"{py}" -m pip install {" ".join(base)} --quiet')
    if result.returncode == 0:
        ok("All Python dependencies installed")
    else:
        warn("pip install had errors — check manually")
        info(f"Run: {py} -m pip install {' '.join(base)}")


# ═══════════════════════════════════════════════════════════════════════════
#  PRE-COMMIT HOOK
# ═══════════════════════════════════════════════════════════════════════════
def install_hook():
    banner("Installing pre-commit secret scanner")
    git_hooks = REPO_DIR / ".git" / "hooks"

    if not git_hooks.exists():
        warn(".git/hooks not found — is this a git repo?")
        info("Run: git init  then re-run setup.py")
        return

    src = REPO_DIR / "hooks" / "pre-commit"
    dst = git_hooks / "pre-commit"

    if not src.exists():
        warn("hooks/pre-commit not found — skipping")
        return

    shutil.copy(src, dst)
    if not IS_WINDOWS:
        os.chmod(dst, 0o755)
    ok("Pre-commit secret scanner installed")


# ═══════════════════════════════════════════════════════════════════════════
#  SSH KEY
# ═══════════════════════════════════════════════════════════════════════════
def setup_ssh_key():
    banner("SSH key setup")
    ssh_dir  = Path.home() / ".ssh"
    key_file = ssh_dir / "id_ed25519"
    pub_file = ssh_dir / "id_ed25519.pub"

    if key_file.exists():
        ok("SSH key already exists")
    else:
        ssh_dir.mkdir(mode=0o700, exist_ok=True)
        result = run(
            f'ssh-keygen -t ed25519 -f "{key_file}" -N "" -C "ghost-sync-agent"',
            capture=True
        )
        if result.returncode == 0:
            ok("SSH key generated")
        else:
            warn("SSH key generation failed — generate manually")
            info("ssh-keygen -t ed25519 -C ghost-sync-agent")

    if pub_file.exists():
        pub = pub_file.read_text().strip()
        print()
        print("  ── Add this public key to GitHub ──────────────────────")
        print("  GitHub.com → Settings → SSH and GPG keys → New SSH key")
        print()
        print(f"  {pub}")
        print()


# ═══════════════════════════════════════════════════════════════════════════
#  SCHEDULER  — installs auto-start for worker agent
# ═══════════════════════════════════════════════════════════════════════════
def setup_cron_linux(interval_minutes: int = 7):
    """Cron job for Linux / macOS workers."""
    py     = sys.executable
    script = str(AGENT_DIR / "ghost_agent.py")
    logf   = str(AGENT_DIR / "cron.log")
    marker = f"ghost_agent.py"

    result = run("crontab -l", capture=True)
    existing = result.stdout if result.returncode == 0 else ""

    if marker in existing:
        ok("Cron job already installed")
        return

    cron_line = f"*/{interval_minutes} * * * * {py} {script} >> {logf} 2>&1\n"
    new_crontab = existing.rstrip("\n") + "\n" + cron_line

    proc = subprocess.Popen(["crontab", "-"], stdin=subprocess.PIPE)
    proc.communicate(input=new_crontab.encode())

    if proc.returncode == 0:
        ok(f"Cron job installed (every {interval_minutes} min)")
    else:
        warn("Cron install failed — add manually:")
        info(f"{cron_line.strip()}")


def setup_windows_task(interval_minutes: int = 7):
    """Windows Task Scheduler for worker agent — runs silently."""
    py        = sys.executable
        # Use pythonw.exe for silent background execution (no console window)
    pythonw   = py.replace("python.exe", "pythonw.exe")
    if not Path(pythonw).exists():
        pythonw = py  # fallback to python.exe if pythonw not found

    script    = str(AGENT_DIR / "ghost_agent.py")
    task_name = "GhostSyncAgent"

    # Delete existing task first (ignore errors)
    run(f'schtasks /delete /tn "{task_name}" /f', capture=True)

    cmd = (
        f'schtasks /create /tn "{task_name}" '
        f'/tr "\\"{pythonw}\\" \\"{script}\\"" '
        f'/sc minute /mo {interval_minutes} '
        f'/f /rl HIGHEST /ru SYSTEM'
    )
    result = run(cmd, capture=True)

    if result.returncode == 0:
        ok(f"Windows Task Scheduler entry created (every {interval_minutes} min)")
        ok("Runs silently in background, even when no window is open")
    else:
        warn("Task Scheduler failed — try running setup.py as Administrator")
        warn("Or add task manually in Task Scheduler GUI:")
        info(f"Program: {pythonw}")
        info(f"Args:    {script}")
        info(f"Trigger: Every {interval_minutes} minutes")


def setup_linux_systemd():
    """Optional: systemd service for always-on Linux workers."""
    py     = sys.executable
    script = str(AGENT_DIR / "ghost_agent.py")
    user   = os.environ.get("USER", "root")

    service = f"""[Unit]
Description=Ghost Sync Worker Agent
After=network.target

[Service]
Type=simple
User={user}
ExecStart={py} {script}
Restart=always
RestartSec=30
StandardOutput=append:{AGENT_DIR}/ghost_agent.log
StandardError=append:{AGENT_DIR}/ghost_agent.log

[Install]
WantedBy=multi-user.target
"""
    svc_path = Path(f"/etc/systemd/system/ghost-sync.service")
    try:
        svc_path.write_text(service)
        run("systemctl daemon-reload")
        run("systemctl enable ghost-sync")
        run("systemctl start ghost-sync")
        ok("systemd service installed and started")
        ok("Run: systemctl status ghost-sync  to verify")
    except PermissionError:
        warn("systemd install needs root — skipping (cron installed instead)")


# ═══════════════════════════════════════════════════════════════════════════
#  START SCRIPTS
# ═══════════════════════════════════════════════════════════════════════════
def write_start_scripts():
    banner("Creating start scripts")
    py = sys.executable
    pythonw = py.replace("python.exe", "pythonw.exe")

    if IS_WINDOWS:
        # Worker: silent background launch via pythonw
        (AGENT_DIR / "start_agent.bat").write_text(
            f'@echo off\n'
            f'echo Starting Ghost Sync Agent...\n'
            f'start "" "{pythonw}" "{AGENT_DIR / "ghost_agent.py"}"\n'
            f'echo Agent launched in background.\n',
            encoding="utf-8"
        )
        # Worker: VBS silent launcher (no CMD window at all)
        (AGENT_DIR / "start_agent_silent.vbs").write_text(
            f'Set WshShell = CreateObject("WScript.Shell")\n'
            f'WshShell.Run Chr(34) & "{pythonw}" & Chr(34) & " " & '
            f'Chr(34) & "{AGENT_DIR / "ghost_agent.py"}" & Chr(34), 0, False\n',
            encoding="utf-8"
        )
        # Commander dashboard
        (REPO_DIR / "start_commander.bat").write_text(
            f'@echo off\n'
            f'echo Starting Ghost Sync Commander Dashboard...\n'
            f'echo Open: http://localhost:8888\n'
            f'"{py}" "{REPO_DIR / "dashboard_server.py"}"\n'
            f'pause\n',
            encoding="utf-8"
        )
        ok("start_agent.bat + start_agent_silent.vbs created")
        ok("start_commander.bat created")
    else:
        sh_agent = AGENT_DIR / "start_agent.sh"
        sh_agent.write_text(
            f'#!/bin/bash\nnohup "{py}" "{AGENT_DIR / "ghost_agent.py"}" '
            f'>> "{AGENT_DIR / "ghost_agent.log"}" 2>&1 &\necho "Agent started (PID=$!)"\n'
        )
        os.chmod(sh_agent, 0o755)

        sh_cmd = REPO_DIR / "start_commander.sh"
        sh_cmd.write_text(
            f'#!/bin/bash\n"{py}" "{REPO_DIR / "dashboard_server.py"}"\n'
        )
        os.chmod(sh_cmd, 0o755)
        ok("start_agent.sh + start_commander.sh created")


# ═══════════════════════════════════════════════════════════════════════════
#  VERIFY REPO URL IS SET
# ═══════════════════════════════════════════════════════════════════════════
def check_repo_url():
    agent_file = AGENT_DIR / "ghost_agent.py"
    if agent_file.exists():
        content = agent_file.read_text(encoding="utf-8")
        if "YOUR_USERNAME" in content:
            warn("REPO_URL not set in agent/ghost_agent.py!")
            info('Edit line: REPO_URL = "git@github.com:YOUR_USERNAME/ghost-sync.git"')
            info("Replace YOUR_USERNAME/ghost-sync with your actual GitHub repo.")
            return False
    ok("REPO_URL check passed")
    return True


# ═══════════════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser(description="Ghost Sync Setup Wizard")
    parser.add_argument("--main",   action="store_true", help="Setup as Main PC (commander)")
    parser.add_argument("--worker", action="store_true", help="Setup as Worker device")
    args = parser.parse_args()

    banner("GHOST SYNC — Setup Wizard v1.1")
    print(f"  OS:     {platform.system()} {platform.version()[:40]}")
    print(f"  Python: {platform.python_version()}")
    print(f"  Dir:    {REPO_DIR}")

    if args.main:
        mode = "main"
    elif args.worker:
        mode = "worker"
    else:
        print("\n  Is this the MAIN PC (commander) or a WORKER device?")
        while True:
            choice = input("  Type 'main' or 'worker': ").strip().lower()
            if choice in ("main", "worker"):
                mode = choice
                break
            print("  Please type exactly: main  or  worker")

    print(f"\n  Mode: {mode.upper()}")

    install_python_deps(mode)
    install_hook()
    setup_ssh_key()
    write_start_scripts()

    if mode == "worker":
        banner("Installing auto-sync scheduler")
        if IS_WINDOWS:
            setup_windows_task(interval_minutes=7)
        elif IS_LINUX:
            setup_cron_linux(interval_minutes=7)
            # Offer systemd if available
            if shutil.which("systemctl"):
                print()
                use_systemd = input("  Install as systemd service? (more reliable) [y/N]: ").strip().lower()
                if use_systemd == "y":
                    setup_linux_systemd()
        elif IS_MAC:
            setup_cron_linux(interval_minutes=7)

    check_repo_url()

    # ── Summary ──────────────────────────────────────────────────────────
    banner(f"Setup Complete — {mode.upper()} MODE")

    if mode == "worker":
        print("  NEXT STEPS:")
        print("  1. Edit agent/ghost_agent.py — set REPO_URL")
        print("  2. Add your SSH public key to GitHub (shown above)")
        print("  3. Start the agent once to register this device:")
        if IS_WINDOWS:
            print("       agent\\start_agent.bat")
            print("     Or for fully silent background start:")
            print("       agent\\start_agent_silent.vbs  (double-click)")
        else:
            print("       ./agent/start_agent.sh")
        print("  4. After that — fully automated. Nothing else to do.")
    else:
        print("  NEXT STEPS:")
        print("  1. Edit agent/ghost_agent.py — set REPO_URL")
        print("  2. Add your SSH public key to GitHub (shown above)")
        print("  3. Set up git-crypt encryption:")
        print("       git-crypt init")
        print("       git-crypt add-gpg-user YOUR_GPG_KEY_ID")
        print("  4. Start the commander dashboard:")
        if IS_WINDOWS:
            print("       start_commander.bat")
        else:
            print("       ./start_commander.sh")
        print("  5. Open: http://localhost:8888")

    print()
    print("  See README.md for full documentation.")
    print("═" * 54 + "\n")


if __name__ == "__main__":
    main()
