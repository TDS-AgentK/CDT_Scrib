"""Sondages de disponibilités (remplacent les sondages YAGPDB ; version A validée par K le 16/11/2026).

Sondage de la semaine (« Inquisiteur des présences ») : seul envoi automatique, chaque dimanche à 08:00 (heure de
Paris, réglable) dans #disponibilités, pour la semaine du lendemain (lundi au dimanche). Le bot ajoute les réactions 1️⃣ à
7️⃣ et ❌ (« pas dispo cette semaine »). Ni rappel ni clôture (K, 16/11/2026) : le sondage reste ouvert et son embed suit
les votes (par jour, le nombre de votants, une barre et les pseudos, une ⭐ au meilleur soir, qui se déplace si le
classement change, et en pied « N réponses sur M »). Le compteur de réactions de Discord inclut le +1 du bot : normal.

Sondage des heures : toujours lancé à la main, avec /dispos heures (jamais par une tâche planifiée). Soir par défaut :
le meilleur soir de la semaine ; créneaux par défaut de 19:00 à 21:00, toutes les 30 min (une horloge par créneau). Un
seul vote par joueur : l'heure à partir de laquelle il est là (une autre réaction retire l'ancienne). Le compte est
cumulé : dispo à 19:00, il compte aussi pour 19:30, 20:00… Chaque ligne donne le total et nomme les nouveaux arrivants ;
⭐ à l'heure où tout le monde est là.

Mise à jour à chaque vote (ajout ou retrait de réaction), regroupée sur ~2 s par message pour rester sous les limites de
Discord. Événements « raw » : ils arrivent même pour les messages absents du cache (après un redémarrage), et le message
est relu sur Discord. Aucun état en mémoire n'est nécessaire : tout se relit sur le message (date dans l'horodatage de
l'embed, créneaux dans son pied, votes dans les réactions). Un redémarrage ne perd donc rien.

Réglages : page /gestion/disponibilites du site (eco_reglages, fiche « general », champs dispo_*), relus toutes les
3 minutes : un changement s'applique sans redéployer. Champ vide (ou champs absents de la base) : variables
d'environnement ci-dessous, puis valeurs par défaut. Le bot y écrit aussi dispo_verif (ses droits dans #séances).
  DISPO_ACTIF=1               envoi automatique du sondage de la semaine (sinon seulement les commandes manuelles) ;
  DISPO_SALON_ID              salon #disponibilités ; DISPO_SALON_SEANCES_ID : salon #séances (sondage des heures) ;
  DISPO_ROLE_ID               rôle des joueurs (« sur M ») ; DISPO_MENTION=1 : mentionner ce rôle à l'envoi ;
  DISPO_ENVOI="6 08:00"       jour (0 = lundi … 6 = dimanche) et heure de l'envoi ;
  DISPO_HEURES="19:00-21:00/30"                     créneaux par défaut du sondage des heures.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

log = logging.getLogger("cdt_scrib.disponibilites")

PARIS = ZoneInfo("Europe/Paris")
JOURS = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
JOURS_COURTS = ["Lun.", "Mar.", "Mer.", "Jeu.", "Ven.", "Sam.", "Dim."]
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]
EMOJIS_JOURS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣"]
PAS_DISPO = "❌"
TITRE_JOURS = "Inquisiteur des présences"
SUFFIXE_HEURES = " : à partir de quelle heure ?"
COULEUR = 0x8A2F22
COULEUR_CLOS = 0x5A4A36  # anciens sondages clôturés (avant le 16/11/2026), restés figés
DELAI_MAJ_S = 2  # votes regroupés avant de modifier le message
LARGEUR_BARRE = 10
MAX_CRENEAUX = 20  # Discord n'accepte pas plus de 20 réactions différentes sur un message


# ---------------------------------------------------------------- réglages

def _jour_heure(valeur: str, defaut: tuple[int, int, int]) -> tuple[int, int, int]:
    m = re.fullmatch(r"\s*(\d)\s+(\d{1,2}):(\d{2})\s*", valeur or "")
    return (int(m[1]), int(m[2]), int(m[3])) if m else defaut


def reglages() -> dict:
    """Réglages des variables d'environnement (secours)."""
    debut, fin, pas = plage(os.getenv("DISPO_HEURES", ""))
    return {
        "actif": os.getenv("DISPO_ACTIF") == "1",
        "salon_id": int(os.getenv("DISPO_SALON_ID") or 0) or None,
        "salon_seances_id": int(os.getenv("DISPO_SALON_SEANCES_ID") or 0) or None,
        "role_id": int(os.getenv("DISPO_ROLE_ID") or 0) or None,
        "mention": os.getenv("DISPO_MENTION") == "1",
        "envoi": _jour_heure(os.getenv("DISPO_ENVOI", ""), (6, 8, 0)),
        "debut": debut, "fin": fin, "pas": pas,
    }


