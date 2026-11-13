"""Liste des RP (demande de K du 13/11/2026) : RP proposés par les joueurs, partagés avec la page /liste-des-rp du site.

Collection `rp_demandes` du site (scripts/setup-rp-demandes.mjs) : le bot écrit en superuser, comme l'économie.

- /proposer-rp categorie [chroniqueur-pret] [illustration] : ouvre la fenêtre « Proposer un RP » (Titre, De quoi ça
  parle ?, Personnage). Aucun état en mémoire : catégorie, chroniqueur et illustration voyagent dans le custom_id de la
  fenêtre (« eco:rpm:<cat>:<0|1>:<ref> »). L'adresse d'une pièce jointe Discord dépasse presque toujours les 100
  caractères d'un custom_id : on crée alors un brouillon (statut « brouillon », invisible sur le site) qui garde
  l'adresse, et la fenêtre porte son ID (« b<id> »). Les brouillons abandonnés sont effacés au bout de 24 h.
  À l'envoi : fiche complétée (auteur retrouvé par ID Discord), image téléversée dans la fiche, annonce postée dans le
  salon réglé sur le site (eco_reglages.salon_rp_annonces, sinon le salon de la commande), lien noté sur la fiche.
- Bouton « Gérer » de l'annonce (auteur et admins) : réponse privée avec Chroniqueur prêt / En cours de création,
  Jouée, Annuler ; l'annonce est mise à jour aussitôt.
- /je-veux-rp [categorie] : tire au hasard un RP proposé dont le chroniqueur est prêt ; « Un autre » retire sans
  retomber sur le même (ID exclu dans le custom_id).
- Boucle (1 min) : annonces des fiches modifiées depuis le site (statut, chroniqueur, titre…) remises à jour ;
  une fiche annulée laisse une annonce grisée et barrée, sans boutons.

Catégories, couleurs et libellés : les mêmes que lib/rpDemandes.ts du site (changer les deux ensemble).
"""
import asyncio
import hashlib
import logging
import os
import random
import re
import unicodedata
from datetime import datetime, timedelta, timezone

import httpx

from app.pocketbase import echapper

log = logging.getLogger("cdt_scrib.rp_demandes")

SITE_PAR_DEFAUT = "https://cdtsite-production.up.railway.app"
COLLECTION = "rp_demandes"
SYNCHRO_S = 60
BROUILLON_MAX = timedelta(hours=24)
ADMINS = {"293135204371922946", "286482667023892480"}  # Agent K et Kyanite (comme les loteries)
GRIS = 0x99AAB5
TYPES_IMAGE = {"image/png", "image/jpeg", "image/gif", "image/webp"}
IMAGE_MAX = 10 * 1024 * 1024

# cle -> (libellé, couleur, emoji)
CATEGORIES = {
    "decouverte": ("RP découverte", 0x7FC8A0, "🧭"),
    "aventure": ("RP aventure", 0xD99A62, "⚔️"),
    "bavard": ("RP bavard", 0x8FB8E6, "💬"),
    "wtf": ("RP wtf am I doin'", 0xE98FB0, "🌀"),
    "magie": ("RP maaaagic", 0xB9A0F2, "✨"),
}


def site() -> str:
    return (os.getenv("SITE_URL") or SITE_PAR_DEFAUT).rstrip("/")


def lien_site(fiche_id: str = "") -> str:
    return f"{site()}/liste-des-rp" + (f"#{fiche_id}" if fiche_id else "")


def categorie(cle) -> tuple[str, int, str]:
    return CATEGORIES.get(cle) or CATEGORIES["decouverte"]


def url_illustration(fiche: dict) -> str:
    """Adresse publique de l'illustration (PB_PUBLIC_URL si PB_URL est une adresse interne)."""
    if not fiche.get("illustration"):
        return ""
    base = (os.getenv("PB_PUBLIC_URL") or os.getenv("PB_URL") or "").rstrip("/")
    return f"{base}/api/files/{COLLECTION}/{fiche['id']}/{fiche['illustration']}" if base else ""


# ---------------------------------------------------------------- custom_id de la fenêtre

def id_fenetre(cle: str, pret: bool, ref: str = "") -> str:
    """« eco:rpm:<cat>:<0|1>:<ref> » ; ref = « u<adresse> » (si ça tient), « b<id du brouillon> » ou vide."""
    return f"eco:rpm:{cle}:{int(bool(pret))}:{ref}"


