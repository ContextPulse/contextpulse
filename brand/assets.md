# ContextPulse Assets Inventory

## Logo
- **None yet** — needs design
- Requirements: works at 16x16 (system tray), 64x64 (installer), 512x512 (store listing)
- Concept: stylized pulse/heartbeat line or radar sweep, in brand primary teal
- Formats needed: SVG (master), ICO (Windows tray/installer), PNG (various sizes)

## System Tray Icon
- `packages/screen/src/contextpulse_screen/icon.py` — programmatically generated
- Currently: solid circle with color fill (green=active, amber=paused)
- Colors: `#00CC66` (active), `#FFB800` (paused) — matches brand accent/warning
- **Action:** Replace with proper logo once designed

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
