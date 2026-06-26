# Ghost Monitor — Dashboard Redesign Spec

> Hand to Claude Code as `dashboard/DESIGN.md`. The current dashboard rates 2/10 visually. The redesign target is a satellite mission control aesthetic — distinctive enough that someone could screenshot any corner of it and tell what product it is.

---

## The brief

**Subject:** A command center where one administrator watches remote machines that could be anywhere on Earth. Each device is functionally a satellite — periodically beaconing telemetry back to mission control.

**Tone:** SpaceX mission console crossed with a high-end consumer fintech app. Deep space dark, but precise and product-grade — not gamer RGB, not generic admin panel.

**Audience:** Small agency owners who currently choose between ugly self-hosted tools and expensive surveillance SaaS. This is the first self-hosted tool that looks like a real product.

**The single job of this page:** make the operator feel like they're in command of a fleet, with consent state, device health, and live telemetry legible in under three seconds.

---

## Visual references (these specifically)

The two screenshots already shared:
1. Satellite tracking dashboard with rotating globe centerpiece, glowing teal accents, dense data clusters around the edges
2. Mission control monitor mockup with multi-card layout, glass-morphism panels, dark space gradient

Take from them: **the palette, the panel materiality, the type personality, the orbital metaphor.** Do not literally copy the globe — we don't have geographic data. Replace the globe with our equivalent: a **fleet constellation view**.

---

## Locked palette

```
--void:        #04060E   /* deepest background */
--space:       #07091A   /* slightly elevated dark */
--nebula:      #0B1228   /* panel surface */
--starlight:   #131D3A   /* hover/active surface */
--rim:         rgba(0, 245, 212, 0.10)   /* default borders */
--rim-glow:    rgba(0, 245, 212, 0.28)   /* active borders */

--mission-teal: #00F5D4   /* primary — online, consent, success */
--solar-amber:  #FFB347   /* paused, warning */
--nova-red:     #FF3D6E   /* offline, critical */
--ion-blue:     #4D8FFF   /* RAM gauge, secondary data */
--plasma-purple:#9B7FFF   /* disk gauge, tertiary */

--text-bright: #F0F7FF
--text:        #B8C7E0
--text-mute:   #5A6E8A
--text-dim:    #2E3F58
```

**Critical rule:** mission-teal is the brand color. Use it for *one job per surface only* — never decorate with it. If something is teal, it means consented/online/success.

---

## Typography

```
Display (headings, device names, stats):  Space Grotesk 600/700
Body (paragraphs, labels):                Inter 400/500
Data (PIDs, IPs, paths, metrics):         JetBrains Mono 400/500
Eyebrows / micro-labels:                  JetBrains Mono 500 ALL CAPS letter-spacing 0.15em
```

**Type scale (px):**
```
hero/device name:   22   (Space Grotesk 700)
section headings:   13   (Space Grotesk 600)
body:               13   (Inter 400)
data:               11   (JetBrains Mono)
eyebrow/micro:      9    (JetBrains Mono ALL CAPS)
```

No font sizes outside this scale. No font weight 800 or 900. No serif fonts anywhere.

---

## The 5 signature elements

These are what make this not look generic. Build all five, exactly as specified.

### 1. The Constellation View (the homepage centerpiece)

When no device is selected, the main area shows a **constellation map** — a circular SVG canvas, ~480px diameter, centered. Each enrolled device is a node positioned around the perimeter in a slow orbit. The commander is the center point (small teal core with a faint glow ring).

- Nodes are 14px circles, color-coded by status (teal/red/amber)
- A thin animated line connects each node to the center, pulsing toward the center every few seconds to indicate the next expected check-in
- Hover a node → tooltip with device name, OS, last seen, CPU%
- Click a node → opens device detail view
- Two faint orbital rings (dashed, rim color) at 50% and 80% of the radius for visual anchoring

This **replaces** the "Activity Feed" as the centerpiece. Activity feed moves to a card *below* the constellation, not above.

### 2. The Orbital Ring on Device Cards

