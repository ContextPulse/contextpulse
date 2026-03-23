# Post-Restart Test Script for Claude Code

Run these after restarting Claude Code to verify all ContextPulse Sight Phase 2.0 features work through the MCP interface.

## Instructions
Copy each test below into Claude Code as a prompt. Check the expected result.

---

## Test 1: Buffer Status (daemon health)
**Prompt:** "Use get_buffer_status to check if the ContextPulse Sight daemon is running"

**Expected:** Shows frame count across 2 monitors, span of history, latest frame age < 30s

**Fail if:** "Buffer is empty" or parse errors on filenames

---

## Test 2: Per-Monitor Screenshots (multi-screen fix)
**Prompt:** "Take a screenshot of all my monitors using get_screenshot with mode all"

**Expected:** Returns 2 SEPARATE images labeled "Monitor 0" and "Monitor 1", each readable at 1280x720

**Fail if:** Returns one stitched image, or text is illegible

---

## Test 3: Single Monitor Screenshot
**Prompt:** "Take a screenshot of just monitor 0 using get_screenshot with mode monitor and monitor_index 0"

**Expected:** Returns one clear image of the left monitor

**Fail if:** Error about monitor index, or returns wrong monitor

---

## Test 4: OCR Text Extraction
**Prompt:** "Use get_screen_text to read what's on my screen right now"

**Expected:** Returns OCR text with char count, confidence, and timing. Should capture terminal/code text.

**Fail if:** Returns "blocked" or crashes

---

## Test 5: Recent Buffer Frames
**Prompt:** "Use get_recent to show me the last 3 frames from the buffer"

**Expected:** Returns frames with monitor labels like [m0], [m1]

**Fail if:** Empty result (daemon not capturing) or parse errors

---

## Test 6: Activity Summary
**Prompt:** "Use get_activity_summary to show me what apps I've been using in the last hour"

**Expected:** Shows app breakdown (e.g., claude.exe, chrome.exe), capture count, time range

**Fail if:** "No activity recorded" after daemon has been running for several minutes

---

## Test 7: Search History
**Prompt:** "Use search_history to search for 'claude' in the last 60 minutes"

**Expected:** Returns timestamped matches with app name and window title

**Fail if:** "No results" when Claude Code has definitely been the foreground app

---

## Test 8: Context Replay
**Prompt:** "Use get_context_at to show me what was on my screen about 2 minutes ago"

**Expected:** Returns metadata (app, window title, monitor) plus the frame image if available

**Fail if:** "No frame found" when daemon has been running

---

## Test 9: Smart Storage Verification
**Prompt:** "Run this command: ls ~/screenshots/buffer/ | head -20"

**Expected:** Files named like `1774126203136_m0.jpg`, `1774126203160_m1.jpg`. May also see `.txt` files (text-only frames from smart storage).

**Fail if:** Old filename format without `_m` prefix

---

## Test 10: Activity Database
**Prompt:** "Run this command: sqlite3 ~/screenshots/activity.db 'SELECT COUNT(*) as total, COUNT(DISTINCT app_name) as apps FROM activity'"

**Expected:** Shows total records > 0 and distinct app count >= 1

**Fail if:** Database doesn't exist or zero records after daemon running

---

## Test 11: Event-Driven Capture (manual)
Switch to a different app (e.g., Chrome), wait 3 seconds, switch back to Claude Code. Then:

**Prompt:** "Use get_activity_summary for the last 0.1 hours"

**Expected:** Shows both apps (chrome.exe and claude.exe) — event-driven capture caught the window switch

---

## Test 12: Full Automated Verification
**Prompt:** "Run this: cd ~/Projects/ContextPulse && .venv/Scripts/python packages/screen/scripts/verify_live.py"

**Expected:** 42 passed, 0 failed. Warnings about mcp_server import and text-only frames are OK.

---

## Quick Smoke Test (if short on time)
Just run tests 1, 2, 6, and 12. If those pass, everything is working.
