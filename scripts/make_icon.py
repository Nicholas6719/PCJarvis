"""Generate the application icon: an arc reactor, drawn at several sizes.

Windows picks a different size depending on where the icon appears -- 16px in
the taskbar, 256px in the Start Menu -- so each is drawn separately rather than
downscaled, which would turn the fine rings into mush at small sizes.

    python scripts/make_icon.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw  # noqa: E402

OUT = ROOT / "assets"
CYAN = (79, 216, 255)
WHITE = (235, 250, 255)


def draw_reactor(size: int) -> Image.Image:
    # Supersample, then downscale once at the end for clean edges.
    ss = 4
    s = size * ss
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c = s / 2
    detailed = size >= 48

    def circle(radius, fill=None, outline=None, width=1):
        d.ellipse([c - radius, c - radius, c + radius, c + radius],
                  fill=fill, outline=outline, width=width)

    # Dark housing so the icon reads on light and dark taskbars alike.
    circle(s * 0.48, fill=(8, 14, 24, 255))
    circle(s * 0.46, outline=(*CYAN, 90), width=max(1, int(s * 0.012)))

    # Glow, built from stacked translucent discs.
    for i in range(18, 0, -1):
        r = s * 0.40 * (i / 18)
        alpha = int(11 * (1 - i / 18) ** 1.6 * 255 / 11)
        circle(r, fill=(*CYAN, max(3, alpha)))

    if detailed:
        # Outer tick ring.
        for i in range(36):
            a = (i / 36) * math.tau
            r1, r2 = s * 0.37, s * 0.43
            d.line([c + math.cos(a) * r1, c + math.sin(a) * r1,
                    c + math.cos(a) * r2, c + math.sin(a) * r2],
                   fill=(*CYAN, 130 if i % 3 else 210),
                   width=max(1, int(s * 0.008)))

        # The coil segments that give the reactor its face.
        for i in range(10):
            a = (i / 10) * math.tau - math.pi / 2
            r = s * 0.27
            rr = s * 0.052
            x, y = c + math.cos(a) * r, c + math.sin(a) * r
            d.ellipse([x - rr, y - rr, x + rr, y + rr],
                      fill=(*CYAN, 205), outline=(*WHITE, 150),
                      width=max(1, int(s * 0.005)))

    circle(s * 0.335, outline=(*CYAN, 235), width=max(1, int(s * 0.018)))
    circle(s * 0.20, outline=(*WHITE, 200), width=max(1, int(s * 0.012)))

    # Hot core.
    for i in range(10, 0, -1):
        circle(s * 0.15 * (i / 10), fill=(*WHITE, int(255 * (1 - i / 12))))
    circle(s * 0.075, fill=(255, 255, 255, 255))

    if detailed:
        # Triangle, echoing the reactor housing in the UI.
        pts = [(c + math.cos(a) * s * 0.135, c + math.sin(a) * s * 0.135)
               for a in (-math.pi / 2, -math.pi / 2 + math.tau / 3,
                         -math.pi / 2 + 2 * math.tau / 3)]
        d.polygon(pts, outline=(*CYAN, 190))

    return img.resize((size, size), Image.LANCZOS)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    sizes = [16, 24, 32, 48, 64, 128, 256]
    images = [draw_reactor(n) for n in sizes]

    ico = OUT / "jarvis.ico"
    images[-1].save(ico, format="ICO",
                    sizes=[(n, n) for n in sizes], append_images=images[:-1])
    images[-1].save(OUT / "jarvis.png")

    print(f"wrote {ico} ({', '.join(str(n) for n in sizes)})")
    print(f"wrote {OUT / 'jarvis.png'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