def _vide(v) -> bool:
    return v is None or v == ""


def reglages_depuis(fiche: dict | None) -> dict:
    """Réglages du site (eco_reglages « general », champs dispo_*) complétés par les variables d'environnement."""
    cfg = reglages()
    if not fiche or "dispo_actif" not in fiche:
        return cfg
    cfg["actif"] = bool(fiche.get("dispo_actif"))
    cfg["mention"] = bool(fiche.get("dispo_mention"))
    for cle, champ in (("salon_id", "dispo_salon_id"), ("salon_seances_id", "dispo_seances_id"), ("role_id", "dispo_role_id")):
        if not _vide(fiche.get(champ)) and str(fiche[champ]).isdigit():
            cfg[cle] = int(fiche[champ])
    for cle in ("envoi",):
        j, h, m = cfg[cle]
        if not _vide(fiche.get(f"dispo_{cle}_jour")):
            j = int(fiche[f"dispo_{cle}_jour"])
        hm = re.fullmatch(r"(\d{1,2}):(\d{2})", str(fiche.get(f"dispo_{cle}_heure") or ""))
        if hm:
            h, m = int(hm[1]), int(hm[2])
        cfg[cle] = (j, h, m)
    if re.fullmatch(r"\d{1,2}:\d{2}", str(fiche.get("dispo_heure_debut") or "")):
        cfg["debut"] = str(fiche["dispo_heure_debut"]).zfill(5)
    if re.fullmatch(r"\d{1,2}:\d{2}", str(fiche.get("dispo_heure_fin") or "")):
        cfg["fin"] = str(fiche["dispo_heure_fin"]).zfill(5)
    if fiche.get("dispo_pas") in (30, 60):
        cfg["pas"] = int(fiche["dispo_pas"])
    cfg["_fiche_id"] = fiche.get("id")
    cfg["_verif"] = fiche.get("dispo_verif") or ""
    return cfg


def texte_verif(perms, nom_salon: str, quand: datetime) -> str:
    """Droits du bot utiles au sondage des heures, pour la page de réglages du site."""
    utiles = [("manage_messages", "Gérer les messages"), ("add_reactions", "Ajouter des réactions"),
              ("send_messages", "Envoyer des messages"), ("embed_links", "Intégrer des liens"),
              ("read_message_history", "Voir les anciens messages")]
    manque = [libelle for attr, libelle in utiles if not getattr(perms, attr, False)]
    if manque:
        return f"Il manque au bot dans #{nom_salon} : {', '.join(manque)}."
    return f"ok dans #{nom_salon} (vérifié le {quand:%d/%m à %H:%M})"


def plage(valeur: str) -> tuple[str, str, int]:
    """« 19:00-21:00/30 » → ("19:00", "21:00", 30) ; valeurs par défaut sinon."""
    m = re.fullmatch(r"\s*(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})\s*(?:/\s*(\d+))?\s*", valeur or "")
    return (m[1].zfill(5), m[2].zfill(5), int(m[3] or 30)) if m else ("19:00", "21:00", 30)


# ---------------------------------------------------------------- dates

def lundi_suivant(d: date) -> date:
    """Lundi de la semaine qui suit (le lendemain, si l'on est dimanche)."""
    return d + timedelta(days=7 - d.weekday())


