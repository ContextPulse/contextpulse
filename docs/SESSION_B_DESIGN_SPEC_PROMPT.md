# Prompt: Apply Visual Design Spec to Productization Code

Read `C:\Users\david\projects\ContextPulse\brand\VISUAL_DESIGN_SPEC.md` — it contains the complete visual design spec for all tkinter UI: splash screen, welcome dialog, settings panel, license dialog, and tray icon states.

Apply this spec to the productization code you're building:

1. **gui_theme.py** — Use the color palette, typography, and ttk style configuration from the spec. The "Tkinter Implementation Notes" section has exact code snippets.

2. **launcher.py (splash)** — Follow the splash screen layout exactly: 400x250, centered, no title bar, logo + name + tagline + progress bar.

3. **first_run.py (welcome)** — Follow the welcome dialog layout: 500x400, hotkey table in a surface-colored box, "Get Started" accent button.

4. **settings.py** — Follow the settings panel layout: 550x600, LabelFrame sections for Capture/Hotkeys/Privacy/License, dark-themed inputs, accent Save button.

5. **license_dialog.py** — Follow the license dialog layout: 500x400, state variations for trial/expired/licensed/expired-license.

6. **icon.py** — Add Error state (`#F85149`) alongside existing Active and Paused states.

Also review:
- Logo concept at `C:\Users\david\Documents\nanobanana_generated\generated_1774132479357.png`
- The CRITICAL note about never creating multiple `tk.Tk()` instances

Do NOT modify files in `site/` or `docs/` — only `packages/core/` and `packages/screen/`.