def ref_illustration(url: str, brouillon_id: str | None = None) -> str | None:
    """Référence de l'illustration pour le custom_id ; None si l'adresse ne tient pas (il faut un brouillon)."""
    if not url:
        return ""
    if len(id_fenetre("decouverte", True, "u" + url)) <= 100:
        return "u" + url
    return f"b{brouillon_id}" if brouillon_id else None


def lire_fenetre(custom_id: str) -> tuple[str, bool, str, str]:
    """(catégorie, chroniqueur prêt, adresse de l'illustration, ID du brouillon)."""
    morceaux = custom_id.split(":", 4)
    cle = morceaux[2] if len(morceaux) > 2 and morceaux[2] in CATEGORIES else "decouverte"
    pret = len(morceaux) > 3 and morceaux[3] == "1"
    ref = morceaux[4] if len(morceaux) > 4 else ""
    if ref.startswith("u"):
        return cle, pret, ref[1:], ""
    if ref.startswith("b"):
        return cle, pret, "", ref[1:]
    return cle, pret, "", ""


def fenetre(cle: str, pret: bool, ref: str) -> dict:
    libelle = categorie(cle)[0]
    return {
        "custom_id": id_fenetre(cle, pret, ref), "title": "Proposer un RP",
        "components": [
            {"type": 1, "components": [{"type": 4, "custom_id": "titre", "label": "Titre", "style": 1, "required": True,
                                        "min_length": 2, "max_length": 100, "placeholder": f"Titre de votre {libelle}"}]},
            {"type": 1, "components": [{"type": 4, "custom_id": "resume", "label": "De quoi ça parle ?", "style": 2, "required": True,
                                        "max_length": 1500, "placeholder": "Le point de départ, l'ambiance, ce qui attend les joueurs…"}]},
            {"type": 1, "components": [{"type": 4, "custom_id": "personnage", "label": "Personnage (facultatif)", "style": 1,
                                        "required": False, "max_length": 100, "placeholder": "Le personnage qui propose ou mène le RP"}]},
        ],
    }


def valeurs_fenetre(data: dict) -> dict:
    sortie = {}
    for rangee in data.get("components", []):
        for c in rangee.get("components", []):
            sortie[c.get("custom_id")] = (c.get("value") or "").strip()
    return sortie


# ---------------------------------------------------------------- annonce

def _norm(x: str) -> str:
    return unicodedata.normalize("NFD", str(x or "")).encode("ascii", "ignore").decode().lower().strip()


def choisir_personnage(tape: str, persos: list[dict]) -> dict | None:
    """Personnage du joueur correspondant au nom tapé : prénom identique (sans accents ni majuscules), sinon unique
    prénom qui contient le texte."""
    t = _norm(tape)
    if not t:
        return None
    exacts = [p for p in persos if _norm(p.get("prenom")) == t]
    if exacts:
        return exacts[0]
    proches = [p for p in persos if t in _norm(p.get("prenom"))]
    return proches[0] if len(proches) == 1 else None


def nom_personnage(fiche: dict) -> str:
    return ((fiche.get("expand") or {}).get("personnage") or {}).get("prenom") or fiche.get("personnage_nom") or ""


def nom_auteur(fiche: dict) -> str:
    return ((fiche.get("expand") or {}).get("auteur") or {}).get("pseudo") or fiche.get("auteur_nom") or "Un joueur"


def embed_annonce(fiche: dict) -> dict:
    libelle, couleur, emoji = categorie(fiche.get("categorie"))
    statut = fiche.get("statut") or "proposee"
    resume = str(fiche.get("resume") or "")
    if len(resume) > 1500:
        resume = resume[:1499] + "…"
    titre = str(fiche.get("titre") or "RP")[:250]
    embed = {
        "author": {"name": f"{nom_auteur(fiche)} propose un RP · {libelle}"[:256]},
        "title": f"{emoji} {titre}", "description": resume, "color": couleur,
        "fields": [{"name": "Chroniqueur", "value": "✅ Prêt" if fiche.get("chroniqueur_pret") else "⏳ En cours de création", "inline": True}],
        "footer": {"text": "Aussi visible sur la Liste des RP du site"},
    }
    if fiche.get("id"):
        embed["url"] = lien_site(fiche["id"])
    perso = nom_personnage(fiche)
    if perso:
        embed["fields"].append({"name": "Personnage", "value": perso[:1024], "inline": True})
    image = url_illustration(fiche)
    if image:
        embed["thumbnail"] = {"url": image}
    if statut == "jouee":
        embed["fields"].append({"name": "Statut", "value": "🎲 RP joué", "inline": True})
    elif statut == "annulee":
        embed.update(color=GRIS, title=f"~~{titre}~~", description=f"❌ **RP annulé**\n~~{resume}~~" if resume else "❌ **RP annulé**")
        embed["author"]["name"] = f"{nom_auteur(fiche)} · RP annulé · {libelle}"[:256]
        embed["fields"] = [f for f in embed["fields"] if f["name"] != "Chroniqueur"]
        embed.pop("thumbnail", None)
    return embed