def periode(lundi: date) -> str:
    """« du 16 au 22 novembre », « du 30 novembre au 6 décembre », « du 29 décembre 2025 au 4 janvier 2026 »."""
    dim = lundi + timedelta(days=6)
    if lundi.year != dim.year:
        return f"du {lundi.day} {MOIS[lundi.month - 1]} {lundi.year} au {dim.day} {MOIS[dim.month - 1]} {dim.year}"
    if lundi.month != dim.month:
        return f"du {lundi.day} {MOIS[lundi.month - 1]} au {dim.day} {MOIS[dim.month - 1]}"
    return f"du {lundi.day} au {dim.day} {MOIS[dim.month - 1]}"


def moment(lundi: date, jour_heure: tuple[int, int, int]) -> datetime:
    """Date et heure (Paris) d'un « jour heure » compté depuis le lundi de la semaine sondée."""
    j, h, m = jour_heure
    return datetime.combine(lundi + timedelta(days=j), datetime.min.time(), PARIS).replace(hour=h, minute=m)


def jour_court(d: date) -> str:
    return f"{JOURS_COURTS[d.weekday()]} {d.day}"


def jour_long(d: date) -> str:
    return f"{JOURS[d.weekday()]} {d.day}"


# ---------------------------------------------------------------- sondage des jours (calcul pur)

def etat_jours(reactions: dict[str, list[int]], bot_id: int | None) -> tuple[list[list[int]], list[int], list[int]]:
    """Votants de chaque jour (lundi → dimanche), votants ❌, et toutes les personnes qui ont répondu (ordre d'arrivée)."""
    par_jour = [[u for u in reactions.get(e, []) if u != bot_id] for e in EMOJIS_JOURS]
    pas_dispo = [u for u in reactions.get(PAS_DISPO, []) if u != bot_id]
    repondants: list[int] = []
    for u in [u for l in par_jour for u in l] + pas_dispo:
        if u not in repondants:
            repondants.append(u)
    return par_jour, pas_dispo, repondants


def meilleur_soir(par_jour: list[list[int]]) -> int | None:
    """Indice du jour qui a le plus de votants (le premier en cas d'égalité), None si personne."""
    meilleur = max(range(7), key=lambda i: (len(par_jour[i]), -i))
    return meilleur if par_jour[meilleur] else None


def barre(n: int, total: int, largeur: int = LARGEUR_BARRE) -> str:
    plein = round(largeur * n / total) if total else 0
    return "▰" * plein + "▱" * (largeur - plein)


def embed_jours(lundi: date, par_jour: list[list[int]], pas_dispo: list[int], noms: dict[int, str],
                attendus: int | None, clos: bool = False) -> dict:
    """Embed du sondage de la semaine (dict Discord)."""
    repondants = {u for l in par_jour for u in l} | set(pas_dispo)
    total_barre = attendus or max([len(l) for l in par_jour] + [1])
    best = meilleur_soir(par_jour)
    lignes = []
    for i in range(7):
        d = lundi + timedelta(days=i)
        votants = par_jour[i]
        etoile = " ⭐" if i == best else ""
        qui = ", ".join(noms.get(u, "?") for u in votants)
        lignes.append(f"{EMOJIS_JOURS[i]} **{jour_court(d)}**{etoile} · {len(votants)} {barre(len(votants), total_barre)}"
                      + (f"\n   {qui}" if qui else ""))
    if pas_dispo:
        lignes.append(f"{PAS_DISPO} **Pas dispo cette semaine** · {len(pas_dispo)}\n   " + ", ".join(noms.get(u, "?") for u in pas_dispo))
    else:
        lignes.append(f"{PAS_DISPO} Pas dispo cette semaine")
    reponses = f"{len(repondants)} réponse{'s' if len(repondants) > 1 else ''}" + (f" sur {attendus}" if attendus else "")
    return {
        "title": TITRE_JOURS,
        "description": f"**Vos disponibilités {periode(lundi)}**\nRéagissez avec les soirs où vous êtes là.\n\n" + "\n".join(lignes),
        "color": COULEUR_CLOS if clos else COULEUR,
        "footer": {"text": f"Clôturé · {reponses}" if clos else reponses},
        # La semaine sondée, relue après un redémarrage.
        "timestamp": datetime.combine(lundi, datetime.min.time(), PARIS).isoformat(),
    }


