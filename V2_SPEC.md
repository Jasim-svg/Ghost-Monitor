# Ghost Monitor v2 — Implementation Spec
### Consent, Auth, and Transparency Layer

> Hand this file to Claude Code as `V2_SPEC.md` in the project root. Each numbered section is a self-contained unit of work — implement and test in order, don't skip ahead. This spec assumes the v1 codebase described in `DEEP_DIVE.md` already exists (commander, agent, dashboard).

---

## 0. Goal & Non-Negotiables

v1 works but has one structural gap: it can be installed and run without the monitored person ever knowing. v2 closes that gap completely. Three rules govern every change below:

1. **The agent must refuse to run without a local, verifiable consent record.** Not a setting — a hard startup gate.
2. **The tray indicator is not optional.** It installs in the same step as the agent, runs for the agent's entire lifetime, and there is no flag or config option to suppress it in the standard install path.
3. **Every file the commander pulls from a device is logged somewhere the monitored device itself can read**, independent of whether the commander is reachable at that moment.

If any implementation choice in this spec conflicts with one of these three rules, the rule wins — flag it rather than silently dropping it.

---

## 1. Per-Device Authentication

**Replaces:** the single shared `agency_secret` in `monitor_settings.json`.

### 1.1 Server-side (`dashboard_server.py`)

- New file `tokens.json` (gitignored), shape:
  ```json
  { "dev_a1b2c3d4": "32-char-hex-token", "dev_e5f6...": "..." }
  ```
- `_generate_token() -> str` — `secrets.token_hex(16)`
- `_save_token(device_id: str, token: str) -> None`
- `_get_device_token(device_id: str) -> str | None`
- `_auth(req, device_id: str) -> bool` — replaces the existing `_auth(req)`. Looks up the token for the specific `device_id` in the request, compares with `secrets.compare_digest`.
- `revoke_device(device_id: str) -> None` — removes the entry from `tokens.json`. Expose via a dashboard endpoint: `POST /api/device/<id>/revoke`.
- **Every** agent-facing endpoint (`checkin`, `snapshot`, `events`, `log`, `files`, `file-content`, `tasks`) switches its header check from `X-Agent-Secret` to `X-Agent-Token`, and passes `device_id` into `_auth()`.

### 1.2 Agent-side (`monitor_agent.py`)

- `local_config.json` stores `token` instead of `secret`.
- `http_post()` / `http_get()` send header `X-Agent-Token` instead of `X-Agent-Secret`.

### 1.3 Migration note for Claude Code

This is a breaking change for any already-enrolled v1 device. Either (a) treat this as a clean cutover — re-enrollment required for all devices, which is acceptable since you're early — or (b) write a one-time migration script that issues fresh per-device tokens to existing devices and pushes a config update. Default to (a) unless told otherwise; it's simpler and the fleet is small.

### Acceptance criteria
- [ ] Revoking one device's token returns 401 for that device only; other devices unaffected
- [ ] `tokens.json` is in `.gitignore`
- [ ] No endpoint still checks the old shared secret

---

## 2. Consent & Enrollment Gate

This is the core of v2. Build this before anything else that depends on it (Sections 3–6).

### 2.1 New data: `data/consent/<device_id>/ack.json`

```json
{
  "device_id": "dev_a1b2c3d4",
  "ack_by": "john",
  "ts": "2026-06-22T14:00:00Z",
  "policy_version": "1.0"
}
```

### 2.2 New server endpoint: `POST /api/consent/ack`

