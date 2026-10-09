"""Jets de dés lancés depuis les fiches de jeu du site : le bot les publie sur Discord.

Le site tire le jet, l'enregistre dans la collection `jets` avec le salon voulu (`discord_salon_id` :
#random par défaut, ou le salon personnel du joueur), `discord_envoye=false` et `discord_statut="en_attente"`.
Comme pour `ros_envois`, le bot relit cette file toutes les JETS_SITE_S secondes, poste l'embed (format B1 : sans
phrase d'ambiance) puis note `discord_statut` (envoye/erreur), `discord_envoye`, `discord_message_id`,
`discord_lien`, `discord_envoye_le` ou `discord_erreur`. Le jeton reste celui du bot : le site n'en a pas besoin.

Objets de hasard : sous chaque jet, « 🔁 Try again » (le lanceur, 10 minutes) et « 💀 Échec critique » (un autre
joueur, 30 secondes) ; custom_id « eco:jet:<id>:ta|ec ». Comme pour les combats, le bot dépose la demande sur le jet
(`demande`, `demande_statut = "a_traiter"`), le site l'applique (inventaire, nouveau résultat), puis le message est
mis à jour. Un jet modifié autrement (sur le site) porte `discord_a_maj = true` : la boucle met son message à jour.
Les boutons disparaissent une fois leur délai passé.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import discord

from app.combat import DELAI_ECHEC, DELAI_TRY_AGAIN, _attendre_jet, _reveiller
from app.pocketbase import PocketBase

log = logging.getLogger(__name__)

JETS_SITE_S = 5
# Un jet plus vieux que ça n'est plus publié (bot arrêté longtemps, jets d'essai) : on le marque seulement.
FRAICHEUR = timedelta(minutes=15)
OR_CDT = 0xC9A24C

# Jets déjà postés mais dont la mise à jour en base a échoué : on ne les reposte pas à chaque tour.
_publies: dict[str, dict] = {}

ICONES = {"competence": "🎯", "sauvegarde": "🛡️", "ca": "🛡️", "attaque": "⚔️", "degats": "💥",
          "sort": "✨", "degats_sort": "💥", "libre": "🎲"}


def _date(jet: dict) -> datetime | None:
    brut = jet.get("date") or jet.get("created") or ""
    try:
        return datetime.fromisoformat(brut.replace("Z", "+00:00").replace(" ", "T"))
    except ValueError:
        return None


def _nom_personnage(jet: dict) -> str:
    # Le site enregistre le nom affiché avec la forme active (« Jurgen (Loup) ») : il passe avant celui du personnage.
    if jet.get("personnage_nom"):
        return jet["personnage_nom"]
    perso = (jet.get("expand") or {}).get("personnage") or {}
    for cle in ("prenom", "nom", "titre", "name"):  # personnages du site : le nom est dans « prenom »
        if perso.get(cle):
            return perso[cle]
    return "Personnage"


def _ecoule(jet: dict) -> timedelta:
    quand = _date(jet)
    return datetime.now(timezone.utc) - quand if quand else timedelta(days=1)


def boutons(jet: dict) -> list:
    """Try again / Échec critique tant que leur délai court et que le jet peut encore en recevoir un."""
    objets = jet.get("objets") or []
    b = []
    if (_ecoule(jet) < DELAI_TRY_AGAIN and jet.get("mode_jet") not in ("echec_auto", "maximum")
            and not {"try_again", "echec_critique", "reussite_critique"} & set(objets)):
        b.append({"type": 2, "style": 2, "label": "🔁 Try again", "custom_id": f'eco:jet:{jet["id"]}:ta'})
    if _ecoule(jet) < DELAI_ECHEC and jet.get("mode_jet") != "echec_auto" and "echec_critique" not in objets:
        b.append({"type": 2, "style": 4, "label": "💀 Échec critique", "custom_id": f'eco:jet:{jet["id"]}:ec'})
    return [{"type": 1, "components": b}] if b else []


def corps_message(jet: dict) -> dict:
    return {"embeds": [construire_embed(jet).to_dict()], "components": boutons(jet)}


async def agir(pb: PocketBase, discord_id: int, jet_id: str, choix: str) -> dict:
    """Bouton sous un jet : demande déposée sur le jet, appliquée par le site ; renvoie le jet à jour."""
    j = await pb.requete("GET", f"/api/collections/jets/records/{jet_id}")
    if j.get("demande_statut") == "a_traiter":
        raise RuntimeError("Une action est déjà en cours sur ce jet : réessayez dans un instant.")
    await pb.maj("jets", jet_id, {"demande": {"action": "objet", "discord_id": str(discord_id), "quoi": "try_again" if choix == "ta" else "echec_critique"},
                                  "demande_statut": "a_traiter", "demande_erreur": ""})
    await _reveiller()
    return await _attendre_jet(pb, jet_id)


def construire_embed(jet: dict) -> discord.Embed:
    """Embed B1 : titre, total en gros, détail des dés, DD pour un sort ; pas de phrase RP."""
    des = jet.get("des") or []
    bonus = jet.get("bonus") or 0
    detail = f"`{jet.get('formule', '')}` → " + (", ".join(str(d) for d in des) if des else "—")
    if bonus:
        detail += f" {'+' if bonus > 0 else '−'} {abs(bonus)}"
    detail += f" = **{jet.get('total')}**"
    ecartes = jet.get("des_ecartes") or []
    if ecartes:
        # Deux dés lancés (« Ça ne compte que pour un », avantage, désavantage) : on montre celui qui est écarté.
        garde = "le pire" if jet.get("mode_jet") == "desavantage" else "le meilleur"
        detail += f"\nDeux dés, on garde {garde} · écarté : {', '.join(str(d) for d in ecartes)}"
    lignes = [f"# {jet.get('total')}", detail]
    # Avantage / désavantage (états, ou « Ça ne compte que pour un ») : les deux jets sont montrés, l'écarté barré.
    if jet.get("mode_jet") in ("avantage", "desavantage") and jet.get("des_ecartes"):
        ecarte = ", ".join(str(d) for d in jet["des_ecartes"])
        garde = "le meilleur" if jet["mode_jet"] == "avantage" else "le moins bon"
        lignes.append(f"🎲 Deux jets, on garde {garde} : [{', '.join(str(d) for d in des)}] gardé · ~~[{ecarte}]~~ écarté")
    elif jet.get("mode_jet") == "echec_auto":
        lignes.append("❌ Échec d'office (état)")
    if jet.get("dd"):
        dd = str(jet["dd"])
        lignes.append(f"DD du sort : **{dd if dd.startswith(('>', '<')) else '> ' + dd}**")
    if jet.get("naturel_max"):
        lignes.append("⭐ Maximum naturel")
    elif jet.get("naturel_min"):
        lignes.append("💀 Face minimale")
    if jet.get("objet_utilise"):
        lignes.append(f"Objet de hasard : {jet['objet_utilise']}")
    icone = ICONES.get(jet.get("type") or "", "🎲")
    embed = discord.Embed(title=f"{icone} {_nom_personnage(jet)} · {jet.get('libelle') or 'Jet'}",
                          description="\n".join(lignes), colour=OR_CDT)
    embed.set_footer(text="🌐 lancé depuis le site")
    return embed


def _maintenant() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.000Z")


async def publier(client: discord.Client, pb: PocketBase, jet: dict):
    # Statut noté comme pour ros_envois : envoye (lien du message) ou erreur (motif).
    quand = _date(jet)
    if jet["id"] in _publies:
        maj = _publies[jet["id"]]
    elif quand and datetime.now(timezone.utc) - quand > FRAICHEUR:
        log.info("Jet %s trop ancien, marqué sans être publié", jet["id"])
        maj = {"discord_envoye": True, "discord_statut": "erreur", "discord_erreur": "Jet de plus de 15 minutes : non publié."}
    else:
        salon = client.get_channel(int(jet["discord_salon_id"]))
        if salon is None:
            salon = await client.fetch_channel(int(jet["discord_salon_id"]))
        from app.eco_vues import vue_discord
        message = await salon.send(embed=construire_embed(jet), view=vue_discord(boutons(jet)), allowed_mentions=discord.AllowedMentions.none())
        maj = _publies[jet["id"]] = {"discord_envoye": True, "discord_statut": "envoye", "discord_message_id": str(message.id),
                                     "discord_lien": message.jump_url, "discord_envoye_le": _maintenant(), "discord_erreur": ""}
    await pb.maj("jets", jet["id"], maj)
    _publies.pop(jet["id"], None)


_signatures: dict[str, str] = {}  # boutons affichés sous les jets récents


async def mettre_a_jour(client: discord.Client, pb: PocketBase, jet: dict):
    """Message d'un jet déjà posté : nouveau résultat (objet joué sur le site) ou boutons dont le délai est passé."""
    from app.eco_vues import vue_discord
    salon = client.get_channel(int(jet["discord_salon_id"])) or await client.fetch_channel(int(jet["discord_salon_id"]))
    await salon.get_partial_message(int(jet["discord_message_id"])).edit(embed=construire_embed(jet), view=vue_discord(boutons(jet)))


