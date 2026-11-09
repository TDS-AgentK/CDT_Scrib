"""Calendrier de l'Avent des Fêtes de Lanjis : le même calendrier que la page /avent du site, ouvert depuis Discord.

Tout se règle sur la page « Calendrier de l'Avent » du site (Saisonniers). Collections (créées par
scripts/setup-avent.mjs du site) :
- avent_reglages (cle « general ») : annee, ouvert, heure_ouverture (« 00:00 », heure de Paris), rattrapage_jusqu_au
  (« MM-JJ », inclus), bonus_compte_rattrapees, salon_annonce, rappel (aucun | prive | salon), salon_rappel, heure_rappel,
  afficher_compteur, bonus (récompenses du bonus des 24), lien_site ; derniere_annonce et dernier_rappel (AAAA-MM-JJ),
  écrits par le bot seulement, pour ne poster qu'une fois par jour même après un redémarrage.
- avent_cases : jour (1..25), nom, phrase, recompenses (liste), fragment (ordre du fragment du Conte dévoilé, 0 = aucun)
- avent_fragments : ordre, jour, texte
- avent_ouvertures : joueur, jour (0 = bonus des 24), ouvert_le, rattrapee, source (site | discord), recompenses.
  Index unique (joueur, jour) : on crée l'ouverture AVANT de donner quoi que ce soit, si bien qu'une fenêtre ne
  s'ouvre qu'une fois, site et Discord confondus (deux clics simultanés : un seul passe).

Une récompense est un dict partagé avec le site : type (or, xp, objet, bureau, role, role_temporaire, multiplicateur),
objet, objet_nom, quantite, role_id, role_nom, duree_jours, facteur, monnaie (or | xp | les_deux), salons, jusqu
(minuit | heures), heures.

Sur Discord : l'annonce de minuit dans salon_annonce (boutons 🎁 Ouvrir ma fenêtre · 📅 Mon calendrier · 🔗 Sur le site),
/avent (calendrier personnel, menu de rattrapage) et le rappel de 20 h dans salon_rappel. Les custom_id sont
« eco:av:… » (app/eco_interactions.py). Toutes les réponses aux boutons sont privées.
"""
import asyncio
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import discord
import httpx

from app import eco_vues
from app.pocketbase import echapper

log = logging.getLogger("cdt_scrib.avent")

PARIS = ZoneInfo("Europe/Paris")
NEIGE = 0x9FD3FF
RECHARGE_S = 60
VERIFIER_TOUTES_S = 60
SITE_PAR_DEFAUT = "https://cdtsite-production.up.railway.app"
MOIS = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre")
ORDINAUX = {1: "première", 2: "deuxième", 3: "troisième", 4: "quatrième", 5: "cinquième", 6: "sixième", 7: "septième",
            8: "huitième", 9: "neuvième", 10: "dixième", 11: "onzième", 12: "douzième", 13: "treizième", 14: "quatorzième",
            15: "quinzième", 16: "seizième", 17: "dix-septième", 18: "dix-huitième", 19: "dix-neuvième", 20: "vingtième",
            21: "vingt et unième", 22: "vingt-deuxième", 23: "vingt-troisième", 24: "vingt-quatrième", 25: "vingt-cinquième"}
DEFAUTS = {
    "annee": 2026, "ouvert": False, "heure_ouverture": "00:00", "rattrapage_jusqu_au": "12-25", "bonus_compte_rattrapees": False,
    "salon_annonce": "", "rappel": "aucun", "salon_rappel": "", "heure_rappel": "20:00", "afficher_compteur": True,
    "bonus": [], "lien_site": "", "derniere_annonce": "", "dernier_rappel": "",
}

