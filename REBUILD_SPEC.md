# Ghost Monitor — End-to-End Test + UI Rebuild Spec
## Hand to Claude Code as REBUILD_SPEC.md

> Run all tests FIRST in order. Fix any failures before touching the UI. Then rebuild the dashboard. Both sections are required.

---

## PART 1 — End-to-End Tests (run in order, fix before proceeding)

Each test has: setup → action → expected result → how to verify.

### Test 1 — Consent gate blocks unenrolled agent

```
Setup:  Delete or rename agent/consent_ack.json if it exists
Action: cd agent && python monitor_agent.py
Pass:   Process exits immediately with log message containing "consent" or "enrollment"
Fail:   Agent starts collecting data — STOP, fix the gate in monitor_agent.py main()
```

### Test 2 — Enrollment script consent prompt

```
Setup:  Have the enrollment .bat or .sh script available
Action: Run the enrollment script, type N when prompted
Pass:   Script exits, zero files written to agent folder, zero POST to commander
Fail:   Script continues after N — STOP, fix the prompt logic in the script

Then:   Run again, type Y
Pass:   consent_ack.json created locally, POST to /api/consent/ack returns 200,
        agent starts, tray icon appears within 30 seconds
Fail:   Any of the above missing — fix before continuing
```

### Test 3 — Tray icon always launches with agent

```
Setup:  Check agent/start_agent.bat contents
Action: Open start_agent.bat and verify it contains pythonw calls for BOTH
        monitor_agent.py AND tray_icon.py
Pass:   Both processes start when bat runs, tray icon visible in system tray
Fail:   Only monitor_agent.py launched — add tray_icon.py launch line

Also:   Confirm start_agent_silent.vbs does NOT exist in the repo
        If it does: git rm agent/start_agent_silent.vbs and commit
```

### Test 4 — Commander receives data

```
Setup:  Commander running (python dashboard_server.py), agent enrolled and running
Action: Wait one full agent cycle (max 10 min) or restart agent to force immediate cycle
Pass:   data/devices/<device_id>/status.json exists and has recent timestamp
        data/consent/<device_id>/ack.json exists
        Dashboard shows device as ONLINE, CONSENTED counter shows 1/1
Fail:   No data files — check agent logs for HTTP errors, check token auth
```

### Test 5 — Per-device token auth

```
Setup:  Agent enrolled and checking in successfully
Action: Open tokens.json, change one character of the device's token, save
        Wait for next agent cycle or restart agent
Pass:   Agent log shows 401 error, device goes offline on dashboard
        Other enrolled devices (if any) unaffected
Then:   Revert the token change — device reconnects next cycle
Fail:   Agent still checks in successfully with wrong token — fix _auth() in dashboard_server.py
```

### Test 6 — File access log

```
Setup:  Agent online with file listing visible in dashboard Files tab
Action: Click "Request Download" on any file in the dashboard
        Wait one agent cycle for the file to be fetched
Pass:   File downloads successfully
        data/consent/<device_id>/access_log.json has a new entry for this file
        Tray icon → "What's being monitored?" shows the file access in the transparency page
Fail:   Access log not written — fix the file-content endpoint in dashboard_server.py
```

### Test 7 — Revoke device

```
Setup:  Device enrolled and online
Action: Click "Revoke" button in dashboard device detail (or POST /api/device/<id>/revoke)
Pass:   Token removed from tokens.json
        Next agent checkin returns 401 and device goes offline
Fail:   Device still checks in — fix revoke endpoint
```

### Test 8 — Quiet hours pause

```
Setup:  Agent running with tray icon visible
Action: Right-click tray icon → Pause monitoring → 1 hour
Pass:   Dashboard shows device as "Paused" not "Offline" (visually distinct)
        Agent still sends lightweight checkin with paused:true
        pause event appears in access_log.json
Fail:   Device shows as offline (indistinguishable) — fix pause checkin in monitor_agent.py
```

---

## PART 2 — Dashboard UI Rebuild

**Replace dashboard/index.html entirely.** Keep all existing JS logic and API calls — only change the HTML structure and CSS. Do not break any existing functionality.

### Design tokens (use exactly these)

```css
:root {
  --bg:         #080B1A;
  --surface:    #0D1628;
  --surface-2:  #111E33;
  --surface-3:  #162540;
  --border:     rgba(148, 170, 200, 0.10);
  --border-2:   rgba(148, 170, 200, 0.18);
  --teal:       #00D4AA;
  --teal-dim:   #00967A;
  --teal-glow:  rgba(0, 212, 170, 0.15);
  --brass:      #E0A458;
  --brass-dim:  #A87C43;
  --red:        #FF4D6A;
  --red-dim:    #C23050;
  --blue:       #4D9EFF;
  --text:       #C8D6E5;
  --text-muted: #6B7E94;
  --text-dim:   #3D5068;
  --text-bright:#EEF2F8;
  --online:     #00D4AA;
  --offline:    #FF4D6A;
  --paused:     #E0A458;
  --radius-sm:  8px;
  --radius:     12px;
  --radius-lg:  18px;
}
```

### Fonts

```html
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=Inter:wght@400;450;500;600&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
```

- Display/headings: `Space Grotesk`
- Body/UI: `Inter`
- Data (IPs, PIDs, paths, code): `JetBrains Mono`

### Layout structure