def lire_embed_jours(embed: dict) -> tuple[date, bool] | None:
    """(lundi, clos) d'un embed de sondage de la semaine, None si ce n'en est pas un. « Clos » : ancien sondage clôturé."""
    if embed.get("title") != TITRE_JOURS or not embed.get("timestamp"):
        return None
    lundi = datetime.fromisoformat(str(embed["timestamp"]).replace("Z", "+00:00")).astimezone(PARIS).date()
    return lundi, (embed.get("footer") or {}).get("text", "").startswith("Clôturé")


# ---------------------------------------------------------------- sondage des heures (calcul pur)

def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def normaliser_heure(s: str) -> str:
    """« 19h », « 19h30 », « 19 », « 19:30 » → « 19:00 » / « 19:30 »."""
    m = re.fullmatch(r"\s*(\d{1,2})\s*(?:[:hH]\s*(\d{2})?)?\s*", s or "")
    if not m or int(m[1]) > 23 or int(m[2] or 0) > 59:
        raise ValueError(f"« {s} » n'est pas une heure (ex. 19:30).")
    return f"{int(m[1]):02d}:{int(m[2] or 0):02d}"


def creneaux(debut: str = "19:00", fin: str = "21:00", pas: int = 30) -> list[str]:
    """Heures proposées, de début à fin incluses (pas de 30 ou 60 min : une horloge Discord par créneau)."""
    debut, fin = normaliser_heure(debut), normaliser_heure(fin)
    if pas not in (30, 60):
        raise ValueError("Le pas doit être de 30 ou 60 minutes.")
    a, b = _minutes(debut), _minutes(fin)
    if a % 30 or b % 30:
        raise ValueError("Les heures doivent tomber à l'heure pile ou à la demie.")
    if b < a:
        raise ValueError("La dernière heure doit suivre la première.")
    out = [f"{m // 60:02d}:{m % 60:02d}" for m in range(a, b + 1, pas)]
    if len(out) > MAX_CRENEAUX:
        raise ValueError(f"{MAX_CRENEAUX} créneaux au plus.")
    return out


def horloge(hhmm: str) -> str:
    """Emoji horloge d'une heure : 19:00 → 🕖, 19:30 → 🕢."""
    h, m = (int(x) for x in hhmm.split(":"))
    h12 = h % 12 or 12
    return chr((0x1F550 if m == 0 else 0x1F55C) + h12 - 1)


def arrivees(reactions: dict[str, list[int]], heures: list[str], bot_id: int | None) -> dict[int, int]:
    """Heure d'arrivée de chaque votant (indice du créneau) ; s'il reste plusieurs réactions, la plus tôt compte."""
    out: dict[int, int] = {}
    for i, h in enumerate(heures):
        for u in reactions.get(horloge(h), []):
            if u != bot_id and u not in out:
                out[u] = i
    return out


def embed_heures(soir: date, heures: list[str], arrivee: dict[int, int], noms: dict[int, str], attendus: int | None) -> dict:
    lignes, star = [], None
    votants = len(arrivee)
    for i, h in enumerate(heures):
        presents = sum(1 for a in arrivee.values() if a <= i)
        if star is None and votants and presents == votants:
            star = i
        nouveaux = [noms.get(u, "?") for u, a in arrivee.items() if a == i]
        lignes.append(f"{horloge(h)} **{h}**{' ⭐' if star == i else ''} · {presents} {barre(presents, attendus or votants or 1)}"
                      + (f"  + {', '.join(nouveaux)}" if nouveaux else ""))
    if star is not None:
        tous = attendus is None or votants >= attendus
        lignes.append("\n" + (f"Tout le monde est là à partir de **{heures[star]}**." if tous
                              else f"Les {votants} répondants sont là à partir de **{heures[star]}**."))
    return {
        "title": f"{jour_long(soir)}{SUFFIXE_HEURES}",
        "description": "Un seul vote : l'heure **à partir de laquelle** vous êtes là.\n\n" + "\n".join(lignes),
        "color": COULEUR,
        "footer": {"text": f"{votants} réponse{'s' if votants > 1 else ''}" + (f" sur {attendus}" if attendus else "")
                   + f" · créneaux {heures[0]}-{heures[-1]}/{_pas(heures)}"},
        "timestamp": datetime.combine(soir, datetime.min.time(), PARIS).isoformat(),
    }


