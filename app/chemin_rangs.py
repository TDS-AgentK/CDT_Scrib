"""« Chemin des rangs » : carte /niveau et bannières d'annonce de niveau.

Les étapes du chemin sont les succès de niveau réglés sur la page Économie du site (eco_paliers déclenchés par un
niveau, et récompenses de niveau qui donnent un succès). Un succès secret reste invisible tant que le membre ne l'a
pas reçu. « Prochaine récompense » = la première récompense de niveau (eco_niveaux_recompenses) au-dessus du niveau
actuel ; si elle n'est qu'un succès secret pas encore reçu, elle s'affiche « Récompense secrète ».

Dessiné avec Pillow en 2x puis réduit (anticrénelage), polices libres livrées dans app/polices/.
"""
import io
import logging
import math
import os
import random
import time

import httpx
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps

log = logging.getLogger("cdt_scrib.chemin_rangs")

S = 2  # suréchantillonnage
LARGEUR, HAUTEUR = 934, 282
BANNIERE_H = 230
POLICES = os.path.join(os.path.dirname(__file__), "polices")
VERT, PALE, OR_ = (92, 214, 176), (190, 220, 210), (226, 196, 120)
MAX_ETAPES = 5
CACHE_SUCCES_S = 300

# Champs d'un succès (page /gestion/succes du site) : secret (case « invisible tant que non obtenu »),
# image téléversée sinon icon (ancienne icône Wix), background_color, resume (phrase sous le titre), description.
CHAMPS_SECRET = ("secret",)
CHAMPS_ICONE = ("image", "icon")
CHAMPS_FOND = ("background_color",)
CHAMPS_DESCRIPTION = ("resume", "description")


def site_url() -> str:
    return (os.getenv("SITE_URL") or "https://cdtsite-production.up.railway.app").rstrip("/")


def _adresse(v: str) -> str:
    """Adresse d'une image du site : absolue, chemin du site (/media/…) ou référence Wix."""
    if v.startswith("http"):
        return v
    if v.startswith("wix:image://v1/"):
        return "https://static.wixstatic.com/media/" + v[len("wix:image://v1/"):].split("/")[0]
    if v.startswith("/"):
        return site_url() + v
    return "https://static.wixstatic.com/media/" + v


# ---------------------------------------------------------------- données

def _champ(d: dict, noms) -> object:
    for n in noms:
        if d.get(n) not in (None, ""):
            return d[n]
    return None


def est_secret(s: dict) -> bool:
    v = _champ(s, CHAMPS_SECRET)
    return v is True or str(v).lower() in ("true", "1", "oui", "yes")


def detenu(s: dict, joueur: dict) -> bool:
    return bool(joueur.get("joueur")) and joueur["joueur"] in (s.get("members") or [])


def url_icone(s: dict, pb_url: str = "") -> str | None:
    v = _champ(s, CHAMPS_ICONE)
    return _adresse(v) if isinstance(v, str) and v else None


def couleur_fond(s: dict) -> tuple[int, int, int]:
    v = str(_champ(s, CHAMPS_FOND) or "").strip().lstrip("#")
    if len(v) == 6:
        try:
            return tuple(int(v[i:i + 2], 16) for i in (0, 2, 4))
        except ValueError:
            pass
    return (62, 160, 220)


_cache_succes: dict[str, tuple[float, dict]] = {}


async def succes_par_id(eco, ids: set[str]) -> dict[str, dict]:
    """Succès demandés, relus au plus toutes les 5 minutes (ceux tout juste donnés sont relus par l'appelant)."""
    sortie, manquants = {}, []
    for i in ids:
        c = _cache_succes.get(i)
        if c and time.time() - c[0] < CACHE_SUCCES_S:
            sortie[i] = c[1]
        else:
            manquants.append(i)
    for i in manquants:
        try:
            s = await eco.pb.requete("GET", f"/api/collections/succes/records/{i}")
        except Exception:
            log.warning("Succès %s illisible", i)
            continue
        _cache_succes[i] = (time.time(), s)
        sortie[i] = s
    return sortie


def oublier_succes(ids):
    for i in ids:
        _cache_succes.pop(i, None)


