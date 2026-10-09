"""Combat des fiches de jeu (/attaque) : le site arbitre, le bot ne fait que l'interface Discord.

Le moteur de calcul n'existe que sur le site : le bot appelle donc la route `/api/bot/combat` du site (variables
SITE_URL et CDT_SECRET_PARTAGE, la même valeur que le secret du site) pour déclarer un combat, accepter ou refuser,
voter en cas d'égalité, et pour l'autocomplétion. Il lit la collection `combats` dans la base pour publier :
- les combats lancés depuis le site (bouton « Attaquer » de la fiche) ;
- chaque changement de statut (en attente → refusé / expiré / égalité / résolu), en modifiant le même message.
`combats.discord_poste` garde le dernier statut affiché : tout combat dont le statut a changé depuis est republié.
Boutons : custom_id « eco:cbt:<id>:ok|no » (accepter / refuser), « eco:cbt:<id>:va|vd » (avantage attaquant / défenseur).
"""
import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

import discord
import httpx

from app.pocketbase import PocketBase, echapper

log = logging.getLogger(__name__)

VERIFIER_S = 5
FRAICHEUR = timedelta(minutes=30)  # un combat plus ancien n'est plus publié (bot arrêté longtemps)
OR_CDT, VERT, ROUGE, GRIS = 0xC9A24C, 0x6FAE7A, 0xB0473B, 0x7D7F86
NOMS_MODE = {"avantage": "avantage", "desavantage": "désavantage", "maximum": "maximum"}


# ---------------------------------------------------------------- appels au site

def _secret() -> str:
    # Nom posé sur Railway : CDT_SECRET_PARTAGE (BOT_SITE_SECRET accepté aussi).
    return os.getenv("CDT_SECRET_PARTAGE") or os.getenv("BOT_SITE_SECRET") or ""


def configure() -> bool:
    return bool(os.getenv("SITE_URL") and _secret())


async def site(methode: str, params: dict | None = None, corps: dict | None = None):
    """Appel de /api/bot/combat ; lève RuntimeError avec le message du site en cas de refus."""
    url = os.getenv("SITE_URL", "").rstrip("/") + "/api/bot/combat"
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.request(methode, url, params=params, json=corps, headers={"X-Bot-Secret": _secret()})
    if r.status_code >= 400:
        raise RuntimeError(r.text or f"Erreur {r.status_code} du site")
    return r.json()


async def autocompletion(discord_id: int, focus: str, options: dict, tape: str) -> list[dict]:
    """Choix proposés pour /attaque : perso (vos fiches), type (armes et sorts de la fiche choisie), cible."""
    try:
        if focus == "perso":
            l = await site("GET", {"discord_id": discord_id, "quoi": "fiches"})
            choix = [{"name": f["nom"][:100], "value": f["fiche"]} for f in l]
        elif focus == "type":
            if not options.get("perso"):
                return [{"name": "Choisissez d'abord votre personnage", "value": "-"}]
            l = await site("GET", {"discord_id": discord_id, "quoi": "attaques", "fiche": options["perso"]})
            choix = [{"name": a["libelle"][:100], "value": a["action"]} for a in l]
        elif focus == "cible":
            l = await site("GET", {"discord_id": discord_id, "quoi": "cibles"})
            choix = [{"name": (("⚔ " if c["table"] else "") + c["nom"])[:100], "value": f'{c["fiche"]}:{c["cible"]}'} for c in l]
        else:
            return []
    except Exception:
        log.exception("Autocomplétion /attaque")
        return []
    return [c for c in choix if tape.lower() in c["name"].lower()][:25]


# ---------------------------------------------------------------- affichage

def _jet(j: dict | None) -> str:
    if not j:
        return "—"
    des = ", ".join(str(d) for d in j.get("des") or [])
    bonus = j.get("bonus") or 0
    txt = f"**{j.get('total')}** `{j.get('formule')}` [{des}]" + (f" {'+' if bonus > 0 else '−'} {abs(bonus)}" if bonus else "")
    mode = j.get("mode_jet") or ""
    if mode in ("avantage", "desavantage"):
        txt += f" · {NOMS_MODE[mode]} (écarté : {', '.join(str(d) for d in j.get('des_ecartes') or [])})"
    if j.get("naturel_max"):
        txt += " · ⭐ maximum naturel"
    return txt


async def _mention(pb: PocketBase, user_id: str) -> str:
    """Mention Discord du joueur d'un compte du site (users → joueurs.discord_id)."""
    if not user_id:
        return ""
    try:
        u = await pb.requete("GET", f"/api/collections/users/records/{user_id}", params={"expand": "joueur"})
        did = ((u.get("expand") or {}).get("joueur") or {}).get("discord_id")
        return f"<@{did}>" if did else u.get("name") or ""
    except Exception:
        return ""


def _boutons(c: dict) -> list:
    if c["statut"] == "en_attente":
        return [{"type": 1, "components": [
            {"type": 2, "style": 3, "label": "Accepter", "custom_id": f'eco:cbt:{c["id"]}:ok'},
            {"type": 2, "style": 4, "label": "Refuser", "custom_id": f'eco:cbt:{c["id"]}:no'}]}]
    if c["statut"] == "egalite":
        return [{"type": 1, "components": [
            {"type": 2, "style": 1, "label": "Avantage attaquant", "custom_id": f'eco:cbt:{c["id"]}:va'},
            {"type": 2, "style": 2, "label": "Avantage défenseur", "custom_id": f'eco:cbt:{c["id"]}:vd'}]}]
    return []


