"""Jets de dés lancés depuis les fiches de jeu du site : le bot les publie sur Discord.

Le site tire le jet, l'enregistre dans la collection `jets` avec le salon voulu (`discord_salon_id` :
#random par défaut, ou le salon personnel du joueur) et `discord_envoye=false`. Comme pour `ros_envois`,
le bot relit cette file toutes les JETS_SITE_S secondes, poste l'embed (format B1 : sans phrase d'ambiance)
puis note `discord_envoye` et `discord_message_id`. Le jeton reste celui du bot : le site n'en a pas besoin.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import discord

from app.pocketbase import PocketBase

log = logging.getLogger(__name__)

JETS_SITE_S = 5
# Un jet plus vieux que ça n'est plus publié (bot arrêté longtemps, jets d'essai) : on le marque seulement.
FRAICHEUR = timedelta(minutes=15)
OR_CDT = 0xC9A24C

# Jets déjà postés mais dont la mise à jour en base a échoué : on ne les reposte pas à chaque tour.
_publies: dict[str, str] = {}

ICONES = {"competence": "🎯", "sauvegarde": "🛡️", "ca": "🛡️", "attaque": "⚔️", "degats": "💥",
          "sort": "✨", "degats_sort": "💥", "libre": "🎲"}


def _date(jet: dict) -> datetime | None:
    brut = jet.get("date") or jet.get("created") or ""
    try:
        return datetime.fromisoformat(brut.replace("Z", "+00:00").replace(" ", "T"))
    except ValueError:
        return None


def _nom_personnage(jet: dict) -> str:
    perso = (jet.get("expand") or {}).get("personnage") or {}
    for cle in ("nom", "titre", "Titre", "name"):
        if perso.get(cle):
            return perso[cle]
    return jet.get("personnage_nom") or "Personnage"


def construire_embed(jet: dict) -> discord.Embed:
    """Embed B1 : titre, total en gros, détail des dés, DD pour un sort ; pas de phrase RP."""
    des = jet.get("des") or []
    bonus = jet.get("bonus") or 0
    detail = f"`{jet.get('formule', '')}` → " + (", ".join(str(d) for d in des) if des else "—")
    if bonus:
        detail += f" {'+' if bonus > 0 else '−'} {abs(bonus)}"
    detail += f" = **{jet.get('total')}**"
    lignes = [f"# {jet.get('total')}", detail]
    if jet.get("dd"):
        dd = str(jet["dd"])
        lignes.append(f"DD du sort : **{dd if dd.startswith(('>', '<')) else '> ' + dd}**")
    if jet.get("naturel_max"):
        lignes.append("⭐ Maximum naturel")
    elif jet.get("naturel_min"):
        lignes.append("💀 Face minimale")
    if jet.get("objet_utilise"):
        lignes.append(f"Objet utilisé : {jet['objet_utilise']}")
    icone = ICONES.get(jet.get("type") or "", "🎲")
    embed = discord.Embed(title=f"{icone} {_nom_personnage(jet)} · {jet.get('libelle') or 'Jet'}",
                          description="\n".join(lignes), colour=OR_CDT)
    embed.set_footer(text="🌐 lancé depuis le site")
    return embed


async def publier(client: discord.Client, pb: PocketBase, jet: dict):
    maj = {"discord_envoye": True}
    quand = _date(jet)
    if jet["id"] in _publies:
        maj["discord_message_id"] = _publies[jet["id"]]
    elif quand and datetime.now(timezone.utc) - quand > FRAICHEUR:
        log.info("Jet %s trop ancien, marqué sans être publié", jet["id"])
    else:
        salon = client.get_channel(int(jet["discord_salon_id"]))
        if salon is None:
            salon = await client.fetch_channel(int(jet["discord_salon_id"]))
        message = await salon.send(embed=construire_embed(jet))
        maj["discord_message_id"] = _publies[jet["id"]] = str(message.id)
    await pb.maj("jets", jet["id"], maj)
    _publies.pop(jet["id"], None)


async def boucle(client: discord.Client, pb: PocketBase):
    await client.wait_until_ready()
    while not client.is_closed():
        try:
            for jet in await pb.lister("jets", 'origine="site" && discord_envoye=false && discord_salon_id!=""',
                                       tri="created", expand="personnage"):
                try:
                    await publier(client, pb, jet)
                except Exception:
                    # Salon introuvable, interdit ou qui n'accepte pas de message : on marque le jet pour ne
                    # pas bloquer la file ; une erreur de la base sera retentée au tour suivant.
                    log.exception("Jet %s non publié (salon %s)", jet.get("id"), jet.get("discord_salon_id"))
                    if jet["id"] not in _publies:
                        try:
                            await pb.maj("jets", jet["id"], {"discord_envoye": True})
                        except Exception:
                            log.exception("Jet %s : impossible de le marquer comme traité", jet.get("id"))
        except Exception:
            log.exception("Erreur dans la boucle des jets du site")
        await asyncio.sleep(JETS_SITE_S)