def _ids_succes(cfg: dict) -> dict[str, int]:
    """{id du succès: niveau} pour les succès liés à un niveau (paliers et récompenses)."""
    niveaux = {}
    for p in cfg.get("paliers") or []:
        if p.get("declencheur") == "niveau" and p.get("succes") and p.get("seuil"):
            niveaux.setdefault(p["succes"], int(p["seuil"]))
    for r in cfg.get("recompenses") or []:
        if r.get("succes") and r.get("niveau"):
            niveaux.setdefault(r["succes"], int(r["niveau"]))
    return niveaux


def etapes(cfg: dict, succes: dict[str, dict], joueur: dict, pb_url: str) -> list[dict]:
    """Étapes visibles du chemin pour ce joueur, par niveau croissant (succès secrets non reçus retirés)."""
    sortie = []
    for sid, niveau in _ids_succes(cfg).items():
        s = succes.get(sid)
        if not s:
            continue
        a = detenu(s, joueur)
        if est_secret(s) and not a:
            continue
        sortie.append({"id": sid, "niveau": niveau, "titre": s.get("title") or "Succès", "secret": est_secret(s),
                       "description": _champ(s, CHAMPS_DESCRIPTION) or "", "icone": url_icone(s, pb_url),
                       "fond": couleur_fond(s), "detenu": a})
    return sorted(sortie, key=lambda e: (e["niveau"], e["titre"]))


def libelle_recompense(r: dict, succes: dict[str, dict], joueur: dict) -> str:
    """Ce que donne une ligne de récompense de niveau : succès (« Récompense secrète » s'il est secret et pas encore
    reçu), objet, Or ou libellé ; le rôle en dernier recours (les rôles de rang sont déjà sur le chemin)."""
    s = succes.get(r.get("succes") or "")
    if s:
        return "Récompense secrète" if est_secret(s) and not detenu(s, joueur) else (s.get("title") or "Succès")
    return r.get("recompense_objet") or (f'{r["recompense_or"]} Or' if r.get("recompense_or") else "") \
        or r.get("libelle") or r.get("recompense_role") or "Récompense"


def prochaine_recompense(cfg: dict, niveau: int, succes: dict[str, dict], joueur: dict) -> tuple[int, str] | None:
    """Récompenses du niveau suivant (ou du premier niveau au-dessus qui en a), toutes réunies ; une ligne qui ne
    donne qu'un rôle n'est gardée que s'il n'y a rien d'autre à ce niveau."""
    suivantes = [r for r in cfg.get("recompenses") or [] if (r.get("niveau") or 0) > niveau]
    if not suivantes:
        return None
    n = min(r["niveau"] for r in suivantes)
    du_niveau = [r for r in suivantes if r["niveau"] == n]
    sans_role = [r for r in du_niveau if not (r.get("recompense_role") and not (r.get("succes") or r.get("recompense_objet")
                                                                                 or r.get("recompense_or") or r.get("objet")))]
    textes = []
    for r in sans_role or du_niveau:
        t = libelle_recompense(r, succes, joueur)
        if t not in textes:
            textes.append(t)
    return n, " + ".join(textes)


async def donnees(eco, joueur: dict) -> tuple[list[dict], dict, dict[str, dict]]:
    cfg = await eco.config()
    succes = await succes_par_id(eco, set(_ids_succes(cfg)))
    return etapes(cfg, succes, joueur, eco.pb.url), cfg, succes


async def avatar_site(eco, joueur: dict, cible=None) -> str | None:
    """Photo de profil choisie sur le site (membres.profile_photo, membre lié par joueurs.joueur),
    à défaut l'avatar Discord."""
    if joueur.get("joueur"):
        try:
            membre = await eco.pb.requete("GET", f'/api/collections/membres/records/{joueur["joueur"]}')
            if membre.get("profile_photo"):
                return _adresse(membre["profile_photo"])
        except Exception:
            log.info("Membre du site %s illisible pour l'avatar", joueur.get("joueur"))
    av = getattr(cible, "display_avatar", None)
    if av is None:
        return None
    try:
        return av.replace(format="png", size=256).url
    except Exception:
        return getattr(av, "url", None)


# ---------------------------------------------------------------- outils de dessin