def _pas(heures: list[str]) -> int:
    return _minutes(heures[1]) - _minutes(heures[0]) if len(heures) > 1 else 30


def lire_embed_heures(embed: dict) -> tuple[date, list[str]] | None:
    """(soir, créneaux) d'un embed de sondage des heures, None si ce n'en est pas un."""
    if not str(embed.get("title", "")).endswith(SUFFIXE_HEURES) or not embed.get("timestamp"):
        return None
    m = re.search(r"créneaux (\d{2}:\d{2})-(\d{2}:\d{2})/(\d+)", (embed.get("footer") or {}).get("text", ""))
    if not m:
        return None
    soir = datetime.fromisoformat(str(embed["timestamp"]).replace("Z", "+00:00")).astimezone(PARIS).date()
    return soir, creneaux(m[1], m[2], int(m[3]))


def soir_choisi(nom: str | None, aujourdhui: date) -> date | None:
    """« Mardi » → le prochain mardi (aujourd'hui compris)."""
    if not nom:
        return None
    i = [j.lower() for j in JOURS].index(nom.lower())
    return aujourdhui + timedelta(days=(i - aujourdhui.weekday()) % 7)


# ---------------------------------------------------------------- Discord

class Disponibilites:
    """Sondages sur le serveur : envoi du sondage de la semaine, mise à jour à chaque vote."""

    def __init__(self, eco):
        self.eco = eco
        self.client = eco.client
        self._verrous: dict[int, asyncio.Lock] = {}
        self._vus: dict[str, int] = {}  # « semaine AAAA-MM-JJ » → id du message, pour ne pas relire le salon à chaque tour
        self._cfg: tuple[float, dict] | None = None
        self._taches: dict[int, asyncio.Task] = {}  # mise à jour en attente, par message

    async def cfg(self) -> dict:
        """Réglages du site (relus toutes les 3 min), variables d'environnement en secours."""
        import time
        if self._cfg and time.monotonic() - self._cfg[0] < 180:
            return self._cfg[1]
        try:
            fiche = await self.eco.pb.premier("eco_reglages", 'cle="general"')
        except Exception:
            log.warning("Disponibilités : réglages du site illisibles, variables d'environnement utilisées")
            fiche = None
        cfg = reglages_depuis(fiche)
        self._cfg = (time.monotonic(), cfg)
        await self._verifier(cfg)
        return cfg

    async def _verifier(self, cfg: dict):
        """Note dans les réglages du site si le bot a les droits utiles dans le salon des séances."""
        if not cfg.get("_fiche_id") or not cfg.get("salon_seances_id"):
            return
        try:
            salon = await self._salon(cfg["salon_seances_id"])
            if not salon or not getattr(salon, "guild", None):
                return
            texte = texte_verif(salon.permissions_for(salon.guild.me), salon.name, datetime.now(PARIS))
            # Écrit seulement si l'état change (la date seule ne suffit pas).
            if texte.split(" (")[0] != str(cfg.get("_verif", "")).split(" (")[0]:
                await self.eco.pb.maj("eco_reglages", cfg["_fiche_id"], {"dispo_verif": texte})
                cfg["_verif"] = texte
        except Exception:
            log.exception("Disponibilités : vérification des droits dans le salon des séances")

    def _verrou(self, mid: int) -> asyncio.Lock:
        return self._verrous.setdefault(mid, asyncio.Lock())

    async def _salon(self, sid: int | None):
        if not sid:
            return None
        return self.client.get_channel(sid) or await self.client.fetch_channel(sid)

    def _joueurs(self, guild, cfg: dict) -> list:
        role = guild.get_role(cfg["role_id"]) if guild and cfg["role_id"] else None
        return [m for m in role.members if not m.bot] if role else []

    async def _reactions(self, message) -> dict[str, list[int]]:
        out: dict[str, list[int]] = {}
        for r in message.reactions:
            out[str(r.emoji)] = [u.id async for u in r.users(limit=None)]
        return out

    def _noms(self, guild, ids) -> dict[int, str]:
        out = {}
        for u in ids:
            m = guild.get_member(u) if guild else None
            out[u] = m.display_name if m else "?"
        return out

    # ---- sondage des jours
    async def poster_jours(self, salon, lundi: date, mention: bool = False):
        cfg = await self.cfg()
        attendus = len(self._joueurs(salon.guild, cfg)) or None
        embed = embed_jours(lundi, [[] for _ in range(7)], [], {}, attendus)
        import discord
        contenu = f"<@&{cfg['role_id']}>" if mention and cfg["role_id"] else None
        message = await salon.send(content=contenu, embed=discord.Embed.from_dict(embed),
                                   allowed_mentions=discord.AllowedMentions(roles=True))
        for e in EMOJIS_JOURS + [PAS_DISPO]:
            await message.add_reaction(e)
        self._vus[lundi.isoformat()] = message.id
        log.info("Sondage des jours %s posté dans #%s", periode(lundi), getattr(salon, "name", "?"))
        return message

    async def trouver_jours(self, salon, lundi: date):
        """Sondage des jours de la semaine `lundi` dans le salon (50 derniers messages), ou None."""
        cle = lundi.isoformat()
        if cle in self._vus:
            try:
                return await salon.fetch_message(self._vus[cle])
            except Exception:
                self._vus.pop(cle, None)
        async for m in salon.history(limit=50):
            if m.author.id == self.client.user.id and m.embeds:
                lu = lire_embed_jours(m.embeds[0].to_dict())
                if lu and lu[0] == lundi:
                    self._vus[cle] = m.id
                    return m
        return None

    async def rafraichir_jours(self, message, lundi: date):
        import discord
        cfg = await self.cfg()
        par_jour, pas_dispo, repondants = etat_jours(await self._reactions(message), self.client.user.id)
        attendus = len(self._joueurs(message.guild, cfg)) or None
        embed = embed_jours(lundi, par_jour, pas_dispo, self._noms(message.guild, repondants), attendus)
        await message.edit(embed=discord.Embed.from_dict(embed))
        return par_jour, repondants

    # ---- sondage des heures
    async def poster_heures(self, salon, soir: date, debut: str, fin: str, pas: int):
        import discord
        heures = creneaux(debut, fin, pas)
        attendus = len(self._joueurs(salon.guild, await self.cfg())) or None
        message = await salon.send(embed=discord.Embed.from_dict(embed_heures(soir, heures, {}, {}, attendus)))
        for h in heures:
            await message.add_reaction(horloge(h))
        return message

    async def meilleur_soir_courant(self, aujourdhui: date) -> date | None:
        """Meilleur soir du dernier sondage des jours (semaine en cours, sinon la suivante)."""
        salon = await self._salon((await self.cfg())["salon_id"])
        if not salon:
            return None
        lundi = aujourdhui - timedelta(days=aujourdhui.weekday())
        for l in (lundi, lundi + timedelta(days=7)):
            m = await self.trouver_jours(salon, l)
            if m:
                par_jour, _, _ = etat_jours(await self._reactions(m), self.client.user.id)
                best = meilleur_soir(par_jour)
                if best is not None:
                    return l + timedelta(days=best)
        return None

    # ---- réactions
    async def reaction(self, payload, ajout: bool):
        """Vote ajouté ou retiré sur un message du bot : mise à jour du sondage, regroupée sur ~2 s."""
        emoji = str(payload.emoji)
        if not (emoji in EMOJIS_JOURS or emoji == PAS_DISPO or 0x1F550 <= ord(emoji[0]) <= 0x1F567):
            return
        if payload.user_id == self.client.user.id:
            return
        auteur = getattr(payload, "message_author_id", None)
        if auteur is not None and auteur != self.client.user.id:
            return
        salon = self.client.get_channel(payload.channel_id)
        if salon is None:  # salon absent du cache (fil, redémarrage) : relu sur Discord
            salon = await self.client.fetch_channel(payload.channel_id)
        if ajout and 0x1F550 <= ord(emoji[0]) <= 0x1F567:
            # Sondage des heures, un seul vote : les autres horloges de ce joueur sont retirées tout de suite.
            async with self._verrou(payload.message_id):
                message = await salon.fetch_message(payload.message_id)
                if message.author.id == self.client.user.id and message.embeds:
                    lh = lire_embed_heures(message.embeds[0].to_dict())
                    if lh and emoji in {horloge(h) for h in lh[1]}:
                        for h in lh[1]:
                            r = next((x for x in message.reactions if str(x.emoji) == horloge(h)), None)
                            if horloge(h) != emoji and r and r.count > 1:
                                try:
                                    await message.remove_reaction(horloge(h), payload.member or discord_objet(payload.user_id))
                                except Exception:
                                    log.warning("Sondage des heures : impossible de retirer une réaction (droit « Gérer les messages » ?)")
        self._planifier(salon, payload.message_id)

    def _planifier(self, salon, mid: int):
        """Une seule mise à jour par message dans les ~2 s qui suivent le dernier vote."""
        ancienne = self._taches.get(mid)
        if ancienne and not ancienne.done():
            ancienne.cancel()
        self._taches[mid] = asyncio.create_task(self._mettre_a_jour(salon, mid))

    async def _mettre_a_jour(self, salon, mid: int):
        await asyncio.sleep(DELAI_MAJ_S)
        self._taches.pop(mid, None)  # désormais, un nouveau vote programme une autre mise à jour sans annuler celle-ci
        try:
            async with self._verrou(mid):
                message = await salon.fetch_message(mid)
                if message.author.id != self.client.user.id or not message.embeds:
                    return
                e = message.embeds[0].to_dict()
                lu = lire_embed_jours(e)
                if lu:
                    if not lu[1]:  # ancien sondage clôturé : figé
                        await self.rafraichir_jours(message, lu[0])
                    return
                lh = lire_embed_heures(e)
                if not lh:
                    return
                import discord
                soir, heures = lh
                arrivee = arrivees(await self._reactions(message), heures, self.client.user.id)
                attendus = len(self._joueurs(message.guild, await self.cfg())) or None
                await message.edit(embed=discord.Embed.from_dict(
                    embed_heures(soir, heures, arrivee, self._noms(message.guild, arrivee), attendus)))
        except Exception:
            log.exception("Disponibilités : mise à jour du sondage %s", mid)

    # ---- planification
    async def tour(self, maintenant: datetime):
        cfg = await self.cfg()
        if not cfg["actif"]:
            return
        salon = await self._salon(cfg["salon_id"])
        if not salon:
            return
        aujourdhui = maintenant.date()
        # Seul envoi automatique : le sondage de la semaine, au jour et à l'heure réglés, pour la semaine qui suit.
        j, h, m = cfg["envoi"]
        if aujourdhui.weekday() == j and (maintenant.hour, maintenant.minute) >= (h, m):
            lundi = lundi_suivant(aujourdhui)
            if not await self.trouver_jours(salon, lundi):
                await self.poster_jours(salon, lundi, cfg["mention"])

    async def boucle(self):
        await self.client.wait_until_ready()
        while not self.client.is_closed():
            try:
                await self.tour(datetime.now(PARIS))
            except Exception:
                log.exception("Sondages de disponibilités : erreur")
            await asyncio.sleep(60)


def brancher(client, dispos: Disponibilites):
    """Abonne le client aux réactions (ajout et retrait)."""
    async def on_raw_reaction_add(payload):
        try:
            await dispos.reaction(payload, True)
        except Exception:
            log.exception("Disponibilités : réaction ajoutée")

    async def on_raw_reaction_remove(payload):
        try:
            await dispos.reaction(payload, False)
        except Exception:
            log.exception("Disponibilités : réaction retirée")

    for fn in (on_raw_reaction_add, on_raw_reaction_remove):
        client.event(fn)


def discord_objet(uid: int):
    """Utilisateur réduit à son identifiant (retrait d'une réaction sans relire le membre)."""
    import discord
    return discord.Object(id=uid)