Each sidebar device card has a 20px SVG to the left of the device name:
- Outer ring: 18px diameter, 1px dashed stroke in status color
- Inner dot: 7px solid, status color, with `box-shadow: 0 0 8px <color>` when online
- The outer ring **rotates** — 6s for online, 12s for paused, static for offline
- This is the visual heartbeat of the card

### 3. Radial Telemetry Gauges (device detail)

CPU, RAM, Disk render as **circular SVG arc meters** — not bars. 80px diameter each.
- Background ring: thin (4px), rim color
- Foreground arc: 4px stroke, color per metric (teal/blue/purple), `stroke-linecap: round`
- Center text: large percentage in Space Grotesk 700
- Below: small JetBrains Mono detail line (e.g. "8.8 / 16 GB")
- Animate the arc filling in on data update (0.6s ease-out)

### 4. The Consent Ring (topbar stat)

The "CONSENTED 4/4" stat in the topbar is **not text** — it's a circular SVG inline with the text:
- 18px diameter ring showing the consented/total ratio as a filled arc
- Color: teal at 100%, amber if any device unconsented, red if zero consented
- This is the most important number in the product — it gets the visual emphasis

### 5. Telemetry Beacon Animation

When new data arrives from a device (any update), the device's card briefly emits a **light pulse** — a 1px teal border ring that fades from rim-glow to transparent over 1.2s. Subtle, but communicates "this device just talked to us."

---

## Layout

```
┌────────────────────────────────────────────────────────────────────────┐
│ ◈ GHOST MONITOR  [LIVE●]  online·offline·total·CONSENTED[ring]·alerts  │  56px topbar
│                                                          [+Enroll] [⚙] │
├──────────────┬─────────────────────────────────────────────────────────┤
│              │                                                          │
│  FLEET (4)   │              [ CONSTELLATION VIEW ]                      │
│              │              ◯ ─── ◉ ─── ◯                              │
│  [device card]│                  ╲│╱                                    │
│  [device card]│                   ◯                                     │
│  [device card]│                                                          │
│  [device card]│              ── activity feed ──                        │
│              │              [recent events stream]                       │
│              │                                                          │
│              │              ── warnings ──                              │
│              │              [warning cards]                              │
│              │                                                          │
│              │ (when device selected, this whole area becomes detail)  │
└──────────────┴─────────────────────────────────────────────────────────┘
```

**Sidebar:** 280px wide. Device cards stacked vertically. Header reads "FLEET (4)" in JetBrains Mono ALL CAPS micro-label style.

**Main area:** when no device selected → constellation centered, scrollable area below for feeds. When device selected → header card with gauges + 5-tab content area.

---

## Materiality (what surfaces look like)

Every panel uses three properties together — this is non-negotiable:

```css
background: rgba(11, 18, 40, 0.65);
backdrop-filter: blur(20px) saturate(140%);
border: 1px solid var(--rim);
```

The `saturate(140%)` is what gives panels that subtle deep-space glow through them. Don't skip it.

Cards get `border-radius: 14px`. No sharp corners anywhere except 1px hairline dividers inside tables.

**One level of elevation only.** No nested shadows, no card-inside-card. The hierarchy is: void → blurred panel → content. That's it.

---

## The star field

A subtle, fixed-position star layer behind everything. Use `radial-gradient` dots in `body::before` — about 25 stars across the viewport at varying opacities (0.3–0.7). Add a slow `twinkle` animation (8s ease-in-out alternate) that drifts opacity across the whole layer.

**Add one diffuse nebula glow** in the top-right corner via `body::after` — a large soft radial-gradient in `rgba(155, 127, 255, 0.04)` blending into `rgba(77, 143, 255, 0.03)` then transparent. This is what gives the space depth.

---

## Motion rules

- All transitions 180ms ease-out unless specified
- Orbital rings rotate continuously (only ones that move ambient)
- Star twinkle is the only ambient body animation
- Hover transitions on cards: border-color + background, 180ms
- Tab switching: 150ms opacity fade
- Number updates: count-up animation, 400ms (not just snap to new value)
- **All animations respect `prefers-reduced-motion`**