def _police(nom: str, taille: float, variante: str | None = None) -> ImageFont.FreeTypeFont:
    try:
        f = ImageFont.truetype(os.path.join(POLICES, nom), int(taille * S))
        if variante:
            try:
                f.set_variation_by_name(variante)
            except Exception:
                pass
        return f
    except OSError:
        return ImageFont.load_default(size=int(taille * S))


def CINZEL(t, v="Bold"):
    return _police("Cinzel.ttf", t, v)


def CORM(t, v="Bold"):
    return _police("CormorantGaramond.ttf", t, v)


def CORM_I(t, v="SemiBold"):
    return _police("CormorantGaramond-Italic.ttf", t, v)


def INTER(t, v="SemiBold"):
    return _police("Inter.ttf", t, v)


class _D:
    """Tracés avec vrai mélange alpha sur une image RGBA (chaque tracé passe par un calque)."""
    def __init__(self, img):
        self.img = img

    def textlength(self, *a, **k):
        return ImageDraw.Draw(self.img).textlength(*a, **k)

    def __getattr__(self, nom):
        def f(*a, **k):
            calque = Image.new("RGBA", self.img.size, (0, 0, 0, 0))
            getattr(ImageDraw.Draw(calque), nom)(*a, **k)
            self.img.alpha_composite(calque)
        return f


def _degrade(w, h, c1, c2, vertical=False):
    base = Image.linear_gradient("L").resize((w, h))
    if not vertical:
        base = base.rotate(90, expand=True).resize((w, h)).transpose(Image.FLIP_LEFT_RIGHT)
    return Image.merge("RGBA", [base.point(lambda v, a=a, b=b: int(a + (b - a) * v / 255)) for a, b in zip(c1, c2)]
                       + [Image.new("L", (w, h), 255)])


