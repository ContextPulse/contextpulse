# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for ContextPulse — unified daemon (Sight + Voice + Touch)."""

import os
from pathlib import Path

block_cipher = None

# Paths — resolved dynamically so spec works on any machine
_spec_dir = Path(SPECPATH)
VENV_SP = _spec_dir / ".venv" / "Lib" / "site-packages"
PACKAGES = _spec_dir / "packages"

# Native binaries that PyInstaller can't auto-detect
binaries = []

# CTranslate2 — Whisper transcription engine (for Voice)
_ct2_dir = VENV_SP / "ctranslate2"
if _ct2_dir.exists():
    for dll in _ct2_dir.glob("*.dll"):
        binaries.append((str(dll), "ctranslate2"))
    for pyd in _ct2_dir.glob("*.pyd"):
        binaries.append((str(pyd), "ctranslate2"))

# PortAudio — audio capture (for Voice)
_pa_dir = VENV_SP / "_sounddevice_data" / "portaudio-binaries"
if _pa_dir.exists():
    for dll in _pa_dir.glob("*.dll"):
        binaries.append((str(dll), "_sounddevice_data/portaudio-binaries"))

# ONNX Runtime — used by Silero VAD (Voice) and RapidOCR (Sight)
_onnx_dir = VENV_SP / "onnxruntime" / "capi"
if _onnx_dir.exists():
    for f in _onnx_dir.glob("*.dll"):
        binaries.append((str(f), "onnxruntime/capi"))
    for f in _onnx_dir.glob("*.pyd"):
        binaries.append((str(f), "onnxruntime/capi"))

# Tokenizers (for huggingface model loading)
_tok = VENV_SP / "tokenizers"
if _tok.exists():
    for pyd in _tok.glob("*.pyd"):
        binaries.append((str(pyd), "tokenizers"))

# Data files
datas = []

# Silero VAD model (used by faster-whisper)
_vad = VENV_SP / "faster_whisper" / "assets" / "silero_vad_v6.onnx"
if _vad.exists():
    datas.append((str(_vad), "faster_whisper/assets"))

# RapidOCR models
_rocr = VENV_SP / "rapidocr_onnxruntime"
if _rocr.exists():
    for onnx in _rocr.rglob("*.onnx"):
        rel = onnx.relative_to(VENV_SP)
        datas.append((str(onnx), str(rel.parent)))

# Brand colors (if present)
_brand = _spec_dir / "brand"
if (_brand / "colors.json").exists():
    datas.append((str(_brand / "colors.json"), "brand"))

# Hidden imports — modules with lazy/conditional imports
hidden_imports = [
    # Core
    "contextpulse_core",
    "contextpulse_core.daemon",
    "contextpulse_core.spine",
    "contextpulse_core.spine.bus",
    "contextpulse_core.spine.events",
    "contextpulse_core.spine.module",
    "contextpulse_core.first_run",
    "contextpulse_core.settings",
    "contextpulse_core.license",
    "contextpulse_core.license_dialog",
    "contextpulse_core.config",
    "contextpulse_core.gui_theme",
    # Sight
    "contextpulse_sight",
    "contextpulse_sight.app",
    "contextpulse_sight.activity",
    "contextpulse_sight.buffer",
    "contextpulse_sight.capture",
    "contextpulse_sight.classifier",
    "contextpulse_sight.clipboard",
    "contextpulse_sight.config",
    "contextpulse_sight.events",
    "contextpulse_sight.icon",
    "contextpulse_sight.mcp_server",
    "contextpulse_sight.ocr_worker",
    "contextpulse_sight.privacy",
    "contextpulse_sight.redact",
    "contextpulse_sight.setup",
    "contextpulse_sight.sight_module",
    # Voice
    "contextpulse_voice",
    "contextpulse_voice.voice_module",
    "contextpulse_voice.recorder",
    "contextpulse_voice.transcriber",
    "contextpulse_voice.cleanup",
    "contextpulse_voice.vocabulary",
    "contextpulse_voice.paster",
    "contextpulse_voice.config",
    "contextpulse_voice.analyzer",
    "contextpulse_voice.model_manager",
    "contextpulse_voice.mcp_server",
    # Touch
    "contextpulse_touch",
    "contextpulse_touch.touch_module",
    "contextpulse_touch.burst_tracker",
    "contextpulse_touch.correction_detector",
    "contextpulse_touch.listeners",
    "contextpulse_touch.config",
    "contextpulse_touch.mcp_server",
    # Project
    "contextpulse_project",
    # Native deps with lazy imports
    "sounddevice",
    "_sounddevice_data",
    "ctranslate2",
    "faster_whisper",
    "onnxruntime",
    "tokenizers",
    "huggingface_hub",
    "rapidocr_onnxruntime",
    "mss",
    "numpy",
    "pyperclip",
    "pyautogui",
    # Platform backends
    "pystray._win32",
    "pynput.keyboard._win32",
    "pynput.mouse._win32",
    # Cryptography (license verification)
    "cryptography",
    "cryptography.hazmat.primitives.asymmetric.ed25519",
    # MCP
    "mcp",
    "mcp.server",
    "mcp.server.fastmcp",
    # Anthropic (optional cloud cleanup)
    "anthropic",
]

a = Analysis(
    [str(PACKAGES / "core" / "src" / "contextpulse_core" / "daemon.py")],
    pathex=[
        str(PACKAGES / "core" / "src"),
        str(PACKAGES / "screen" / "src"),
        str(PACKAGES / "voice" / "src"),
        str(PACKAGES / "touch" / "src"),
        str(PACKAGES / "project" / "src"),
        str(PACKAGES / "memory" / "src"),
        str(PACKAGES / "agent" / "src"),
    ],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Exclude CUDA libs — CPU only
        "torch",
        "torchaudio",
        "torchvision",
        "tensorrt",
        # Exclude unused GUI frameworks (keep tkinter)
        "matplotlib",
        "PyQt5",
        "PyQt6",
        # Exclude test/dev tools
        "pytest",
        "py",
        "ruff",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ContextPulse",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX can break native DLLs
    console=False,  # No console — runs in system tray
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/contextpulse.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="ContextPulse",
)