def composants_annonce(fiche: dict) -> list:
    if (fiche.get("statut") or "proposee") == "annulee":
        return []
    return [{"type": 1, "components": [
        {"type": 2, "style": 5, "label": "Voir sur le site", "url": lien_site(fiche.get("id", ""))},
        {"type": 2, "style": 2, "label": "Gérer", "custom_id": f"eco:rp:g:{fiche.get('id', '')}", "emoji": {"name": "⚙️"}},
    ]}]


def signature(fiche: dict) -> str:
    """Ce que montre l'annonce : changée sur le site, l'annonce est remise à jour."""
    morceaux = [fiche.get("statut") or "proposee", "1" if fiche.get("chroniqueur_pret") else "0", fiche.get("titre") or "",
                fiche.get("resume") or "", fiche.get("categorie") or "", nom_personnage(fiche), fiche.get("illustration") or "",
                nom_auteur(fiche)]
    return hashlib.sha1("\x1f".join(map(str, morceaux)).encode()).hexdigest()[:16]


def message_de(fiche: dict) -> tuple[str, str]:
    """(salon, message) de l'annonce, depuis « salonId/messageId »."""
    m = re.fullmatch(r"(\d+)/(\d+)", str(fiche.get("message_discord") or ""))
    return (m.group(1), m.group(2)) if m else ("", "")


# ---------------------------------------------------------------- Discord (REST, jeton du bot)

async def _discord(methode: str, chemin: str, corps: dict | None = None) -> dict | None:
    """Appel à l'API Discord avec le jeton du bot ; None en cas d'échec (journalisé)."""
    try:
        async with httpx.AsyncClient() as client:
            r = await client.request(methode, f"https://discord.com/api/v10{chemin}", json=corps, timeout=15,
                                     headers={"Authorization": f'Bot {os.environ.get("DISCORD_TOKEN", "")}'})
        if r.status_code >= 400:
            log.error("Discord %s %s -> %s %s", methode, chemin, r.status_code, r.text[:300])
            return None
        return r.json() if r.content else {}
    except httpx.HTTPError as exc:
        log.error("Erreur réseau vers Discord : %s", exc)
        return None


async def editer_annonce(fiche: dict) -> bool:
    salon, message = message_de(fiche)
    if not message:
        return False
    return await _discord("PATCH", f"/channels/{salon}/messages/{message}",
                          {"embeds": [embed_annonce(fiche)], "components": composants_annonce(fiche)}) is not None


# ---------------------------------------------------------------- droits

def est_admin(membre) -> bool:
    if getattr(getattr(membre, "guild_permissions", None), "administrator", False):
        return True
    return str(getattr(membre, "id", "")) in ADMINS


def peut_gerer(membre, fiche: dict) -> bool:
    return est_admin(membre) or (bool(fiche.get("auteur_discord_id")) and str(membre.id) == str(fiche["auteur_discord_id"]))


# ---------------------------------------------------------------- /proposer-rp

async def commande_proposer(eco, membre, data: dict) -> dict:
    """Réponse immédiate à /proposer-rp : la fenêtre (type 9), ou un message d'erreur privé."""
    opt = {o["name"]: o.get("value") for o in data.get("options", [])}
    cle = opt.get("categorie") if opt.get("categorie") in CATEGORIES else "decouverte"
    pret = bool(opt.get("chroniqueur-pret"))
    url = ""
    if opt.get("illustration"):
        piece = ((data.get("resolved") or {}).get("attachments") or {}).get(str(opt["illustration"])) or {}
        if str(piece.get("content_type") or "").split(";")[0] not in TYPES_IMAGE:
            return _prive("L'illustration doit être une image (PNG, JPEG, GIF ou WebP).")
        if int(piece.get("size") or 0) > IMAGE_MAX:
            return _prive("L'illustration dépasse 10 Mo.")
        url = piece.get("url") or ""
    ref = ref_illustration(url)
    if ref is None:  # adresse trop longue pour le custom_id : brouillon qui la garde jusqu'à l'envoi de la fenêtre
        try:
            b = await eco.pb.creer(COLLECTION, {"titre": "(brouillon)", "categorie": cle, "statut": "brouillon",
                                               "auteur_discord_id": str(membre.id), "message_discord": "", "illustration_source": url})
        except Exception:
            log.exception("Brouillon de /proposer-rp non créé")
            return _prive("La Liste des RP est injoignable pour l'instant, réessayez dans un instant.")
        ref = ref_illustration(url, b["id"])
    return {"type": 9, "data": fenetre(cle, pret, ref)}