# Textes (wording validé par K, doc « Calendrier de l'Avent — Wording »).
T_DEJA = "Cette fenêtre est déjà ouverte. La facture de chauffage s'envole, merde alors."
T_FUTURE = "T'es pas Jenaelle ni Héraclite, tu vois pas dans le couscous encore."
T_FERME = "Le calendrier des Fêtes de Lanjis est fermé pour cette année. À lanjis prochain !"
T_ERREUR = "La neige a bloqué la fenêtre. Ne pas hésiter à râler sur Kelora pour ses features buguées."
T_SANS_FICHE = "Aucune fiche joueur liée à ce compte Discord. Lie ton compte sur le site pour ouvrir le calendrier."
T_RATTRAPAGE_FINI = "Le temps du rattrapage est passé : cette fenêtre est restée fermée."
T_BONUS = "Tu as ouvert les 24 fenêtres du village. Le Père Lanjis te laisse un présent de plus."
T_RAPPEL = "🕯️ Ta fenêtre du jour est encore fermée. Le Père Lanjis passe avant minuit…"
T_SPECIAUX = {
    1: "❄️ Les Fêtes de Lanjis commencent ! Chaque nuit jusqu'au 24, une fenêtre du village s'allume. "
       "Ouvre la première ici ou sur le site.",
    24: "🌙 C'est la dernière fenêtre avant la Nuit de Lanjis. Ceux qui auront ouvert les 24 trouveront un bonus demain.",
    25: "🎄 C'est la Nuit de Lanjis ! La grande cheminée est ouverte à tous. Le Conte de Lanjis est complet : "
        "lis-le en entier sur le site.",
}

_cache: dict = {"a": 0.0, "r": None}


