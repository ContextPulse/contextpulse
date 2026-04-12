# ContextPulse Sight — User Test Plan

Performance optimization release: monitor summary, smart mode, JPEG cache, DXcam backend, adaptive region.

**Commit:** `820274a`
**Date:** 2026-04-08

---

## Prerequisites

- ContextPulse Sight daemon running (`get_buffer_status()` returns frames)
- Two monitors connected
- Have at least 2 different apps open on different monitors

---

## Test 1: Monitor Summary (get_monitor_summary)

**Goal:** Verify Claude gets accurate, lightweight metadata about each monitor.

**Steps:**
1. Open VS Code on monitor 0, Chrome on monitor 1
2. Ask Claude: "What's on my screens right now?"
3. Claude should call `get_monitor_summary()` — NOT `get_screenshot()`

**Verify:**
- [ ] Response lists both monitors with correct dimensions
- [ ] Active monitor (where cursor is) marked `[ACTIVE]`
- [ ] App names are correct (e.g., `Code.exe`, `chrome.exe`)
- [ ] Window titles are accurate
- [ ] Change level makes sense (static vs minor/major change)
- [ ] Response is fast (<1 second)
- [ ] Token cost is low (~50-100 tokens, not ~1,200)

**Edge cases:**
- [ ] Move cursor to the other monitor, ask again — `[ACTIVE]` marker moves
- [ ] Minimize all windows on one monitor — should show `explorer.exe` / Desktop

---

## Test 2: Smart Capture Mode

**Goal:** Verify smart mode skips unchanged monitors and saves tokens.

**Steps:**
1. Leave one monitor completely static (don't touch it for 30+ seconds)
2. Actively work on the other monitor (switch apps, scroll, type)
3. Ask Claude: "Show me what changed on my screens"
4. Claude should call `get_screenshot(mode="smart")`

**Verify:**
- [ ] Changed monitor returns a full screenshot image
- [ ] Static monitor returns text-only summary (no image)
- [ ] Text summary includes app name and "unchanged" label
- [ ] If BOTH monitors are static (idle 30s+), returns "All monitors are static" message

**Compare token cost:**
- [ ] `mode="smart"` with 1 static monitor uses roughly half the tokens of `mode="all"`

---

## Test 3: Targeted Monitor Capture

**Goal:** Verify Claude can capture a specific monitor after reading the summary.

**Steps:**
1. Ask Claude: "What's on monitor 0?"
2. Claude should call `get_monitor_summary()` first, then `get_screenshot(mode="monitor", monitor_index=0)`

**Verify:**
- [ ] Returns screenshot of the correct monitor (not the other one)
- [ ] Image is readable at 1280x720 downscale
- [ ] Text on screen is legible (code, browser content, etc.)

---

## Test 4: JPEG Cache (Speed)

**Goal:** Verify repeated screenshot requests are fast.

**Steps:**
1. Ask Claude: "Take a screenshot of my active monitor"
2. Immediately ask again: "Take another screenshot"

**Verify:**
- [ ] Second request is noticeably faster (cache hit within 2s window)
- [ ] Check daemon logs for "serving from cache" message:
  ```powershell
  Get-Content ~/screenshots/contextpulse_sight.log -Tail 20 | Select-String "cache"
  ```

---

## Test 5: Adaptive Region Capture

**Goal:** Verify region capture auto-sizes to the active window.

**Steps:**
1. Open a small dialog/window (e.g., Settings, Calculator, a small terminal)
2. Make sure it's the focused/foreground window
3. Ask Claude: "Capture the region around my focused window"
4. Claude should call `get_screenshot(mode="region")`

**Verify:**
- [ ] Captured region is roughly the size of the focused window + padding (not the full monitor)
- [ ] Window content is centered in the capture
- [ ] Surrounding context (desktop, other windows) visible as padding

**Fallback test:**
- [ ] Click on the desktop (no foreground window) then request region capture
- [ ] Should fall back to 800x600 cursor-centered capture

---

## Test 6: Privacy Blocklist

**Goal:** Verify blocked windows don't leak titles in the new tools.

**Steps:**
1. Add a test pattern to the blocklist:
   ```powershell
   $env:CONTEXTPULSE_BLOCKLIST = "password,banking"
   ```
2. Open a window with "password" in the title (e.g., rename a Notepad window)
3. Call `get_monitor_summary()`

**Verify:**
- [ ] Blocked window shows `[BLOCKED]` for both title AND app name
- [ ] Real title/app never appears in the response

---

## Test 7: Full Workflow (End-to-End)

**Goal:** Simulate the intended usage pattern — summary first, then targeted capture.

**Steps:**
1. Have a complex multi-monitor setup (IDE + browser + terminal + chat)
2. Ask Claude: "I'm getting an error in my terminal, can you see it?"
3. Observe Claude's tool call sequence

**Expected behavior:**
1. Claude calls `get_monitor_summary()` to find which monitor has the terminal
2. Claude calls `get_screenshot(mode="monitor", monitor_index=N)` for that monitor
3. OR Claude calls `get_screen_text()` if the summary suggests text-heavy content

**Verify:**
- [ ] Claude makes informed decisions about which monitor to capture
- [ ] Total token cost is lower than the old approach (capturing all monitors every time)
- [ ] Response time feels faster than before

---

## Test 8: DXcam Backend (Optional)

**Only if dxcam is installed:** `pip install dxcam`

**Steps:**
1. Install dxcam: `pip install "dxcam>=0.3.0"`
2. Restart the Sight daemon
3. Check logs for "Using DXcam capture backend"
4. Run any screenshot capture

**Verify:**
- [ ] Logs show "Using DXcam capture backend (Desktop Duplication API)"
- [ ] Screenshots still render correctly (no color channel swap, correct orientation)
- [ ] Capture feels faster

**Fallback test:**
- [ ] Uninstall dxcam: `pip uninstall dxcam -y`
- [ ] Restart daemon — logs should show "Using mss capture backend (GDI)"
- [ ] Everything still works

---

## Known Limitations

- **DXcam not auto-installed:** It's an optional dependency (`pip install contextpulse-sight[windows]`). Without it, mss is used (still works, just slower).
- **Smart mode depends on daemon activity data:** If the daemon just started, all monitors show as "unknown" and smart mode includes all of them. After ~30 seconds of daemon runtime, change detection kicks in.
- **Adaptive region requires Windows:** `get_active_window_rect()` uses Win32 DWM API. On macOS/Linux, region capture falls back to 800x600 cursor-centered.
- **Cache TTL is 2 seconds:** Rapid successive calls within 2s return the same frame. If you need a guaranteed fresh capture, wait 2 seconds.