def _signature(jet: dict) -> str:
    return "|".join(b["custom_id"] for r in boutons(jet) for b in r["components"]) + f'#{jet.get("total")}'


async def boucle(client: discord.Client, pb: PocketBase):
    await client.wait_until_ready()
    while not client.is_closed():
        try:
            # Jets modifiés sur le site après leur publication, et boutons dont le délai vient de passer.
            depuis = (datetime.now(timezone.utc) - DELAI_TRY_AGAIN - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
            for jet in await pb.lister("jets", f'origine="site" && discord_message_id!="" && (discord_a_maj=true || date>="{depuis}")', expand="personnage"):
                sig = _signature(jet)
                if jet.get("discord_a_maj") or (jet["id"] in _signatures and _signatures[jet["id"]] != sig):
                    try:
                        await mettre_a_jour(client, pb, jet)
                    except Exception:
                        log.exception("Jet %s : message non mis à jour", jet.get("id"))
                    if jet.get("discord_a_maj"):
                        await pb.maj("jets", jet["id"], {"discord_a_maj": False})
                _signatures[jet["id"]] = sig
        except Exception:
            log.exception("Erreur dans la mise à jour des jets du site")
        try:
            for jet in await pb.lister("jets", 'origine="site" && discord_envoye=false && discord_salon_id!=""',
                                       tri="created", expand="personnage"):
                try:
                    await publier(client, pb, jet)
                except Exception as e:
                    # Salon introuvable, interdit ou qui n'accepte pas de message : on marque le jet pour ne
                    # pas bloquer la file ; une erreur de la base sera retentée au tour suivant.
                    log.exception("Jet %s non publié (salon %s)", jet.get("id"), jet.get("discord_salon_id"))
                    if jet["id"] not in _publies:
                        try:
                            await pb.maj("jets", jet["id"], {"discord_envoye": True, "discord_statut": "erreur",
                                                             "discord_erreur": f"{type(e).__name__} : {e}"[:500]})
                        except Exception:
                            log.exception("Jet %s : impossible de le marquer comme traité", jet.get("id"))
        except Exception:
            log.exception("Erreur dans la boucle des jets du site")
        await asyncio.sleep(JETS_SITE_S)
