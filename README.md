# Ghost Monitor

**Agency-grade passive security monitoring.** A commander PC runs the server; agents installed on staff machines push activity data over HTTP. No GitHub. No git. No manual setup on agent devices — just share a link.

---

## What It Does

- **Watches** every monitored machine: running processes, network connections, CPU / RAM / disk, active window title, recently modified files
- **Alerts** on high CPU/RAM, new external network connections, suspicious process activity
- **File browser** — browse the file system of any staff machine and download any file on demand
- **Enrollment** — generate a one-click setup link per device; staff member opens it in a browser, double-clicks the downloaded script, done
- **Remote access** — Cloudflare Tunnel support so staff in different cities connect over the internet with zero router configuration

---

## Tech Stack

| Layer | Technology |
|---|---|
| **Commander server** | Python 3.8+, Flask, flask-cors |
| **Agent** | Python 3.8+, psutil (stdlib only otherwise) |
| **Dashboard** | Vanilla HTML/CSS/JS — no build step, no framework |
| **Transport** | Plain HTTP POST (LAN) or HTTPS via Cloudflare Tunnel (WAN) |
| **Auth** | Per-agency secret token in `X-Agent-Secret` header |
| **Remote tunneling** | Cloudflare Tunnel (`cloudflared`) — free, no account required for quick tunnels |
| **Persistence** | JSON files in `data/` on the commander (no database) |
| **Startup** | Windows Scheduled Task (agents) / cron with pidfile lock (Linux/macOS) |

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                 YOUR COMMANDER PC                   │
│                                                     │
│  python dashboard_server.py  →  http://localhost:8888│
│                                                     │
│  data/                                              │
│  ├── devices/<id>/status.json    ← heartbeat        │
│  ├── logs/<id>/snapshots/        ← full snapshots   │
│  ├── logs/<id>/events/           ← diffs & alerts   │
│  ├── logs/<id>/agent.log         ← agent output     │
│  ├── file_browser/<id>/          ← file listings    │
│  ├── agent_tasks/<id>/           ← download queue   │
│  └── file_cache/<id>/            ← downloaded files │
└──────────────┬──────────────────────────────────────┘
               │  HTTP POST  (LAN or Cloudflare HTTPS)
               │
   ┌───────────┴────────────┐
   │                        │
┌──▼──────────┐     ┌───────▼──────┐
│ john-laptop │     │ workstation-2│  ...any number of devices
│             │     │              │
│ monitor_    │     │ monitor_     │
│ agent.py    │     │ agent.py     │
│             │     │              │
│ local_data/ │     │ local_data/  │  ← always written locally too
└─────────────┘     └──────────────┘
```

---

## Requirements

### Commander PC (your machine)

- Python 3.8+
- `pip install flask flask-cors`

### Agent devices (staff machines)

- Python 3.8+ (must be on PATH)
- `psutil` — installed automatically by the enrollment script

---

## Quick Start

### 1. Start the Commander

```bash
# Clone or download this repo on YOUR machine only
pip install flask flask-cors
python dashboard_server.py
```

Dashboard opens at **http://localhost:8888**

On startup the terminal prints your LAN IP and agency secret:

```
══════════════════════════════════════════════════════
  Ghost Monitor — Agency Security Dashboard
  Your dashboard:  http://localhost:8888
  Network access:  http://192.168.1.5:8888
  Agency secret:   a3f8c2d1...
  Enrollment URL:  http://192.168.1.5:8888/enroll/<name>.bat
