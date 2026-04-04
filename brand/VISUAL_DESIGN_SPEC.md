# ContextPulse Visual Design Spec

For Session B (productization code) and any UI work. All tkinter dialogs, tray icons, and splash screens should follow this spec.

## Color Palette (from colors.json)

### Dark Theme (primary — all UI is dark)
| Role | Hex | Usage |
|------|-----|-------|
| Background | `#0D1117` | Window/dialog background |
| Surface | `#161B22` | Cards, input fields, sections |
| Surface2 | `#1C2330` | Hover states, secondary containers |
| Border | `#30363D` | All borders, separators |
| Text | `#E6EDF3` | Primary text |
| Text Muted | `#8B949E` | Labels, secondary text, placeholders |
| Primary | `#0A6E8A` | Brand teal (headings, section labels) |
| Primary Light | `#12B5E0` | Links, active states, highlights |
| Accent | `#00CC66` | CTAs, success states, active tray icon |
| Success | `#00E676` | Confirmation, "licensed" badge |
| Warning | `#FFB800` | Paused tray icon, trial expiring |
| Error | `#F85149` | Errors, expired license, cost numbers |

## Typography

| Element | Font | Weight | Size |
|---------|------|--------|------|
| Window title | Segoe UI | 700 | 14pt |
| Section heading | Segoe UI | 600 | 12pt |
| Body text | Segoe UI | 400 | 10pt |
| Labels | Segoe UI | 400 | 9pt |
| Monospace (code, paths) | Cascadia Code | 400 | 9pt |
| Button text | Segoe UI | 600 | 10pt |

## Logo

**Current concept:** Eye shape formed by a pulse/heartbeat line, teal-to-green gradient.
**Generated image:** (see brand/assets/logo_concept.png)

For system tray, use the existing programmatic icon (icon.py) until the logo is finalized as SVG. The current circle-with-dot design is clean and works at 16x16.

### Tray Icon States
| State | Color | Meaning |
|-------|-------|---------|
| Active | `#00CC66` (accent) | Daemon running, capturing |
| Paused | `#FFB800` (warning) | User paused or blocklist match |
| Error | `#F85149` (error) | Capture failed, daemon unhealthy |

## Splash Screen Design

**File:** `packages/core/src/contextpulse_core/launcher.py`
**Window:** 400x250px, centered, no title bar (overrideredirect), border-radius simulated with dark bg

```
+------------------------------------------+
|                                          |
|            [Logo/Icon 64x64]             |
|                                          |
|            ContextPulse                  |
|     Always-on context for AI agents      |
|                                          |
|          ████████░░░░ Loading...         |
|                                          |
+------------------------------------------+
```

| Element | Style |
|---------|-------|
| Window bg | `#0D1117` |
| Logo | 64x64 icon from `icon.py` centered |
| Product name | Segoe UI 700, 16pt, `#E6EDF3` |
| Tagline | Segoe UI 400, 10pt, `#8B949E` |
| Progress bar bg | `#161B22` |
| Progress bar fill | `#00CC66` (accent) |
| Status text | Segoe UI 400, 9pt, `#8B949E`, below bar |
| Min display time | 1.5 seconds |

## Welcome Dialog (First Run)

**File:** `packages/core/src/contextpulse_core/first_run.py`
**Window:** 500x400px, centered, title: "Welcome to ContextPulse"

```
+--------------------------------------------------+
|  Welcome to ContextPulse                    [X]  |
|--------------------------------------------------|
|                                                  |
|  [Icon 48x48]  Always-on context for AI agents   |
|                                                  |
|  Quick Start                                     |
|  ┌──────────────────────────────────────────┐    |
|  │  Ctrl+Shift+S    Quick capture            │    |
|  │  Ctrl+Shift+A    All monitors             │    |
|  │  Ctrl+Shift+Z    Cursor region            │    |
|  │  Ctrl+Shift+P    Pause / Resume           │    |
|  └──────────────────────────────────────────┘    |
|                                                  |
|  Look for the green dot in your system tray.     |
|                                                  |
|              [ Get Started ]                     |
|                                                  |
+--------------------------------------------------+
```

