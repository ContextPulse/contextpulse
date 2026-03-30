# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for ContextPulse macOS .app bundle.

Build with:
    pyinstaller packaging/macos/contextpulse_macos.spec
"""

import sys
from pathlib import Path

block_cipher = None

# Hidden imports — all wrapped in try/except at runtime, but PyInstaller
# needs them listed so it bundles the binaries.
hidden_imports = [
    # PyObjC frameworks
    "objc",
    "Foundation",
    "AppKit",
    "Cocoa",
    "Quartz",
    "CoreFoundation",
    "CoreGraphics",
    "CoreText",
    "Vision",
    "ApplicationServices",
    "pyobjc_framework_Cocoa",
    "pyobjc_framework_Quartz",
    "pyobjc_framework_Vision",
    "pyobjc_framework_ApplicationServices",
    # Menu-bar helper
    "rumps",
    # MLX Whisper (Apple Silicon inference)
    "mlx_whisper",
    "mlx",
    # ContextPulse packages
    "contextpulse_core",
    "contextpulse_sight",
    "contextpulse_voice",
    "contextpulse_touch",
]

a = Analysis(
    ["../../packages/core/src/contextpulse_core/daemon.py"],
    pathex=[
        "../../packages/core/src",
        "../../packages/screen/src",
        "../../packages/voice/src",
        "../../packages/touch/src",
    ],
    binaries=[],
    datas=[],
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
    upx=True,
    console=False,
    target_arch="universal2",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="ContextPulse",
)

app = BUNDLE(
    coll,
    name="ContextPulse.app",
    icon="../../assets/contextpulse.icns",
    bundle_identifier="com.jerardventures.contextpulse",
    info_plist={
        "CFBundleDisplayName": "ContextPulse",
        "CFBundleShortVersionString": "0.1.0",
        "CFBundleVersion": "0.1.0",
        "LSMinimumSystemVersion": "13.0",
        "LSUIElement": True,
        "NSMicrophoneUsageDescription": (
            "ContextPulse uses the microphone for voice transcription "
            "to provide real-time context to your AI coding assistant."
        ),
        "NSScreenCaptureUsageDescription": (
            "ContextPulse captures screen content so your AI coding "
            "assistant can see what you see and provide better help."
        ),
        "NSHighResolutionCapable": True,
    },
)
