@echo off
REM THROWAWAY - Phase 0 wedge-probe consolidator wrapper.
REM Plain OS scheduled batch (NOT a CC-Desktop/Fable task). Delete at Phase 1.
REM Runs every 6h with a 6h window: a full day exceeds the 1500-event cap, so
REM 4x/day short windows cover the whole day instead of the most recent ~1.75h.
set CP=C:\Users\david\Projects\ContextPulse
"%CP%\.venv\Scripts\python.exe" "%CP%\scripts\probe_consolidator.py" --hours 6 >> "%CP%\logs\probe_consolidator.log" 2>&1
