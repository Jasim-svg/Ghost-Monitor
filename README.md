# Ghost Monitor

**Self-hosted fleet monitoring that your staff can actually see.**

Most monitoring tools are built to be invisible. Ghost Monitor is built to be the opposite — agents can only run on a device after the person using it has explicitly acknowledged what's collected, and a persistent tray indicator reminds them it's running every day. Administrators see everything. So do the people being monitored.

Zero database. Zero Docker. Python + a JSON folder.

---

## Why this exists

Every open-source monitoring tool I found had the same problem: the monitoring was structurally silent. Either the tool required covert deployment by design, or adding disclosure was an afterthought left to the operator. Ghost Monitor makes disclosure a hard requirement — the agent literally refuses to start without a logged consent record, and there is no configuration option to suppress the tray indicator.

This makes it the right tool for:
- Small agencies monitoring their own devices
- Freelance teams with remote staff who want visibility without surveillance culture
- Anyone who needs fleet monitoring but wants to look their staff in the eye while doing it

---

## How it's different

| | Ghost Monitor | Cattr | ActivityWatch | Commercial tools |
|---|:---:|:---:|:---:|:---:|
| Self-hosted | ✅ | ✅ | ✅ | ❌ |
| Zero infrastructure (no Docker/Postgres) | ✅ | ❌ | ✅ | ❌ |
| Fleet dashboard (multiple devices) | ✅ | ✅ | ❌ | ✅ |
| Consent gate (agent refuses to run without acknowledgment) | ✅ | ❌ | ❌ | ❌ |
| Persistent tray indicator on monitored device | ✅ | ❌ | ❌ | Rare |
| Employee transparency view (what was collected, what was accessed) | ✅ | ❌ | ✅ self only | Rare |
| File access log visible to the monitored device | ✅ | ❌ | ❌ | ❌ |
| Quiet hours / self-pause (logged, visible to admin) | ✅ | ❌ | ❌ | ❌ |

---

## Architecture

```
Your PC (Commander)                   Staff Devices (Agents)
┌────────────────────────┐            ┌─────────────────────────┐
│  dashboard_server.py   │            │  monitor_agent.py       │
│  (Flask)               │◄── POST ───│  (runs every 5-10 min)  │
│                        │            │                         │
│  /api/agent/*          │            │  tray_icon.py           │
│  /api/consent/*        │            │  (always visible)       │
│  /api/fleet            │            │                         │
│  /enroll/*             │            │  local_data/            │
│                        │            │  └── summary.html       │
│  data/                 │            │  └── access_log.json    │
│  ├── devices/          │            └─────────────────────────┘
│  ├── consent/          │
│  │   ├── ack.json      │  ← consent record, permanent
│  │   └── access_log    │  ← every file pull, append-only
│  └── logs/             │
└────────────────────────┘
```

No sockets. No message queues. Agents push via HTTP POST. The tray indicator reads local files only — it stays accurate even when the commander is unreachable.

---

## Stack

| Component | Tech | Why |
|---|---|---|
| Commander server | Python + Flask | Minimal, single file, no ORM needed |
| Agent | Python + psutil | Runs same code on Windows, Linux, macOS |
| Tray indicator | pystray + plyer | Cross-platform, no GUI framework |
| Storage | JSON files | Human-readable, zero config, easy backup |
| Remote access | Cloudflare Tunnel | Free, stable URL, no port forwarding |
| Startup (Windows) | Scheduled Task | No admin rights needed, runs as current user |
| Startup (Linux) | cron + pidfile | User-level, no systemd required |

---

## What it collects

Collected every 5–10 minutes and visible to the monitored person at any time via the tray icon:

- Running processes (name, CPU%, RAM%)
- Network connections (local + external addresses, state)
- CPU, RAM, and disk usage
- Active window title
- File listing of Documents, Desktop, Downloads (browsable on request — all requests logged)

---

## Quick start

### Commander (your PC)

```bash
git clone https://github.com/Jasim-svg/Ghost-sync.git
cd Ghost-sync
pip install flask flask-cors
python dashboard_server.py
# Open http://localhost:8888
```

### Enrolling a device

1. Open the dashboard → click **+ Enroll Device**
2. Enter a name for the device, select OS, copy the enrollment link
3. Send the link to the staff member — they run it on their machine
4. The enrollment script shows them exactly what will be collected and requires them to type **Y** to continue
5. Their acknowledgment is logged on your commander before anything installs
6. The agent and tray indicator start automatically

### Remote access (optional)

```bash
# Install cloudflared, then:
cloudflared tunnel --url http://localhost:8888
# Paste the generated URL into Settings → Commander URL
# Agents on any network will now reach your commander
```

---

## The consent flow, step by step

When a staff member runs an enrollment script:

```
This device will be monitored by [Your Agency].

What will be collected:
  - Running processes (name, CPU usage, memory usage)
  - Network connections (outbound addresses and state)
  - CPU, RAM, and disk metrics
  - Active window title
  - File listing of Documents, Desktop, Downloads
    (individual files only downloaded on explicit admin request,
     all requests logged and visible to you)

You can view what's been collected at any time via the tray icon.
To ask questions about this policy, contact: [admin contact]

Type Y to continue, anything else to cancel:
```

Non-Y input exits immediately. No files written, no network calls. The acknowledgment (who, when, what policy version) is sent to the commander before anything else happens. The agent will not start on future reboots without that local record.

---

## The transparency page

Every monitored device has a local `summary.html` (opened via the tray icon → "What's being monitored?") showing:

- When monitoring started and under which policy
- A plain-language list of data categories collected
- Every file an administrator has downloaded from this device, with timestamps
- Last sync time and whether the agent is currently reaching the commander

This page is generated from local files — it works with no network connection and requires no login.

---

## Requirements

**Commander:**
- Python 3.8+
- `pip install flask flask-cors`
- Any OS

**Agent devices:**
- Python 3.8+
- `pip install psutil pystray plyer`
- Windows 10+ / Linux / macOS

No database. No Docker. No npm. No build step.

---

## Lawful use

Ghost Monitor is designed for monitoring devices you own or have organizational authority over, with the users of those devices informed and consenting — which is what the enrollment flow structurally enforces. The tool is deliberately designed so there is no path to deploy it silently. Using any monitoring software outside of applicable law and your organization's policies is a separate responsibility.

---

## License

Apache 2.0 — see `LICENSE`.

The Apache 2.0 license includes an explicit patent non-aggression clause. You can use, modify, and distribute this freely.

---

## Contributing

Issues and PRs welcome. If you find a security problem, open a private issue or email directly — don't post it publicly.

Built by [Muhammad Jasim Fiaz](https://github.com/Jasim-svg) · AI & Full-Stack Developer
