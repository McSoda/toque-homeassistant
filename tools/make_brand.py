"""Génère les images de marque (icône et logo) : une toque de chef devant une maison.

Usage : python tools/make_brand.py
Écrit dans custom_components/toque/brand/. Pillow est requis (installé avec Home Assistant).
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent.parent / "custom_components" / "toque" / "brand"
SS = 4  # suréchantillonnage pour des bords lisses

HOUSE = "#2F7FD1"
ROOF = "#1B5AA6"
DOOR = "#1B5AA6"
HAT = "#FFFFFF"
HAT_LINE = "#C9D3DF"
HAT_SHADE = "#E6ECF3"
TEXT = "#1B5AA6"
TEXT_DARK = "#8FC0F5"


def draw_icon(size: int) -> Image.Image:
    """Icône carrée à fond transparent."""
    s = size * SS
    k = s / 1024
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    def r(x0, y0, x1, y1):
        return [x0 * k, y0 * k, x1 * k, y1 * k]

    # Maison : corps puis toit
    d.rounded_rectangle(r(170, 430, 854, 940), radius=60 * k, fill=HOUSE)
    # Toit : triangle rentré, épaissi par un trait rond (sommets arrondis, sans dépassement)
    cx0, cy0, f = 512, 343, 0.93
    roof = [(512, 70), (80, 480), (944, 480)]
    pts = [(cx0 + (x - cx0) * f, cy0 + (y - cy0) * f) for x, y in roof]
    d.polygon([(x * k, y * k) for x, y in pts], fill=ROOF)
    for i in range(3):
        (x0, y0), (x1, y1) = pts[i], pts[(i + 1) % 3]
        d.line([(x0 * k, y0 * k), (x1 * k, y1 * k)], fill=ROOF, width=int(70 * k))
        d.ellipse(r(x0 - 35, y0 - 35, x0 + 35, y0 + 35), fill=ROOF)
    # Cheminée
    d.rounded_rectangle(r(700, 150, 790, 330), radius=14 * k, fill=ROOF)

    # Toque de chef devant la maison : le contour clair est dessiné d'abord, élargi,
    # puis les formes blanches par-dessus, pour obtenir une silhouette unique.
    w = 14
    puffs = [(372, 520, 120), (652, 520, 120), (512, 470, 150)]
    for grow, fill in ((w, HAT_LINE), (0, HAT)):
        for cx, cy, rad in puffs:
            d.ellipse(r(cx - rad - grow, cy - rad - grow, cx + rad + grow, cy + rad + grow), fill=fill)
        d.rounded_rectangle(
            r(372 - grow, 520 - grow, 652 + grow, 800 + grow), radius=30 * k, fill=fill
        )
    # Plis de la toque
    for x in (452, 512, 572):
        d.line([(x * k, 610 * k), (x * k, 760 * k)], fill=HAT_SHADE, width=int(16 * k))
    # Bandeau
    d.rounded_rectangle(r(352, 770, 672, 850), radius=26 * k, fill=HAT, outline=HAT_LINE, width=int(14 * k))

    return img.resize((size, size), Image.LANCZOS)


def font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for name in ("segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def draw_logo(height: int, dark: bool = False) -> Image.Image:
    """Logo : icône suivie du mot « Toque »."""
    icon = draw_icon(height)
    f = font(int(height * 0.5))
    probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    box = probe.textbbox((0, 0), "Toque", font=f)
    tw, th = box[2] - box[0], box[3] - box[1]
    gap = int(height * 0.08)
    width = height + gap + tw + int(height * 0.06)
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    img.alpha_composite(icon, (0, 0))
    ImageDraw.Draw(img).text(
        (height + gap - box[0], (height - th) // 2 - box[1]), "Toque", font=f, fill=TEXT_DARK if dark else TEXT
    )
    return img


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    draw_icon(256).save(OUT / "icon.png", optimize=True)
    draw_icon(512).save(OUT / "icon@2x.png", optimize=True)
    draw_logo(256).save(OUT / "logo.png", optimize=True)
    draw_logo(512).save(OUT / "logo@2x.png", optimize=True)
    draw_logo(256, dark=True).save(OUT / "dark_logo.png", optimize=True)
    draw_logo(512, dark=True).save(OUT / "dark_logo@2x.png", optimize=True)
    print("écrit dans", OUT)


if __name__ == "__main__":
    main()