Do not add: floating particles, scrolling code rain, mouse trail effects, or any other gamer-aesthetic motion.

---

## Strict do-not list

These mistakes will drop it back to 2/10. Avoid all of them:

- ❌ Neon green on black (looks like a hacker terminal)
- ❌ Orbitron font (corny sci-fi)
- ❌ Glow shadows everywhere (use them on ONE element per view max)
- ❌ ALL CAPS body text or headings (only eyebrows/micro-labels)
- ❌ Gradient text on headings
- ❌ Multiple accent colors competing (teal is brand, amber and red are status only)
- ❌ Decorative emoji or icon-font usage — use only inline SVG
- ❌ Card-inside-card-inside-card nesting
- ❌ Border-radius > 16px anywhere
- ❌ A literal globe with continents (we have no geographic data)
- ❌ Loading spinners as a default state (use empty states with directive text)
- ❌ "Awesome!" "Hello!" "Welcome!" copy — keep tone direct and technical

---

## Copy / writing

The tone is **flight controller**. Direct, technical, calm, never apologetic.

| Bad | Good |
|---|---|
| "Loading your devices..." | "Awaiting telemetry" |
| "No data found 😞" | "No telemetry received yet. Agents beacon every 5–10 min." |
| "Submit" | "Save changes" |
| "Welcome to the dashboard!" | (no welcome message — show the data) |
| "Click here to add device" | "Enroll the first device" |
| Empty events list: "Nothing here" | "Mission nominal. No events." |

Buttons name the exact verb of what happens: "Enroll Device" produces a toast "Device enrolled." Status badges read "ONLINE" / "OFFLINE" / "PAUSED" in micro-caps mono.

---

## Build order (Claude Code)

1. **Lock the tokens first.** Drop the CSS variables (palette + type) into a `:root` block. No hardcoded colors anywhere else in the file.
2. **Star field + nebula glow on body.** Verify the depth feels right.
3. **Topbar.** Logo, live badge, stat chips with the consent ring SVG, clock, actions.
4. **Sidebar with device card template.** Get the orbital ring rendering correctly — this is signature element #2 and must be right before moving on.
5. **Constellation view in main area.** Signature element #1. Take time on this. SVG, animated, fully responsive.
6. **Device detail view.** Header card, radial gauges (signature #3), tabs, panels.
7. **Modals** (enroll + settings).
8. **The beacon pulse** (signature #5) — wire it to fire on data update.
9. **Animation audit** — verify reduced-motion, no excessive motion.

After build: take a screenshot and self-critique against this spec. Anything that matches the "Do Not" list — fix before committing.

---

## Acceptance criteria

The redesign is done when all of these are true:

- [ ] Constellation view exists, renders device nodes orbiting a central commander point
- [ ] Every sidebar device card shows a rotating orbital ring (visible motion)
- [ ] Device detail shows radial gauges, not bars, for CPU/RAM/Disk
- [ ] Consented stat in topbar shows a visual ring, not just text
- [ ] Background has star field + nebula glow with backdrop blur on panels
- [ ] No emoji icons — all icons are inline SVG
- [ ] Type uses Space Grotesk + Inter + JetBrains Mono only
- [ ] All API endpoints from the previous dashboard still work — no JS regressions
- [ ] Reduced-motion mode disables ambient animation
- [ ] A non-developer looking at the screenshot would describe it as "looks like NASA mission control" or "looks like a SpaceX dashboard"

---

## Kickoff prompt for Claude Code

```
Read dashboard/DESIGN.md and rebuild dashboard/index.html from scratch
following the spec exactly. Pay special attention to the 5 signature
elements (constellation view, orbital rings, radial gauges, consent ring,
beacon pulse) — those are what make this distinctive.

Preserve every API call the current index.html makes. No backend changes.

Before committing, run through the acceptance criteria checklist at
the bottom of the spec. Take a screenshot and self-critique against the
"Do Not" list. If any item matches, fix before pushing.
```
