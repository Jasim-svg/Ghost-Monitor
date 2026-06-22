# Ghost Monitor — Complete Technical Deep Dive

> Everything you need to rebuild this system from scratch, understand every decision, and know why each piece exists.

---

## Table of Contents

1. [What Problem This Solves](#1-what-problem-this-solves)
2. [High-Level Architecture](#2-high-level-architecture)
3. [Tech Stack — Every Choice Explained](#3-tech-stack--every-choice-explained)
4. [How the Commander Server Works](#4-how-the-commander-server-works)
5. [How the Agent Works](#5-how-the-agent-works)
6. [How the Dashboard Works](#6-how-the-dashboard-works)
7. [Complete Data Flow Walkthroughs](#7-complete-data-flow-walkthroughs)
8. [The Authentication System](#8-the-authentication-system)
9. [The Enrollment System](#9-the-enrollment-system)
10. [The File Download System](#10-the-file-download-system)
11. [The Cloudflare Tunnel Integration](#11-the-cloudflare-tunnel-integration)
12. [Data Storage Design](#12-data-storage-design)
13. [Security Model](#13-security-model)
14. [Rebuild from Scratch — Step by Step](#14-rebuild-from-scratch--step-by-step)

---

## 1. What Problem This Solves

You run an agency. You have staff on multiple machines — maybe in the same office, maybe in different cities. You want to know:

- What programs are running on their machines right now
- What external servers they're connecting to
- How much CPU/RAM/disk they're using
- What files they've been working on
- If anything suspicious happens (new process, high resource use, new external connection)
- And if needed, download a specific file from any machine

**The constraint:** You can't ask IT to configure VPNs or open firewall ports on every machine. You can't require staff to manually set anything up beyond running one script. You need it to work silently.

**The solution:** An agent installed silently on each staff machine pushes data to a central server you run on your own PC. You view everything in a web dashboard. The connection works on your local network OR over the internet via Cloudflare.

---

## 2. High-Level Architecture

```
╔══════════════════════════════════════════════════════════════════╗
║                    YOUR COMMANDER PC                            ║
║                                                                  ║
║  ┌─────────────────────────────────────────────────────────┐    ║
║  │           dashboard_server.py  (Flask)                   │    ║
║  │                                                          │    ║
║  │  /api/agent/*    ← agents POST data here (auth)         │    ║
║  │  /api/*          ← browser fetches data here            │    ║
║  │  /enroll/*.bat   ← generates setup scripts              │    ║
║  │  /agent/...py    ← serves agent source to enrollees     │    ║
║  │  /               ← serves dashboard/index.html          │    ║
║  └─────────────────────────────────────────────────────────┘    ║
║                           │                                      ║
║                    data/ (JSON files)                            ║
║              ┌────────────┴─────────────┐                       ║
║         devices/                      logs/                      ║
║         └── dev_a1b2/                 └── dev_a1b2/             ║
║             └── status.json              ├── snapshots/         ║
║                                          ├── events/            ║
║                                          └── agent.log          ║
╚══════════════════════════════════════════════════════════════════╝
         ↑                    ↑
         │ HTTP POST           │ HTTP POST
    (every 5-10 min)     (every 5-10 min)
         │                    │
┌────────┴──────┐    ┌────────┴──────┐
│  john-laptop  │    │  workstation2 │    ...more devices
│               │    │               │
│ monitor_      │    │ monitor_      │
│ agent.py      │    │ agent.py      │
│               │    │               │
│ local_data/   │    │ local_data/   │  ← offline resilience
└───────────────┘    └───────────────┘
```

**The three layers:**

| Layer | File | Role |
|---|---|---|
| Commander | `dashboard_server.py` | Receives data, stores it, serves dashboard |
| Agent | `agent/monitor_agent.py` | Collects data, pushes it to commander |
| Dashboard | `dashboard/index.html` | Shows the data to you in a browser |

These three layers only communicate in two ways:
1. **Agent → Commander:** HTTP POST (agent pushes data)
2. **Browser → Commander:** HTTP GET/POST (dashboard fetches data to display)

There is no direct Browser ↔ Agent communication. Everything goes through the commander.

---

## 3. Tech Stack — Every Choice Explained

### Python (everywhere)

**Why Python and not Node.js, Go, Ruby, etc.?**

- **Cross-platform without compilation.** The same `monitor_agent.py` file runs on Windows, Linux, and macOS without any changes. Go would need separate compiled binaries for each OS.
- **`psutil` only exists properly in Python.** The library that reads process lists, network connections, and system metrics is best in Python. Every other language wraps it anyway.
- **Already installed.** Most business/enterprise machines already have Python. Running `pip install psutil` is a one-liner. No installing a runtime.
- **Easy to read.** If someone needs to audit what the agent does (which you want, since you're installing it on staff machines), they can read plain Python.

**Version: 3.8+** — chosen because f-strings, `Path.stat()`, `Path.mkdir(parents=True, exist_ok=True)`, and `secrets` module all require 3.6+. 3.8 is widely available even on older machines.

---

### Flask (commander server only)

**Why Flask and not Django, FastAPI, aiohttp, etc.?**

```
Django:   Too heavy. Needs ORM, migrations, admin setup, settings.py, URLs config.
          For a 10-endpoint server with JSON files, this is 10x the code needed.

FastAPI:  Excellent choice but requires understanding async/await, Pydantic models,
          and type hints. Adds complexity for what is fundamentally a simple server.

aiohttp:  Async-first. Same async complexity as FastAPI.

Flask:    Synchronous. Minimal. You write a function, you decorate it with @app.route,
          it works. Zero boilerplate. The entire server is one file.
```

**The Flask server does exactly 5 things:**
1. Serves static files (the dashboard HTML)
2. Receives POST requests from agents and writes JSON to disk
3. Serves GET requests to the browser (reads JSON from disk, returns it)
4. Generates enrollment scripts on the fly
5. Manages one settings file

Flask is perfectly sized for this. The server has no database, no background workers, no message queue — just file I/O wrapped in HTTP endpoints.

**Why `flask-cors`?**

CORS (Cross-Origin Resource Sharing) is a browser security feature. Without it, your browser would refuse to let the dashboard JS (`index.html` at `http://localhost:8888`) make `fetch()` calls back to `http://localhost:8888/api/fleet` because browsers are strict about this.

`CORS(app)` adds the headers (`Access-Control-Allow-Origin: *`) that tell the browser "this server allows requests from any origin." One line of code, eliminates an entire class of confusing errors.

---

### psutil (agent only)

**Why psutil exists:** The operating system doesn't give you process lists, network connections, or memory stats in a simple readable way. On Linux it's `/proc/` files. On Windows it's WMI calls. On macOS it's different again. `psutil` wraps all three OSes in one consistent API.

```python
# Without psutil on Windows you'd need:
import subprocess
result = subprocess.run(['tasklist', '/FO', 'CSV'], capture_output=True)
# Then parse the CSV output... messy, fragile, slow

# With psutil:
for p in psutil.process_iter(['pid', 'name', 'cpu_percent']):
    print(p.info)  # clean dict, every OS
```

**It reads:**
- `psutil.process_iter()` — all running processes with PID, name, CPU%, RAM%, user
- `psutil.net_connections()` — all TCP/UDP connections with local/remote address and state
- `psutil.virtual_memory()` — RAM total, used, free, percent
- `psutil.disk_usage()` — disk total, used, free, percent
- `psutil.cpu_percent()` — CPU usage (needs two calls spaced apart for accuracy — see warm-up call)
- `psutil.boot_time()` — when the machine last rebooted

**The one catch:** `net_connections()` requires Administrator on Windows. Without it, the call raises `AccessDenied`. The agent catches this, logs a warning, and continues — you still get everything else, just not network data.

---

### urllib (agent HTTP, stdlib)

**Why not `requests`?**

`requests` is a beautiful library. But it's a third-party dependency. The goal for agents is **minimal install friction** — staff machines only need `pip install psutil`. If we used `requests`, they'd need `pip install psutil requests`.

`urllib.request` is in Python's standard library (built-in, always available). The trade-off:

```python
# requests version (clean):
import requests
r = requests.post(url, json=data, headers=headers, timeout=20)

# urllib version (verbose but no install needed):
import urllib.request, json
body = json.dumps(data).encode('utf-8')
req = urllib.request.Request(url, data=body, method='POST')
req.add_header('Content-Type', 'application/json')
req.add_header('X-Agent-Secret', secret)
with urllib.request.urlopen(req, timeout=20) as resp:
    return json.loads(resp.read())
```

More verbose, same result, zero extra dependencies. Worth it for agents.

---

### JSON files (storage, no database)

**Why not SQLite, PostgreSQL, MongoDB?**

```
SQLite:     Would work fine. But adds complexity: schema, SQL queries, migrations,
            connection handling. And the data is binary — you can't just open it to debug.

PostgreSQL: Overkill. Needs a server running. Way more complex to set up.

MongoDB:    Also overkill. Same as above.

JSON files: Open any file in Notepad. See exactly what's stored. Delete a device?
            Delete its folder. Debug a bug? Read the file. No query language needed.
            For <100 devices checking in every 5-10 min, disk I/O is trivial.
```

**The structure maps directly to the data:**
```
data/
├── devices/
│   └── dev_a1b2c3d4/        ← one folder per device
│       └── status.json       ← latest heartbeat
├── logs/
│   └── dev_a1b2c3d4/
│       ├── snapshots/
│       │   └── 2026-06-22/
│       │       └── 2026-06-22T14-30-00.json   ← one file per cycle
│       ├── events/
│       │   └── 2026-06-22T14-30-00_events.json
│       └── agent.log
```

Reading the latest snapshot: open the last file in the last date folder. Reading all events: iterate the `events/` directory sorted by filename (filenames start with timestamps, so alphabetical = chronological). No SQL needed.

---

### Vanilla JavaScript (dashboard)

**Why not React, Vue, Angular?**

```
React/Vue/Angular:  Need npm, node_modules, webpack/vite build step,
                    100MB+ of dependencies, separate dev server.
                    The dashboard is ONE FILE. Zero build step.
                    Open in browser, it works.
```

**The entire dashboard is `dashboard/index.html`** — CSS, HTML, and JavaScript in one file. Flask serves it directly. No npm. No build. No `package.json`.

The JavaScript uses:
- `fetch()` — built-in browser API for HTTP requests (replaces jQuery AJAX)
- `setInterval()` — auto-refresh every 60 seconds
- Template literals (backtick strings) — build HTML strings with variables
- `async/await` — clean async code without callbacks
- `document.getElementById`, `innerHTML`, `classList` — standard DOM manipulation

Everything is 2015+ JavaScript that works in every modern browser.

---

### Cloudflare Tunnel (remote access)

**Why not ngrok, port forwarding, a VPS?**

```
Port forwarding:  Requires access to the router. Opens your home/office network
                  to the internet. Security risk. Not everyone has router access.

ngrok (free):     URL changes every restart. Need to update all agents every time.
                  Not reliable long-term.

ngrok (paid):     $8-20/month for stable URLs. Unnecessary cost.

VPS (cloud server): ~$5-10/month. More complex. You'd need to run the server
                  on the VPS, not your local machine. Data stored remotely.

Cloudflare Tunnel: FREE. Stable URL possible (with free account). HTTPS automatic.
                  You keep running on your local machine. No router config.
                  The tunnel is a secure outbound connection — no inbound ports opened.
```

**How Cloudflare Tunnel works technically:**

```
Your PC runs: cloudflared tunnel --url http://localhost:8888

What happens:
1. cloudflared opens an OUTBOUND connection from your PC to Cloudflare's edge
2. Cloudflare assigns a URL: https://proud-lion-abc.trycloudflare.com
3. When an agent in another city hits that URL:
   Agent → Cloudflare Edge → (existing tunnel) → cloudflared → localhost:8888 → Flask

Your PC never opens any inbound port.
The connection is: agent opens HTTPS to Cloudflare, your PC opens HTTPS to Cloudflare.
Cloudflare bridges them. Your IP is never exposed.
```

---

### Windows Scheduled Task (agent startup)

**Why not a Windows Service?**

Windows Services run as SYSTEM and require Admin to install. They're the right tool for production server software. But for agent software on staff machines:

- Installing a Service needs elevation (admin rights)
- Requires `pywin32` or `NSSM` (extra dependencies)
- Overkill for something that just runs a Python script

A Scheduled Task runs as the current user, can be created with a single `schtasks` command, and triggers on every login:

```batch
schtasks /create /tn "GhostMonitorAgent" /tr "pythonw C:\Users\John\GhostMonitor\monitor_agent.py" /sc onlogon /ru "John" /rl highest /f
```

`/sc onlogon` = trigger on login  
`/ru "%USERNAME%"` = run as current user  
`/rl highest` = highest privilege level available to that user  
`/f` = force overwrite if already exists  

`pythonw` (with a `w`) is the Windows silent Python launcher — no console window appears. The agent runs invisibly in the background.

---

### Cron + Pidfile (Linux/macOS agent startup)

**Why cron instead of systemd?**

`systemd` is modern and correct for system services but requires root access and a `.service` file in `/etc/systemd/system/`. Staff machines won't have that.

User-level cron (`crontab -e`) requires zero privileges. Every Unix user has one. The entry:

```cron
*/8 * * * * /home/john/GhostMonitor/run_agent.sh
```

Runs every 8 minutes. BUT the agent is a long-running process (loops forever, sleeping 5-10 min between cycles). If cron starts it every 8 minutes without checking, you'd end up with multiple agents.

**The pidfile solution:**

```bash
#!/bin/bash
# run_agent.sh
PIDFILE="$HOME/GhostMonitor/agent.pid"
if [ -f "$PIDFILE" ]; then
    OLD_PID=$(cat "$PIDFILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        exit 0   # already running — do nothing
    fi
fi
echo $$ > "$PIDFILE"
python3 "$HOME/GhostMonitor/monitor_agent.py"
rm -f "$PIDFILE"
```

`kill -0 PID` doesn't kill the process — it just checks if the PID exists. If the agent is running, cron exits immediately. If the agent crashed, the PID doesn't exist anymore, so cron starts a fresh agent. Self-healing restart system with zero extra software.

---

## 4. How the Commander Server Works

`dashboard_server.py` is one Flask application with four categories of routes.

### 4.1 Application Setup

```python
app = Flask(__name__, static_folder="dashboard")
CORS(app)
app.config["MAX_CONTENT_LENGTH"] = 150 * 1024 * 1024  # 150 MB
```

- `static_folder="dashboard"` — Flask serves files from the `dashboard/` folder automatically
- `CORS(app)` — adds `Access-Control-Allow-Origin: *` to all responses
- `MAX_CONTENT_LENGTH` — Flask rejects any request body > 150 MB. Prevents a rogue agent from uploading a 10 GB file and crashing the server with out-of-memory

### 4.2 Directory Initialization

```python
for _d in [DATA_DIR, DEVICES_DIR, LOGS_DIR, FILES_DIR, TASKS_DIR, FILE_CACHE]:
    _d.mkdir(parents=True, exist_ok=True)
```

Runs at startup. Creates the entire `data/` tree if it doesn't exist. `parents=True` means it creates intermediate directories. `exist_ok=True` means it doesn't raise an error if the folder already exists. You can restart the server as many times as you want — idempotent.

### 4.3 Helper Functions

**`_safe_id(raw)` and `_safe_name(raw)`**

```python
def _safe_id(raw: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_\-]", "", str(raw))[:40]
```

Every `device_id` that comes from an agent goes through `_safe_id` before being used as a directory name. This prevents path traversal attacks:

```
Malicious device_id: "../../etc/passwd"
After _safe_id:      "etcpasswd"
Result path:         data/devices/etcpasswd/status.json  ← safe
```

**`_is_online(last_seen_iso)`**

```python
def _is_online(last_seen_iso: str) -> bool:
    last   = datetime.fromisoformat(last_seen_iso.replace("Z", "+00:00"))
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=30)
    return last > cutoff
```

Devices are "online" if they checked in within the last 30 minutes. This is called every time the dashboard requests fleet data — there's no real-time socket, just polling with a time comparison.

**`_get_secret()` and `_auth(req)`**

```python
def _get_secret() -> str:
    s = _load_settings()
    if not s.get("agency_secret"):
        s["agency_secret"] = secrets.token_hex(16)  # 32 hex chars = 128 bits entropy
        _save_settings(s)
    return s["agency_secret"]

def _auth(req) -> bool:
    expected = _get_secret()
    provided = req.headers.get("X-Agent-Secret", "")
    return secrets.compare_digest(expected, provided)
```

`secrets.token_hex(16)` generates a 32-character hex string with 128 bits of randomness. Cryptographically secure. Generated once and saved to `monitor_settings.json`.

`secrets.compare_digest` is used instead of `expected == provided` because it's constant-time — it takes the same amount of time regardless of whether the first character matches or not. This prevents timing attacks where an attacker could guess the secret one character at a time by measuring response times.

**`_get_lan_ip()`**

```python
def _get_lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect(("8.8.8.8", 80))   # Google's DNS, but no data is sent
    ip = s.getsockname()[0]       # asks the OS "which local IP would I use to reach 8.8.8.8?"
    s.close()
    return ip
```

This is a classic trick. No actual network packet is sent (UDP, `connect` on UDP just sets the routing decision in the OS). The OS looks up its routing table and returns which local IP it would use to reach `8.8.8.8`. This gives you the machine's primary LAN IP (e.g., `192.168.1.5`) rather than `127.0.0.1`.

### 4.4 Agent API Endpoints

These receive POST requests from agents. All require the `X-Agent-Secret` header.

**`POST /api/agent/checkin`**

```python
@app.route("/api/agent/checkin", methods=["POST"])
def agent_checkin():
    if not _auth(request): return jsonify({"error": "unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    sid  = _safe_id(data.get("device_id", ""))
    _write_json(DEVICES_DIR / sid / "status.json", data)
    return jsonify({"ok": True})
```

This is the heartbeat. Every cycle, the agent sends: device_id, display_name, last_seen timestamp, OS, hostname, IP, metrics (CPU/RAM/disk), active window. This data drives the fleet sidebar — device cards, online/offline badge, CPU/RAM bars.

**`POST /api/agent/snapshot`**

The full snapshot: everything from checkin PLUS the complete process list and network connections. Stored per-day per-device:

```
data/logs/dev_a1b2/snapshots/2026-06-22/2026-06-22T14-30-00.json
```

The server prunes to keep only the **last 2 days** of snapshots to avoid filling up the disk:

```python
days = sorted(d for d in snap_root.iterdir() if d.is_dir())
for old in days[:-2]:    # delete everything except the last 2 folders
    shutil.rmtree(old, ignore_errors=True)
```

**`POST /api/agent/events`**

Events are diffs — changes detected since the last snapshot (new process, stopped process, high CPU, new external connection). Stored per-file, pruned to last 200 files per device.

**`POST /api/agent/log`**

The agent sends its own last 200 log lines. Overwrites `agent.log` each cycle. This lets you see what the agent is doing (or what errors it hit) without having direct access to the staff machine.

**`POST /api/agent/files`**

Full file listing (up to 500 files from Documents/Desktop/Downloads). Overwrites `data/file_browser/<sid>/listing.json`. Sent on ~40% of cycles to reduce overhead.

**`POST /api/agent/file-content`**

The agent sends a requested file's content encoded in base64. The server:
1. Deletes the task file from `data/agent_tasks/<sid>/` (request fulfilled)
2. Decodes the base64 back to bytes
3. Saves the file to `data/file_cache/<sid>/<rid>/<filename>`
4. Writes `status.json` with `{"status": "ready", "filename": "..."}`

**`GET /api/agent/tasks/<device_id>`**

Agents poll this every cycle. Returns a list of pending task JSON objects. Currently only one task type exists: `get_file` (file download request). If there are no tasks, returns `[]`.

### 4.5 Dashboard API Endpoints

These serve data to the browser. No authentication (the browser can't hold secrets safely).

**`GET /api/fleet`** — all devices with online status, sorted by last_seen  
**`GET /api/stats`** — total/online/offline counts, alert count in last 1 hour  
**`GET /api/device/<id>/status`** — latest checkin data for one device  
**`GET /api/device/<id>/snapshot`** — most recent full snapshot (process list + network)  
**`GET /api/device/<id>/events`** — recent events for one device  
**`GET /api/device/<id>/logs`** — last 100 lines of agent log  
**`GET /api/device/<id>/files`** — file listing  
**`GET /api/alerts`** — all warning/critical events across all devices  
**`GET /api/activity`** — all events across all devices (the activity feed)

**`POST /api/device/<id>/request-file`** — dashboard requests a file from an agent  
**`GET /api/device/<id>/file-status/<rid>`** — polls for file availability  
**`GET /api/device/<id>/download/<rid>`** — downloads the cached file

### 4.6 Static Serving

```python
@app.route("/")
def index():
    return send_from_directory("dashboard", "index.html")

@app.route("/agent/monitor_agent.py")
def serve_agent_script():
    return send_from_directory("agent", "monitor_agent.py", mimetype="text/plain")
```

The second endpoint is what makes enrollment work. When a new agent machine runs the enrollment script, it downloads `monitor_agent.py` directly from your commander:

```bash
# Inside the enrollment .bat script:
Invoke-WebRequest 'http://192.168.1.5:8888/agent/monitor_agent.py' -OutFile 'C:\Users\John\GhostMonitor\monitor_agent.py'
```

This means you never need to send the agent file separately. The commander distributes itself.

---

## 5. How the Agent Works

`agent/monitor_agent.py` is a long-running daemon. It loops forever, collecting data, pushing it, then sleeping.

### 5.1 Identity Setup

On first run (or when `local_config.json` doesn't exist), the agent prompts for configuration. But in normal enrollment, the setup script writes `local_config.json` BEFORE starting the agent, so the agent never needs to prompt.

```python
def get_or_create_identity():
    if CONFIG_FILE.exists() and IDENTITY_FILE.exists():
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        return cfg["device_id"], cfg["display_name"], cfg["commander_url"], cfg["secret"]
    # First run: prompt or use enrollment-written config
```

**Device ID generation:**

```python
device_id = "dev_" + hashlib.md5(
    (socket.gethostname() + display_name).encode()
).hexdigest()[:8]
```

The device ID is deterministic — same hostname + name always produces the same ID. This means re-enrolling a machine with the same name doesn't create a duplicate entry in the dashboard. The device ID `dev_a1b2c3d4` is 12 characters, human-readable, short enough for filenames.

### 5.2 The Main Loop

```python
def main():
    device_id, display_name, commander_url, secret = get_or_create_identity()
    LOCAL_DATA.mkdir(parents=True, exist_ok=True)
    prev_snapshot = None
    interval = random.randint(300, 600)  # 5-10 minutes, chosen once at startup

    while True:
        try:
            prev_snapshot = run_cycle(device_id, display_name, commander_url, secret, prev_snapshot)
        except Exception as e:
            log.error(f"Cycle error: {e}", exc_info=True)
        time.sleep(interval)
```

The interval is randomized **once** at startup. Why random?
- If you enroll 20 machines at once, they'd all sync every 5 minutes at the same second → server spike
- Randomizing spreads the load across the 5-10 minute window

The `try/except` around `run_cycle` means a crash in one cycle doesn't kill the agent — it logs the error and sleeps until the next cycle.

### 5.3 Data Collection Functions

**`collect_processes()`**

```python
def collect_processes():
    psutil.cpu_percent(interval=0.1)   # warm-up call — first call always returns 0
    procs = []
    for p in psutil.process_iter(["pid", "name", "username", "cpu_percent",
                                   "memory_percent", "create_time", "status"]):
        try:
            info = p.info.copy()
            ct = info.get("create_time")
            if ct:
                info["create_time"] = datetime.datetime.fromtimestamp(ct).isoformat()
            procs.append(info)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return sorted(procs, key=lambda p: p.get("cpu_percent") or 0, reverse=True)
```

The warm-up call `psutil.cpu_percent(interval=0.1)` is important. psutil measures CPU by comparing two readings spaced apart. On the first call with no previous reading, it returns 0.0 for every process. The 0.1-second warm-up gives it a baseline, so the subsequent per-process readings are meaningful.

`try/except` around each process is essential. Between iterating the process list and reading its info, the process might have exited (`NoSuchProcess`) or be a system process you can't read (`AccessDenied`). Both are normal — skip and continue.

**`collect_network()`**

```python
for c in psutil.net_connections(kind="inet"):
    conns.append({
        "laddr": f"{c.laddr.ip}:{c.laddr.port}" if c.laddr else None,
        "raddr": f"{c.raddr.ip}:{c.raddr.port}" if c.raddr else None,
        "status": c.status,
        "pid":    c.pid,
        "proto":  "TCP" if c.type.name == "SOCK_STREAM" else "UDP",
    })
```

`kind="inet"` filters to IPv4/IPv6 (TCP and UDP). Excludes Unix domain sockets and other non-internet connections.

**`collect_metrics()`**

```python
"cpu_percent":  psutil.cpu_percent(interval=1),  # 1-second sample for overall CPU
"ram_percent":  mem.percent,
"disk": {"percent": d.percent, "used_gb": ..., "total_gb": ...}
```

Note: `cpu_percent(interval=1)` here is for the OVERALL CPU, not per-process. This blocks for 1 second but gives accurate system-wide CPU reading. The dashboard shows this as the headline CPU metric.

**`get_active_window()`**

```python
if platform.system() != "Windows":
    return None
hwnd   = ctypes.windll.user32.GetForegroundWindow()
length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
buf    = ctypes.create_unicode_buffer(length + 1)
ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
return buf.value or None
```

Windows-only. Uses the `ctypes` module (built-in) to call Windows API functions directly — no extra library needed. `GetForegroundWindow()` returns a handle to the currently active window. `GetWindowTextW()` reads its title bar text. This tells you "User is currently looking at: Microsoft Excel — Q2 Report.xlsx".

**`collect_recent_files(hours=1)`**

Scans Documents, Desktop, Downloads for files modified in the last hour. Limited to 30 results. This answers "what has the user been working on recently?"

**`collect_file_listing(time_budget=25.0)`**

Full scan of Documents/Desktop/Downloads, up to 500 files. Has a 25-second time budget — if scanning takes longer (e.g., thousands of nested files), returns what it has so far and moves on. Prevents the agent from hanging for minutes on machines with huge file trees.

### 5.4 Event Detection

```python
def detect_events(snapshot: dict, prev: dict) -> list:
    # New processes since last snapshot
    cur_procs = {p["name"] for p in snapshot.get("processes", [])}
    prv_procs = {p["name"] for p in prev.get("processes", [])}
    for name in (cur_procs - prv_procs):
        events.append({"type": "process_started", "detail": name, "severity": "info", ...})

    # High resource usage
    if cpu > 85:
        events.append({"type": "high_cpu", "severity": "warning", ...})

    # New external connections
    cur_ext = {c["raddr"] for c in snapshot["network"] if not _is_local(c["raddr"])}
    prv_ext = {c["raddr"] for c in prev["network"] if not _is_local(c["raddr"])}
    for addr in (cur_ext - prv_ext):
        events.append({"type": "new_ext_connection", "severity": "info", ...})
```

This is a **diff** between two snapshots. Uses Python sets: `cur_procs - prv_procs` = processes that are in current but not in previous = new processes. Same pattern for external connections.

This is why `prev_snapshot` is passed into every `run_cycle` call and returned — it threads the previous state through each iteration of the loop.

**Why `_is_local(addr)`:**

```python
def _is_local(addr: str) -> bool:
    ip = addr.split(":")[0]
    return ip.startswith(("127.", "192.168.", "10.", "172.", "::1", "0.0."))
```

RFC 1918 private ranges: `10.x.x.x`, `172.16.x.x - 172.31.x.x`, `192.168.x.x`. These are LAN addresses. External connections (to actual internet IPs) are what we care about — that's where potential data exfiltration or malware C2 traffic would appear.

### 5.5 Local Data Write

```python
def write_local(sub: str, filename: str, data: dict):
    path = LOCAL_DATA / sub
    path.mkdir(parents=True, exist_ok=True)
    (path / filename).write_text(json.dumps(data, indent=2), encoding="utf-8")
```

Before any HTTP push, the agent writes everything locally. `LOCAL_DATA = SCRIPT_DIR / "local_data"` — always in the same folder as the agent script.

**Why write locally?** If the commander is down (rebooting, network issue, Cloudflare flaking), the data isn't lost. The local data stays on the device. You can retrieve it later via the file browser, or it's there if someone opens the device physically.

---

## 6. How the Dashboard Works

`dashboard/index.html` is a single-page application. No page reloads — JavaScript fetches data and updates the DOM.

### 6.1 Page Structure

```
┌─ header ──────────────────────────────────────────────────────┐
│ GHOST MONITOR  [LIVE]  [time]  [Refresh]  [+Enroll]  [⚙ Settings] │
├─ stats-bar ────────────────────────────────────────────────────┤
│ Online: 3  Offline: 1  Total: 4  Alerts(1h): 2               │
├─ sidebar ─────────────────────┬─ main-panel ──┬─ right-panel ─┤
│ DEVICES                       │               │ WARNINGS      │
│ ┌───────────────┐             │ (fleet view   │ alert 1       │
│ │ john-laptop   │             │  OR device    │ alert 2       │
│ │ [ONLINE] Win  │             │  detail view) │               │
│ │ 2m ago        │             │               │               │
│ │ ██░░ CPU 45%  │             │               │               │
│ │ ███░ RAM 60%  │             │               │               │
│ └───────────────┘             │               │               │
└───────────────────────────────┴───────────────┴───────────────┘
```

### 6.2 State Management

```javascript
let selectedDevice = null;  // null = fleet view, string = device ID in detail view
let enrollOS = 'win';       // which OS tab is selected in enroll modal
let enrollBaseURL = '';      // loaded from /api/settings when enroll modal opens
let fileRequests = {};       // { filePath: { request_id, status } }
let _fileListCache = {};     // { deviceId: [files] } — cleared on device select
```

No external state library. Five variables cover all application state.

### 6.3 The Two Views

**Fleet view (`#viewAll`):** shown when no device is selected. Shows the global activity feed and warnings tab.

**Device detail view (`#viewDevice`):** shown when a device card is clicked. Shows status header (name, badge, IP), metric cards (CPU/RAM/disk), and five tabs.

```javascript
async function selectDevice(id) {
    selectedDevice = id;
    delete _fileListCache[id];              // clear stale file cache
    document.getElementById('viewAll').style.display    = 'none';
    document.getElementById('viewDevice').style.display = 'block';
    // fetch status, build metric cards, load first tab (processes)
}
```

### 6.4 Tab System

```javascript
function dTab(name, el) {
    // remove active from all tabs and sections
    document.querySelectorAll('#viewDevice .tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('#viewDevice .section').forEach(s => s.classList.remove('active'));
    // add active to clicked tab and its section
    el.classList.add('active');
    document.getElementById('dt-' + name).classList.add('active');
    // load data for this tab
    if (selectedDevice) loadDeviceTab(selectedDevice, name);
}
```

Each tab is a `<div class="section">` that's `display:none` by default. Adding `class="active"` makes it `display:block` (CSS rule: `.section.active { display: block; }`). Data is fetched lazily — only when the tab is clicked.

### 6.5 Security in the Dashboard

The file browser renders filenames from agent data. A malicious filename like `<script>alert(1)</script>` would execute if inserted into innerHTML directly. Three escape functions prevent this:

```javascript
function escHtml(s)  { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }
function escAttr(s)  { return String(s).replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/'/g,'&#39;'); }
function escJs(s)    { return String(s).replace(/\\/g,'\\\\').replace(/'/g,"\\'").replace(/"/g,'\\"'); }
```

- `escHtml` — for text inside HTML elements (prevents `<script>` injection)
- `escAttr` — for values inside HTML attributes (prevents attribute boundary breaking)
- `escJs` — for values inside JavaScript string literals in inline event handlers

The `onclick="requestFileDownload('${deviceId}','${escAttr(escJs(f.path))}',..."` pattern applies both: `escJs` first to escape the JS string, then `escAttr` to escape the HTML attribute.

### 6.6 Auto-Refresh

```javascript
setInterval(refreshAll, 60_000);   // refresh every 60 seconds
refreshAll();                       // initial load
```

`refreshAll` reloads stats, fleet list, and right-panel alerts. If a device is selected, it also refreshes that device's current tab. No websockets. Simple polling is sufficient for 5-10 minute agent cycles.

---

## 7. Complete Data Flow Walkthroughs

### 7.1 Normal Monitoring Cycle

```
T=0:00  Agent wakes up (or starts for the first time)
        │
        ├── collect_metrics()         → CPU 45%, RAM 62%, Disk 80%
        ├── collect_processes()       → 87 processes (sorted by CPU)
        ├── collect_network()         → 23 connections (5 external)
        ├── get_active_window()       → "Microsoft Excel — Budget.xlsx"
        ├── collect_recent_files()    → 8 files modified in last hour
        │
        ├── detect_events(current, previous)
        │     diff: "chrome.exe" is new → process_started event
        │           CPU was 45% last cycle, now 89% → high_cpu event
        │
        ├── write_local("snapshots/2026-06-22", "2026-06-22T14-30-00.json", snapshot)
        ├── write_local("events", "2026-06-22T14-30-00_events.json", {events})
        │
        ├── POST /api/agent/checkin    (heartbeat + metrics + active window)
        ├── POST /api/agent/snapshot   (full data dump)
        ├── POST /api/agent/events     (the diffs)
        ├── POST /api/agent/log        (last 200 log lines)
        ├── POST /api/agent/files      (40% chance — file listing)  
        ├── GET  /api/agent/tasks/dev_a1b2  → [] (no pending tasks)
        │
T=0:01  Agent sleeps 300-600 seconds
T=6:00  Next cycle begins
```

### 7.2 You View a Device in the Dashboard

```
You click "john-laptop" in the sidebar
│
Browser: GET /api/device/dev_a1b2/status
         ← { display_name: "john-laptop", online: true, metrics: {...}, active_window: "..." }
         → renders title bar, badge, metric cards

Browser: GET /api/device/dev_a1b2/snapshot  (Processes tab loads first)
         ← { processes: [{pid:1234, name:"chrome.exe", cpu_percent:12.3, ...}, ...] }
         → renders process table (top 80 by CPU)

You click "Network" tab:
Browser: GET /api/device/dev_a1b2/snapshot  (same endpoint, same data)
         → filters network connections, splits into external/internal
         → external ones highlighted in amber

You click "Files" tab:
Browser: GET /api/device/dev_a1b2/files
         ← { files: [{path:"C:\Users\John\Documents\budget.xlsx", ...}], ts: "..." }
         → renders searchable file table with "Request Download" buttons
```

### 7.3 You Download a File

```
You click "Request Download" next to "budget.xlsx"
│
Browser: POST /api/device/dev_a1b2/request-file
         body: { path: "C:\Users\John\Documents\budget.xlsx" }
         ← { ok: true, request_id: "a1b2c3d4e5f6" }

Commander: writes data/agent_tasks/dev_a1b2/a1b2c3d4e5f6.json
           { type: "get_file", request_id: "a1b2c3d4e5f6", path: "C:\...\budget.xlsx" }
           also writes data/file_cache/dev_a1b2/a1b2c3d4e5f6/status.json
           { status: "pending" }

Dashboard: shows "Uploading… wait for next agent sync"
           starts polling every 30 seconds: GET /api/device/dev_a1b2/file-status/a1b2c3d4e5f6

                    ← 5-10 minutes pass →

Agent wakes up, runs cycle
        │
        ├── GET /api/agent/tasks/dev_a1b2
        │   ← [{ type: "get_file", request_id: "a1b2c3d4e5f6", path: "C:\...\budget.xlsx" }]
        │
        ├── handle_file_requests():
        │     opens C:\Users\John\Documents\budget.xlsx
        │     reads bytes → base64 encodes → POST /api/agent/file-content
        │     body: { device_id, request_id, filename, content_b64, size }
        │     timeout: 300 seconds (allows for large files)
        │
Commander receives POST /api/agent/file-content:
        │     decodes base64 → writes bytes to data/file_cache/dev_a1b2/a1b2c3d4e5f6/budget.xlsx
        │     deletes data/agent_tasks/dev_a1b2/a1b2c3d4e5f6.json  (task complete)
        │     updates status.json: { status: "ready", filename: "budget.xlsx", size: 142304 }

Dashboard poll fires (30s interval):
        GET /api/device/dev_a1b2/file-status/a1b2c3d4e5f6
        ← { status: "ready", filename: "budget.xlsx" }
        → replaces badge with "⬇ Download Now" link
        → link: /api/device/dev_a1b2/download/a1b2c3d4e5f6

You click "Download Now":
        GET /api/device/dev_a1b2/download/a1b2c3d4e5f6
        ← budget.xlsx file (send_file with Content-Disposition: attachment)
        → browser downloads the file to your Downloads folder
```

---

## 8. The Authentication System

### How It Works

Every request from an agent includes an HTTP header:

```
X-Agent-Secret: a3f8c2d1e4b7f9a2c8d5e1b3f6a9c2d5
```

The server checks this header against the stored secret using `secrets.compare_digest()`.

### Where the Secret Lives

- **Commander:** `monitor_settings.json` (gitignored)
- **Each agent:** `local_config.json` in the installation folder (`~/GhostMonitor/local_config.json`)
- **In transit:** HTTP header (LAN = unencrypted, Cloudflare = HTTPS encrypted)

### The Secret Is Embedded in Enrollment Scripts

When the enrollment script is generated, the current secret is baked in:

```batch
set AGENT_SECRET=a3f8c2d1e4b7f9a2c8d5e1b3f6a9c2d5
```

This is then written to `local_config.json` on the agent machine during setup.

### What Happens If the Secret Is Wrong

```python
if not _auth(request):
    return jsonify({"error": "unauthorized"}), 401
```

The server returns HTTP 401 Unauthorized. The agent logs the failure and continues. The data is still written locally — it's not lost, just not pushed.

### Regenerating the Secret

From Settings modal → "Regenerate Secret". This:
1. Creates a new 32-char hex secret
2. Saves to `monitor_settings.json`
3. All existing agents NOW FAIL AUTH — they still have the old secret
4. You must re-enroll all devices with new enrollment scripts that have the new secret

---

## 9. The Enrollment System

### What Happens When You Generate a Link

```python
@app.route("/enroll/<filename>")
def enroll_script(filename):
    name, ext = filename.rsplit(".", 1)        # "john-laptop", "bat"
    device_name  = _safe_name(name)            # sanitize: letters/numbers/hyphens only
    secret       = _get_secret()               # current agency secret
    commander_url = s.get("commander_url_override") or f"http://{lan_ip}:{PORT}"
    script = _make_windows_script(device_name, commander_url, secret)
    return Response(script, mimetype="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="ghost_monitor_{device_name}.bat"'})
```

The server generates the script on the fly with the correct values embedded. The URL `http://192.168.1.5:8888/enroll/john-laptop.bat` downloads a `.bat` file containing the agent's configuration baked in.

### What the Windows Enrollment Script Does

The `.bat` script runs 4 steps:

**Step 1 — Check Python:**
```batch
python --version >nul 2>&1
if %errorlevel% neq 0 ( echo ERROR: Python not found. ... pause & exit /b 1 )
```
If Python isn't installed, the script stops immediately with a clear error message.

**Step 2 — Download agent from commander:**
```batch
powershell -Command "Invoke-WebRequest 'http://192.168.1.5:8888/agent/monitor_agent.py' -OutFile '%INSTALL_DIR%\monitor_agent.py'"
```
Downloads `monitor_agent.py` from your commander. This is why the commander must be running when enrollment happens.

**Step 3 — Configure device (space-safe approach):**
```batch
set GM_NAME=john-laptop
set GM_URL=http://192.168.1.5:8888
set GM_SECRET=a3f8c2d1...
python -c "
import json, hashlib, socket, os
install_dir = os.path.join(os.path.expanduser('~'), 'GhostMonitor')
n   = os.environ['GM_NAME']   # reads from env var, not embedded in command
did = 'dev_' + hashlib.md5((socket.gethostname() + n).encode()).hexdigest()[:8]
cfg = {'device_id': did, 'display_name': n, 'commander_url': ..., 'secret': ...}
open(os.path.join(install_dir, 'local_config.json'), 'w').write(json.dumps(cfg))
"
```

Note: values are passed via environment variables (`os.environ['GM_NAME']`) rather than embedded directly in the Python code. This handles usernames with spaces (e.g., "John Smith" → `C:\Users\John Smith\GhostMonitor`) — if the path were embedded in the Python one-liner, spaces would break the command.

**Step 4 — Create silent startup task:**
```batch
schtasks /create /tn "GhostMonitorAgent" /tr "pythonw C:\Users\John\GhostMonitor\monitor_agent.py" /sc onlogon /ru "John" /rl highest /f
```
Then immediately starts the agent in a minimised window.

### The Linux Enrollment Script

Same 4 steps but uses:
- `curl` or `wget` (tries both, one usually exists)
- `pip3` or `pip` (tries both)
- Python one-liner for config (same env var approach)
- `run_agent.sh` wrapper + crontab for startup

The `run_agent.sh` pidfile wrapper is the key Linux-specific piece — prevents multiple cron instances.

---

## 10. The File Download System

This is the most complex flow because the commander can't reach the agent directly (agents are behind firewalls, NAT, etc.). So the system uses a **polling task queue**:

```
Commander                    Agent
   │                           │
   │ ← POST /request-file      │  (dashboard user clicks "Request Download")
   │                           │
   │  writes task JSON         │
   │  data/agent_tasks/<id>/   │
   │                           │
   │                     (agent sleeps)
   │                           │
   │                    GET /api/agent/tasks/<id>
   │ ──────────────────────────►
   │ ◄─────────────────────────
   │   [{ type: "get_file", path: "...", request_id: "..." }]
   │                           │
   │                    reads the file
   │                    base64 encodes it
   │                    POST /api/agent/file-content
   │ ◄─────────────────────────
   │                           │
   │ decodes, saves to cache   │
   │ deletes task file         │
   │ updates status: "ready"   │
   │                           │
   │ ← GET /file-status/<rid>  │  (browser polls every 30 seconds)
   │ → { status: "ready" }     │
   │                           │
   │ ← GET /download/<rid>     │  (user clicks "Download Now")
   │ → file bytes              │
```

**Why base64?**

The agent uses `http_post()` which sends JSON. JSON is text — it can't contain raw binary bytes. Base64 converts binary to text (ASCII characters only) at the cost of ~33% size increase. A 1 MB file becomes ~1.33 MB of base64 JSON.

Alternative would be multipart form data, but that requires more complex HTTP handling on the agent side. JSON + base64 keeps the agent code simple (just `urllib`).

**The 100 MB limit:**

```python
FILE_SIZE_LIMIT = 100 * 1024 * 1024   # 100 MB

if size > FILE_SIZE_LIMIT:
    http_post(endpoint, {"device_id": device_id, "request_id": request_id,
                         "error": f"File too large ({size // 1024 // 1024} MB > 100 MB limit)"}, secret)
```

100 MB × 1.33 (base64) × JSON overhead ≈ 135 MB JSON body. With `MAX_CONTENT_LENGTH = 150 MB` on Flask, this barely fits. Going higher risks OOM on the commander.

---

## 11. The Cloudflare Tunnel Integration

### Architecture

```
Agent in London                  Cloudflare Edge          Your PC in Karachi
       │                              │                         │
       │  HTTPS to trycloudflare.com  │                         │
       ├─────────────────────────────►│                         │
       │                              │  (existing tunnel)      │
       │                              │◄────────────────────────┤
       │                              │   cloudflared process   │
       │                              │   (HTTP to localhost)   │
       │          HTTP 200            │                         │
       │◄─────────────────────────────┤                         │
```

The `cloudflared` process running on your PC maintains a persistent outbound HTTPS connection to Cloudflare's infrastructure. When Cloudflare receives an inbound HTTPS request for your tunnel URL, it routes it through that persistent connection to your local Flask server.

**No inbound ports.** Your firewall doesn't need any changes. Your router doesn't need any changes. Your ISP's NAT doesn't matter.

### How the Commander Knows to Use It

In `monitor_settings.json`:
```json
{
  "agency_secret": "a3f8c2d1...",
  "commander_url_override": "https://proud-lion-abc123.trycloudflare.com"
}
```

When generating enrollment scripts:
```python
commander_url = s.get("commander_url_override") or f"http://{lan_ip}:{PORT}"
```

If `commander_url_override` is set, all enrollment scripts get the Cloudflare URL baked in. The agent's `local_config.json` will have:
```json
{ "commander_url": "https://proud-lion-abc123.trycloudflare.com" }
```

And all HTTP posts go to Cloudflare → your PC.

### Free vs Paid Tunnels

```
cloudflared tunnel --url http://localhost:8888   ← free quick tunnel, URL changes on restart

Stable URL (free Cloudflare account):
  cloudflared tunnel create ghost-monitor
  cloudflared tunnel route dns ghost-monitor monitor.yourdomain.com
  cloudflared tunnel run ghost-monitor
```

For production use, a named tunnel with a Cloudflare account gives a permanent stable URL.

---

## 12. Data Storage Design

### Why No Database

For this system, the "database" is the filesystem. Each device gets a folder. Each snapshot is a file. This design has specific advantages:

1. **No query language needed** — Python's `pathlib` is the query engine. "Get all events for device X, last 30 sorted by time" = `sorted(event_dir.iterdir(), reverse=True)[:30]`
2. **Human-readable** — open any file in a text editor
3. **Easy backup** — `xcopy data\ backup\` 
4. **Easy debug** — if something looks wrong, read the file
5. **Zero configuration** — no database server to start, no schema to migrate

### Data Layout

```
data/
├── devices/
│   └── dev_a1b2c3d4/
│       └── status.json          ← overwritten each checkin
│                                   { device_id, display_name, last_seen, os, hostname,
│                                     ip, metrics, active_window }
│
├── logs/
│   └── dev_a1b2c3d4/
│       ├── snapshots/
│       │   └── 2026-06-22/
│       │       ├── 2026-06-22T14-00-00.json   ← full snapshot
│       │       └── 2026-06-22T14-30-00.json   ← next cycle
│       │   (only last 2 days kept)
│       │
│       ├── events/
│       │   ├── 2026-06-22T14-00-00_events.json  ← { events: [...], device_id, display_name }
│       │   └── 2026-06-22T14-30-00_events.json
│       │   (last 200 files kept)
│       │
│       └── agent.log            ← overwritten each cycle (last 200 lines of agent's log)
│
├── file_browser/
│   └── dev_a1b2c3d4/
│       └── listing.json         ← overwritten when agent sends file listing
│                                   { device_id, files: [{path, rel, name, dir, size_kb, modified}], ts }
│
├── agent_tasks/
│   └── dev_a1b2c3d4/
│       └── a1b2c3d4e5f6.json    ← pending file request, deleted when fulfilled
│                                   { type: "get_file", request_id, path, requested_at }
│
└── file_cache/
    └── dev_a1b2c3d4/
        └── a1b2c3d4e5f6/
            ├── status.json      ← { status: "pending"|"ready"|"error", filename, size }
            └── budget.xlsx      ← the actual downloaded file
```

### Data Retention

| Data type | Retention policy |
|---|---|
| Device status | Overwritten each cycle — 1 file per device |
| Snapshots | Last 2 calendar days per device |
| Events | Last 200 files per device |
| Agent log | Overwritten each cycle (last 200 log lines) |
| File listings | Overwritten when agent sends one |
| File cache | Never auto-deleted — you manage manually |

---

## 13. Security Model

### Threat Model

This is internal agency monitoring software. The threats considered:

1. **Rogue agent trying to impersonate another device** — defeated by the `device_id` in each request being sanitized with `_safe_id()` and the secret header being required
2. **Rogue device trying to inject malicious data** — the server only writes JSON, never executes anything agents send
3. **Path traversal via device_id** — `_safe_id()` strips everything except `[a-zA-Z0-9_\-]`
4. **OOM via huge upload** — `MAX_CONTENT_LENGTH = 150 MB` rejects the body before it's processed
5. **XSS via filename in dashboard** — `escHtml()` applied to all agent-supplied text in the browser
6. **Timing attack on secret comparison** — `secrets.compare_digest()` is constant-time
7. **Secret in git** — `monitor_settings.json` is in `.gitignore`; secret never leaves the machine
8. **Network eavesdropping (LAN)** — plain HTTP on LAN; acceptable for internal networks
9. **Network eavesdropping (remote)** — Cloudflare Tunnel is HTTPS end-to-end

### What the System Deliberately Does NOT Do

- **No command execution.** There is no endpoint that runs a command on an agent machine. The only "tasks" are `get_file` — passive retrieval.
- **No agent-to-agent communication.** Agents only talk to the commander.
- **No agent-to-browser direct path.** Everything goes commander → browser.
- **No persistent sessions.** The dashboard has no login. Access is controlled by who can reach port 8888.

### Dashboard Access Control

The dashboard has no authentication. Anyone who can reach `http://your-commander:8888` can see the data. This is by design — it's meant to be accessed only by you on your machine (`http://localhost:8888`) or from a network/tunnel you control.

If you need dashboard auth, add Flask-Login or put nginx with basic auth in front of Flask.

---

## 14. Rebuild from Scratch — Step by Step

If you were starting over with a blank folder, here's the exact order to build this:

### Phase 1 — Basic HTTP receiver (Day 1)

1. Create `dashboard_server.py` with Flask
2. Add one endpoint: `POST /api/agent/checkin` that writes to a JSON file
3. Add one endpoint: `GET /api/fleet` that reads all JSON files and returns them
4. Test with `curl -X POST http://localhost:8888/api/agent/checkin -H "Content-Type: application/json" -d '{"device_id":"test","display_name":"Test PC"}'`

### Phase 2 — Agent basics (Day 1)

1. Create `agent/monitor_agent.py`
2. Add identity setup (read/write `local_config.json`)
3. Add `collect_metrics()` using psutil
4. Add `http_post()` using urllib
5. Add main loop: collect → post → sleep
6. Test: run agent, watch data appear in `data/devices/`

### Phase 3 — More data collection (Day 2)

1. Add `collect_processes()`
2. Add `collect_network()`
3. Add `get_active_window()` (Windows only)
4. Add `collect_recent_files()`
5. Add `POST /api/agent/snapshot` to server
6. Add `detect_events()` — the diff engine
7. Add `POST /api/agent/events` to server

### Phase 4 — Basic dashboard (Day 2)

1. Create `dashboard/index.html` with minimal HTML
2. Add `fetch('/api/fleet')` to load device list
3. Add `fetch('/api/device/<id>/snapshot')` for process/network display
4. Add CSS (dark theme, table styles)
5. Add auto-refresh

### Phase 5 — Enrollment system (Day 3)

1. Add `_get_secret()` and `_auth()` to server
2. Add auth check to all agent endpoints
3. Add `GET /api/settings` endpoint
4. Add `POST /api/settings` endpoint (save URL override)
5. Add `GET /agent/monitor_agent.py` endpoint (serve agent script)
6. Add `GET /enroll/<filename>` — generate Windows/Linux setup scripts
7. Add enrollment modal to dashboard HTML

### Phase 6 — File browser and download (Day 3)

1. Add `collect_file_listing()` to agent
2. Add `POST /api/agent/files` to server
3. Add `GET /api/device/<id>/files` to server
4. Add file browser tab in dashboard HTML
5. Add `POST /api/device/<id>/request-file` — create task
6. Add `GET /api/agent/tasks/<id>` — agent polls tasks
7. Add `handle_file_requests()` to agent
8. Add `POST /api/agent/file-content` — agent uploads file
9. Add `GET /api/device/<id>/file-status/<rid>` — poll status
10. Add `GET /api/device/<id>/download/<rid>` — serve file
11. Add polling in dashboard JS (`setInterval` → file-status → show download link)

### Phase 7 — Production hardening (Day 4)

1. Add `MAX_CONTENT_LENGTH` to Flask
2. Add `_safe_id()` and `_safe_name()` for all user inputs
3. Add `escHtml()`, `escAttr()`, `escJs()` in dashboard
4. Add time budget to `collect_file_listing()`
5. Add warm-up call for `cpu_percent`
6. Add separate 300s timeout for file uploads
7. Add pidfile lock to Linux cron wrapper
8. Fix Windows bat to use env vars for paths with spaces
9. Add `pythonw` fallback detection in bat script
10. Set `OFFLINE_THRESHOLD_MINUTES = 30`
11. Add Cloudflare instructions to settings modal
12. Update `.gitignore` for `monitor_settings.json` and `data/`

### Phase 8 — Polish (Day 4)

1. Add stats bar (online/offline/total/alerts count)
2. Add right-panel alerts feed
3. Add active window display
4. Add metric cards (CPU/RAM/Disk with fill bars)
5. Add event severity colors
6. Add startup auto-browser-open
7. Write README and DEEP_DIVE docs

---

## Key Numbers to Remember

| Parameter | Value | Why |
|---|---|---|
| Server port | 8888 | Avoids common ports (80, 443, 3000, 8080) |
| Agent sleep | 300–600s (5–10 min) | Balance between freshness and load |
| Offline threshold | 30 minutes | 3× max sleep interval, comfortable buffer |
| Snapshot retention | 2 days | Enough history, limited disk use |
| Event retention | 200 files | ~16–33 hours of data |
| File listing limit | 500 files | Cap on scan time and JSON size |
| File listing time budget | 25 seconds | Agent cycle shouldn't block > 30s |
| File size limit | 100 MB | Fits in 150 MB Flask limit after base64 |
| File upload timeout | 300 seconds | 5 min for 100MB on slow link |
| Secret entropy | 128 bits (32 hex) | Industry standard for symmetric keys |
| Process display limit | 80 | Dashboard performance |
| File browser display | 200 | Dashboard performance |

---

*Built with Python 3.8+, Flask, psutil, and Cloudflare Tunnel. No database. No npm. No compilation. One server file, one agent file, one HTML file.*