def _pb_date(d: datetime | None = None) -> str:
    return (d or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.000Z")


def _heure(texte: str | None, defaut: str = "00:00") -> tuple[int, int]:
    try:
        h, m = (texte or defaut).split(":")[:2]
        return max(0, min(23, int(h))), max(0, min(59, int(m)))
    except (ValueError, AttributeError):
        h, m = defaut.split(":")
        return int(h), int(m)


def date_txt(jour: int) -> str:
    return f"{jour} décembre" if jour != 1 else "1er décembre"


# ---------------------------------------------------------------- réglages et dates

async def reglages(eco, frais: bool = False) -> dict:
    """Réglages du calendrier (valeurs par défaut pour les champs vides), relus au plus toutes les minutes."""
    if not frais and _cache["r"] is not None and time.time() - _cache["a"] < RECHARGE_S:
        return _cache["r"]
    try:
        r = await eco.pb.premier("avent_reglages", 'cle="general"') or {}
    except Exception:  # collection absente (script du site pas encore lancé) : calendrier fermé
        log.warning("Réglages du calendrier de l'Avent illisibles (collection avent_reglages absente ?)")
        r = {}
    fusion = {**DEFAUTS, **{k: v for k, v in r.items() if v not in (None, "")}}
    fusion["id"] = r.get("id", "")
    _cache.update(a=time.time(), r=fusion)
    return fusion


def oublier_cache():
    _cache.update(a=0.0, r=None)


def jour_courant(r: dict, maintenant: datetime | None = None) -> int:
    """Fenêtre allumée à cet instant : 1..25 en décembre de l'année du calendrier (après l'heure d'ouverture),
    25 jusqu'à la fin du rattrapage, 0 avant le 1er décembre, -1 une fois le rattrapage terminé."""
    m = (maintenant or datetime.now(timezone.utc)).astimezone(PARIS)
    annee = int(r.get("annee") or DEFAUTS["annee"])
    h, mn = _heure(r.get("heure_ouverture"))
    m_decale = m - timedelta(hours=h, minutes=mn)  # avant l'heure d'ouverture, on est encore « hier »
    if m_decale.year < annee or (m_decale.year == annee and m_decale.month < 12):
        return 0
    if m_decale.year > annee + 1 or (m_decale.year == annee + 1 and m_decale.month > 1):
        return -1
    if m_decale.year == annee and m_decale.month == 12 and m_decale.day <= 25:
        return m_decale.day
    return 25 if dans_rattrapage(r, m) else -1


def dans_rattrapage(r: dict, maintenant: datetime | None = None) -> bool:
    """Vrai tant que la date limite de rattrapage (MM-JJ de décembre ou de janvier, incluse) n'est pas passée."""
    m = (maintenant or datetime.now(timezone.utc)).astimezone(PARIS)
    annee = int(r.get("annee") or DEFAUTS["annee"])
    try:
        mois, jour = (int(x) for x in str(r.get("rattrapage_jusqu_au") or "12-25").split("-")[:2])
    except ValueError:
        mois, jour = 12, 25
    limite = datetime(annee + (1 if mois < 12 else 0), mois, jour, 23, 59, 59, tzinfo=PARIS)
    return m <= limite


# ---------------------------------------------------------------- données

async def cases(eco) -> dict[int, dict]:
    try:
        return {int(c.get("jour") or 0): c for c in await eco.pb.lister("avent_cases")}
    except Exception:
        log.exception("Cases du calendrier de l'Avent illisibles")
        return {}


async def ouvertures(eco, joueur_id: str) -> dict[int, dict]:
    lignes = await eco.pb.lister("avent_ouvertures", f'joueur="{echapper(joueur_id)}"')
    return {int(o.get("jour") or 0): o for o in lignes}


async def fragment(eco, ordre: int) -> dict | None:
    if not ordre:
        return None
    try:
        return await eco.pb.premier("avent_fragments", f"ordre={int(ordre)}")
    except Exception:
        return None


async def nb_ouvertures_du_jour(eco, jour: int) -> int:
    try:
        return len(await eco.pb.lister("avent_ouvertures", f"jour={int(jour)}"))
    except Exception:
        return 0


# ---------------------------------------------------------------- récompenses

def texte_recompense(rec: dict) -> str:
    t, q = rec.get("type"), max(1, int(rec.get("quantite") or 1))
    if t == "or":
        return f"{eco_vues._n(q)} pièces d'or"
    if t == "xp":
        return f"{eco_vues._n(q)} XP"
    if t in ("objet", "bureau"):
        nom = rec.get("objet_nom") or "un objet"
        return f"{q} × {nom}" if q > 1 else nom
    if t in ("role", "role_temporaire"):
        nom = rec.get("role_nom") or rec.get("role_id") or "?"
        jours = rec.get("duree_jours") or 0
        return f"Rôle « {nom} »" + (f" pendant {jours:g} jour{'s' if jours > 1 else ''}" if t == "role_temporaire" and jours else "")
    if t == "multiplicateur":
        quoi = {"or": "Or", "xp": "XP"}.get(rec.get("monnaie"), "Or et XP")
        ou = f" sur {len(rec.get('salons') or [])} salon(s)" if rec.get("salons") else ""
        quand = "jusqu'à minuit" if rec.get("jusqu", "minuit") == "minuit" else f"pendant {rec.get('heures') or 0:g} h"
        return f"{quoi} ×{float(rec.get('facteur') or 1):g}{ou} {quand}"
    return rec.get("objet_nom") or rec.get("libelle") or "?"


def _fin_multiplicateur(rec: dict, maintenant: datetime) -> datetime:
    if rec.get("jusqu", "minuit") == "minuit":
        local = maintenant.astimezone(PARIS)
        return datetime(local.year, local.month, local.day, tzinfo=PARIS) + timedelta(days=1)
    return maintenant + timedelta(hours=float(rec.get("heures") or 0) or 24)


async def appliquer(eco, membre: discord.Member, joueur: dict, rec: dict, libelle: str, salon=None,
                    maintenant: datetime | None = None) -> str | None:
    """Donne une récompense. Renvoie None si c'est fait, sinon la raison de l'échec."""
    from app.eco_actions import _gain, _xp, changer_or
    t, q = rec.get("type"), max(1, int(rec.get("quantite") or 1))
    origine = f"Calendrier de l'Avent : {libelle}"
    if t in ("role", "role_temporaire"):
        jours = (rec.get("duree_jours") or 0) if t == "role_temporaire" else 0
        return await eco.donner_role(membre, joueur, rec.get("role_id") or None, rec.get("role_nom") or None, jours, origine=origine)
    if t == "or":
        await changer_or(eco, joueur["id"], q)
        await _gain(eco, joueur["id"], f"avent : {libelle}", "Or", q, "")
    elif t == "xp":
        await _xp(eco, membre, joueur["id"], q, origine, salon)
    elif t == "objet":
        if not rec.get("objet"):
            return f"l'objet « {rec.get('objet_nom') or '?'} » n'est pas relié au catalogue"
        await eco.ajouter_objet(joueur["id"], rec["objet"], q, "don", origine)
    elif t == "bureau":
        if not rec.get("objet"):
            return f"l'objet du bureau « {rec.get('objet_nom') or '?'} » n'est pas relié au catalogue"
        await donner_objet_bureau(eco, joueur, rec)
    elif t == "multiplicateur":
        m = maintenant or datetime.now(timezone.utc)
        facteur = float(rec.get("facteur") or 1)
        monnaie = rec.get("monnaie") or "les_deux"
        salons = [s for s in (rec.get("salons") or []) if s]
        await eco.pb.creer("eco_multiplicateurs", {
            "libelle": f"{origine} ({membre.display_name})", "portee": "salons" if salons else "global", "salons": salons,
            **({"facteur_or": facteur} if monnaie in ("or", "les_deux") else {}),
            **({"facteur_xp": facteur} if monnaie in ("xp", "les_deux") else {}),
            "debut": _pb_date(m), "fin": _pb_date(_fin_multiplicateur(rec, m)), "actif": True})
        eco._charge_a = 0  # recharge la config : le multiplicateur compte tout de suite
    else:
        return f"type de récompense inconnu : {t}"
    return None


async def donner_objet_bureau(eco, joueur: dict, rec: dict):
    """Objet du bureau des missives : même enregistrement que le site (lib/bureauObjets.ts)."""
    q = max(1, int(rec.get("quantite") or 1))
    ligne = await eco.pb.premier("bureau_inventaire", f'joueur="{echapper(joueur["id"])}" && objet="{echapper(rec["objet"])}"')
    if ligne:
        await eco.pb.maj("bureau_inventaire", ligne["id"], {"quantite": (ligne.get("quantite") or 0) + q})
    else:
        await eco.pb.creer("bureau_inventaire", {"joueur": joueur["id"], "objet": rec["objet"], "quantite": q})


async def _donner_tout(eco, membre, joueur, recompenses: list[dict], libelle: str, salon=None) -> list[dict]:
    """Chaque récompense est tentée même si une autre échoue ; le résultat de chacune est noté (suivi du site)."""
    resultats = []
    for rec in recompenses or []:
        try:
            raison = await appliquer(eco, membre, joueur, rec, libelle, salon)
        except Exception as exc:
            log.exception("Récompense de l'Avent non donnée (%s)", libelle)
            raison = str(exc)[:200] or "erreur"
        resultats.append({**rec, "libelle": texte_recompense(rec), "ok": raison is None, "erreur": raison or ""})
    return resultats


# ---------------------------------------------------------------- ouverture

async def ouvrir(eco, membre: discord.Member, jour: int | None = None, salon=None,
                 maintenant: datetime | None = None) -> tuple[bool, dict]:
    """Ouvre une fenêtre (celle du jour par défaut). Renvoie (ouverte, embed privé à afficher)."""
    r = await reglages(eco, frais=True)
    if not r.get("ouvert"):
        return False, eco_vues.erreur(T_FERME)
    aujourdhui = jour_courant(r, maintenant)
    if aujourdhui == -1:
        return False, eco_vues.erreur(T_FERME)
    joueur = await eco.joueur_de(membre)
    if not joueur:
        return False, eco_vues.erreur(T_SANS_FICHE)
    jour = jour or aujourdhui
    if aujourdhui == 0 or jour < 1 or jour > 25 or jour > aujourdhui:
        return False, eco_vues.erreur(T_FUTURE)
    rattrapee = jour < aujourdhui
    async with eco._verrou(membre.id):
        deja = await eco.pb.premier("avent_ouvertures", f'joueur="{echapper(joueur["id"])}" && jour={jour}')
        if deja:
            return False, await _embed_deja(eco, jour, deja)
        les_cases = await cases(eco)
        case = les_cases.get(jour)
        if not case:
            return False, eco_vues.erreur(T_ERREUR)
        try:
            ouverture = await eco.pb.creer("avent_ouvertures", {
                "joueur": joueur["id"], "jour": jour, "ouvert_le": _pb_date(maintenant), "rattrapee": rattrapee,
                "source": "discord", "recompenses": []})
        except Exception:  # index unique : ouverte à l'instant sur le site
            deja = await eco.pb.premier("avent_ouvertures", f'joueur="{echapper(joueur["id"])}" && jour={jour}')
            if deja:
                return False, await _embed_deja(eco, jour, deja)
            log.exception("Ouverture de la fenêtre %s impossible", jour)
            return False, eco_vues.erreur(T_ERREUR)
        resultats = await _donner_tout(eco, membre, joueur, case.get("recompenses") or [], case.get("nom") or date_txt(jour), salon)
        try:
            await eco.pb.maj("avent_ouvertures", ouverture["id"], {"recompenses": resultats})
        except Exception:
            log.exception("Résultat des récompenses de l'Avent non noté")
        frag = await fragment(eco, int(case.get("fragment") or 0))
        embed = embed_case(jour, case, resultats, frag)
        bonus = await _bonus_si_merite(eco, membre, joueur, r, aujourdhui, salon, maintenant)
        if bonus:
            embed.setdefault("fields", []).append(bonus)
        return True, embed


async def _bonus_si_merite(eco, membre, joueur, r, aujourdhui, salon, maintenant) -> dict | None:
    """Bonus des 24 (jour 0) : à partir du 25, pour qui a ouvert les 24 fenêtres le bon jour (ou rattrapées si
    bonus_compte_rattrapees). Donné une seule fois (index unique)."""
    if aujourdhui < 25 or not r.get("bonus"):
        return None
    faites = await ouvertures(eco, joueur["id"])
    if 0 in faites:
        return None
    compte = [j for j in range(1, 25) if j in faites and (r.get("bonus_compte_rattrapees") or not faites[j].get("rattrapee"))]
    if len(compte) < 24:
        return None
    try:
        ligne = await eco.pb.creer("avent_ouvertures", {
            "joueur": joueur["id"], "jour": 0, "ouvert_le": _pb_date(maintenant), "rattrapee": False, "source": "discord",
            "recompenses": []})
    except Exception:
        return None
    resultats = await _donner_tout(eco, membre, joueur, r["bonus"], "bonus des 24 fenêtres", salon)
    try:
        await eco.pb.maj("avent_ouvertures", ligne["id"], {"recompenses": resultats})
    except Exception:
        log.exception("Résultat du bonus de l'Avent non noté")
    return {"name": "🌟 Bonus des 24 fenêtres", "value": f"{T_BONUS}\n" + _lignes(resultats)}


def _lignes(resultats: list[dict]) -> str:
    return "\n".join(f"• **{x['libelle']}**" + (f" — ⚠️ pas encore donné ({x['erreur']}) : un gérant s'en occupe."
                                                 if not x.get("ok") else "") for x in resultats) or "—"


def embed_case(jour: int, case: dict, resultats: list[dict], frag: dict | None) -> dict:
    surtitre = "Pour la Nuit de Lanjis…" if jour == 25 else "Le Père Lanjis t'a laissé…"
    if resultats:
        noms = " et ".join(f"**{x['libelle']}**" for x in resultats)
        description = f"🎁 {surtitre[:-1]} {noms}. Crédité sur ton compte, ici et sur le site."
    else:
        description = f"🎁 {surtitre}"
    description += f"\n\n*{case.get('phrase')}*" if case.get("phrase") else ""
    if any(not x.get("ok") for x in resultats):
        description += "\n\n" + _lignes([x for x in resultats if not x.get("ok")])
    embed = {"title": f"❄️ {date_txt(jour).capitalize()} · {case.get('nom') or ''}".rstrip(" ·"),
             "description": description, "color": NEIGE}
    if frag and (frag.get("texte") or "").strip():
        texte = frag["texte"].strip()
        embed["fields"] = [{"name": "📜 Le Père Lanjis t'a laissé un fragment du Conte de Lanjis",
                            "value": f"« {texte[:950]}{'…' if len(texte) > 950 else ''} »"}]
    return embed


async def _embed_deja(eco, jour: int, ouverture: dict) -> dict:
    e = eco_vues.erreur(T_DEJA)
    if ouverture.get("recompenses"):
        e["description"] += "\n\nElle contenait :\n" + "\n".join(f"• {x.get('libelle') or texte_recompense(x)}"
                                                               for x in ouverture["recompenses"])
    return e


# ---------------------------------------------------------------- vues

def lien_site(r: dict) -> str:
    if r.get("lien_site"):
        return r["lien_site"]
    return (os.getenv("SITE_URL") or SITE_PAR_DEFAUT).rstrip("/") + "/avent"


def boutons(r: dict) -> list[dict]:
    return [{"type": 1, "components": [
        {"type": 2, "style": 1, "label": "Ouvrir ma fenêtre", "emoji": {"name": "🎁"}, "custom_id": "eco:av:ouv"},
        {"type": 2, "style": 2, "label": "Mon calendrier", "emoji": {"name": "📅"}, "custom_id": "eco:av:cal"},
        {"type": 5, "label": "Sur le site", "emoji": {"name": "🔗"}, "url": lien_site(r)},
    ]}]


async def vue_calendrier(eco, membre: discord.Member, maintenant: datetime | None = None) -> dict:
    """/avent et bouton « Mon calendrier » : les 25 fenêtres du joueur et le menu de rattrapage (message privé)."""
    r = await reglages(eco)
    if not r.get("ouvert") or jour_courant(r, maintenant) == -1:
        return {"embeds": [eco_vues.erreur(T_FERME)], "flags": eco_vues.PRIVE}
    joueur = await eco.joueur_de(membre)
    if not joueur:
        return {"embeds": [eco_vues.erreur(T_SANS_FICHE)], "flags": eco_vues.PRIVE}
    aujourdhui = jour_courant(r, maintenant)
    faites = await ouvertures(eco, joueur["id"])
    a_rattraper = [j for j in range(1, min(aujourdhui, 25)) if j not in faites] if dans_rattrapage(r, maintenant) else []

    def case(j):
        if j in faites:
            return "🎁"
        if j == aujourdhui:
            return "✨"
        if j < aujourdhui:
            return "🕯️"
        return "❄️"
    lignes = [" ".join(f"`{j:>2}`{case(j)}" for j in range(debut, min(debut + 5, 26))) for debut in range(1, 26, 5)]
    ouvertes = len([j for j in faites if 1 <= j <= 24])
    resume = f"**{ouvertes}** fenêtre{'s' if ouvertes > 1 else ''} ouverte{'s' if ouvertes > 1 else ''} sur 24"
    if a_rattraper:
        resume += f" · {len(a_rattraper)} à rattraper"
    if ouvertes >= 24:
        resume = "Les 24 fenêtres sont ouvertes : la Nuit de Lanjis te réserve un bonus."
    embed = {"title": "📅 Ton calendrier du village", "color": NEIGE,
             "description": resume + "\n\n" + "\n".join(lignes) + "\n\n🎁 ouverte · ✨ aujourd'hui · 🕯️ à rattraper · ❄️ à venir"}
    composants = boutons(r)
    composants[0]["components"] = [b for b in composants[0]["components"] if b.get("custom_id") != "eco:av:cal"]
    if aujourdhui in faites or aujourdhui < 1:
        composants[0]["components"] = [b for b in composants[0]["components"] if b.get("custom_id") != "eco:av:ouv"]
    if a_rattraper:
        composants.append({"type": 1, "components": [{
            "type": 3, "custom_id": "eco:av:rat", "placeholder": "Choisis une fenêtre à rattraper",
            "options": [{"label": date_txt(j).capitalize(), "value": str(j), "emoji": {"name": "🕯️"}} for j in a_rattraper[-25:]]}]})
    return {"embeds": [embed], "components": composants, "flags": eco_vues.PRIVE}


async def annonce(eco, r: dict, jour: int) -> dict:
    """Message public de minuit : la fenêtre du jour s'allume."""
    rang = ORDINAUX.get(jour, f"{jour}e")
    texte = (f"« La neige tombe sur Rostheim. Quelque part dans le village, une {rang} fenêtre attend. »"
             if jour < 25 else "« Cette nuit, la grande cheminée du village est ouverte à tous. »")
    if jour in T_SPECIAUX:
        texte = f"{T_SPECIAUX[jour]}\n\n{texte}"
    embed = {"title": f"❄️ La fenêtre du {date_txt(jour)} s'est allumée", "description": texte, "color": NEIGE,
             "fields": [{"name": "Le calendrier du village.", "value": "Ouvre ta fenêtre ici ou sur le site."}],
             "footer": {"text": f"Fêtes de Lanjis {int(r.get('annee') or 2026) - 769} · aussi sur le site"}}
    return {"embeds": [embed], "components": boutons(r), "allowed_mentions": {"parse": []}}


# ---------------------------------------------------------------- annonces programmées

async def _poster(salon_id: str, corps: dict) -> bool:
    if not salon_id:
        return False
    try:
        async with httpx.AsyncClient() as client:
            rep = await client.post(f"https://discord.com/api/v10/channels/{salon_id}/messages", json=corps, timeout=10,
                                    headers={"Authorization": f'Bot {os.environ.get("DISCORD_TOKEN", "")}'})
            if rep.status_code >= 400:
                log.error("Message du calendrier de l'Avent refusé : %s %s", rep.status_code, rep.text)
            return rep.status_code < 400
    except httpx.HTTPError as exc:
        log.error("Erreur réseau vers Discord : %s", exc)
        return False


async def tour(eco, maintenant: datetime | None = None, poster=None) -> list[str]:
    """Un passage de la boucle : annonce de minuit puis rappel du soir, chacun une seule fois par jour.
    Renvoie ce qui a été posté (pour les tests)."""
    poster = poster or _poster
    fait = []
    r = await reglages(eco, frais=True)
    if not r.get("ouvert") or not r.get("id"):
        return fait
    m = (maintenant or datetime.now(timezone.utc)).astimezone(PARIS)
    jour = jour_courant(r, m)
    if not 1 <= jour <= 25:
        return fait
    date = f"{int(r.get('annee') or 2026)}-12-{jour:02d}"
    if r.get("salon_annonce") and r.get("derniere_annonce", "") < date:
        # Noté avant d'envoyer : en cas d'échec, on préfère une annonce manquée à une annonce en double.
        await eco.pb.maj("avent_reglages", r["id"], {"derniere_annonce": date})
        if await poster(r["salon_annonce"], await annonce(eco, r, jour)):
            fait.append(f"annonce {date}")
    h, mn = _heure(r.get("heure_rappel"), "20:00")
    if (r.get("rappel") == "salon" and r.get("salon_rappel") and jour <= 24 and r.get("dernier_rappel", "") < date
            and (m.hour, m.minute) >= (h, mn) and m.day == jour):
        await eco.pb.maj("avent_reglages", r["id"], {"dernier_rappel": date})
        mentions = await _retardataires(eco, jour)
        if mentions:
            corps = {"content": f"{T_RAPPEL}\n" + " ".join(f"<@{d}>" for d in mentions),
                     "components": boutons(r), "allowed_mentions": {"users": mentions[:100]}}
            if await poster(r["salon_rappel"], corps):
                fait.append(f"rappel {date}")
    return fait


async def _retardataires(eco, jour: int) -> list[str]:
    """Joueurs liés à Discord qui ont déjà ouvert une fenêtre cette année mais pas celle du jour
    (on ne relance pas ceux qui ne jouent pas au calendrier)."""
    try:
        toutes = await eco.pb.lister("avent_ouvertures")
    except Exception:
        return []
    participants = {o["joueur"] for o in toutes if o.get("joueur")}
    du_jour = {o["joueur"] for o in toutes if int(o.get("jour") or 0) == jour}
    ids = []
    for jid in sorted(participants - du_jour):
        try:
            j = await eco.pb.requete("GET", f"/api/collections/joueurs/records/{jid}")
        except Exception:
            continue
        if str(j.get("discord_id") or "").isdigit() and len(str(j["discord_id"])) >= 15:
            ids.append(str(j["discord_id"]))
    return ids


async def boucle(eco):
    await eco.client.wait_until_ready()
    while not eco.client.is_closed():
        try:
            for quoi in await tour(eco):
                log.info("Calendrier de l'Avent : %s", quoi)
        except Exception:
            log.exception("Erreur dans les annonces du calendrier de l'Avent")
        await asyncio.sleep(VERIFIER_TOUTES_S)
