"""Cartes image pour /argent et /niveau : avatar rond, pseudo, barre de progression, place et niveau/record.

Dessinées avec Pillow et sa police intégrée (aucune police ni image téléchargée) ; l'avatar Discord du membre
est récupéré à la volée.
"""
import io

import httpx
from PIL import Image, ImageDraw, ImageFont

LARGEUR, HAUTEUR = 934, 282
FOND, PISTE, GRIS = (32, 34, 41), (70, 73, 82), (140, 143, 150)


def _police(taille: int) -> ImageFont.FreeTypeFont:
    return ImageFont.load_default(size=taille)


def abrege(v: float) -> str:
    """12 781 → « 12,8 k », 45 500 → « 45,5 k »."""
    v = float(v or 0)
    if v >= 1_000_000:
        return f"{v / 1_000_000:.1f}".rstrip("0").rstrip(".").replace(".", ",") + " M"
    if v >= 1000:
        return f"{v / 1000:.1f}".rstrip("0").rstrip(".").replace(".", ",") + " k"
    return str(int(v))


async def _avatar(url: str | None, taille: int) -> Image.Image:
    rond = Image.new("RGBA", (taille, taille), (0, 0, 0, 0))
    image = None
    if url:
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(url, timeout=10)
                r.raise_for_status()
                image = Image.open(io.BytesIO(r.content)).convert("RGBA").resize((taille, taille))
        except Exception:
            image = None
    if image is None:
        image = Image.new("RGBA", (taille, taille), PISTE)
    masque = Image.new("L", (taille, taille), 0)
    ImageDraw.Draw(masque).ellipse((0, 0, taille, taille), fill=255)
    rond.paste(image, (0, 0), masque)
    return rond


async def carte(nom: str, avatar_url: str | None, couleur: tuple[int, int, int], ratio: float,
                haut_droite: str, bas_gauche: str, bas_droite: str) -> bytes:
    """Carte PNG : renvoie les octets de l'image."""
    img = Image.new("RGBA", (LARGEUR, HAUTEUR), FOND)
    d = ImageDraw.Draw(img)
    img.alpha_composite(await _avatar(avatar_url, 190), (40, 46))
    x0, x1 = 270, LARGEUR - 50
    d.text((x0 + 6, 112), nom[:22], font=_police(52), fill=couleur, anchor="ls")
    d.text((x1, 112), haut_droite, font=_police(26), fill=couleur, anchor="rs")
    # Barre de progression aux bords arrondis.
    y0, y1 = 128, 166
    d.rounded_rectangle((x0, y0, x1, y1), radius=19, fill=PISTE)
    plein = x0 + max(38, int((x1 - x0) * max(0.0, min(1.0, ratio))))
    if ratio > 0:
        d.rounded_rectangle((x0, y0, plein, y1), radius=19, fill=couleur)
    d.text((x0 + 6, 200), bas_gauche, font=_police(28), fill=GRIS, anchor="lt")
    d.text((x1, 200), bas_droite, font=_police(28), fill=couleur, anchor="rt")
    sortie = io.BytesIO()
    img.convert("RGB").save(sortie, "PNG", optimize=True)
    return sortie.getvalue()
