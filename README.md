# Ghost Monitor v2

**Agency-grade passive security monitoring with full consent and transparency.**  
A commander PC runs the server; agents installed on staff machines push activity data over HTTP.  
No GitHub. No git. No manual setup on agent devices — just share a link.

v2 adds a hard consent gate, per-device token auth, a system tray indicator on every monitored machine, a local transparency page staff can open any time, and logged access for every file an admin downloads.

---

## What It Does

- **Watches** every monitored machine: running processes, network connections, CPU / RAM / disk, active window title, recently modified files
- **Alerts** on high CPU/RAM, new external network connections, suspicious process activity
- **File browser** — browse the file system of any staff machine and download any file on demand
- **Enrollment** — generate a one-click setup link per device; staff member reads the policy, types Y, and the script does the rest
- **Remote access** — Cloudflare Tunnel support so staff in different cities connect over the internet with zero router configuration
- **Consent gate** — the agent refuses to start without a local consent record; enrolling without seeing the policy is impossible
- **Tray indicator** — a green/amber dot lives in the system tray on every monitored machine for the agent's entire lifetime; right-click it to see exactly what's being collected
- **Transparency page** — a local HTML report (no internet needed) showing what's collected, every file an admin has downloaded, and every pause the user has triggered
- **Quiet hours** — staff can pause monitoring for 1–2 hours from the tray; capped at 2 uses per day; admin always sees paused state (never looks like offline)
- **Per-device revoke** — one click in the dashboard invalidates a single device's token without touching any other device

---

## Tech Stack

| Layer | Technology |
|---|---|
| **Commander server** | Python 3.8+, Flask, flask-cors |
| **Agent** | Python 3.8+, psutil (stdlib only otherwise) |
| **Tray indicator** | Python 3.8+, pystray, Pillow |
| **Dashboard** | Vanilla HTML/CSS/JS — no build step, no framework |
| **Transport** | Plain HTTP POST (LAN) or HTTPS via Cloudflare Tunnel (WAN) |
| **Auth** | Per-device token in `X-Agent-Token` header — each enrolled device gets its own token; revoking one device leaves all others working |
| **Remote tunneling** | Cloudflare Tunnel (`cloudflared`) — free, no account required for quick tunnels |
| **Persistence** | JSON files in `data/` on the commander (no database) |
| **Startup** | Windows: two Scheduled Tasks (agent + tray) / Linux/macOS: cron + pidfile for agent, `@reboot` cron for tray |

---

## Architecture

```
┌──────────────────────────────────────────────────────┐
│                  YOUR COMMANDER PC                   │
│                                                      │
│  python dashboard_server.py  →  http://localhost:8888│
│                                                      │
│  tokens.json            ← per-device tokens          │
│  data/                                               │
│  ├── devices/<id>/status.json   ← heartbeat          │
│  ├── logs/<id>/snapshots/       ← full snapshots     │
│  ├── logs/<id>/events/          ← diffs & alerts     │
│  ├── logs/<id>/agent.log        ← agent output       │
│  ├── file_browser/<id>/         ← file listings      │
│  ├── agent_tasks/<id>/          ← download queue     │
│  ├── file_cache/<id>/           ← downloaded files   │
│  └── consent/<id>/              ← consent ack +      │
│       ├── ack.json                  access log       │
│       └── access_log.json                            │
└──────────────┬───────────────────────────────────────┘
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
│ tray_icon.py│     │ tray_icon.py │  ← always visible in tray
│             │     │              │
│ local_data/ │     │ local_data/  │  ← always written locally
│  summary.html     │  summary.html│  ← transparency page
└─────────────┘     └──────────────┘
```

---

## Requirements

### Commander PC (your machine)

- Python 3.8+
- `pip install flask flask-cors`

### Agent devices (staff machines)

- Python 3.8+ (must be on PATH)
- `psutil`, `pystray`, `plyer`, `Pillow` — all installed automatically by the enrollment script

---

## Quick Start

### 1. Start the Commander

```bash
# Clone or download this repo on YOUR machine only
pip install flask flask-cors
python dashboard_server.py
```

Dashboard opens at **http://localhost:8888**

On startup the terminal prints your network address and enrollment URL:

```
══════════════════════════════════════════════════════════
  Ghost Monitor v2 — Agency Security Dashboard
  Dashboard:     http://localhost:8888
  Network:       http://192.168.1.5:8888
  Auth:          per-device tokens  (tokens.json)
  Enrollment:    http://192.168.1.5:8888/enroll/<name>.bat
  Press Ctrl+C to stop
══════════════════════════════════════════════════════════
```

### 2. Enroll a Staff Device

1. Click **+ Enroll Device** in the dashboard
2. Type the device name (e.g. `john-laptop`)
3. Choose **Windows** or **Linux / macOS**
4. Click **Copy Link** and send it to the staff member  
   — or click **Download Script** to send the file yourself

**What happens when the staff member runs the script (v2 flow):**

1. The script prints the full monitoring policy and asks: `Type Y to continue, anything else to cancel`  
   → If they type anything other than Y, **zero files are written and zero network calls are made**
