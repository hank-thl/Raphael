"""Regenerate Raphael's vector logo and raster icons (local tool; requires Pillow).

From the repository root: python3 frontend/scripts/generate_brand_assets.py
Pillow is only a design-time dependency, never needed by Nuxt or the backend.
"""
from pathlib import Path
from PIL import Image, ImageDraw

PUBLIC = Path(__file__).resolve().parents[1] / "public"
BACKGROUND = "#304d40"
INK = "#f7f9f3"
ACCENT = "#b9cf98"
# A custom serif r, represented as geometry rather than a font dependency.
COMMANDS = [
    ("M", 142, 330), ("L", 142, 190), ("L", 124, 190),
    ("L", 124, 168), ("L", 198, 168), ("L", 198, 208),
    ("C", 214, 174, 235, 160, 264, 160),
    ("C", 284, 160, 298, 172, 298, 189),
    ("C", 298, 205, 287, 214, 274, 214),
    ("C", 260, 214, 250, 204, 250, 191),
    ("C", 250, 182, 252, 178, 257, 174),
    ("C", 230, 179, 214, 198, 206, 226),
    ("L", 206, 330), ("L", 233, 330), ("L", 233, 354),
    ("L", 124, 354), ("L", 124, 330), ("Z",),
]
DOT = (348, 330, 24)


def svg() -> str:
    path = " ".join(command[0] + " ".join(str(v) for v in command[1:]) for command in COMMANDS)
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" fill="none">
  <title>Raphael</title>
  <rect width="512" height="512" rx="112" fill="{BACKGROUND}"/>
  <path d="{path}" fill="{INK}"/>
  <circle cx="{DOT[0]}" cy="{DOT[1]}" r="{DOT[2]}" fill="{ACCENT}"/>
</svg>
'''


def render() -> Image.Image:
    # Supersample both curves and final downscales for clean small favicons.
    scale = 4
    image = Image.new("RGB", (512 * scale, 512 * scale), BACKGROUND)
    draw = ImageDraw.Draw(image)
    points = []
    x = y = 0
    for command in COMMANDS:
        kind, *values = command
        if kind in ("M", "L"):
            x, y = values
            points.append((x * scale, y * scale))
        elif kind == "C":
            x1, y1, x2, y2, x3, y3 = values
            for step in range(1, 65):
                t = step / 64
                u = 1 - t
                px = u**3*x + 3*u*u*t*x1 + 3*u*t*t*x2 + t**3*x3
                py = u**3*y + 3*u*u*t*y1 + 3*u*t*t*y2 + t**3*y3
                points.append((px * scale, py * scale))
            x, y = x3, y3
    draw.polygon(points, fill=INK)
    cx, cy, radius = DOT
    draw.ellipse(((cx-radius)*scale, (cy-radius)*scale, (cx+radius)*scale, (cy+radius)*scale), fill=ACCENT)
    return image


def main():
    (PUBLIC / "brand").mkdir(parents=True, exist_ok=True)
    (PUBLIC / "icons").mkdir(parents=True, exist_ok=True)
    (PUBLIC / "brand/raphael-mark.svg").write_text(svg())
    (PUBLIC / "favicon.svg").write_text(svg())
    original = render()
    for filename, size in [("apple-touch-icon.png", 180), ("icons/icon-192.png", 192),
                           ("icons/icon-512.png", 512), ("favicon-32x32.png", 32)]:
        original.resize((size, size), Image.Resampling.LANCZOS).save(PUBLIC / filename, optimize=True)
    original.resize((256, 256), Image.Resampling.LANCZOS).save(
        PUBLIC / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
    print("Generated SVG logo, favicon SVG/ICO/PNG, Apple touch icon and 192/512 icons.")


if __name__ == "__main__":
    main()
