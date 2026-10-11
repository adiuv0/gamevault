"""The app icon, drawn in code so there's no image file to lose."""

from __future__ import annotations

from functools import lru_cache

from PIL import Image, ImageDraw

ACCENT = (88, 166, 255)
ACCENT_DARK = (31, 111, 235)

BADGES = {
    "idle": (63, 185, 80),  # green
    "syncing": (88, 166, 255),  # blue
    "ready": (88, 166, 255),
    "paused": (210, 153, 34),  # amber
    "offline": (210, 153, 34),
    "signed_out": (248, 81, 73),  # red
    "error": (248, 81, 73),
    "setup": (139, 148, 158),  # grey
}


def _base(size: int) -> Image.Image:
    scale = 4  # draw big, shrink for smooth edges
    s = size * scale
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # Vertical gradient on a rounded square.
    gradient = Image.new("RGBA", (s, s))
    gdraw = ImageDraw.Draw(gradient)
    for y in range(s):
        t = y / max(1, s - 1)
        color = tuple(round(a + (b - a) * t) for a, b in zip(ACCENT, ACCENT_DARK))
        gdraw.line([(0, y), (s, y)], fill=(*color, 255))
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, s - 1, s - 1], radius=int(s * 0.22), fill=255)
    img.paste(gradient, (0, 0), mask)

    white = (255, 255, 255, 255)
    w = max(scale, int(s * 0.085))
    cx = s // 2
    # Upload arrow.
    top, shaft_bottom = int(s * 0.2), int(s * 0.6)
    draw.line([(cx, top + w), (cx, shaft_bottom)], fill=white, width=w)
    head = int(s * 0.17)
    draw.line([(cx - head, top + head), (cx, top)], fill=white, width=w, joint="curve")
    draw.line([(cx + head, top + head), (cx, top)], fill=white, width=w, joint="curve")
    draw.ellipse([cx - w // 2, top - w // 2, cx + w // 2, top + w // 2], fill=white)
    # Tray under it.
    left, right, tray_top, tray_bottom = int(s * 0.24), int(s * 0.76), int(s * 0.58), int(s * 0.78)
    draw.line([(left, tray_top), (left, tray_bottom), (right, tray_bottom), (right, tray_top)],
              fill=white, width=w, joint="curve")
    for x, y in ((left, tray_top), (right, tray_top), (left, tray_bottom), (right, tray_bottom)):
        draw.ellipse([x - w // 2, y - w // 2, x + w // 2, y + w // 2], fill=white)
    return img.resize((size, size), Image.LANCZOS)


@lru_cache(maxsize=32)
def app_icon(size: int = 256, status: str | None = None) -> Image.Image:
    img = _base(size)
    color = BADGES.get(status or "")
    if color is None:
        return img
    scale = 4
    s = size * scale
    overlay = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    r = int(s * 0.25)
    cx = cy = s - r - 1
    ring = max(scale, int(s * 0.05))
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(13, 17, 23, 255))
    draw.ellipse([cx - r + ring, cy - r + ring, cx + r - ring, cy + r - ring], fill=(*color, 255))
    return Image.alpha_composite(img, overlay.resize((size, size), Image.LANCZOS))


def save_ico(path: str) -> None:
    sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
    app_icon(256).save(path, format="ICO", sizes=[(n, n) for n in sizes],
                       append_images=[app_icon(n) for n in sizes[:-1]])
