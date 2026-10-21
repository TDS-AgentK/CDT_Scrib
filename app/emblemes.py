"""Tirages d'emblèmes (objets eco_objets dont le champ « emb_tirage » est rempli, voir scripts/setup-emblemes.mjs du site).

/utiliser objet:<Tirage …> [quantite:N] → le joueur choisit la collection (boutons « Collection 1256 / 1257 ») →
5 emblèmes différents par tirage, 5×N tous différents pour N tirages ouverts d'un coup.
- Pools : Lignée (Lignée, Famille, Maison), Faction, Personnages, Emblème (toutes catégories), Leveinard (toutes
  catégories, seulement les emblèmes que le joueur n'a pas). Hors Leveinard, un emblème déjà possédé peut sortir :
  « déjà possédé », il rapporte emb_or_doublon (0 par défaut).
- Pool plus petit que 5×N : seuls les tirages nécessaires sont consommés (⌈emblèmes donnés / 5⌉), le reste est
  rendu (jamais retiré). Pool vide : rien n'est consommé.
- Pondération (tirage pondéré sans remise) : un emblème dont le personnage, la faction ou la lignée apparaît dans
  les emb_nb_entrees_chronologie dernières entrées de la chronologie a emb_poids_chronologie fois plus de chances.
- Journal emb_tirages : une ligne par emblème obtenu (type de tirage, tirages consommés, lot commun).
La possession est la relation emblemes.joueur_en_possession (fiches « membres », comme sur le site).
"""
import math
import os
import random
import uuid
from datetime import datetime, timezone

from app import eco_vues
from app.eco_actions import _gain, changer_or, quantite, retirer_objet
from app.pocketbase import echapper

PAR_TIRAGE = 5
SERIES = ("1256", "1257")
TYPES = {
    "lignee": ("Tirage de Lignée", ("Lignée", "Famille", "Maison")),
    "faction": ("Tirage de Faction", ("Faction",)),
    "personnages": ("Tirage de Personnages", ("Personnage",)),
    "embleme": ("Tirage d'Emblème", None),
    "leveinard": ("Tirage Leveinard", None),
}
EMOJI_SERIE = {"1256": "🩵", "1257": "🩷"}
COULEURS_DEFAUT = {"1256": 0x1F9E98, "1257": 0xD6336C}
EMOJI_CATEGORIE = {"Personnage": "👤", "Faction": "⚔️", "Lignée": "🌳", "Famille": "🏠", "Maison": "🏰"}
MAX_IMAGES = 9  # 10 embeds par message Discord : le récapitulatif + 9 images
SITE_PAR_DEFAUT = "https://cdtsite-production.up.railway.app"
_en_cours: set[str] = set()  # messages dont un bouton de collection est déjà traité (double clic)


def type_de(objet: dict | None) -> str | None:
    t = (objet or {}).get("emb_tirage")
    return t if t in TYPES else None


def serie_de(e: dict) -> str:
    return "1257" if e.get("serie") in ("1257", "nouvelle") else "1256"


def possede(e: dict, membre_id: str) -> bool:
    return membre_id in (e.get("joueur_en_possession") or [])


def pool(emblemes: list[dict], type_tirage: str, serie: str, membre_id: str) -> list[dict]:
    categories = TYPES[type_tirage][1]
    return [e for e in emblemes if serie_de(e) == serie and (categories is None or e.get("collection") in categories)
            and not (type_tirage == "leveinard" and possede(e, membre_id))]


def plan(demandes: int, taille_pool: int) -> tuple[int, int]:
    """(tirages consommés, emblèmes donnés) pour `demandes` tirages ouverts sur un pool de `taille_pool` emblèmes."""
    donnes = min(PAR_TIRAGE * max(0, demandes), max(0, taille_pool))
    return math.ceil(donnes / PAR_TIRAGE), donnes


def poids_de(e: dict, mis_en_avant: set[str], poids: float) -> float:
    lie = {e.get("personnage"), e.get("faction"), e.get("lignee")} - {None, ""}
    return poids if poids > 0 and lie & mis_en_avant else 1.0


