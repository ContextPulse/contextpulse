# ContextPulse Assets Inventory

## Logo
- **Responsive logo system** — two variants of eye + pulse wave
- **A1 (Full mark)** — bolder, more detail. Use at 64px+ (website, social, README, landing page)
  - `brand/logo/logo-primary-A1.png` — Primary (teal + navy on white)
  - `brand/logo/logo-reversed-white-on-black.png` — White on black
  - `brand/logo/logo-mono-black.png` — Black silhouette
- **A3 (Simplified mark)** — thinner, cleaner. Use at 16-48px (system tray, favicon, small UI)
  - `brand/logo/logo-backup-A3-clean.png` — Primary (teal + navy on white)
  - `brand/logo/logo-A3-mono-black.png` — Black silhouette
- Backups: A2, B1, C1, D1 variants in `brand/logo/`
- **Still needed:** SVG traces, ICO (Windows tray/installer), sized PNGs (16, 32, 64, 128, 256, 512)

## System Tray Icon
- `packages/screen/src/contextpulse_sight/icon.py` — programmatically generated
- Currently: solid circle with color fill (green=active, amber=paused)
- Colors: `#00CC66` (active), `#FFB800` (paused) — matches brand accent/warning
- **Action:** Replace with logo-primary-A1 once sized to 64x64

## Screenshots / Demo
- **None yet**
- Needed for: README, PyPI listing, landing page, Gumroad
- Capture list:
  - System tray icon in action (active + paused states)
  - MCP tool call in Claude Code showing a returned screenshot
  - Before/after: manual Snip Tool workflow vs ContextPulse auto-capture
  - Privacy blocklist in action (blocked window message)

## Demo GIF
- **None yet**
- Needed for: README hero image, Gumroad listing
- Concept: 10-15 second GIF showing auto-capture → Claude Code reads screen via MCP
