"""Generates assets/logo.png + multi-size logo.ico for OllamaToBlender.

Run once after editing:  python assets/make_logo.py

Design rationale
----------------
Strong, simple silhouette that survives down to 16×16:
    - rounded-square dark background (modern app-icon look)
    - bold isometric cube taking ~70% of the canvas
    - three faces shaded for depth (light top / mid left / dark right)
    - crisp ink-coloured edges
    - one accent dot top-right (acts as the "signal / AI" hint)
    - subtle chat-tail at bottom-left of the cube — only visible at >=64 px,
      added by overlay so it disappears gracefully on tiny renders.
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

HERE = Path(__file__).resolve().parent

# Palette — keep in sync with gui/theme.py
BG_TOP = (22, 26, 34, 255)
BG_BOTTOM = (40, 46, 60, 255)
ACCENT = (255, 122, 41, 255)        # Blender orange
ACCENT_HI = (255, 178, 112, 255)     # top-face highlight
ACCENT_MID = (236, 102, 30, 255)    # left face
ACCENT_LO = (170, 64, 16, 255)      # right face (deep)
INK = (245, 240, 232, 255)
EDGE_DARK = (24, 16, 8, 255)


def vertical_gradient(size, top, bottom):
    w, h = size
    g = Image.new("RGBA", (1, h))
    for y in range(h):
        t = y / max(h - 1, 1)
        c = tuple(int(top[i] * (1 - t) + bottom[i] * t) for i in range(4))
        g.putpixel((0, y), c)
    return g.resize((w, h))


def rounded_mask(size, radius):
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=255)
    return m


def isometric_cube(canvas, cx, cy, edge, line_w, *, draw_tail=True):
    """Filled 3-face cube, centred on (cx, cy), with edge length `edge`."""
    a = math.radians(30)
    dx = edge * math.cos(a)
    dy = edge * math.sin(a)

    top = (cx, cy - edge)
    tl = (cx - dx, cy - edge + dy)
    tr = (cx + dx, cy - edge + dy)
    front = (cx, cy)
    left = (cx - dx, cy + dy)
    right = (cx + dx, cy + dy)
    bot = (cx, cy + 2 * dy)

    # Drop shadow underneath
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.ellipse(
        (cx - dx * 1.05, cy + 2 * dy - dy * 0.35, cx + dx * 1.05, cy + 2 * dy + dy * 0.55),
        fill=(0, 0, 0, 110),
    )
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(edge // 6)))

    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)

    # Faces (back-to-front so highlights overlap nicely)
    d.polygon([tl, front, bot, left], fill=ACCENT_MID)               # left face
    d.polygon([front, tr, right, bot], fill=ACCENT_LO)               # right face
    d.polygon([top, tr, front, tl], fill=ACCENT_HI)                  # top face

    # Subtle inner top-edge highlight
    d.line([tl, top], fill=(255, 220, 180, 220), width=max(1, line_w - 1))
    d.line([top, tr], fill=(255, 220, 180, 220), width=max(1, line_w - 1))

    # Crisp dark edges (full silhouette)
    edges = [
        (top, tl), (top, tr),
        (tl, left), (tr, right),
        (top, front), (front, left), (front, right),
        (left, bot), (right, bot),
    ]
    for p, q in edges:
        d.line([p, q], fill=EDGE_DARK, width=line_w)

    # Optional chat-tail — kept tiny so it's a "signature" detail, not noise
    if draw_tail:
        tail_origin = (cx - dx * 0.6, cy + dy * 1.6)
        d.polygon(
            [
                (tail_origin[0], tail_origin[1]),
                (tail_origin[0] - edge * 0.32, tail_origin[1] + edge * 0.30),
                (tail_origin[0] + edge * 0.05, tail_origin[1] - edge * 0.05),
            ],
            fill=ACCENT_MID,
            outline=EDGE_DARK,
        )

    canvas.alpha_composite(layer)


def build_logo(size: int = 512, *, with_tail: bool = True) -> Image.Image:
    pad = size // 18
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))

    # Rounded background
    bg = vertical_gradient((size, size), BG_TOP, BG_BOTTOM)
    bg.putalpha(rounded_mask((size, size), radius=size // 6))
    img.alpha_composite(bg)

    # Soft accent glow behind the cube
    glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gx, gy = size // 2, int(size * 0.55)
    gr = size // 3
    gd.ellipse((gx - gr, gy - gr, gx + gr, gy + gr), fill=(255, 122, 41, 70))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(size // 14)))

    # Cube — sized so its bounding box is ~70% of the icon
    cube_edge = int(size * 0.30)
    isometric_cube(
        img, cx=size // 2, cy=int(size * 0.52),
        edge=cube_edge,
        line_w=max(2, size // 90),
        draw_tail=with_tail and size >= 96,
    )

    # Top-right accent dot
    dot = Image.new("RGBA", img.size, (0, 0, 0, 0))
    dd = ImageDraw.Draw(dot)
    r = size // 22
    cx = size - pad - r
    cy = pad + r
    # outer halo
    halo = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(halo).ellipse((cx - r * 1.6, cy - r * 1.6, cx + r * 1.6, cy + r * 1.6),
                                  fill=(255, 122, 41, 110))
    img.alpha_composite(halo.filter(ImageFilter.GaussianBlur(size // 60)))
    dd.ellipse((cx - r, cy - r, cx + r, cy + r), fill=ACCENT, outline=INK,
               width=max(1, size // 200))
    img.alpha_composite(dot)

    return img


def main() -> None:
    big = build_logo(512, with_tail=True)
    big.save(HERE / "logo.png")

    # Crisper renders for small sizes (no chat-tail below 64 px)
    for sz, with_tail in [(256, True), (128, True), (96, True), (64, False), (48, False), (32, False), (16, False)]:
        build_logo(sz * 2, with_tail=with_tail).resize((sz, sz), Image.LANCZOS).save(HERE / f"logo_{sz}.png")

    # Multi-size ICO
    big.save(
        HERE / "logo.ico",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )

    print(f"Logo written: {HERE / 'logo.png'}")
    print(f"Icon written: {HERE / 'logo.ico'}")


if __name__ == "__main__":
    main()