def tirer(candidats: list[dict], k: int, mis_en_avant: set[str], poids: float, rng: random.Random | None = None) -> list[dict]:
    """Tirage pondéré sans remise de k emblèmes (méthode d'Efraimidis-Spirakis : clé u^(1/w), on garde les k plus grandes)."""
    rng = rng or random.Random()
    cles = [(rng.random() ** (1 / poids_de(e, mis_en_avant, poids)), i) for i, e in enumerate(candidats)]
    return [candidats[i] for _, i in sorted(cles, reverse=True)[:k]]


async def mis_en_avant(pb, nb_entrees: int) -> set[str]:
    """Ids des personnages, factions et lignées (familles des personnages) des dernières entrées de la chronologie."""
    if nb_entrees <= 0:
        return set()
    params = {"page": 1, "perPage": nb_entrees, "skipTotal": 1, "sort": "-date", "fields": "portraits,factions"}
    entrees = (await pb.requete("GET", "/api/collections/chronologie/records", params=params))["items"]
    persos = {p for c in entrees for p in (c.get("portraits") or [])}
    ids = persos | {f for c in entrees for f in (c.get("factions") or [])}
    if persos:
        filtre = " || ".join(f'id="{echapper(p)}"' for p in persos)
        for p in await pb.lister("personnages", filtre):
            ids |= set(p.get("familles") or [])
    return ids


def reglages_emb(cfg: dict) -> dict:
    r = cfg.get("reglages") or {}
    nombre = lambda k, d: d if r.get(k) is None else r.get(k)
    return {"poids": float(nombre("emb_poids_chronologie", 3)), "entrees": int(nombre("emb_nb_entrees_chronologie", 3)),
            "or_doublon": int(r.get("emb_or_doublon") or 0)}


def _date() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.000Z")


# ---------------------------------------------------------------- choix de la collection

async def menu(eco, membre, objet: dict, demandes: int) -> tuple[dict, list]:
    """Embed + boutons « Collection 1256 / 1257 » (avec le nombre d'emblèmes disponibles ; bouton grisé si vide)."""
    joueur = await eco.joueur_de(membre)
    if not joueur:
        return eco_vues.erreur("Aucune fiche joueur liée à ton compte Discord."), []
    _, stock = await quantite(eco, joueur["id"], objet["id"])
    if stock < 1:
        return eco_vues.erreur(f"Tu n'as pas de {objet['nom']}."), []
    n = max(1, min(int(demandes or 1), stock))
    t = type_de(objet)
    emblemes = await eco.pb.lister("emblemes")
    boutons = []
    for s in SERIES:
        taille = len(pool(emblemes, t, s, joueur.get("joueur") or ""))
        suffixe = f" · {taille} à trouver" if t == "leveinard" else ""
        boutons.append({"type": 2, "style": 2, "emoji": {"name": EMOJI_SERIE[s]}, "label": f"Collection {s}{suffixe}",
                        "custom_id": f"eco:emb:{membre.id}:{objet['id']}:{n}:{s}", "disabled": taille == 0})
    plafond = f" (plafonné à ton stock : {stock})" if int(demandes or 1) > stock else ""
    embed = {"title": f"{objet.get('emoji') or '🎴'} {objet['nom']}".strip(), "color": eco_vues.OR_DEFAUT,
             "description": f"{membre.mention}, tu ouvres **{n}** tirage{'s' if n > 1 else ''}{plafond} : "
                            f"**{PAR_TIRAGE * n}** emblèmes différents.\nDans quelle collection ?"}
    return embed, [{"type": 1, "components": boutons}]


# ---------------------------------------------------------------- tirage

