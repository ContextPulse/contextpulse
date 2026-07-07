@echo off
REM THROWAWAY - Phase 0 wedge-probe nightly consolidator wrapper.
REM Plain OS scheduled batch (NOT a CC-Desktop/Fable task). Delete at Phase 1.
set CP=C:\Users\david\Projects\ContextPulse
"%CP%\.venv\Scripts\python.exe" "%CP%\scripts\probe_consolidator.py" --hours 24 >> "%CP%\logs\probe_consolidator.log" 2>&1