| Element | Style |
|---------|-------|
| Window bg | `#0D1117` |
| Title bar | Default tkinter (let OS handle) |
| Icon | 48x48 from `icon.py` |
| "Welcome to" | Segoe UI 400, 11pt, `#8B949E` |
| "ContextPulse" | Segoe UI 700, 16pt, `#E6EDF3` |
| Tagline | Segoe UI 400, 10pt, `#8B949E` |
| "Quick Start" heading | Segoe UI 600, 11pt, `#12B5E0` |
| Hotkey table bg | `#161B22` with `#30363D` border |
| Hotkey keys | Cascadia Code 400, 9pt, `#12B5E0` |
| Hotkey descriptions | Segoe UI 400, 9pt, `#8B949E` |
| Tray hint | Segoe UI 400, 9pt, `#8B949E` |
| Button "Get Started" | bg `#00CC66`, text `#0D1117`, Segoe UI 600, 10pt, 12px pad, 6px radius |

## Settings Panel

**File:** `packages/core/src/contextpulse_core/settings.py`
**Window:** 550x600px, title: "ContextPulse — Settings"

```
+--------------------------------------------------+
|  ContextPulse — Settings                    [X]  |
|--------------------------------------------------|
|                                                  |
|  ┌─ Capture ────────────────────────────────┐    |
|  │  Capture interval     [====|====] 5s      │    |
|  │  Storage mode         [smart     v]       │    |
|  └──────────────────────────────────────────┘    |
|                                                  |
|  ┌─ Hotkeys ────────────────────────────────┐    |
|  │  Quick capture   [Ctrl+Shift+S    ]       │    |
|  │  All monitors    [Ctrl+Shift+A    ]       │    |
|  │  Cursor region   [Ctrl+Shift+Z    ]       │    |
|  │  Pause/Resume    [Ctrl+Shift+P    ]       │    |
|  │  ⓘ Changes take effect after restart      │    |
|  └──────────────────────────────────────────┘    |
|                                                  |
|  ┌─ Privacy ────────────────────────────────┐    |
|  │  Window blocklist (one per line):         │    |
|  │  ┌──────────────────────────────────┐     │    |
|  │  │ 1Password.exe                     │     │    |
|  │  │ KeePassXC.exe                     │     │    |
|  │  └──────────────────────────────────┘     │    |
|  │  Always-both apps (one per line):         │    |
|  │  ┌──────────────────────────────────┐     │    |
|  │  │ thinkorswim.exe                   │     │    |
|  │  └──────────────────────────────────┘     │    |
|  └──────────────────────────────────────────┘    |
|                                                  |
|  ┌─ License ────────────────────────────────┐    |
|  │  Status: ● Trial (5 days remaining)       │    |
|  │  [ Enter License Key ]                    │    |
|  └──────────────────────────────────────────┘    |
|                                                  |
|         [ Save ]          [ Cancel ]             |
|                                                  |
+--------------------------------------------------+
```

| Element | Style |
|---------|-------|
| Window bg | `#0D1117` |
| LabelFrame border | `#30363D`, text `#12B5E0` (Primary Light) |
| LabelFrame bg | `#0D1117` (same as window) |
| Input fields bg | `#161B22`, border `#30363D`, text `#E6EDF3` |
| Input focus border | `#12B5E0` |
| Slider track | `#161B22` |
| Slider fill | `#00CC66` |
| Combobox bg | `#161B22`, border `#30363D` |
| Text areas (blocklist) | `#161B22`, border `#30363D`, text `#E6EDF3`, font Cascadia Code 9pt |
| Info text (ⓘ) | Segoe UI 400, 8pt, `#8B949E` |
| License status dot | `#00CC66` (licensed), `#FFB800` (trial), `#F85149` (expired) |
| License status text | Segoe UI 400, 10pt, color matches dot |
| "Enter License Key" button | bg `#161B22`, border `#30363D`, text `#E6EDF3` |
| "Save" button | bg `#00CC66`, text `#0D1117`, Segoe UI 600, 10pt |
| "Cancel" button | bg `#161B22`, border `#30363D`, text `#8B949E` |

## License Dialog (Nag)

**File:** `packages/core/src/contextpulse_core/license_dialog.py`
**Window:** 500x400px, title: "ContextPulse — License"