async def ouvrir(eco, membre, objet_id: str, demandes: int, serie: str, rng: random.Random | None = None) -> dict:
    """Ouvre jusqu'à `demandes` tirages de l'objet dans la collection `serie`. Renvoie un dict pour resultat_embeds()
    (clé « erreur » si rien n'a été fait)."""
    if serie not in SERIES:
        return {"erreur": "Collection inconnue."}
    joueur = await eco.joueur_de(membre)
    if not joueur:
        return {"erreur": "Aucune fiche joueur liée à ton compte Discord."}
    membre_id = joueur.get("joueur") or ""
    if not membre_id:
        return {"erreur": "Ta fiche joueur n'est reliée à aucun membre du site : préviens un admin."}
    objet = await eco.pb.requete("GET", f"/api/collections/eco_objets/records/{objet_id}")
    t = type_de(objet)
    if not t:
        return {"erreur": f"**{objet.get('nom')}** n'est pas un tirage d'emblèmes."}
    cfg = await eco.config()
    reg = reglages_emb(cfg)
    async with eco._verrou(membre.id):
        _, stock = await quantite(eco, joueur["id"], objet["id"])
        if stock < 1:
            return {"erreur": f"Tu n'as plus de {objet['nom']}."}
        n = max(1, min(int(demandes or 1), stock))
        emblemes = await eco.pb.lister("emblemes")
        candidats = pool(emblemes, t, serie, membre_id)
        consommes, nb = plan(n, len(candidats))
        if not nb:
            vide = "tu possèdes déjà tous ses emblèmes" if t == "leveinard" else "elle ne contient aucun emblème pour ce tirage"
            return {"erreur": f"Collection {serie} : {vide}. Aucun tirage n'a été utilisé."}
        avant = await mis_en_avant(eco.pb, reg["entrees"]) if reg["poids"] != 1 else set()
        tires = tirer(candidats, nb, avant, reg["poids"], rng)
        await retirer_objet(eco, joueur["id"], objet["id"], consommes, "utilisation", f"{consommes} tirage(s), collection {serie}")
        lot, lignes = uuid.uuid4().hex[:12], []
        for e in tires:
            doublon = possede(e, membre_id)
            if not doublon:
                await eco.pb.maj("emblemes", e["id"], {"joueur_en_possession+": membre_id})
                e["joueur_en_possession"] = [*(e.get("joueur_en_possession") or []), membre_id]
            or_ = reg["or_doublon"] if doublon else 0
            if or_:
                await changer_or(eco, joueur["id"], or_)
                await _gain(eco, joueur["id"], f"utiliser : {objet['nom']} (doublon)", "Or", or_, "")
            await eco.pb.creer("emb_tirages", {
                "joueur": joueur["id"], "objet": objet["id"], "serie": serie, "categorie": e.get("collection") or "",
                "embleme": e["id"], "embleme_nom": e.get("emblem_name") or "", "doublon": doublon,
                "pondere": poids_de(e, avant, reg["poids"]) != 1, "or_doublon": or_, "date": _date(),
                "type_tirage": t, "tirages_consommes": consommes, "lot": lot})
            lignes.append({"e": e, "doublon": doublon, "or": or_})
    total = [e for e in emblemes if serie_de(e) == serie]
    return {"objet": objet, "type": t, "serie": serie, "demandes": n, "consommes": consommes, "rendus": n - consommes,
            "lignes": lignes, "obtenus": sum(possede(e, membre_id) for e in total), "total": len(total),
            "or_txt": eco_vues.or_txt(cfg), "membre": membre}


def image_url(ref: str | None) -> str:
    ref = (ref or "").strip()
    if not ref:
        return ""
    if ref.startswith("wix:"):
        ident = ref.split("//v1/", 1)[-1].split("/", 1)[0].split("#", 1)[0]
        return f"https://static.wixstatic.com/media/{ident}"
    if ref.startswith("/"):
        return (os.getenv("SITE_URL") or SITE_PAR_DEFAUT).rstrip("/") + ref
    return ref if ref.startswith("http") else f"https://static.wixstatic.com/media/{ref}"