```
┌─ topbar (64px) ──────────────────────────────────────────────────┐
│ ◈ GHOST MONITOR   [LIVE ●]   [stats row]   [+ Enroll] [⚙ Settings]│
├─ sidebar (280px) ──────────┬─ main panel ─────────────────────────┤
│                            │                                       │
│  DEVICES  (N online)       │  fleet view (default):               │
│  ┌──────────────────────┐  │    activity feed + warnings           │
│  │ ● device-name    Win │  │                                       │
│  │   CPU ████░ 45%      │  │  device detail view:                  │
│  │   2 min ago  ✓       │  │    header card → tabs                 │
│  └──────────────────────┘  │    Processes / Network / Files /      │
│                            │    Events / Log                       │
│  ┌──────────────────────┐  │                                       │
│  │ ○ device-2      Lin  │  │                                       │
│  │   OFFLINE            │  │                                       │
│  └──────────────────────┘  │                                       │
└────────────────────────────┴───────────────────────────────────────┘
```

### Stats bar (in topbar, after logo)

Five stat chips inline:

```
[● ONLINE: 3]  [○ OFFLINE: 1]  [□ TOTAL: 4]  [◎ CONSENTED: 3/4 ▓▓▓░]  [△ ALERTS: 2]
```

The CONSENTED chip is the hero stat — render it with a small inline SVG arc/progress bar showing the fraction visually. Color it teal when all consented, amber when partial, red when none.

### Device cards (sidebar)

Each card has:
- Left border: 3px solid `var(--online)` / `var(--offline)` / `var(--paused)`
- Status dot + device name (Space Grotesk 600)
- OS badge (small pill: WIN / LIN / MAC)
- Mini CPU bar (thin, 6px height, teal fill)
- Mini RAM bar
- "N min ago" timestamp + consent checkmark (✓ teal) or warning (⚠ brass)
- Active window title in muted text (truncated)
- Hover: surface-3 background, slight border brightening
- Selected: left border glows, background surface-3

### Device detail header

When a device is selected, show a header card above the tabs:

```
┌─────────────────────────────────────────────────────────────────┐
│ ● ONLINE    john-laptop                              [Revoke]   │
│ Windows 10 · 192.168.1.45 · Consented Jun 22, 2026 ✓           │
│                                                                  │
│ [CPU ████████░░ 82%]  [RAM ██████░░░░ 61%]  [DISK ████░░ 44%]  │
│                                                                  │
│ Active: Microsoft Excel — Budget Q2.xlsx                         │
└─────────────────────────────────────────────────────────────────┘
```

### Tabs styling

Tabs as a horizontal strip under the device header:
- `Processes` `Network` `Files` `Events` `Log`
- Active tab: teal bottom border (2px), teal text
- Inactive: muted text, no border
- No background boxes on tabs — just the underline

### Process table

Columns: PID · Name · User · CPU% · RAM% · Status
- JetBrains Mono for PID, Name
- CPU% color-coded: >50% brass, >80% red
- Alternating row backgrounds: surface / surface-2
- Top 5 by CPU highlighted subtly

### Network table

Columns: Local · Remote · State · PID
- JetBrains Mono for all addresses
- External IPs: teal text
- Local IPs: muted text
- ESTABLISHED: teal badge, LISTEN: blue badge, CLOSE_WAIT: brass badge

### Files tab

- Search bar at top
- Table: Name · Path · Modified · Size · [Request Download]
- Download button: small, teal outline, becomes a spinner when pending, green when ready
- Below table: "Files accessed by administrator" section showing access_log entries

### Events tab

Timeline-style layout (not a table):
```
  Jun 22 14:31  ● chrome.exe started          [process_started]  info
  Jun 22 14:28  △ CPU spike: 89%              [high_cpu]         warning
  Jun 22 14:15  ◆ New connection: 8.8.8.8:53  [new_ext_conn]     info
```
Color-coded by severity: info=muted, warning=brass, critical=red

### Enrollment modal

Two-column layout:
- Left: OS selector tabs (Windows / Linux / macOS), install command in a styled code block with copy button
- Right: Policy preview — show exactly what the staff member will see during enrollment. Label it clearly: "What your staff will see:"
- Bottom: note about consent being logged before install begins

### Settings modal

Clean form layout:
- Commander URL field
- Policy text textarea (editable)
- Agency name field
- Save button (teal, full width at bottom)

### Empty states

- No devices: Large centered illustration area + "Click + Enroll Device to add your first device" — not just tiny muted text
- No activity: "Waiting for agents to check in. Data arrives every 5–10 minutes."
- No files: "No files indexed yet."
- All of these: directive, not apologetic

### Micro-interactions

- Device cards: smooth transition on select (150ms)
- Status dots: subtle pulse animation on ONLINE devices (CSS keyframes, 3s cycle, reduced-motion respected)
- Consent ring: CSS transition when fraction changes
- Tab switch: 150ms opacity fade on content
- Refresh button: rotates icon during fetch

### What NOT to do

- No neon green on black (looks like a terminal, not a product)
- No all-caps everywhere (only for eyebrows/labels)
- No border-radius > 18px on cards
- No shadow stacking (one level of elevation, not three)
- No placeholder content in empty states — tell the user what to do

---

## Delivery order

1. Run Tests 1–8 in order. Fix any failures. Commit fixes.
2. Rebuild dashboard/index.html with the new design.
3. Test the rebuilt dashboard against all 8 tests again to confirm nothing broke.
4. `git add dashboard/index.html && git commit -m "redesign: professional dashboard UI" && git push origin main`

## Kickoff prompt for Claude Code

```
Read REBUILD_SPEC.md. Execute Part 1 first (all 8 tests in order).
For each test: run it, report pass/fail, fix any failure before moving
to the next test. Do not start Part 2 until all 8 tests pass.

Then execute Part 2: rebuild dashboard/index.html using the exact
design tokens, fonts, and layout described. Keep all existing JS API
calls working — only change HTML structure and CSS.

After the rebuild, run through all 8 tests one more time to confirm
nothing broke. Then commit and push.
```