2. Consent is posted to the commander (if the commander is unreachable, the script stops — no silent installs)
3. Python is checked
4. `monitor_agent.py` **and** `tray_icon.py` are downloaded from your commander
5. `psutil`, `pystray`, `Pillow` are installed
6. `local_config.json` and `consent_ack.json` are written locally
7. **Two** startup entries are created — one for the agent, one for the tray indicator
8. Both processes start immediately

The device appears in the dashboard within **5–10 minutes**.  
A shield/dot icon appears in the staff member's system tray immediately.

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

### Stats Bar

| Stat | Meaning |
|---|---|
| **Online** | Devices that checked in within the last 30 minutes |
| **Offline** | Devices not seen recently (or revoked) |
| **Total** | All enrolled devices |
| **Consented** | Devices with a valid consent record (turns amber if any device is missing one) |
| **Alerts (1h)** | Warning/critical events in the last hour |

### Fleet Sidebar

Device cards show online/offline/paused badge, a `✓` (consented) or `!` (no consent) indicator, last-seen time, and live CPU/RAM mini-bars.

### Per-Device Tabs

| Tab | What you see |
|---|---|
| **Processes** | All running processes sorted by CPU. PID, name, user, CPU%, RAM%, status |
| **Network** | External connections highlighted in amber. Local connections below |
| **Files** | Searchable listing of Documents / Desktop / Downloads. Up to 500 files |
| **Events** | Timeline of diffs: new/stopped processes, high CPU/RAM, new external connections |
| **Agent Log** | Last 100 lines of the agent's own log on that device |

### Device Detail Header

Alongside the name and online badge you'll see:
- **✓ CONSENTED** badge (with timestamp) or **! NO CONSENT** warning
- **✕ Revoke Device** button — immediately invalidates that device's token; all other devices are unaffected; the device must be re-enrolled with a new link to resume

### File Browser + On-Demand Download

In the **Files** tab, click **Request Download** next to any file.  
The agent uploads it on its next cycle (5–10 min). A **Download Now** link appears when ready.  
File size limit: 100 MB.

Every download is logged in the device's `data/consent/<id>/access_log.json` and synced to the device's local transparency page within one agent cycle.

### Activity Feed
Global timeline of all events across all devices.

### Warnings Panel
Right sidebar shows all recent high-CPU, high-RAM, and external-connection alerts.

### Settings Modal
- View your LAN IP
- Read the current monitoring policy (exactly what staff see at enrollment)
- Set Cloudflare Tunnel URL for remote access

---

## Tray Indicator (Staff Side)

The tray icon runs on the staff machine continuously, independent of whether the commander is reachable.

| Icon color | Meaning |
|---|---|
| **Green** | Agent synced within the last 35 minutes — monitoring is active |
| **Amber** | Agent hasn't synced recently, or monitoring is paused |

Right-click the icon to:
- See when monitoring last synced
- Open **What's being monitored?** — a local HTML page showing all data categories, every file an admin has downloaded, and every pause you've triggered. Opens with zero network dependency.
- Open **View monitoring policy** — the full policy text from the commander
- **Pause monitoring for 1 hour / 2 hours** — capped at 2 pauses per day. While paused the agent still sends a lightweight heartbeat so the admin sees "PAUSED" not "OFFLINE".

---

## Security

| Feature | Detail |
|---|---|
| **Per-device auth** | Each device gets its own `secrets.token_hex(16)` token at enrollment. Stored in `tokens.json` (gitignored). Revoking one device does not affect others. |
| **Token header** | Every agent request carries `X-Agent-Token`. Requests with a missing or wrong token return 401. Comparison uses `secrets.compare_digest` (constant-time, timing-attack resistant). |
| **Consent gate** | Agent refuses to start if `consent_ack.json` is absent. This file is only created by the enrollment script, after the staff member explicitly types Y. Copying `monitor_agent.py` to a machine by hand does nothing — it exits immediately. |
| **Consent recorded twice** | Server writes `data/consent/<id>/ack.json`. Enrollment script writes `consent_ack.json` locally. Both must exist independently — neither one is derived from the other. |
| **Access log** | Every file download by an admin is appended to `data/consent/<id>/access_log.json` on the server and synced to the device within one agent cycle. The log is append-only — no endpoint edits or deletes past entries. |
| **Pause transparency** | Every pause event is logged to the same access log so the admin sees paused state too. Mutual transparency — not a hidden escape hatch. |
| **In transit** | LAN: plain HTTP. Remote: HTTPS via Cloudflare Tunnel (end-to-end encrypted). |
| **Upload limit** | Server rejects payloads > 150 MB to prevent OOM attacks. |
| **Passive only** | Agents never execute commands. The server has zero command-dispatch endpoints. |
| **Local resilience** | Agents always write to `local_data/` on the device regardless of commander connectivity. |

---

## File Structure