def resultat_embeds(r: dict) -> list[dict]:
    """Récapitulatif (nouveaux en premier, catégorie, collection, progression) puis galerie des nouveaux."""
    if r.get("erreur"):
        return [eco_vues.erreur(r["erreur"])]
    serie, couleur = r["serie"], COULEURS_DEFAUT[r["serie"]]
    nouveaux = [l for l in r["lignes"] if not l["doublon"]]
    doublons = [l for l in r["lignes"] if l["doublon"]]

    def ligne(l) -> str:
        e = l["e"]
        cat = e.get("collection") or "?"
        num = f" n°{e['numero']}" if e.get("numero") else ""
        if l["doublon"]:
            gain = f" (+{l['or']} {r['or_txt']})" if l["or"] else ""
            return f"▫️ {e.get('emblem_name')} · {cat}{num} — *déjà possédé*{gain}"
        return f"{EMOJI_CATEGORIE.get(cat, '✨')} **{e.get('emblem_name')}** · {cat}{num}"

    texte = [ligne(l) for l in nouveaux + doublons]
    description, coupe = "", 0
    for i, t in enumerate(texte):
        if len(description) + len(t) + 60 > 3900:
            coupe = len(texte) - i
            break
        description += t + "\n"
    if coupe:
        description += f"… et {coupe} autre{'s' if coupe > 1 else ''}."
    n, c = r["demandes"], r["consommes"]
    entete = f"{r['membre'].mention} ouvre **{c}** × {r['objet']['nom']} — {EMOJI_SERIE[serie]} Collection {serie}\n"
    if r["rendus"]:
        entete += f"Il ne restait que **{len(r['lignes'])}** emblème{'s' if len(r['lignes']) > 1 else ''} à tirer : " \
                  f"{r['rendus']} tirage{'s' if r['rendus'] > 1 else ''} sur {n} non utilisé{'s' if r['rendus'] > 1 else ''}, gardé{'s' if r['rendus'] > 1 else ''} dans ton inventaire.\n"
    pct = round(100 * r["obtenus"] / (r["total"] or 1))
    recap = {
        "title": f"🎴 {len(nouveaux)} nouveau{'x' if len(nouveaux) != 1 else ''} emblème{'s' if len(nouveaux) != 1 else ''}"
                 + (f" · {len(doublons)} déjà possédé{'s' if len(doublons) > 1 else ''}" if doublons else ""),
        "description": (entete + "\n" + description).strip(), "color": couleur,
        "footer": {"text": f"Collection {serie} : {r['obtenus']} / {r['total']} emblèmes ({pct} %)"},
    }
    site = (os.getenv("SITE_URL") or SITE_PAR_DEFAUT).rstrip("/")
    images = [l["e"] for l in nouveaux if image_url(l["e"].get("illustration"))][:MAX_IMAGES]
    galerie = []
    for i, e in enumerate(images):
        # Discord regroupe en galerie (4 images) les embeds consécutifs qui ont la même url.
        url = f"{site}/mon-espace#emblemes-{i // 4}"
        if i % 4 == 0:
            galerie.append({"url": url, "color": couleur, "image": {"url": image_url(e.get("illustration"))},
                            "title": "Nouveaux emblèmes" if i == 0 else None})
        else:
            galerie.append({"url": url, "image": {"url": image_url(e.get("illustration"))}})
    galerie = [{k: v for k, v in g.items() if v is not None} for g in galerie]
    if len(nouveaux) > len(images) and images:
        recap["footer"]["text"] += f" · {len(images)} image{'s' if len(images) > 1 else ''} sur {len(nouveaux)}"
    return [recap, *galerie]


async def clic(eco, membre, payload: dict, morceaux: list[str]) -> list[dict] | None:
    """Bouton « Collection … » (custom_id eco:emb:<discord>:<objet>:<n>:<serie>) : None si ce n'est pas au joueur."""
    if str(membre.id) != morceaux[2]:
        return None
    cle = str((payload.get("message") or {}).get("id") or payload.get("token"))
    if cle in _en_cours:
        return []
    _en_cours.add(cle)
    try:
        return resultat_embeds(await ouvrir(eco, membre, morceaux[3], int(morceaux[4]), morceaux[5]))
    finally:
        if len(_en_cours) > 500:
            _en_cours.clear()
