@echo off
rem ContextPulse Build Script — PyInstaller + Inno Setup
rem Produces: installer_output\ContextPulseSetup-0.1.0.exe

echo === ContextPulse Build ===
echo.

rem Step 1: Kill any running instances
echo [1/4] Stopping running instances...
taskkill /f /im ContextPulse.exe 2>nul
taskkill /f /im pythonw.exe /fi "WINDOWTITLE eq ContextPulse*" 2>nul

rem Step 2: Clean previous build
echo [2/4] Cleaning previous build...
if exist dist\ContextPulse rmdir /s /q dist\ContextPulse
if exist build\ContextPulse rmdir /s /q build\ContextPulse

rem Step 3: PyInstaller
echo [3/4] Building with PyInstaller...
.venv\Scripts\python -m PyInstaller contextpulse.spec --noconfirm
if errorlevel 1 (
    echo ERROR: PyInstaller build failed
    exit /b 1
)

echo.
echo PyInstaller output: dist\ContextPulse\
echo.

rem Step 4: Inno Setup (if available) — check common install locations
set ISCC=
for %%P in (
    "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
    "%ProgramFiles%\Inno Setup 6\ISCC.exe"
    "%LocalAppData%\Programs\Inno Setup 6\ISCC.exe"
) do (
    if exist %%P set ISCC=%%P
)
rem Also check PATH
if not defined ISCC (
    where ISCC.exe >nul 2>&1 && set ISCC=ISCC.exe
)
if defined ISCC (
    echo [4/4] Building installer with Inno Setup...
    %ISCC% installer.iss
    if errorlevel 1 (
        echo ERROR: Inno Setup build failed
        exit /b 1
    )
    echo.
    echo === BUILD COMPLETE ===
    echo Installer: installer_output\ContextPulseSetup-0.1.0.exe
) else (
    echo [4/4] Skipping installer — Inno Setup not found at %ISCC%
    echo You can run dist\ContextPulse\ContextPulse.exe directly.
    echo.
    echo === BUILD COMPLETE (no installer) ===
)
