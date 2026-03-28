"""Floating recording overlay — animated icon + status text."""

import logging
import math
import threading
import tkinter as tk
from io import BytesIO

from PIL import Image, ImageDraw, ImageTk

logger = logging.getLogger(__name__)

# Animation settings
FRAME_COUNT = 16
FRAME_DELAY_MS = 80  # ~12.5 fps
ICON_SIZE = 28


def _create_recording_frame(size: int, phase: float) -> Image.Image:
    """Create one frame of the animated recording icon."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    cx, cy = size // 2, size // 2

    # Microphone body
    mic_w = int(size * 0.16)
    mic_h = int(size * 0.30)
    mic_top = cy - int(size * 0.18)
    mic_radius = mic_w // 2

    # Pulsing glow behind mic
    glow_alpha = int(40 + 30 * math.sin(phase * 2))
    glow_size = int(size * 0.04)
    draw.rounded_rectangle(
        [cx - mic_w - glow_size, mic_top - glow_size,
         cx + mic_w + glow_size, mic_top + mic_h + glow_size],
        radius=mic_radius + glow_size,
        fill=(239, 68, 68, glow_alpha),  # Red glow
    )

    draw.rounded_rectangle(
        [cx - mic_w, mic_top, cx + mic_w, mic_top + mic_h],
        radius=mic_radius,
        fill=(239, 68, 68, 255),  # Red mic when recording
    )

    # Arc
    arc_w = int(size * 0.24)
    arc_top = mic_top + int(mic_h * 0.3)
    arc_bottom = mic_top + mic_h + int(size * 0.08)
    lw = max(int(size * 0.04), 2)

    draw.arc(
        [cx - arc_w, arc_top, cx + arc_w, arc_bottom + int(size * 0.04)],
        start=0, end=180,
        fill=(239, 68, 68, 255),
        width=lw,
    )

    # Stand
    stand_top = arc_bottom + int(size * 0.01)
    stand_bottom = stand_top + int(size * 0.08)
    draw.line([(cx, stand_top), (cx, stand_bottom)], fill=(239, 68, 68, 255), width=lw)
    base_w = int(size * 0.12)
    draw.line([(cx - base_w, stand_bottom), (cx + base_w, stand_bottom)],
              fill=(239, 68, 68, 255), width=lw)

    # Animated sound waves
    wave_cx = cx + int(size * 0.20)
    wave_cy = mic_top + mic_h // 2
    for i in range(3):
        wave_offset = (phase + i * 0.8) % (math.pi * 2)
        progress = (math.sin(wave_offset) + 1) / 2
        alpha = int(220 * (1 - progress * 0.7))
        r = int(size * (0.06 + 0.10 * progress))
        if alpha > 30:
            draw.arc(
                [wave_cx - r, wave_cy - r, wave_cx + r, wave_cy + r],
                start=-40, end=40,
                fill=(239, 68, 68, alpha),
                width=max(int(size * 0.03), 2),
            )

    return img


def _create_ready_frame(size: int) -> Image.Image:
    """Create the static ready icon (green mic)."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    cx, cy = size // 2, size // 2
    mic_w = int(size * 0.16)
    mic_h = int(size * 0.30)
    mic_top = cy - int(size * 0.18)
    mic_radius = mic_w // 2

    draw.rounded_rectangle(
        [cx - mic_w, mic_top, cx + mic_w, mic_top + mic_h],
        radius=mic_radius,
        fill=(74, 222, 128, 255),
    )

    arc_w = int(size * 0.24)
    arc_top = mic_top + int(mic_h * 0.3)
    arc_bottom = mic_top + mic_h + int(size * 0.08)
    lw = max(int(size * 0.04), 2)

    draw.arc(
        [cx - arc_w, arc_top, cx + arc_w, arc_bottom + int(size * 0.04)],
        start=0, end=180, fill=(74, 222, 128, 255), width=lw,
    )

    stand_top = arc_bottom + int(size * 0.01)
    stand_bottom = stand_top + int(size * 0.08)
    draw.line([(cx, stand_top), (cx, stand_bottom)], fill=(74, 222, 128, 255), width=lw)
    base_w = int(size * 0.12)
    draw.line([(cx - base_w, stand_bottom), (cx + base_w, stand_bottom)],
              fill=(74, 222, 128, 255), width=lw)

    return img


