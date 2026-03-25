# Voiceasy Long-Running Process Analysis

**Date:** 2026-03-25
**Context:** Voiceasy (voice dictation app) exhibits performance degradation after ~4-8 hours of continuous use. This analysis documents root causes and fixes applied, serving as a reference for ContextPulse Voice module design.

## Symptom

Voiceasy works well in the morning but degrades by afternoon — slower transcriptions, increased latency, occasional unresponsiveness. The app runs as a single long-lived process (system tray + global hotkey listener).

---

## Root Causes Found

### 1. CRITICAL: Unbounded Thread Spawning

**File:** `app.py` — `_on_press()`, `_on_release()`, `_fix_last()`

Every hotkey press spawns a new `threading.Thread` with no pool or semaphore. Over a full workday (100+ dictations), this creates 100+ threads. If any thread hangs (e.g., 15-second API timeout in cleanup proxy), threads accumulate and compete for CPU, memory, and file descriptors.

**Impact:** Thread count grows linearly with dictation count. No upper bound. Hanging API calls make it worse.

**Fix applied:** Replaced bare `threading.Thread` calls with a `ThreadPoolExecutor(max_workers=3)`. Extra dictation requests queue instead of spawning new threads. Pool is shut down on app quit.

**ContextPulse lesson:** Any long-running module that spawns work threads MUST use a bounded pool or semaphore. Never use bare `threading.Thread` in a daemon process.

---

### 2. HIGH: Recorder Frame Buffer Not Cleaned on Error

**File:** `recorder.py` — `stop()`

The `_frames` list (numpy arrays from mic callback) was only cleared on the next `start()` call. If the transcription pipeline raised an exception after `stop()` returned, the frames stayed in memory until the next recording cycle. With 16kHz audio at ~32KB/sec, a 30-second recording holds ~960KB that leaks on error.

**Fix applied:** Wrapped `stop()` in `try/finally` so `self._frames = []` always executes, even if WAV conversion fails.

**ContextPulse lesson:** Any buffer that accumulates data from a callback (audio, screen frames, input events) must be cleared in a `finally` block, not just on the happy path.

---

### 3. HIGH: Analyzer Thread Pile-Up

**File:** `history.py` — `_maybe_auto_analyze()`

The auto-analyzer spawns a background thread every 100 dictations. No guard prevented a second analyzer from launching while the first was still running (e.g., slow Anthropic API call). Each analyzer loads the entire history JSONL file into memory.

**Fix applied:** Added a `threading.Lock` + `_analyzer_running` flag. If an analyzer is already in progress, subsequent requests are silently skipped. The flag is always cleared in a `finally` block.

**ContextPulse lesson:** Background analysis/maintenance tasks must be guarded with a lock or "already running" flag. Especially relevant for ContextPulse's `analyze_with_llm()` and any future EventBus-triggered analysis.

---

### 4. MEDIUM: Zombie PowerShell Notification Processes

**File:** `notifications.py` — `notify()`

Each toast notification spawned a `subprocess.Popen` for PowerShell that was never waited on. The Popen object went out of scope without cleanup, leaving zombie processes. Over a day with 50+ dictations, this means 50+ orphaned PowerShell processes.

**Fix applied:** Added a `_reap_process()` function that runs `proc.wait(timeout=10)` in a daemon thread. If the process doesn't terminate within 10s, it's killed.

**ContextPulse lesson:** Any subprocess spawned by a long-running process must be explicitly waited on. Fire-and-forget `Popen` calls are a zombie factory on Windows.

---

### 5. MEDIUM: History Rotation Checked Every Dictation

**File:** `history.py` — `append_history()`

`_rotate_if_needed()` was called after every single dictation, which meant `stat()`-ing a 10MB file on every call. When rotation did trigger, it loaded the entire file into memory, split into lines, kept the last 5000, and wrote it back — a ~30MB memory spike.

**Fix applied:** Added a `_dictation_counter` that only checks rotation every 25 dictations. The file size check is an O(1) stat call, but doing it 25x less often reduces I/O pressure and avoids unnecessary memory spikes.

**ContextPulse lesson:** Maintenance operations (rotation, compaction, cleanup) should be amortized, not run on every event. Use a counter or timer to batch checks.

---

## Issues Identified But Not Fixed (Low Priority)

### 6. Overlay PhotoImage Cache (LOW)

The `RecordingOverlay` allocates 16 PIL `PhotoImage` objects for animation frames. These are allocated once and reused — only a problem if the overlay window is recreated, which is an edge case.

### 7. Global Mutable State (LOW)

Several modules use module-level globals (`_compiled_patterns`, `_last_paste_time`, `_settings_open`). These are never explicitly cleared but don't grow unboundedly — just stale state risk.

### 8. No Log File Sink (LOW)

Logs go to stderr only. In the frozen .exe with no console, logs are lost entirely. No unbounded growth, but no observability either.

---

## Architecture Patterns to Carry to ContextPulse Voice

1. **Bounded thread pools** — Any work spawned from an event handler must go through a pool with a fixed max_workers.
2. **Buffer cleanup in finally blocks** — Audio frames, screen captures, input event buffers must always be freed.
3. **Single-instance background tasks** — Analysis, maintenance, and LLM calls must be guarded against concurrent execution.
4. **Subprocess lifecycle management** — Always reap child processes. Use `proc.wait()` or `proc.communicate()`.
5. **Amortized maintenance** — Don't rotate/compact/analyze on every event. Use counters or timers.
6. **Model loading** — Whisper model loaded once in `__init__` is correct. Never reload per-transcription.
7. **API timeout cascades** — A 15-second timeout on one cleanup call blocks the thread. With unbounded threads, N timeouts = N blocked threads = system freeze. The thread pool caps this at max_workers blocked threads.

---

## Test Results After Fixes

All 72 existing tests pass (1 skipped). No behavioral changes — only resource lifecycle improvements.
