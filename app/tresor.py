"""« Trésor » : carte /argent et classement /topargent, même famille visuelle que le Chemin des rangs (thème or).

La pièce dessinée est l'emoji d'Or du serveur (réglage or_emoji_id) quand il existe, sinon une pièce dessinée.
"""
import io

from PIL import Image, ImageDraw

from app import chemin_rangs as cr
from app.chemin_rangs import (CINZEL, CORM, CORM_I, INTER, LARGEUR, HAUTEUR, MEDAILLES, S, _D, _banniere, _degrade,
                              _espace, _grain, _lueur, _nb, _rond, _sortie, _telecharger, _tronquer, abrege)

OR_CLAIR, OR_FONCE = (248, 214, 120), (176, 132, 40)
CREME, BEIGE = (250, 240, 220), (222, 204, 170)
TISSU = ((122, 34, 40), (70, 18, 26))  # bannière bordeaux
CLASSEMENT_ENTETE, CLASSEMENT_LIGNE, CLASSEMENT_PIED = 100, 100, 40

_emoji: dict[str, Image.Image | None] = {}


def _fond(w, h):
    img = _degrade(w, h, (34, 26, 18), (14, 11, 10))
    img = _grain(img, 6)
    _lueur(img, (int(w * .66), int(h * .7)), 300 * S, (200, 150, 50), 55)
    return img


async def icone_or(emoji_id: str) -> Image.Image | None:
    """Image de l'emoji d'Or du serveur (mise en cache), None si absent."""
    if not emoji_id:
        return None
    if emoji_id not in _emoji:
        _emoji[emoji_id] = await _telecharger(f"https://cdn.discordapp.com/emojis/{emoji_id}.png?size=128")
    return _emoji[emoji_id]