class RecordingOverlay:
    """Floating pill with animated mic icon during recording."""

    def __init__(self) -> None:
        self._root: tk.Tk | None = None
        self._visible = False
        self._animating = False
        self._frame_idx = 0
        self._recording_frames: list[ImageTk.PhotoImage] = []
        self._ready_frame: ImageTk.PhotoImage | None = None
        self._hide_after_id: str | None = None  # Track pending hide timer
        self._ready_event = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready_event.wait(timeout=3)

    def _run(self) -> None:
        try:
            self._root = tk.Tk()
            self._root.overrideredirect(True)
            self._root.attributes("-topmost", True)
            self._root.attributes("-alpha", 0.65)
            self._root.configure(bg="#1a1a2e")

            screen_w = self._root.winfo_screenwidth()
            w, h = 160, 36
            x = (screen_w - w) // 2
            y = 18
            self._root.geometry(f"{w}x{h}+{x}+{y}")

            # Compact layout
            self._container = tk.Frame(self._root, bg="#1a1a2e", padx=4, pady=2)
            self._container.pack(fill="both", expand=True)

            # Icon label (will hold animated frames)
            self._icon_label = tk.Label(self._container, bg="#1a1a2e")
            self._icon_label.pack(side="left", padx=(2, 4))

            # Text label
            self._text_label = tk.Label(
                self._container, text="Recording...",
                font=("Segoe UI", 10, "bold"),
                fg="#ef4444", bg="#1a1a2e",
            )
            self._text_label.pack(side="left")

            # Pre-render animation frames
            bg_color = (26, 26, 46)
            for i in range(FRAME_COUNT):
                phase = (i / FRAME_COUNT) * math.pi * 2
                frame = _create_recording_frame(ICON_SIZE, phase)
                # Composite onto bg
                bg = Image.new("RGB", (ICON_SIZE, ICON_SIZE), bg_color)
                bg.paste(frame, mask=frame.split()[3])
                self._recording_frames.append(ImageTk.PhotoImage(bg))

            # Ready frame
            ready_img = _create_ready_frame(ICON_SIZE)
            bg = Image.new("RGB", (ICON_SIZE, ICON_SIZE), bg_color)
            bg.paste(ready_img, mask=ready_img.split()[3])
            self._ready_frame = ImageTk.PhotoImage(bg)

            self._root.withdraw()
            self._ready_event.set()
            self._root.mainloop()
        except Exception:
            logger.debug("Overlay failed to initialize")
            self._ready_event.set()

    def _animate(self) -> None:
        """Advance to next animation frame."""
        if not self._animating or not self._root:
            return
        self._frame_idx = (self._frame_idx + 1) % FRAME_COUNT
        self._icon_label.configure(image=self._recording_frames[self._frame_idx])
        self._root.after(FRAME_DELAY_MS, self._animate)

    @staticmethod
    def _get_caret_position() -> tuple[int, int] | None:
        """Get the text caret (blinking cursor) position via platform provider."""
        try:
            from contextpulse_core.platform import get_platform_provider
            return get_platform_provider().get_caret_position()
        except Exception:
            return None

    def _position_near_cursor(self) -> None:
        """Move overlay above the text caret, falling back to mouse pointer."""
        try:
            caret = self._get_caret_position()
            if caret:
                cx, cy = caret
            else:
                cx, cy = self._root.winfo_pointerxy()
            w = 160
            x = cx - w // 2
            y = cy - 80
            self._root.geometry(f"+{x}+{y}")
        except Exception:
            pass

    def show_recording(self) -> None:
        if self._root is None:
            return
        try:
            self._root.after(0, self._show_recording_ui)
        except Exception:
            pass

    def _cancel_pending_hide(self) -> None:
        """Cancel any scheduled hide so it doesn't fire during a new recording."""
        if self._hide_after_id is not None:
            self._root.after_cancel(self._hide_after_id)
            self._hide_after_id = None

    def _show_recording_ui(self) -> None:
        self._cancel_pending_hide()
        self._frame_idx = 0
        self._icon_label.configure(image=self._recording_frames[0])
        self._text_label.configure(text="Recording...", fg="#ef4444")
        self._animating = True
        self._position_near_cursor()
        self._root.deiconify()
        self._visible = True
        self._animate()

    def show_transcribing(self) -> None:
        if self._root is None:
            return
        try:
            self._root.after(0, self._show_transcribing_ui)
        except Exception:
            pass

    def _show_transcribing_ui(self) -> None:
        self._animating = False
        self._icon_label.configure(image=self._ready_frame)
        self._text_label.configure(text="Transcribing...", fg="#facc15")
        self._root.deiconify()
        self._visible = True

    def show_cleaning(self) -> None:
        if self._root is None:
            return
        try:
            self._root.after(0, self._show_cleaning_ui)
        except Exception:
            pass

    def _show_cleaning_ui(self) -> None:
        self._animating = False
        self._icon_label.configure(image=self._ready_frame)
        self._text_label.configure(text="Cleaning up...", fg="#60a5fa")
        self._root.deiconify()
        self._visible = True

    def show_ready(self) -> None:
        if self._root is None:
            return
        try:
            self._root.after(0, self._show_ready_ui)
        except Exception:
            pass

    def _show_ready_ui(self) -> None:
        self._animating = False
        self._icon_label.configure(image=self._ready_frame)
        self._text_label.configure(text="Ready", fg="#4ade80")
        self._root.deiconify()
        self._visible = True
        self._hide_after_id = self._root.after(1500, self.hide)

    def hide(self) -> None:
        if self._root is None:
            return
        try:
            self._root.after(0, self._hide_ui)
        except Exception:
            pass

    def _hide_ui(self) -> None:
        self._animating = False
        self._root.withdraw()
        self._visible = False