```
+--------------------------------------------------+
|  ContextPulse — License                     [X]  |
|--------------------------------------------------|
|                                                  |
|  ┌──────────────────────────────────────────┐    |
|  │  ● Trial — 5 days remaining               │    |
|  │  Sight features are always free.          │    |
|  │  Memory features require a license.       │    |
|  └──────────────────────────────────────────┘    |
|                                                  |
|  Paste your license key:                         |
|  ┌──────────────────────────────────────────┐    |
|  │                                          │    |
|  │                                          │    |
|  └──────────────────────────────────────────┘    |
|                                                  |
|  [ Activate ]     [ Continue Trial ]             |
|                                                  |
|  Don't have a license?                           |
|  Purchase at contextpulse.ai/pricing             |
|                                                  |
+--------------------------------------------------+
```

### State Variations

**Trial active (days > 0):**
- Status: `● Trial — X days remaining` (dot = `#FFB800`, text = `#E6EDF3`)
- "Continue Trial" button enabled

**Trial expired:**
- Status: `● Trial expired` (dot = `#F85149`, text = `#F85149`)
- "Continue Trial" button disabled (grayed out)
- Additional text: "Sight features remain free. Memory features are locked."

**Licensed:**
- Status: `● Licensed to user@email.com (Pro)` (dot = `#00CC66`, text = `#00CC66`)
- "Continue Trial" hidden
- "Activate" hidden
- Show: "Licensed. Thank you."

**Expired license:**
- Status: `● License expired — renew at contextpulse.ai` (dot = `#F85149`)
- "Activate" button for new key
- "Sight features remain free."

## Tkinter Implementation Notes

1. **CRITICAL: Never create multiple `tk.Tk()` instances.** Use `gui_theme.py` singleton root + `tk.Toplevel` for all dialogs.

2. **Dark theme in tkinter:** Set `root.configure(bg="#0D1117")`. For ttk widgets, configure styles:
   ```python
   style = ttk.Style()
   style.theme_use("clam")  # most customizable theme
   style.configure(".", background="#0D1117", foreground="#E6EDF3",
                    fieldbackground="#161B22", bordercolor="#30363D")
   style.configure("Accent.TButton", background="#00CC66", foreground="#0D1117")
   style.configure("TLabelframe", background="#0D1117", foreground="#12B5E0")
   style.configure("TLabelframe.Label", background="#0D1117", foreground="#12B5E0")
   ```

3. **Font loading:** `tkinter.font.Font(family="Segoe UI", size=10, weight="normal")`. Cascadia Code may not be installed on all Windows machines — fall back to Consolas.

4. **Scale widget (slider):** Use `ttk.Scale` with `style.configure("TScale", troughcolor="#161B22")`. The fill color isn't natively configurable on ttk.Scale — use a `tk.Canvas` overlay or accept the default colored fill.

5. **Text widget for license key:** Use `tk.Text(height=3)` not `tk.Entry` — license keys are multi-line. Set `wrap="word"`.

## OG Image (Social Sharing)

**Dimensions:** 1200x630px
**Layout:**
```
+--------------------------------------------------+
|  #0D1117 background                              |
|                                                  |
|     [Logo 80x80]                                 |
|                                                  |
|     ContextPulse                                 |
|     Always-on context for AI agents              |
|                                                  |
|     pip install contextpulse-sight               |
|                                                  |
|     contextpulse.ai                              |
+--------------------------------------------------+
```
- Product name: Inter 700, 48px, `#E6EDF3`
- Tagline: Inter 400, 24px, `#8B949E`
- Install command: JetBrains Mono 400, 20px, `#12B5E0`, in a `#161B22` rounded box
- URL: Inter 500, 18px, `#00CC66`

**File:** Save as `site/og-image.png` (referenced in landing page meta tags)

## File Locations

| Asset | Path | Status |
|-------|------|--------|
| Logo concept | `~/Documents/nanobanana_generated/generated_1774132479357.png` | Generated, needs refinement |
| This spec | `brand/VISUAL_DESIGN_SPEC.md` | Complete |
| Tray icon code | `packages/screen/src/contextpulse_sight/icon.py` | Working, keep as-is |
| Color palette | `brand/colors.json` | Complete |
| Typography | `brand/typography.json` | Complete |
| OG image | `site/og-image.png` | Not yet created |
| Favicon | Inline SVG in index.html | Working |