def _prive(texte: str) -> dict:
    return {"type": 4, "data": {"embeds": [{"description": texte, "color": 0xC0392B}], "flags": 1 << 6}}


async def _telecharger(url: str) -> tuple[bytes, str] | None:
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(url, timeout=20)
        if r.status_code >= 400:
            return None
        type_mime = r.headers.get("content-type", "").split(";")[0]
        if type_mime not in TYPES_IMAGE or len(r.content) > IMAGE_MAX:
            return None
        return r.content, type_mime
    except httpx.HTTPError:
        return None


async def salon_annonces(eco, salon_commande) -> str:
    try:
        cfg = await eco.config()
        salon = str((cfg.get("reglages") or {}).get("salon_rp_annonces") or "")
    except Exception:
        salon = ""
    return salon if salon.isdigit() else str(salon_commande or "")


async def envoyer_proposition(eco, membre, payload: dict) -> tuple[bool, str]:
    """Fenêtre « Proposer un RP » envoyée : fiche, illustration, annonce. Renvoie (ok, texte de la réponse privée)."""
    data = payload["data"]
    cle, pret, url, brouillon_id = lire_fenetre(data.get("custom_id", ""))
    v = valeurs_fenetre(data)
    titre, resume, tape = v.get("titre", "")[:200], v.get("resume", "")[:4000], v.get("personnage", "")[:200]
    if len(titre) < 2:
        return False, "Il faut un titre."
    pb = eco.pb
    brouillon = None
    if brouillon_id:
        brouillon = await pb.premier(COLLECTION, f'id="{echapper(brouillon_id)}" && statut="brouillon"')
        if brouillon and str(brouillon.get("auteur_discord_id")) == str(membre.id):
            url = brouillon.get("illustration_source") or ""
        else:
            brouillon = None
    joueur = await eco.joueur_de(membre)
    perso = None
    if tape and joueur and joueur.get("pseudo"):
        persos = await pb.lister("personnages", f'joueur="{echapper(joueur["pseudo"])}"')
        perso = choisir_personnage(tape, persos)
    champs = {
        "titre": titre, "categorie": cle, "resume": resume, "chroniqueur_pret": pret, "statut": "proposee",
        "auteur": joueur["id"] if joueur else "", "auteur_discord_id": str(membre.id),
        "auteur_nom": getattr(membre, "display_name", "") or getattr(membre, "name", ""),
        "personnage": perso["id"] if perso else "", "personnage_nom": "" if perso else tape,
        "illustration_source": "",
    }
    fiche = await (pb.maj(COLLECTION, brouillon["id"], champs) if brouillon else pb.creer(COLLECTION, champs))
    image_ratee = False
    if url:
        fichier = await _telecharger(url)
        if fichier:
            ext = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp"}[fichier[1]]
            try:
                fiche = await pb.envoyer_fichier(COLLECTION, fiche["id"], "illustration", f"illustration.{ext}", fichier[0], fichier[1])
            except Exception:
                log.exception("Illustration du RP %s non téléversée", fiche["id"])
                image_ratee = True
        else:
            image_ratee = True
    fiche.setdefault("expand", {})
    if joueur:
        fiche["expand"]["auteur"] = joueur
    if perso:
        fiche["expand"]["personnage"] = perso
    salon = await salon_annonces(eco, payload.get("channel_id"))
    poste = await _discord("POST", f"/channels/{salon}/messages", {
        "embeds": [embed_annonce(fiche)], "components": composants_annonce(fiche), "allowed_mentions": {"parse": []},
    }) if salon else None
    suite = " (l'illustration n'a pas pu être récupérée)" if image_ratee else ""
    if not poste:
        return True, (f"RP **{titre}** enregistré sur la Liste des RP{suite}, mais l'annonce n'a pas pu être postée "
                      f"(permissions du bot dans le salon ?). Voir : {lien_site(fiche['id'])}")
    lien = f"https://discord.com/channels/{payload.get('guild_id')}/{salon}/{poste['id']}"
    await pb.maj(COLLECTION, fiche["id"], {"message_discord": f"{salon}/{poste['id']}", "lien_discord": lien,
                                           "discord_etat": signature(fiche)})
    return True, f"RP **{titre}** proposé{suite} : {lien}"