```
ghost-sync/
│
├── dashboard_server.py       ← Commander server (Flask, port 8888)
├── monitor_settings.json     ← Auto-generated, gitignored (URL override only)
├── tokens.json               ← Auto-generated, gitignored (per-device tokens)
│
├── agent/
│   ├── monitor_agent.py      ← Agent daemon (installed on each staff machine)
│   ├── tray_icon.py          ← Tray indicator (installed alongside agent)
│   ├── requirements.txt      ← psutil, pystray, plyer, Pillow
│   ├── start_agent.bat       ← Manual start (Windows, for testing)
│   └── start_agent_silent.vbs← Silent start helper
│
├── dashboard/
│   └── index.html            ← Dashboard UI (dark theme, no build step)
│
└── data/                     ← All runtime data (gitignored)
    ├── devices/              ← Device heartbeats + paused state
    ├── logs/                 ← Snapshots, events, agent logs
    ├── file_browser/         ← File listings from agents
    ├── agent_tasks/          ← Pending file-download requests
    ├── file_cache/           ← Downloaded files waiting for you
    └── consent/              ← Consent records + access logs
        └── <device_id>/
            ├── ack.json          ← Consent acknowledgment
            └── access_log.json   ← File downloads + pause events
```

On the **staff machine** (`~/GhostMonitor/` after enrollment):

```
~/GhostMonitor/
├── monitor_agent.py      ← agent daemon
├── tray_icon.py          ← tray indicator
├── local_config.json     ← device_id, display_name, commander_url, token
├── consent_ack.json      ← local consent record (agent won't start without this)
├── monitor_agent.log     ← agent log
└── local_data/
    ├── snapshots/        ← local copies of all snapshots
    ├── events/           ← local event log
    ├── access_log.json   ← synced file-access + pause log
    ├── paused_until.json ← written by tray when user pauses
    └── summary.html      ← transparency page (opened by tray icon)
```

---

## Agent Behaviour

- **Consent gate** — exits immediately with a clear log message if `consent_ack.json` is absent
- Runs every **5–10 minutes** (random interval to avoid sync storms)
- Collects: processes, network connections, system metrics, active window title, recently modified files
- **If paused** — skips data collection, sends a lightweight `{"paused": true}` heartbeat only; still syncs the access log and regenerates the transparency page
- Always writes to `local_data/` on the device first, then pushes to commander
- Polls for file-download tasks from the commander on every cycle
- Syncs the server-side access log to `local_data/access_log.json` every cycle
- Regenerates `local_data/summary.html` (the transparency page) every cycle
- On Linux/macOS: cron wrapper with pidfile lock prevents multiple agent instances; tray runs via `@reboot` cron
- On Windows: agent runs as a Scheduled Task via `pythonw` (silent); tray has its own Scheduled Task

---

## Troubleshooting

**Device not appearing in dashboard**

```
# Check if the agent is running on the staff machine:
# Windows: Task Manager → look for pythonw or python
# Linux:   ps aux | grep monitor_agent

# Check the agent log:
# Windows: %USERPROFILE%\GhostMonitor\monitor_agent.log
# Linux:   ~/GhostMonitor/agent.log
```

Common causes:
- Commander PC not running — start `python dashboard_server.py`
- Wrong commander URL in the enrollment script — re-enroll with the correct URL
- Firewall blocking port 8888 — allow inbound on the commander

**Agent log shows "No local consent record — refusing to start"**

The `consent_ack.json` file is missing. This means the agent was installed without going through the enrollment script, or the file was deleted. Re-run the enrollment script on the device.

**Device shows NO CONSENT badge in dashboard**

The consent ACK was not recorded on the server (e.g., the commander was unreachable when the staff member ran the script, or the device was revoked). Re-enroll the device with a new enrollment link.

**Device shows OFFLINE even though it's running**

Agent is considered offline after 30 minutes without a check-in. If the agent just started, wait one cycle (up to 10 min). If the device is paused, the dashboard shows **PAUSED** (amber) instead of OFFLINE — this is expected and correct.

**Tray icon not appearing after enrollment**

- Windows: check Task Manager for a `pythonw` process running `tray_icon.py`. If missing, run `pythonw "%USERPROFILE%\GhostMonitor\tray_icon.py"` manually and check for errors.
- Linux: check `~/GhostMonitor/tray.log`. The tray requires a desktop environment with a system tray (most standard desktop environments work).

**File download stuck at "Uploading… wait for next agent sync"**

The agent uploads on its next cycle. Wait up to 10 minutes. If it stays stuck, the file may be > 100 MB or the agent may have lost connectivity. Check the Agent Log tab.

**"Could not download agent" during enrollment**

The enrollment script downloads `monitor_agent.py` and `tray_icon.py` from your commander. Make sure:
1. `python dashboard_server.py` is running on the commander
2. The enrollment URL uses the correct IP (LAN) or Cloudflare URL (remote)
3. Port 8888 is not blocked by a firewall

**Network tab shows no connections on Windows**

`psutil.net_connections()` requires Administrator privileges on Windows. The agent logs a warning and continues. For full network monitoring, run the scheduled task as Administrator or right-click the `.bat` and choose "Run as administrator" during enrollment.

---

## License

Private — internal agency use only.
