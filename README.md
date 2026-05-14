# ⚡ GHOST SYNC

> *Silent. Distributed. Deadly.*

A private GitHub repo as a fully automated distributed task execution and file sync system across 9 heterogeneous research devices — with chain-of-command access control, end-to-end encryption for secrets, and a beautiful commander dashboard.

---

## Architecture

```
MAIN PC (Windows 10)  ←→  GitHub Private Repo  ←→  Workers (1-8)
  [COMMANDER]                  [SECURE HUB]           [SOLDIERS]
  Full access                  Encrypted             Blind to main PC
  Web dashboard                Task bus              Push results only
  Dispatches tasks             Audit log             Auto-sync 5-10 min
```

---

## Chain of Command Rules

| Action | Main PC | Workers |
|---|---|---|
| Dispatch tasks | ✅ | ❌ |
| Read task queue | ✅ | ✅ |
| Read own results | ✅ | ✅ own only |
| Read other workers' results | ✅ | ❌ |
| Decrypt secure/ | ✅ | ❌ |
| See Main PC identity | — | ❌ Never |

---

## Quick Start

### Step 1 — Create Private GitHub Repo

```bash
gh repo create ghost-sync --private
# or create manually at github.com
```

### Step 2 — Clone on Main PC (Windows 10)

```bash
git clone git@github.com:YOUR_USERNAME/ghost-sync.git
cd ghost-sync
python setup.py --main
```

### Step 3 — Set Repo URL

Edit `agent/ghost_agent.py` line 36:
```python
REPO_URL = "git@github.com:YOUR_USERNAME/ghost-sync.git"
```

### Step 4 — Set Up git-crypt (Encryption)

```bash
# On main PC only
git-crypt init
git-crypt add-gpg-user YOUR_GPG_KEY_ID
git-crypt status
```

Install git-crypt:
- Windows: `choco install git-crypt` or download from https://github.com/AGWA/git-crypt
- Linux: `apt install git-crypt`
- macOS: `brew install git-crypt`

### Step 5 — Set Up Each Worker

```bash
# On each worker device:
git clone git@github.com:YOUR_USERNAME/ghost-sync.git
cd ghost-sync
python setup.py --worker
# Answer ONE question (device name) — never asked again
# Cron/Task Scheduler installed automatically
```

### Step 6 — Start Commander Dashboard (Main PC)

```bash
# Windows:
start_commander.bat

# Or:
python dashboard_server.py
```

Open: **http://localhost:8888**

---

## Dispatching Tasks

### From Dashboard
Use the Dispatch panel in the web UI.

### From Command Line (Main PC)

```bash
# Simple command to any capable worker
python dispatch.py --cmd "nmap -sV 192.168.1.1" --tools nmap --power weak

# Heavy task — strong devices only
python dispatch.py --cmd "hashcat -m 0 hash.txt rockyou.txt" --power strong --priority 1

# Check fleet status
python dispatch.py --fleet

# See all task statuses
python dispatch.py --list

# See worker results
python dispatch.py --results
```

---

## Repo Structure

```
ghost-sync/
├── .gitignore              ← Never commits secrets
├── .gitattributes          ← git-crypt rules (secure/ encrypted)
│
├── tasks/
│   ├── queue/              ← Main PC drops tasks here
│   ├── claimed/            ← Workers move tasks here when starting
│   └── done/               ← Completed tasks
│
├── results/
│   └── <device_id>/        ← Each worker's output (isolated)
│
├── devices/
│   └── <device_id>/
│       ├── status.json     ← Heartbeat + online status
│       └── capabilities.json
│
├── secure/                 ← 🔐 FULLY ENCRYPTED (git-crypt)
│   ├── credentials/        ← Finding credentials
│   ├── api_keys/           ← API keys
│   └── shared_secrets/     ← Shared secrets
│
├── resources/              ← Shared tools + wordlists (unencrypted)
│   ├── wordlists/
│   ├── scripts/
│   └── configs/
│
├── logs/
│   └── <device_id>/        ← Worker agent logs
│
├── agent/
│   ├── ghost_agent.py      ← Worker daemon (runs on all 8 workers)
│   └── requirements.txt
│
├── dashboard/
│   └── index.html          ← Commander web UI
│
├── dashboard_server.py     ← Local Flask server (Main PC only)
├── dispatch.py             ← CLI task dispatcher (Main PC only)
├── setup.py                ← One-time setup wizard
└── hooks/
    └── pre-commit          ← Secret leak scanner
```

---

## Encryption (secure/ folder)

Files in `secure/` are **automatically encrypted** when pushed and **automatically decrypted** when pulled on the main PC. Workers pull the encrypted blobs — they cannot read them.

```bash
# Add a secret (main PC — auto-encrypts on push)
echo '{"openai": "sk-..."}' > secure/api_keys/openai.json
git add . && git commit -m "add api key" && git push

# Workers see: binary encrypted blob — unreadable
# Main PC sees: plain JSON — readable as normal
```

---

## Worker Task Format

```json
{
  "task_id": "a1b2c3d4",
  "command": "nmap -sV -p- 10.0.0.1",
  "requires": {
    "power_class": "medium",
    "tools": ["nmap"]
  },
  "priority": 3,
  "timeout_seconds": 600,
  "created_at": "2025-01-01T12:00:00Z"
}
```

---

## Security Notes

- Each device has its own SSH key — revoke individually if compromised
- Pre-commit hook blocks API keys, passwords, private keys from being committed
- Workers write ONLY to `results/<their_device_id>/` and `devices/<their_device_id>/`
- Main PC identity is never stored in the repo
- git log provides full audit trail of every push

---

## Troubleshooting

**Worker not picking up tasks**
```bash
# Check agent is running
cat agent/ghost_agent.log

# Manual run
python agent/ghost_agent.py
```

**Push conflicts**
```bash
git pull --rebase
git push
```

**git-crypt unlock on main PC**
```bash
git-crypt unlock
```