async def message(pb: PocketBase, c: dict) -> dict:
    """Message Discord d'un combat (embed + boutons selon le statut). `c` : enregistrement combats avec ses jets."""
    ex = c.get("expand") or {}
    att, dfn = c.get("attaquant_nom") or "?", c.get("defenseur_nom") or "?"
    defenseur = await _mention(pb, c.get("defenseur_user"))
    lignes = [f"Avec : {c.get('action_libelle') or '?'}"]
    statut, couleur, contenu = c["statut"], OR_CDT, ""
    if statut == "en_attente":
        lignes.append(f"{defenseur or dfn}, acceptez-vous le combat ? (10 minutes)")
        contenu = defenseur
    elif statut in ("refuse", "expire"):
        lignes.append("Défi refusé : rien n'est lancé." if statut == "refuse" else "Défi expiré : personne n'a répondu dans les 10 minutes.")
        couleur = GRIS
    else:
        lignes.append(f"⚔️ Attaque : {_jet(ex.get('jet_attaque'))}")
        lignes.append(f"🛡️ Défense : {_jet(ex.get('jet_defense'))}")
        if statut == "egalite":
            votes = c.get("votes") or {}
            lignes.append("**Égalité** : chacun choisit ; en cas de désaccord, 1d2 tranche."
                          + (f" Votes : attaquant {'✓' if votes.get('attaquant') else '…'}, défenseur {'✓' if votes.get('defenseur') else '…'}" if votes else ""))
        elif c.get("issue") == "passe":
            lignes.append(f"**L'attaque surpasse la défense. {c.get('degats') or 0} points de dégâts.** {_jet(ex.get('jet_degats'))}")
            couleur = VERT
        else:
            lignes.append("**L'attaque ne surpasse pas la défense.**")
            couleur = ROUGE
        if c.get("detail") and statut == "resolu":
            lignes.append(f"*{c['detail']}*")
    embed = {"title": f"⚔️ {att} attaque {dfn}", "description": "\n".join(lignes), "color": couleur,
             "footer": {"text": "Combat lancé depuis le site" if c.get("origine") == "site" else "Combat · fiches de jeu CDT"}}
    return {"content": contenu, "embeds": [embed], "components": _boutons(c), "allowed_mentions": {"parse": ["users"]}}


async def combat_complet(pb: PocketBase, combat_id: str) -> dict:
    return await pb.requete("GET", f"/api/collections/combats/records/{combat_id}", params={"expand": "jet_attaque,jet_defense,jet_degats"})


# ---------------------------------------------------------------- boucle : combats lancés depuis le site, expirations

def _date(brut) -> datetime | None:
    try:
        return datetime.fromisoformat(str(brut).replace("Z", "+00:00").replace(" ", "T")) if brut else None
    except ValueError:
        return None


async def publier(client: discord.Client, pb: PocketBase, c: dict):
    """Publie un combat (premier message) ou met à jour son message, puis note le statut affiché."""
    from app.eco_vues import vue_discord
    corps = await message(pb, c)
    salon = client.get_channel(int(c["salon_id"])) or await client.fetch_channel(int(c["salon_id"]))
    embed, vue = discord.Embed.from_dict(corps["embeds"][0]), vue_discord(corps["components"])
    maj = {"discord_poste": c["statut"]}
    if c.get("discord_message_id"):
        await salon.get_partial_message(int(c["discord_message_id"])).edit(content=corps["content"] or None, embed=embed, view=vue)
    else:
        m = await salon.send(content=corps["content"] or None, embed=embed, view=vue,
                             allowed_mentions=discord.AllowedMentions(users=True, everyone=False, roles=False))
        maj["discord_message_id"] = str(m.id)
    await pb.maj("combats", c["id"], maj)


async def boucle(client: discord.Client, pb: PocketBase):
    await client.wait_until_ready()
    while not client.is_closed():
        try:
            maintenant = datetime.now(timezone.utc)
            # Défis non acceptés à temps.
            for c in await pb.lister("combats", 'statut="en_attente"'):
                fin = _date(c.get("expire_le"))
                if fin and fin < maintenant:
                    await pb.maj("combats", c["id"], {"statut": "expire", "detail": "Défi non accepté dans les 10 minutes."})
            # Combats dont le statut a changé depuis le dernier affichage (dont ceux lancés depuis le site).
            for c in await pb.lister("combats", 'salon_id!="" && statut!=discord_poste', tri="created"):
                quand = _date(c.get("created"))
                if quand and maintenant - quand > FRAICHEUR and not c.get("discord_message_id"):
                    await pb.maj("combats", c["id"], {"discord_poste": c["statut"]})
                    continue
                try:
                    await publier(client, pb, await combat_complet(pb, c["id"]))
                except Exception:
                    log.exception("Combat %s non publié (salon %s)", c.get("id"), c.get("salon_id"))
                    await pb.maj("combats", c["id"], {"discord_poste": c["statut"]})
        except Exception:
            log.exception("Erreur dans la boucle des combats")
        await asyncio.sleep(VERIFIER_S)