# ---------------------------------------------------------------- bouton « Gérer »

def vue_gestion(fiche: dict) -> dict:
    """Message privé de gestion d'un RP (auteur et admins)."""
    fid, statut = fiche["id"], fiche.get("statut") or "proposee"
    boutons = []
    if statut == "proposee":
        boutons.append({"type": 2, "style": 2, "label": "En cours de création", "custom_id": f"eco:rp:a:{fid}:encours", "emoji": {"name": "⏳"}}
                       if fiche.get("chroniqueur_pret") else
                       {"type": 2, "style": 3, "label": "Chroniqueur prêt", "custom_id": f"eco:rp:a:{fid}:pret", "emoji": {"name": "✅"}})
        boutons.append({"type": 2, "style": 1, "label": "Jouée", "custom_id": f"eco:rp:a:{fid}:jouee", "emoji": {"name": "🎲"}})
    elif statut == "jouee":
        boutons.append({"type": 2, "style": 2, "label": "Remettre en proposée", "custom_id": f"eco:rp:a:{fid}:rouvrir", "emoji": {"name": "↩️"}})
    if statut != "annulee":
        boutons.append({"type": 2, "style": 4, "label": "Annuler", "custom_id": f"eco:rp:a:{fid}:annuler", "emoji": {"name": "✖️"}})
    etat = {"proposee": "⏳ en cours de création" if not fiche.get("chroniqueur_pret") else "✅ chroniqueur prêt",
            "jouee": "🎲 joué", "annulee": "❌ annulé"}.get(statut, statut)
    return {"embeds": [{"title": f"Gérer « {str(fiche.get('titre') or 'RP')[:200]} »", "color": categorie(fiche.get("categorie"))[1],
                        "description": f"État : {etat}.\nL'entrée de chronologie se relie sur la page Liste des RP du site."}],
            "components": [{"type": 1, "components": boutons}] if boutons else [], "flags": 1 << 6}


ACTIONS = {"pret": {"chroniqueur_pret": True}, "encours": {"chroniqueur_pret": False}, "jouee": {"statut": "jouee"},
           "rouvrir": {"statut": "proposee"}, "annuler": {"statut": "annulee"}}
TEXTES = {"pret": "Chroniqueur prêt ✅", "encours": "Remis en cours de création ⏳", "jouee": "RP marqué joué 🎲",
          "rouvrir": "RP remis en proposée ↩️", "annuler": "RP annulé ❌"}


async def lire_fiche(pb, fiche_id: str) -> dict | None:
    if not re.fullmatch(r"[a-z0-9]{15}", fiche_id or ""):
        return None
    return await pb.premier(COLLECTION, f'id="{fiche_id}" && statut!="brouillon"')


async def appliquer(eco, membre, fiche_id: str, action: str) -> tuple[bool, str, dict | None]:
    """Action du message de gestion : fiche mise à jour, puis annonce Discord."""
    if action not in ACTIONS:
        return False, "Action inconnue.", None
    fiche = await lire_fiche(eco.pb, fiche_id)
    if not fiche:
        return False, "Ce RP n'existe plus.", None
    if not peut_gerer(membre, fiche):
        return False, "Seuls l'auteur du RP et les administrateurs peuvent le gérer.", None
    fiche = await eco.pb.maj(COLLECTION, fiche_id, ACTIONS[action])
    complet = await _avec_expand(eco.pb, fiche)
    if await editer_annonce(complet):
        await eco.pb.maj(COLLECTION, fiche_id, {"discord_etat": signature(complet)})
    return True, TEXTES[action], complet


async def _avec_expand(pb, fiche: dict) -> dict:
    try:
        return await pb.requete("GET", f"/api/collections/{COLLECTION}/records/{fiche['id']}", params={"expand": "auteur,personnage"})
    except Exception:
        return fiche


# ---------------------------------------------------------------- /je-veux-rp

def tirer(fiches: list[dict], exclu: str = "", hasard=random) -> dict | None:
    """Un RP au hasard, sans retomber sur `exclu` s'il y en a d'autres."""
    autres = [f for f in fiches if f.get("id") != exclu] or fiches
    return hasard.choice(autres) if autres else None


