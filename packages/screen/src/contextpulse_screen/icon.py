"""Programmatic icon generation for system tray."""

from PIL import Image, ImageDraw


def create_icon(color: str = "#00CC66", size: int = 64) -> Image.Image:
    """Create a simple camera/eye icon for the system tray.

    Args:
        color: Fill color. Green=active, yellow=paused.
        size: Icon dimensions (square).
    """
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Dark background circle
    margin = size // 8
    draw.ellipse(
        [margin, margin, size - margin, size - margin],
        fill="#1a1a2e",
        outline=color,
        width=max(2, size // 16),
    )

    # Inner "lens" circle
    center = size // 2
    radius = size // 5
    draw.ellipse(
        [center - radius, center - radius, center + radius, center + radius],
        fill=color,
    )

    return img