def _grain(img, force=8, graine=11):
    rnd = random.Random(graine)
    bruit = Image.effect_noise(img.size, force * 4).filter(ImageFilter.GaussianBlur(1.2))
    bruit = bruit.point(lambda v: 128 + (v - 128) * force // 32)
    rgb = img.convert("RGB")
    rgb = ImageChops.add(ImageChops.subtract(rgb, Image.merge("RGB", [bruit.point(lambda v: max(0, 128 - v))] * 3)),
                         Image.merge("RGB", [bruit.point(lambda v: max(0, v - 128))] * 3))
    rgb.putalpha(img.getchannel("A"))
    rnd.random()
    return rgb


def _lueur(img, xy, rayon, couleur, force=160):
    calque = Image.new("RGBA", img.size, (0, 0, 0, 0))
    x, y = xy
    ImageDraw.Draw(calque).ellipse((x - rayon, y - rayon, x + rayon, y + rayon), fill=tuple(couleur) + (force,))
    img.alpha_composite(calque.filter(ImageFilter.GaussianBlur(rayon * 0.6)))


def _ombre(img, forme, decal=(0, 6), flou=10, opacite=170):
    calque = Image.new("RGBA", img.size, (0, 0, 0, 0))
    forme(ImageDraw.Draw(calque), decal, (0, 0, 0, opacite))
    img.alpha_composite(calque.filter(ImageFilter.GaussianBlur(flou * S)))


def _espace(d, xy, t, f, fill, ecart=4):
    """Texte centré avec espacement des lettres."""
    largeur = sum(d.textlength(c, font=f) for c in t) + ecart * S * (len(t) - 1)
    x, y = xy[0] - largeur / 2, xy[1]
    for c in t:
        d.text((x, y), c, font=f, fill=fill, anchor="lm")
        x += d.textlength(c, font=f) + ecart * S


def _couper(d, texte, f, largeur, lignes=2):
    mots, sortie, ligne = texte.split(), [], ""
    for m in mots:
        essai = (ligne + " " + m).strip()
        if d.textlength(essai, font=f) <= largeur or not ligne:
            ligne = essai
        else:
            sortie.append(ligne)
            ligne = m
    sortie.append(ligne)
    if len(sortie) > lignes:
        sortie = sortie[:lignes]
        sortie[-1] = sortie[-1].rstrip(".") + "…"
    return sortie


def _tronquer(d, texte, f, largeur):
    if d.textlength(texte, font=f) <= largeur:
        return texte
    while texte and d.textlength(texte + "…", font=f) > largeur:
        texte = texte[:-1]
    return texte.rstrip() + "…"


async def _telecharger(url: str | None, pb=None) -> Image.Image | None:
    if not url:
        return None
    try:
        entetes = {}
        if pb is not None and url.startswith(pb.url):
            entetes["Authorization"] = await pb._auth()
        async with httpx.AsyncClient() as client:
            r = await client.get(url, timeout=10, headers=entetes, follow_redirects=True)
            r.raise_for_status()
        return Image.open(io.BytesIO(r.content)).convert("RGBA")
    except Exception:
        log.info("Image non téléchargée : %s", url)
        return None


def _rond(image: Image.Image | None, taille: int, initiale: str = "?") -> Image.Image:
    if image is None:
        image = _degrade(taille, taille, (231, 160, 120), (120, 72, 150))
        _D(image).text((taille / 2, taille / 2), (initiale or "?")[:1].upper(), font=CINZEL(taille / S * .45), fill=(255, 246, 230), anchor="mm")
    image = ImageOps.fit(image, (taille, taille), Image.LANCZOS)
    masque = Image.new("L", (taille * 4, taille * 4), 0)
    ImageDraw.Draw(masque).ellipse((0, 0, taille * 4 - 1, taille * 4 - 1), fill=255)
    image = image.copy()
    image.putalpha(ImageChops.multiply(image.getchannel("A"), masque.resize((taille, taille), Image.LANCZOS)))
    return image


def _pastille(icone: Image.Image | None, fond, taille: int, eteinte: bool) -> Image.Image:
    """Icône du succès dans un carré arrondi coloré (comme sur le site)."""
    p = Image.new("RGBA", (taille, taille), (0, 0, 0, 0))
    ImageDraw.Draw(p).rounded_rectangle((0, 0, taille - 1, taille - 1), taille // 5, fill=tuple(fond) + (255,))
    if icone is not None:
        i = ImageOps.contain(icone, (int(taille * .8), int(taille * .8)), Image.LANCZOS)
        p.alpha_composite(i, ((taille - i.width) // 2, (taille - i.height) // 2))
    if eteinte:
        gris = ImageOps.grayscale(p.convert("RGB")).point(lambda v: int(v * .55))
        a = p.getchannel("A").point(lambda v: int(v * .8))
        p = Image.merge("RGBA", (gris, gris, gris, a))
    return p


def _fond(w, h):
    img = _degrade(w, h, (18, 34, 36), (10, 18, 24))
    img = _grain(img, 6)
    _lueur(img, (int(w * .62), int(h * .75)), 300 * S, (40, 140, 120), 60)
    return img


def _banniere(img, x0, x1, h, avatar: Image.Image, niveau: int, haut_avatar: int):
    def forme(dd, dec, col):
        o = dec[0] * S, dec[1] * S
        dd.polygon([(x0 + o[0], 0), (x1 + o[0], 0), (x1 + o[0], h - 30 * S + o[1]), ((x0 + x1) / 2 + o[0], h - 2 * S + o[1]),
                    (x0 + o[0], h - 30 * S + o[1])], fill=col)
    _ombre(img, forme)
    cal = Image.new("RGBA", img.size, (0, 0, 0, 0))
    forme(ImageDraw.Draw(cal), (0, 0), (255, 255, 255, 255))
    tissu = _grain(_degrade(img.width, img.height, (24, 92, 82), (12, 52, 50), vertical=True), 7, 12)
    tissu.putalpha(cal.getchannel("A"))
    img.alpha_composite(tissu)
    d = _D(img)
    d.line([(x0 + 10 * S, 0), (x0 + 10 * S, h - 36 * S), ((x0 + x1) / 2, h - 14 * S), (x1 - 10 * S, h - 36 * S), (x1 - 10 * S, 0)],
           fill=OR_ + (200,), width=2 * S)
    ar = avatar.width // 2
    ax, ay = (x0 + x1) // 2, haut_avatar
    d.ellipse((ax - ar - 5 * S, ay - ar - 5 * S, ax + ar + 5 * S, ay + ar + 5 * S), fill=OR_)
    img.alpha_composite(avatar, (ax - ar, ay - ar))
    d = _D(img)
    _espace(d, (ax, ay + ar + 26 * S), "NIVEAU", INTER(11), (200, 230, 220))
    d.text((ax, ay + ar + 62 * S), str(niveau), font=CINZEL(44 if niveau < 100 else 36), fill=(255, 240, 200), anchor="mm")


def _sortie(img, w, h) -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").resize((w, h), Image.LANCZOS).save(buf, "PNG", optimize=True)
    return buf.getvalue()


def _x_curseur(xs: list[float], niveaux: list[int], v: float, x_debut: float, n_debut: int, x_fin: float) -> float:
    """Position du curseur sur le chemin (v = niveau + fraction du niveau en cours)."""
    pts = [(x_debut, n_debut)] + list(zip(xs, niveaux))
    if v >= niveaux[-1]:
        return min(x_fin, xs[-1] + 26 * S + (v - niveaux[-1]) * 4 * S)
    for (xa, na), (xb, nb) in zip(pts, pts[1:]):
        if na <= v <= nb:
            return xa + (xb - xa) * ((v - na) / (nb - na) if nb > na else 1)
    return x_debut


def fenetre(etapes_visibles: list[dict], niveau: int, maxi: int = MAX_ETAPES) -> tuple[list[dict], int]:
    """Étapes à dessiner (au plus maxi : les deux dernières atteintes puis les suivantes) et niveau de départ du tracé."""
    if not etapes_visibles:
        return [], 0
    k = next((i for i, e in enumerate(etapes_visibles) if e["niveau"] > niveau), len(etapes_visibles))
    debut = max(0, min(k - 2, len(etapes_visibles) - maxi))
    vue = etapes_visibles[debut:debut + maxi]
    return vue, (etapes_visibles[debut - 1]["niveau"] if debut else 0)


async def _icones(vue, pb):
    for e in vue:
        e["_img"] = await _telecharger(e.get("icone"), pb)


def _chemin(img, vue, niveau_depart, v, niveau, x0, x1, y, taille_icone=30, libelles=True):
    d = _D(img)
    if not vue:
        return
    pas = (x1 - x0) / max(1, len(vue))
    xs = [x0 + 34 * S + (x1 - x0 - 34 * S) * (i + 1) / len(vue) - pas / 2 for i in range(len(vue))]
    xa = _x_curseur(xs, [e["niveau"] for e in vue], v, x0, niveau_depart, x1)
    d.line((x0, y, x1, y), fill=(255, 255, 255, 45), width=4 * S)
    halo = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(halo).line((x0, y, xa, y), fill=VERT + (255,), width=8 * S)
    img.alpha_composite(halo.filter(ImageFilter.GaussianBlur(6 * S)))
    d.line((x0, y, xa, y), fill=VERT, width=4 * S)
    t = taille_icone * S
    for e, x in zip(vue, xs):
        atteint = niveau >= e["niveau"]
        pastille = _pastille(e.get("_img"), e["fond"], t, not atteint)
        if atteint:
            d.rounded_rectangle((x - t / 2 - 3 * S, y - t / 2 - 3 * S, x + t / 2 + 3 * S, y + t / 2 + 3 * S), t // 4, fill=VERT)
        else:
            d.rounded_rectangle((x - t / 2 - 2 * S, y - t / 2 - 2 * S, x + t / 2 + 2 * S, y + t / 2 + 2 * S), t // 4, fill=(16, 30, 32),
                                outline=(255, 255, 255, 90), width=S)
        img.alpha_composite(pastille, (int(x - t / 2), int(y - t / 2)))
        d = _D(img)
        if libelles:
            col = (236, 246, 240) if atteint else (150, 170, 166)
            d.text((x, y - t / 2 - 9 * S), f"niv. {e['niveau']}", font=INTER(12), fill=col, anchor="ms")
            for j, ligne in enumerate(_couper(d, e["titre"], CORM(17), pas - 10 * S)):
                d.text((x, y + t / 2 + (19 + 16 * j) * S), ligne, font=CORM(17), fill=col, anchor="ms")
    _lueur(img, (int(xa), y), 16 * S, (140, 255, 210), 220)
    _D(img).ellipse((xa - 6 * S, y - 6 * S, xa + 6 * S, y + 6 * S), fill=(240, 255, 250))


def _nb(v) -> str:
    return f"{int(v):,}".replace(",", " ")


# ---------------------------------------------------------------- carte /niveau

async def carte(nom: str, avatar_url: str | None, niveau: int, fait: int, besoin: int, place: int, titre: str,
                etapes_visibles: list[dict], prochaine: tuple[int, str] | None, pb=None) -> bytes:
    W, H = LARGEUR * S, HAUTEUR * S
    img = _fond(W, H)
    avatar = _rond(await _telecharger(avatar_url, pb), 128 * S, nom)
    _banniere(img, 34 * S, 214 * S, H, avatar, niveau, 92 * S)
    d = _D(img)
    x, xd = 250 * S, W - 48 * S
    d.text((x, 68 * S), _tronquer(d, nom, CINZEL(40), xd - x), font=CINZEL(40), fill=(236, 246, 240), anchor="ls")
    sous = "  ·  ".join(t for t in (titre, f"#{place} du serveur" if place else "") if t)
    d.text((x + 2 * S, 96 * S), sous, font=CORM(24), fill=PALE, anchor="ls")
    ratio = fait / besoin if besoin else 1
    vue, depart = fenetre(etapes_visibles, niveau)
    await _icones(vue, pb)
    if vue:
        _chemin(img, vue, depart, niveau + ratio, niveau, x, xd, 152 * S, 28)
    else:  # aucun succès de niveau : simple barre
        d.rounded_rectangle((x, 140 * S, xd, 166 * S), 13 * S, fill=(255, 255, 255, 40))
        d.rounded_rectangle((x, 140 * S, x + max(26 * S, (xd - x) * ratio), 166 * S), 13 * S, fill=VERT)
    d = _D(img)
    gauche = f"{_nb(fait)} / {_nb(besoin)} XP vers le niveau {niveau + 1}" if besoin else "Niveau maximal atteint"
    d.text((x, 236 * S), gauche, font=INTER(16), fill=(236, 246, 240), anchor="ls")
    if besoin:
        d.text((x, 262 * S), f"Encore {_nb(besoin - fait)} XP avant le niveau {niveau + 1}", font=CORM_I(21), fill=PALE, anchor="ls")
    if prochaine:
        f = INTER(15, "Medium")
        place_libre = xd - x - d.textlength(gauche, font=INTER(16)) - 24 * S
        d.text((xd, 236 * S), _tronquer(d, f"Prochaine récompense : {prochaine[1]}", f, place_libre), font=f, fill=OR_, anchor="rs")
        d.text((xd, 262 * S), f"au niveau {prochaine[0]}", font=CORM_I(21), fill=OR_, anchor="rs")
    return _sortie(img, LARGEUR, HAUTEUR)


# ---------------------------------------------------------------- bannière d'annonce de niveau

def phrase(mention: str, avant: int, apres: int, etape: dict | None, graine=None) -> str:
    """Texte de l'annonce (varié pour ne pas lasser)."""
    rnd = random.Random(graine)
    if etape and etape.get("secret"):
        return f"{mention} atteint le niveau **{apres}**… et découvre un succès secret : **{etape['titre']}** !"
    if etape:
        return rnd.choice([
            f"{mention} atteint le niveau **{apres}** et franchit une étape du chemin : **{etape['titre']}**.",
            f"Une nouvelle étape pour {mention} : niveau **{apres}**, succès **{etape['titre']}** obtenu.",
        ])
    if apres - avant > 1:
        return f"{mention} grimpe de {apres - avant} niveaux d'un coup : niveau **{apres}** !"
    return rnd.choice([
        f"{mention} avance sur le chemin des rangs : niveau **{apres}**.",
        f"Un pas de plus pour {mention}, qui atteint le niveau **{apres}**.",
        f"{mention} gravit une marche : niveau **{apres}**.",
        f"Le chemin s'ouvre un peu plus pour {mention} : niveau **{apres}**.",
    ])


async def banniere(nom: str, avatar_url: str | None, avant: int, apres: int, etapes_visibles: list[dict],
                   etape: dict | None, recompenses: list[str], pb=None) -> bytes:
    """Image de l'annonce : bannière, titre, étape franchie ou portion du chemin, récompenses."""
    W, H = LARGEUR * S, BANNIERE_H * S
    img = _fond(W, H)
    secret = bool(etape and etape.get("secret"))
    if secret:
        _lueur(img, (int(W * .55), int(H * .45)), 260 * S, (226, 170, 60), 110)
    avatar = _rond(await _telecharger(avatar_url, pb), 100 * S, nom)
    _banniere(img, 30 * S, 190 * S, H, avatar, apres, 74 * S)
    d = _D(img)
    x, xd = 226 * S, W - 44 * S
    sur_titre = "SUCCÈS SECRET RÉVÉLÉ" if secret else "ÉTAPE FRANCHIE" if etape else \
        (f"+{apres - avant} NIVEAUX" if apres - avant > 1 else "NOUVEAU NIVEAU")
    couleur = OR_ if secret else VERT
    largeur = sum(d.textlength(c, font=INTER(12, "Bold")) for c in sur_titre) + 3 * S * (len(sur_titre) - 1)
    _espace(d, (x + largeur / 2, 40 * S), sur_titre, INTER(12, "Bold"), couleur, 3)
    d.text((x, 88 * S), _tronquer(d, nom, CINZEL(36), xd - x - 200 * S), font=CINZEL(36), fill=(236, 246, 240), anchor="ls")
    d.text((xd, 88 * S), f"Niveau {apres}", font=CINZEL(36), fill=(255, 240, 200), anchor="rs")
    if etape:
        t = 74 * S
        icone = await _telecharger(etape.get("icone"), pb)
        if secret:
            _lueur(img, (x + t // 2, 152 * S), 60 * S, (255, 210, 120), 160)
        d = _D(img)
        d.rounded_rectangle((x - 4 * S, 115 * S - 4 * S, x + t + 4 * S, 115 * S + t + 4 * S), 18 * S, fill=OR_ if secret else VERT)
        img.alpha_composite(_pastille(icone, etape["fond"], t, False), (x, 115 * S))
        d = _D(img)
        tx = x + t + 22 * S
        d.text((tx, 140 * S), _tronquer(d, etape["titre"], CINZEL(28), xd - tx), font=CINZEL(28), fill=(255, 240, 200) if secret else (236, 246, 240), anchor="ls")
        for j, ligne in enumerate(_couper(d, etape.get("description") or "", CORM(21, "SemiBold"), xd - tx, 2)):
            d.text((tx, (168 + 22 * j) * S), ligne, font=CORM(21, "SemiBold"), fill=PALE, anchor="ls")
    else:
        vue, depart = fenetre(etapes_visibles, apres, 3)
        await _icones(vue, pb)
        if vue:
            _chemin(img, vue, depart, apres, apres, x, xd, 150 * S, 26, True)
        else:
            d.rounded_rectangle((x, 138 * S, xd, 160 * S), 11 * S, fill=VERT)
    if recompenses:
        d = _D(img)
        texte = "Récompenses : " + " · ".join(recompenses)
        d.text((x, 216 * S) if etape else (x, 218 * S), _tronquer(d, texte, INTER(15, "Medium"), xd - x), font=INTER(15, "Medium"), fill=OR_, anchor="ls")
    return _sortie(img, LARGEUR, BANNIERE_H)


# ---------------------------------------------------------------- classement /topniveau

CLASSEMENT_ENTETE, CLASSEMENT_LIGNE, CLASSEMENT_PIED = 100, 100, 40
MEDAILLES = {1: ((248, 214, 120), (176, 132, 40)), 2: ((226, 232, 236), (140, 150, 160)), 3: ((232, 168, 112), (150, 90, 50))}


async def classement(lignes: list[dict], page: int, pages: int, pb=None) -> bytes:
    """Image du classement des niveaux. Chaque ligne : rang, nom, avatar (URL), niveau, fait, besoin, titre,
    badges (étapes reçues, les plus récentes en dernier)."""
    h = CLASSEMENT_ENTETE + CLASSEMENT_LIGNE * max(1, len(lignes)) + CLASSEMENT_PIED
    W, H = LARGEUR * S, h * S
    img = _fond(W, H)
    d = _D(img)
    x0, xd = 36 * S, W - 36 * S
    _espace(d, (x0 + 74 * S, 34 * S), "CHEMIN DES RANGS", INTER(12, "Bold"), VERT, 3)
    d.text((x0, 80 * S), "Classement des niveaux", font=CINZEL(34), fill=(236, 246, 240), anchor="ls")
    d.text((xd, 80 * S), f"Page {page + 1} / {pages}", font=CORM_I(22), fill=PALE, anchor="rs")
    d.line((x0, 96 * S, xd, 96 * S), fill=OR_ + (110,), width=S)
    if not lignes:
        d.text((W / 2, (CLASSEMENT_ENTETE + 50) * S), "Personne n'a encore gagné d'XP.", font=CORM_I(24), fill=PALE, anchor="mm")
    for k, l in enumerate(lignes):
        y0 = (CLASSEMENT_ENTETE + 8 + k * CLASSEMENT_LIGNE) * S
        yc = y0 + 42 * S
        rang = l["rang"]
        d = _D(img)
        d.rounded_rectangle((x0, y0, xd, y0 + 84 * S), 16 * S, fill=(255, 255, 255, 14 if rang > 3 else 22),
                            outline=(MEDAILLES[rang][0] + (90,)) if rang in MEDAILLES else (255, 255, 255, 30), width=S)
        # rang
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
            d.text((rx, yc + S), str(rang), font=INTER(17, "Bold"), fill=(220, 236, 230), anchor="mm")
        # avatar
        ar = 30 * S
        ax = rx + 72 * S
        d.ellipse((ax - ar - 4 * S, yc - ar - 4 * S, ax + ar + 4 * S, yc + ar + 4 * S), fill=OR_)
        img.alpha_composite(_rond(await _telecharger(l.get("avatar"), pb), 2 * ar, l["nom"]), (ax - ar, yc - ar))
        d = _D(img)
        # nom + titre
        tx = ax + ar + 22 * S
        d.text((tx, yc - 2 * S), _tronquer(d, l["nom"], CINZEL(25), 250 * S), font=CINZEL(25), fill=(236, 246, 240), anchor="ls")
        if l.get("titre"):
            d.text((tx + S, yc + 24 * S), _tronquer(d, l["titre"], CORM_I(20), 250 * S), font=CORM_I(20), fill=PALE, anchor="ls")
        # badges des étapes reçues (3 dernières)
        bx = 520 * S
        for b in (l.get("badges") or [])[-3:]:
            t = 30 * S
            img.alpha_composite(_pastille(await _telecharger(b.get("icone"), pb), b["fond"], t, False), (int(bx), int(yc - t / 2)))
            bx += 36 * S
        # barre d'XP
        d = _D(img)
        b0, b1 = 640 * S, xd - 110 * S
        ratio = l["fait"] / l["besoin"] if l.get("besoin") else 1
        d.rounded_rectangle((b0, yc - 12 * S, b1, yc), 6 * S, fill=(255, 255, 255, 40))
        d.rounded_rectangle((b0, yc - 12 * S, b0 + max(12 * S, (b1 - b0) * ratio), yc), 6 * S, fill=VERT)
        txt = f"{abrege(l['fait'])} / {abrege(l['besoin'])} XP" if l.get("besoin") else "niveau max"
        d.text((b0, yc + 22 * S), txt, font=INTER(13, "Medium"), fill=PALE, anchor="ls")
        # niveau
        nx = xd - 52 * S
        _espace(d, (nx, yc - 20 * S), "NIV.", INTER(10, "Bold"), (200, 230, 220), 2)
        d.text((nx, yc + 14 * S), str(l["niveau"]), font=CINZEL(34), fill=(255, 240, 200), anchor="mm")
    _D(img).text((W / 2, H - 16 * S), "Tape /niveau pour voir ta carte", font=CORM_I(19), fill=(150, 175, 168), anchor="ms")
    return _sortie(img, LARGEUR, h)


def abrege(v) -> str:
    v = float(v or 0)
    if v >= 1_000_000:
        return f"{v / 1_000_000:.1f}".rstrip("0").rstrip(".").replace(".", ",") + " M"
    if v >= 10_000:
        return f"{v / 1000:.1f}".rstrip("0").rstrip(".").replace(".", ",") + " k"
    return _nb(v)
