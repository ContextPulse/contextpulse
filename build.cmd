@echo off
rem ContextPulse Windows build: PyInstaller + Inno Setup.
rem Produces installer_output\ContextPulseSetup-<version>.exe and prints its SHA-256.
rem The work is in scripts\build_windows_installer.ps1. This no longer kills
rem running ContextPulse or pythonw processes: building does not need to.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\build_windows_installer.ps1" %*
exit /b %errorlevel%