══════════════════════════════════════════════════════
```

### 2. Enroll a Staff Device

1. Click **+ Enroll Device** in the dashboard
2. Type the device name (e.g. `john-laptop`)
3. Choose **Windows** or **Linux / macOS**
4. Click **Copy Link** and send it to the staff member  
   — or click **Download Script** to send the file yourself

**Staff member on Windows:** opens the link in a browser → double-clicks the downloaded `.bat` file → done.  
**Staff member on Linux / macOS:** opens the link in a browser → runs `bash ghost_monitor_devicename.sh` in a terminal.

The script automatically:
- Downloads the monitoring agent from your commander
- Installs `psutil`
- Configures the device ID and secret
- Creates a silent startup task (Windows Scheduled Task / cron)
- Starts monitoring immediately

The device appears in the dashboard within **5–10 minutes**.

---

## Remote Access (Different Cities / Networks)

By default agents connect via LAN IP. For staff on different networks use **Cloudflare Tunnel** — free, no account needed for quick tunnels.

### One-time setup on the commander PC

**1. Download cloudflared**

Go to: `developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads`  
Download the Windows `.exe` (or Linux/macOS binary).

**2. Start the tunnel** (keep this terminal open alongside the dashboard)

```bash
cloudflared tunnel --url http://localhost:8888
```

It prints a URL like:
```
https://proud-lion-abc123.trycloudflare.com
```

**3. Save the URL in the dashboard**

Dashboard → ⚙ Settings → paste the URL into **Public URL Override** → Save

All new enrollment links will now use the Cloudflare URL. Staff anywhere in the world can connect.

> **Note:** Free quick-tunnel URLs change every time you restart `cloudflared`. For a permanent URL, create a free Cloudflare account and set up a named tunnel.

---

## Dashboard Features

### Fleet Sidebar
Device cards with online/offline badge, last-seen time, and live CPU/RAM mini-bars.

### Per-Device Tabs

| Tab | What you see |
|---|---|
| **Processes** | All running processes sorted by CPU. PID, name, user, CPU%, RAM%, status |
| **Network** | External connections highlighted in amber. Local connections below |
| **Files** | Searchable listing of Documents / Desktop / Downloads. Up to 500 files |
| **Events** | Timeline of diffs: new/stopped processes, high CPU/RAM, new external connections |
| **Agent Log** | Last 100 lines of the agent's own log on that device |

### File Browser + On-Demand Download

In the **Files** tab, click **Request Download** next to any file.  
The agent uploads it on its next cycle (5–10 min). A **Download Now** link appears when ready.  
File size limit: 100 MB.

### Activity Feed
Global timeline of all events across all devices.

### Warnings Panel
Right sidebar shows all recent high-CPU, high-RAM, and external-connection alerts.

### Settings Modal
- View your LAN IP and agency secret
- Set Cloudflare Tunnel URL for remote access
- Regenerate the agency secret (invalidates all existing agents — requires re-enrollment)

---

## Security

| Feature | Detail |
|---|---|
| **Authentication** | Every agent request carries `X-Agent-Secret` header. Requests without a valid secret return 401 |
| **Secret storage** | Auto-generated `secrets.token_hex(16)`, stored in `monitor_settings.json` (gitignored) |
| **In transit** | LAN: plain HTTP. Remote: HTTPS via Cloudflare Tunnel (end-to-end encrypted) |
| **Upload limit** | Server rejects payloads > 150 MB to prevent OOM attacks |
| **Passive only** | Agents never execute commands. The server has zero command-dispatch endpoints |
| **Local resilience** | Agents always write to `local_data/` on the device regardless of commander connectivity |

---

## File Structure

```
ghost-sync/
│
├── dashboard_server.py       ← Commander server (Flask, port 8888)
├── monitor_settings.json     ← Auto-generated, gitignored (holds secret + URL override)
│
├── agent/
│   ├── monitor_agent.py      ← Agent daemon (installed on each staff machine)
│   ├── requirements.txt      ← psutil only
│   ├── start_agent.bat       ← Manual start (Windows)
│   └── start_agent_silent.vbs← Silent start helper
│
├── dashboard/
│   └── index.html            ← Dashboard UI (dark theme, no build step)
│
└── data/                     ← All runtime data (gitignored)
    ├── devices/              ← Device heartbeats
    ├── logs/                 ← Snapshots, events, agent logs
    ├── file_browser/         ← File listings from agents
    ├── agent_tasks/          ← Pending file-download requests
    └── file_cache/           ← Downloaded files waiting for you
```

---

## Agent Behaviour

- Runs every **5–10 minutes** (random interval to avoid sync storms)
- Collects: processes, network connections, system metrics, active window title, recently modified files
- Always writes to `local_data/` on the device first, then pushes to commander
- Polls for file-download tasks from the commander on every cycle
- On Linux/macOS: cron wrapper with pidfile lock prevents multiple instances
- On Windows: runs as a Scheduled Task via `pythonw` (silent, no console window)

---

## Troubleshooting

**Device not appearing in dashboard**

```
# On the staff machine, check if the agent is running
# Windows: Task Manager → look for pythonw or python
# Linux: ps aux | grep monitor_agent

# Check the agent log on the device:
# Windows: %USERPROFILE%\GhostMonitor\monitor_agent.log
# Linux:   ~/GhostMonitor/agent.log
```

Common causes:
- Commander PC not running — start `python dashboard_server.py`
- Wrong commander URL in the enrollment script — re-enroll with the correct URL
- Firewall blocking port 8888 — allow inbound on the commander

**Device shows OFFLINE even though it's running**

Agent is considered offline after 30 minutes without a check-in. If the agent just started, wait one cycle (up to 10 min). If using Cloudflare, make sure the tunnel is still running.

**File download stuck at "Uploading… wait for next agent sync"**

The agent uploads on its next cycle. Wait up to 10 minutes. If it stays stuck, the file may be > 100 MB or the agent may have lost connectivity. Check the Agent Log tab.

**"Could not download agent" during enrollment**

The enrollment script downloads `monitor_agent.py` from your commander. Make sure:
1. `python dashboard_server.py` is running on the commander
2. The enrollment URL uses the correct IP (LAN) or Cloudflare URL (remote)
3. Port 8888 is not blocked by a firewall

**Network tab shows no connections on Windows**

`psutil.net_connections()` requires Administrator privileges on Windows. The agent logs a warning and continues. For full network monitoring, run the scheduled task as Administrator or right-click the `.bat` and choose "Run as administrator" during enrollment.

---

## License

Private — internal agency use only.