def piece(taille: int, emoji: Image.Image | None = None) -> Image.Image:
    """Pièce d'or (taille en pixels réels du dessin 2x)."""
    p = Image.new("RGBA", (taille, taille), (0, 0, 0, 0))
    if emoji is not None:
        from PIL import ImageOps
        e = ImageOps.contain(emoji, (taille, taille), Image.LANCZOS)
        p.alpha_composite(e, ((taille - e.width) // 2, (taille - e.height) // 2))
        return p
    d = _D(p)
    d.ellipse((0, 0, taille - 1, taille - 1), fill=OR_FONCE)
    m = taille * .07
    disque = _degrade(taille, taille, (255, 232, 150), (214, 160, 50), vertical=True)
    masque = Image.new("L", (taille, taille), 0)
    ImageDraw.Draw(masque).ellipse((m, m, taille - 1 - m, taille - 1 - m), fill=255)
    disque.putalpha(masque)
    p.alpha_composite(disque)
    d = _D(p)
    m2 = taille * .2
    d.ellipse((m2, m2, taille - 1 - m2, taille - 1 - m2), outline=(150, 105, 30, 200), width=max(1, taille // 22))
    d.text((taille / 2, taille / 2 + taille * .03), "O", font=CINZEL(taille / S * .42, "Black"), fill=(140, 96, 24), anchor="mm")
    return p


def _barre(img, x0, y0, x1, y1, ratio):
    d = _D(img)
    r = (y1 - y0) / 2
    d.rounded_rectangle((x0, y0, x1, y1), r, fill=(255, 255, 255, 34))
    fin = x0 + max(y1 - y0, (x1 - x0) * max(0.0, min(1.0, ratio)))
    remplissage = _degrade(int(fin - x0) + 1, int(y1 - y0) + 1, (176, 124, 36), (255, 224, 130))
    masque = Image.new("L", remplissage.size, 0)
    ImageDraw.Draw(masque).rounded_rectangle((0, 0, remplissage.width - 1, remplissage.height - 1), r, fill=255)
    remplissage.putalpha(masque)
    halo = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(halo).rounded_rectangle((x0, y0, fin, y1), r, fill=OR_CLAIR + (200,))
    from PIL import ImageFilter
    img.alpha_composite(halo.filter(ImageFilter.GaussianBlur(5 * S)))
    img.alpha_composite(remplissage, (int(x0), int(y0)))


# ---------------------------------------------------------------- carte /argent

async def carte(nom: str, avatar_url: str | None, solde: int, record: int, depense: int, place: int,
                nom_or: str = "Or", emoji: Image.Image | None = None, pb=None) -> bytes:
    W, H = LARGEUR * S, HAUTEUR * S
    img = _fond(W, H)
    avatar = _rond(await _telecharger(avatar_url, pb), 128 * S, nom)
    _banniere(img, 34 * S, 214 * S, H, avatar, f"#{place}" if place else "—", 92 * S, "PLACE", TISSU, (240, 206, 200))
    d = _D(img)
    x, xd = 250 * S, W - 48 * S
    d.text((x, 68 * S), _tronquer(d, nom, CINZEL(40), xd - x), font=CINZEL(40), fill=CREME, anchor="ls")
    sous = f"{'#' + str(place) + ' des fortunes du serveur' if place else 'Pas encore classé'}"
    d.text((x + 2 * S, 96 * S), sous, font=CORM(24), fill=BEIGE, anchor="ls")
    # montant
    t = 46 * S
    _lueur(img, (x + t // 2, 148 * S), 34 * S, (255, 200, 90), 90)
    img.alpha_composite(piece(t, emoji), (x, 148 * S - t // 2))
    d = _D(img)
    montant = _nb(solde)
    d.text((x + t + 16 * S, 166 * S), montant, font=CINZEL(48), fill=(255, 236, 180), anchor="ls")
    lm = d.textlength(montant, font=CINZEL(48))
    d.text((x + t + 28 * S + lm, 166 * S), nom_or, font=CORM_I(30), fill=OR_CLAIR, anchor="ls")
    # barre solde / record
    ratio = solde / record if record else 0
    _barre(img, x, 188 * S, xd, 202 * S, ratio)
    d = _D(img)
    d.text((x, 236 * S), f"Record : {_nb(record)} {nom_or}", font=INTER(16), fill=CREME, anchor="ls")
    if record and solde < record:
        d.text((x, 262 * S), f"Encore {_nb(record - solde)} {nom_or} pour égaler ton record", font=CORM_I(21), fill=BEIGE, anchor="ls")
    else:
        d.text((x, 262 * S), "Ta bourse n'a jamais été aussi pleine !" if solde else "Ta bourse attend sa première pièce.",
               font=CORM_I(21), fill=BEIGE, anchor="ls")
    d.text((xd, 236 * S), f"Dépensé au total : {_nb(depense)} {nom_or}", font=INTER(15, "Medium"), fill=OR_CLAIR, anchor="rs")
    d.text((xd, 262 * S), "en boutiques et chez le receleur", font=CORM_I(21), fill=OR_CLAIR, anchor="rs")
    return _sortie(img, LARGEUR, HAUTEUR)


# ---------------------------------------------------------------- classement /topargent

async def classement(lignes: list[dict], page: int, pages: int, nom_or: str = "Or", emoji: Image.Image | None = None,
                     pb=None) -> bytes:
    """Chaque ligne : rang, nom, avatar (URL), solde, record, sous_titre ; la barre compare le solde au record."""
    h = CLASSEMENT_ENTETE + CLASSEMENT_LIGNE * max(1, len(lignes)) + CLASSEMENT_PIED
    W, H = LARGEUR * S, h * S
    img = _fond(W, H)
    d = _D(img)
    x0, xd = 36 * S, W - 36 * S
    _espace(d, (x0 + 66 * S, 34 * S), "LE TRÉSOR", INTER(12, "Bold"), OR_CLAIR, 3)
    d.text((x0, 80 * S), "Classement des fortunes", font=CINZEL(34), fill=CREME, anchor="ls")
    d.text((xd, 80 * S), f"Page {page + 1} / {pages}", font=CORM_I(22), fill=BEIGE, anchor="rs")
    d.line((x0, 96 * S, xd, 96 * S), fill=OR_CLAIR + (110,), width=S)
    if not lignes:
        d.text((W / 2, (CLASSEMENT_ENTETE + 50) * S), "Personne n'a encore d'Or.", font=CORM_I(24), fill=BEIGE, anchor="mm")
    p = piece(26 * S, emoji)
    for k, l in enumerate(lignes):
        y0 = (CLASSEMENT_ENTETE + 8 + k * CLASSEMENT_LIGNE) * S
        yc = y0 + 42 * S
        rang = l["rang"]
        d = _D(img)
        d.rounded_rectangle((x0, y0, xd, y0 + 84 * S), 16 * S, fill=(255, 255, 255, 14 if rang > 3 else 22),
                            outline=(MEDAILLES[rang][0] + (90,)) if rang in MEDAILLES else (255, 255, 255, 30), width=S)
        rx = x0 + 40 * S
        if rang in MEDAILLES:
            clair, fonce = MEDAILLES[rang]
            _lueur(img, (rx, yc), 26 * S, clair, 70)
            d = _D(img)
            d.ellipse((rx - 21 * S, yc - 21 * S, rx + 21 * S, yc + 21 * S), fill=fonce)
            d.ellipse((rx - 18 * S, yc - 18 * S, rx + 18 * S, yc + 18 * S), fill=clair)
            d.text((rx, yc + S), str(rang), font=CINZEL(22, "Black"), fill=(60, 40, 20), anchor="mm")
        else:
            d.ellipse((rx - 19 * S, yc - 19 * S, rx + 19 * S, yc + 19 * S), outline=(255, 255, 255, 80), width=S)
            d.text((rx, yc + S), str(rang), font=INTER(17, "Bold"), fill=(236, 226, 206), anchor="mm")
        ar = 30 * S
        ax = rx + 72 * S
        d.ellipse((ax - ar - 4 * S, yc - ar - 4 * S, ax + ar + 4 * S, yc + ar + 4 * S), fill=OR_CLAIR)
        img.alpha_composite(_rond(await _telecharger(l.get("avatar"), pb), 2 * ar, l["nom"]), (ax - ar, yc - ar))
        d = _D(img)
        tx = ax + ar + 22 * S
        d.text((tx, yc - 2 * S), _tronquer(d, l["nom"], CINZEL(25), 270 * S), font=CINZEL(25), fill=CREME, anchor="ls")
        if l.get("sous_titre"):
            d.text((tx + S, yc + 24 * S), _tronquer(d, l["sous_titre"], CORM_I(20), 270 * S), font=CORM_I(20), fill=BEIGE, anchor="ls")
        # barre : solde / record personnel
        b0, b1 = 520 * S, 700 * S
        record = max(l.get("record") or 0, l["solde"])
        _barre(img, b0, yc - 12 * S, b1, yc, l["solde"] / record if record else 0)
        d = _D(img)
        d.text((b0, yc + 22 * S), f"record {abrege(record)}", font=INTER(13, "Medium"), fill=BEIGE, anchor="ls")
        # montant
        montant = abrege(l["solde"])
        f = CINZEL(28 if len(montant) <= 7 else 24)
        d.text((xd - 22 * S, yc + 11 * S), montant, font=f, fill=(255, 236, 180), anchor="rs")
        lm = d.textlength(montant, font=f)
        img.alpha_composite(p, (int(xd - 22 * S - lm - 10 * S - p.width), int(yc - p.height / 2)))
    _D(img).text((W / 2, H - 16 * S), "Tape /argent pour voir ta bourse", font=CORM_I(19), fill=(180, 164, 136), anchor="ms")
    return _sortie(img, LARGEUR, h)