- No auth header required (the device doesn't have a token yet at this point in the flow — token issuance happens in the same enrollment step, see 2.4).
- Body: `{ device_id, ack_by, policy_version }`
- Writes `data/consent/<device_id>/ack.json`. This file's existence is the source of truth for "this device is consented."
- Returns `{ ok: true }`.

### 2.3 New server config: policy text

- Add a constant or a small `policy.json`: the exact plain-language text shown at enrollment (what's collected, who can see it, how to ask questions). Keep it short — 5-6 lines, not a legal document. Surface it in the dashboard's enrollment modal too, so the operator sees exactly what staff will see before sending the link.

### 2.4 Enrollment script changes (both Windows `.bat` and Linux `.sh`)

Rewrite the script so it runs in this exact order — **nothing after step 1 executes if step 1 is declined**:

1. Print the policy text. Prompt: `Type Y to continue, anything else to cancel:`. Non-Y input exits immediately, no files written, nothing downloaded.
2. POST to `/api/consent/ack` with the device info. **This must succeed before continuing** — if the commander is unreachable, the script stops with a clear error rather than proceeding and "logging consent later."
3. Generate (server-side, at link-generation time) a fresh per-device token (Section 1), embed it in the script.
4. Check Python is available.
5. Download `monitor_agent.py` AND `tray_icon.py` from the commander — always both, never one without the other.
6. `pip install psutil pystray plyer`.
7. Write `local_config.json` with `device_id`, `display_name`, `commander_url`, `token`.
8. Register **two** startup entries — agent and tray icon — both triggered the same way (Scheduled Task on login / cron `@reboot`).
9. Start both processes immediately.

### 2.5 Agent startup gate (`monitor_agent.py`)

```python
def _consent_acknowledged() -> bool:
    # checks for a local marker written by the enrollment script
    # (mirrors the server's ack.json — written locally so the agent
    # never has to phone home just to check this)
    return (SCRIPT_DIR / "consent_ack.json").exists()

def main():
    if not _consent_acknowledged():
        log.error("No local consent record — refusing to start.")
        sys.exit(1)
    ...
```

The enrollment script writes `consent_ack.json` locally (mirroring what step 2.4.2 sent to the server) as part of step 7. This means: even if someone copies `monitor_agent.py` onto a machine by hand, bypassing the enrollment script entirely, it will not start — there's no local consent record, and there's no code path that creates one outside the enrollment flow.

### Acceptance criteria
- [ ] Running the enrollment script and answering "N" results in zero files written, zero network calls beyond nothing
- [ ] Running `monitor_agent.py` directly in a fresh folder (no enrollment) exits immediately with a clear log message
- [ ] `data/consent/<id>/ack.json` exists on the commander for every device that successfully enrolled, and for no others

---

## 3. Tray Indicator (`agent/tray_icon.py`)

New file, new process, started alongside the agent (never instead of it).

### 3.1 Dependencies
`pystray`, `plyer`, `Pillow` (for the icon image — a simple generated dot/shield icon is fine, doesn't need to be fancy).

### 3.2 Behavior

- Runs continuously, independent of the agent's network calls — it reads **local files only** (`local_data/`, `local_config.json`), so it stays accurate even if the commander is unreachable.
- Menu items:
  - `Ghost Monitor — Active` (disabled label, just a status header)
  - `Last check-in: <relative time>` (disabled label, read from the most recent local snapshot timestamp)
  - `What's being monitored?` → opens the local transparency page (Section 5)
  - `View policy` → opens the policy text (can be a local file written at enroll time, or a link to the commander if reachable)
  - `Pause monitoring...` → opens the quiet-hours submenu (Section 6, optional but recommended)
- Icon visual state: one color/style when the agent's last successful push was within the expected interval (e.g., green dot), a different one if it's been silent longer than `OFFLINE_THRESHOLD_MINUTES` (e.g., amber dot) — gives the user, not just you, visibility into whether it's actually working.

### Acceptance criteria
- [ ] Tray icon appears within seconds of the agent starting, every time
- [ ] Killing the commander process doesn't crash or hide the tray icon
- [ ] Tray icon survives across reboots (registered as its own startup entry, per 2.4.8)

---

## 4. Access Logging (File Requests)

### 4.1 Server-side

In the existing `POST /api/agent/file-content` handler, after a successful save to `file_cache/`, append an entry to `data/consent/<device_id>/access_log.json` (array, append-only):

```json
{ "file": "budget.xlsx", "path": "C:\\Users\\John\\Documents\\budget.xlsx", "ts": "2026-06-22T14:31:00Z" }
```

### 4.2 New lightweight sync to the agent

Add `GET /api/agent/access-log/<device_id>` (token-authed) returning the device's own log entries since a given timestamp. The agent calls this once per cycle (cheap — small payload) and writes the result to `local_data/access_log.json`, which is what the tray icon's "What's being monitored?" page reads. This keeps the access log visible locally even when the commander is briefly unreachable, since it's always reading the last-synced local copy.

### Acceptance criteria
- [ ] Every successful file download appears in the device's local access log within one agent cycle
- [ ] The log is append-only — no endpoint deletes or edits past entries

---

## 5. Local Transparency Page

Opened via the tray icon's "What's being monitored?" item. This is the single highest-value piece of v2 — see the differentiator note at the bottom of this spec.

### 5.1 Implementation

Simplest approach: the agent (or tray process) writes a static `summary.html` to `local_data/` once per cycle, regenerated from local state — no server needed to view it, the tray icon just opens it with the OS's default browser (`webbrowser.open()`).

### 5.2 Contents (plain language, not raw JSON)

- "This device has been monitored since [enrollment date] under [Agency]'s policy."
- A category list of what's collected (processes, network connections, CPU/RAM/disk, active window title, file listings in Documents/Desktop/Downloads) — described in one line each, not technical field names.
- "Files accessed by an administrator" — pulled from the local `access_log.json` (Section 4.2), most recent first.
- A link/reference to the full policy text and who to contact with questions.
- Last sync time and whether the agent is currently reaching the commander.

### Acceptance criteria
- [ ] Page opens with zero network dependency (works even with the commander fully offline)
- [ ] Content is accurate as of the last completed agent cycle, not stale beyond that

---

## 6. Quiet Hours / Self-Pause (recommended, not required for v2 completion)

This is the differentiator feature flagged in the earlier project review — almost nothing in this category lets the monitored person negotiate visibility rather than just disclose it.

### 6.1 Mechanism

- Tray menu: `Pause monitoring for 1 hour` / `2 hours` (cap it — e.g., max 2 hours, max 2 uses per day, configurable constants).
- Writes a local `paused_until` timestamp. The agent checks this each cycle; if paused, it **still sends a lightweight checkin** (`{"paused": true, "until": "..."}`) rather than going dark — this matters because indistinguishable-from-offline is a gap an admin would otherwise have to investigate, and distinguishable-and-logged is what keeps this a transparency feature rather than a workaround.
- Every pause event is appended to the same `access_log.json` pattern (Section 4) so it's visible to the admin too — mutual transparency, not a hidden escape hatch.

### Acceptance criteria
- [ ] Paused state is visibly different from "offline" on the dashboard
- [ ] Pause events are capped and logged, never silent or unlimited

---

## 7. Dashboard Updates (`dashboard/index.html`)

- Stats bar: add a `Consented: X/Y` counter, reading whether each device has a valid `ack.json`.
- Device detail header: show consent status + acknowledgment timestamp + policy version.
- Settings modal: surface the current policy text (read-only display) so the operator can confirm what staff are seeing.
- New action: `Revoke Device` button per device card, calling `POST /api/device/<id>/revoke` (Section 1.1).

---

## File Changes Checklist

| File | Change |
|---|---|
| `dashboard_server.py` | Per-device tokens, consent endpoints, access-log endpoint, revoke endpoint |
| `agent/monitor_agent.py` | Token auth, consent gate at startup, access-log sync, pause check |
| `agent/tray_icon.py` | **New file** |
| `agent/local_transparency.py` (or inline in agent) | **New** — generates `summary.html` |
| `dashboard/index.html` | Consent indicators, revoke button, policy display |
| `enroll_windows.bat` template | Consent prompt as step 1, ack POST as step 2 |
| `enroll_linux.sh` template | Same, mirrored |
| `tokens.json`, `.gitignore` | Add `tokens.json`, keep `monitor_settings.json` and `data/` ignored |

---

## Suggested Build Order

1. Section 1 (auth) — foundational, everything else depends on `device_id`-scoped tokens
2. Section 2 (consent gate) — the core safety property; get this airtight before building anything on top
3. Section 3 (tray icon) — depends on consent existing to have something to report
4. Section 4 (access log) — small, mechanical, depends on 1
5. Section 5 (transparency page) — depends on 3 and 4 both being in place
6. Section 7 (dashboard) — can happen in parallel with 3–5
7. Section 6 (quiet hours) — last; it's additive polish, not load-bearing

---

## Kickoff Prompt for Claude Code

```
Read V2_SPEC.md and DEEP_DIVE.md in this repo. Implement v2 in the order
listed under "Suggested Build Order." After each numbered section, run
its "Acceptance criteria" checklist before moving to the next section —
don't proceed if any box doesn't pass. Section 2 (Consent & Enrollment
Gate) is the most important — if anything is ambiguous there, stop and
ask rather than guessing.
```