def id_autre(cle: str, exclu: str) -> str:
    return f"eco:rp:t:{cle or '-'}:{exclu}"


async def candidats(pb, cle: str = "") -> list[dict]:
    filtre = 'statut="proposee" && chroniqueur_pret=true'
    if cle in CATEGORIES:
        filtre += f' && categorie="{cle}"'
    return await pb.lister(COLLECTION, filtre, expand="auteur,personnage")


def vue_tirage(fiches: list[dict], cle: str = "", exclu: str = "", hasard=random) -> dict:
    """Message du tirage (embeds + composants)."""
    f = tirer(fiches, exclu, hasard)
    if not f:
        quoi = f" dans la catégorie {categorie(cle)[0]}" if cle in CATEGORIES else ""
        return {"embeds": [{"title": "Le registre est vide", "color": GRIS,
                            "description": f"Aucun RP proposé avec un chroniqueur prêt{quoi} pour l'instant.\n"
                                           "Proposez le vôtre avec **/proposer-rp** !"}],
                "components": [{"type": 1, "components": [{"type": 2, "style": 5, "label": "Voir la Liste des RP", "url": lien_site()}]}]}
    libelle, couleur, emoji = categorie(f.get("categorie"))
    resume = str(f.get("resume") or "")
    embed = {"author": {"name": f"Le registre a parlé · {libelle}"}, "title": f"{emoji} {str(f.get('titre') or 'RP')[:250]}",
             "url": lien_site(f["id"]), "description": resume[:1500] + ("…" if len(resume) > 1500 else ""), "color": couleur,
             "fields": [{"name": "Proposé par", "value": nom_auteur(f), "inline": True}],
             "footer": {"text": f"Tiré parmi {len(fiches)} RP proposé{'s' if len(fiches) > 1 else ''} et prêt{'s' if len(fiches) > 1 else ''}"}}
    perso = nom_personnage(f)
    if perso:
        embed["fields"].append({"name": "Personnage", "value": perso[:1024], "inline": True})
    image = url_illustration(f)
    if image:
        embed["thumbnail"] = {"url": image}
    boutons = [{"type": 2, "style": 1, "label": "Un autre", "custom_id": id_autre(cle, f["id"]), "emoji": {"name": "🎲"},
                "disabled": len(fiches) < 2}]
    if f.get("lien_discord"):
        boutons.append({"type": 2, "style": 5, "label": "Voir l'annonce", "url": f["lien_discord"]})
    boutons.append({"type": 2, "style": 5, "label": "Voir sur le site", "url": lien_site(f["id"])})
    return {"embeds": [embed], "components": [{"type": 1, "components": boutons}]}


# ---------------------------------------------------------------- synchronisation site -> Discord

def _pb_date(d: datetime) -> str:
    return d.strftime("%Y-%m-%d %H:%M:%S.000Z")


async def synchroniser(pb, editer, depuis: datetime | None, maintenant: datetime) -> int:
    """Annonces des fiches modifiées depuis `depuis` (toutes au premier tour) remises à jour si ce qu'elles montrent a
    changé ; brouillons abandonnés effacés. Renvoie le nombre d'annonces modifiées."""
    filtre = 'message_discord!="" && statut!="brouillon"'
    if depuis:
        filtre += f' && updated>="{_pb_date(depuis)}"'
    n = 0
    for f in await pb.lister(COLLECTION, filtre, expand="auteur,personnage"):
        sig = signature(f)
        if sig == f.get("discord_etat"):
            continue
        if await editer(f):
            n += 1
        # Annonce effacée ou salon inaccessible : on note quand même, pour ne pas réessayer chaque minute.
        await pb.maj(COLLECTION, f["id"], {"discord_etat": sig})
    for b in await pb.lister(COLLECTION, f'statut="brouillon" && created<"{_pb_date(maintenant - BROUILLON_MAX)}"'):
        await pb.supprimer(COLLECTION, b["id"])
    return n


async def boucle(eco):
    await eco.client.wait_until_ready()
    depuis = None
    while not eco.client.is_closed():
        debut = datetime.now(timezone.utc)
        try:
            await synchroniser(eco.pb, editer_annonce, depuis, debut)
            depuis = debut - timedelta(seconds=5)  # marge : horloges du bot et de PocketBase
        except Exception:
            log.exception("Liste des RP : synchronisation des annonces")
        await asyncio.sleep(SYNCHRO_S)
