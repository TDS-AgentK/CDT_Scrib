"""Duels de Personus sur Discord (concept validé par K le 19/11/2026) : le site arbitre, le bot ne fait que l'interface.

Même principe que les combats (app/combat.py) : le bot et le site ne s'appellent pas, ils passent par la base partagée.
- /duel joueur @adversaire, /duel ouvert, et les boutons des cartes (Confirmer, Accepter, Surenchérir, Refuser,
  Retirer) : le bot dépose une demande dans `personus_duel_demandes` (statut « a_traiter », avec l'ID Discord du joueur),
  « réveille » le site (/api/combat-file, même adresse que les combats), puis attend « traitee » ou « erreur ».
  Le site applique les mêmes règles que sa page (double acceptation, mises bloquées à l'acceptation, surenchère par pas
  de 50 plafonnée par les deux bourses, 3 relances, garde-fous) au nom du joueur dont le compte Discord est lié.
- Les messages à afficher viennent du site par `personus_duel_annonces` (défi, défi ouvert, surenchère, accepté,
  refusé, expiré, annulé, résultat) : la boucle les lit toutes les ANNONCES_S secondes et met à jour LA carte du duel
  (un seul message par duel, gardé dans personus_duels.message_id / salon_id), ou la poste si elle n'existe pas encore,
  dans le salon indiqué par le site (#commandes réglé sur /gestion/personus, ou le salon où /duel a été tapé).
On joue toujours sur le site : la carte d'un duel accepté porte un lien « Jouer sur le site ».
Boutons : custom_id « eco:pdu:<duel>:conf|acc|sur|ref|ret » ; fenêtre de surenchère « eco:pdm:<duel> ».
"""
import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

import discord
import httpx

from app.pocketbase import PocketBase, echapper

log = logging.getLogger(__name__)

ANNONCES_S = 5
FRAICHEUR = timedelta(hours=72)  # une annonce plus ancienne (bot arrêté longtemps) n'est plus postée, seulement marquée
SITE_PAR_DEFAUT = "https://cdtsite-production.up.railway.app"
SALON_PAR_DEFAUT = "1027210265785282731"  # #commandes
MISE_DEFAUT, PAS, MAX_RELANCES = 100, 50, 3
OR_CDT, VERT, ROUGE, GRIS, BLEU = 0xC9A24C, 0x6FAE7A, 0xB0473B, 0x7D7F86, 0x3C4BB0
COULEURS = {"defi": OR_CDT, "defi_ouvert": OR_CDT, "surenchere": BLEU, "accepte": VERT, "resultat": OR_CDT,
            "refuse": GRIS, "expire": GRIS, "annule": GRIS}
ETIQUETTES = {"conf": ("Confirmer le défi", 3), "acc": ("Accepter", 3), "sur": ("Surenchérir…", 1),
              "ref": ("Refuser", 4), "ret": ("Retirer le défi", 2)}
BOUTONS_SITE = {"accepter": "acc", "refuser": "ref", "surencherir": "sur", "retirer": "ret"}


def site() -> str:
    return (os.getenv("SITE_URL") or SITE_PAR_DEFAUT).rstrip("/")


def lien_duel(duel_id: str) -> str:
    return f"{site()}/personus?onglet=duels&duel={duel_id}"


# ---------------------------------------------------------------- règles recopiées du site (lib/personusDuelRegles.ts)

def bornes_surenchere(mise: int, solde_a: int, solde_b: int) -> tuple[int, int] | None:
    """De mise + 50 à la plus petite des deux bourses, par pas de 50 ; None si la relance est impossible."""
    mini = mise + PAS
    maxi = mise + ((min(solde_a, solde_b) - mise) // PAS) * PAS
    return (mini, maxi) if maxi >= mini else None


def montant_valide(texte: str, bornes: tuple[int, int], mise: int) -> int | None:
    """Montant tapé dans la fenêtre « Nouvelle mise » : entier, dans les bornes, par pas de 50 (sinon None)."""
    t = (texte or "").replace(" ", "").replace(" ", "").removesuffix("PO").removesuffix("po").strip()
    if not t.isdigit():
        return None
    n = int(t)
    return n if bornes[0] <= n <= bornes[1] and (n - mise) % PAS == 0 else None


# ---------------------------------------------------------------- demandes déposées dans la base

async def _reveiller():
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            await client.get(site() + "/api/combat-file")
    except httpx.HTTPError:
        pass  # le site traitera la file à sa prochaine requête


async def deposer(pb: PocketBase, action: str, discord_id, secondes: float = 12, **donnees) -> str:
    """Dépose une demande, attend que le site la traite ; renvoie l'id du duel, ou lève RuntimeError (message du site)."""
    d = await pb.creer("personus_duel_demandes", {"action": action, "discord_id": str(discord_id), "statut": "a_traiter", **donnees})
    await _reveiller()
    for _ in range(int(secondes / 0.5)):
        r = await pb.requete("GET", f"/api/collections/personus_duel_demandes/records/{d['id']}")
        if r.get("statut") == "erreur":
            raise RuntimeError(r.get("erreur") or "Demande refusée par le site.")
        if r.get("statut") == "traitee":
            return r.get("duel") or ""
        await asyncio.sleep(0.5)
    raise RuntimeError("Le site n'a pas encore traité la demande : réessayez dans un instant.")


async def bornes_pour(pb: PocketBase, duel_id: str, discord_id) -> tuple[tuple[int, int], int]:
    """Bornes de la surenchère pour ce joueur (lues dans la base, revérifiées par le site) et mise actuelle."""
    d = await pb.requete("GET", f"/api/collections/personus_duels/records/{duel_id}")
    if d.get("statut") != "propose":
        raise RuntimeError("Ce défi n'attend plus de réponse.")
    if int(d.get("relances") or 0) >= MAX_RELANCES:
        raise RuntimeError(f"La mise a déjà été relevée {MAX_RELANCES} fois : acceptez ou retirez-vous.")
    moi = await pb.premier("joueurs", f'discord_id="{echapper(discord_id)}"')
    if not moi:
        raise RuntimeError("Votre compte Discord n'est relié à aucun joueur du site.")
    autre = d.get("adversaire") or moi["id"]
    lanceur, adversaire = (await pb.requete("GET", f"/api/collections/joueurs/records/{d['lanceur']}"),
                           await pb.requete("GET", f"/api/collections/joueurs/records/{autre}"))
    mise = int(d.get("mise") or MISE_DEFAUT)
    b = bornes_surenchere(mise, int(lanceur.get("eco_or") or 0), int(adversaire.get("eco_or") or 0))
    if not b:
        raise RuntimeError("Les bourses ne permettent pas de relever la mise.")
    return b, mise


def fenetre_surenchere(duel_id: str, bornes: tuple[int, int], mise: int) -> dict:
    """Fenêtre Discord « Nouvelle mise » (maquette de la surenchère validée par K)."""
    mini, maxi = bornes
    return {
        "custom_id": f"eco:pdm:{duel_id}", "title": "Relever la mise",
        "components": [{"type": 1, "components": [{
            "type": 4, "custom_id": "montant", "style": 1, "required": True, "min_length": 1, "max_length": 7,
            "label": f"Nouvelle mise : de {mini} à {maxi} PO, par {PAS}"[:45], "value": str(mini),
            "placeholder": f"Mise actuelle : {mise} PO",
        }]}],
    }


# ---------------------------------------------------------------- carte du duel

def boutons(duel_id: str, codes: list[str], jouer: bool = False) -> list:
    rangee = [{"type": 2, "style": ETIQUETTES[c][1], "label": ETIQUETTES[c][0], "custom_id": f"eco:pdu:{duel_id}:{c}"} for c in codes]
    if jouer:
        rangee.append({"type": 2, "style": 5, "label": "Jouer sur le site", "url": lien_duel(duel_id)})
    return [{"type": 1, "components": rangee}] if rangee else []


def carte(duel_id: str, evenement: str, texte: str, codes: list[str], mentions: list[str]) -> dict:
    """Corps de la carte d'un duel : mentions (qui doit agir), texte du site, boutons de l'étape."""
    fin = evenement in ("refuse", "expire", "annule", "resultat")
    embed = {"title": "⚔️ Duel Personus", "description": texte[:4000], "color": COULEURS.get(evenement, OR_CDT)}
    if not fin:
        embed["footer"] = {"text": "Rien n'est prélevé avant l'accord des deux joueurs · on joue sur le site"}
    return {"content": " ".join(f"<@{m}>" for m in mentions if m), "embeds": [embed],
            "components": boutons(duel_id, codes, jouer=evenement == "accepte")}


def carte_a_confirmer(duel_id: str, lanceur_id, adversaire_id, mise: int = MISE_DEFAUT) -> dict:
    """Réponse publique à /duel : récapitulatif et bouton « Confirmer le défi » (acceptation du défiant)."""
    qui = f"<@{adversaire_id}>" if adversaire_id else "le premier joueur qui acceptera (défi ouvert)"
    texte = (f"<@{lanceur_id}> propose un duel Personus à {qui} pour **{mise} PO**.\n"
             "Même mystère pour les deux, aucun indice, 24 h pour jouer après l'acceptation. Le gagnant prend les deux mises.\n"
             "Confirmez pour lancer le défi : rien n'est prélevé avant que l'adversaire accepte.")
    return {"content": "", "embeds": [{"title": "⚔️ Duel Personus", "description": texte, "color": OR_CDT}],
            "components": boutons(duel_id, ["conf", "ret"])}


def codes_boutons(annonce: dict) -> list[str]:
    return [BOUTONS_SITE[b] for b in (annonce.get("boutons") or []) if b in BOUTONS_SITE]


def _date(brut) -> datetime | None:
    try:
        return datetime.fromisoformat(str(brut).replace("Z", "+00:00").replace(" ", "T")) if brut else None
    except ValueError:
        return None


async def publier(client: discord.Client, pb: PocketBase, annonce: dict):
    """Met à jour la carte du duel (ou la poste), puis note l'annonce « envoye »."""
    from app.eco_vues import vue_discord
    duel = await pb.requete("GET", f"/api/collections/personus_duels/records/{annonce['duel']}")
    corps = carte(duel["id"], annonce.get("evenement") or "", annonce.get("texte") or "", codes_boutons(annonce), annonce.get("mentions") or [])
    embed, vue = discord.Embed.from_dict(corps["embeds"][0]), vue_discord(corps["components"])
    mentions = discord.AllowedMentions(users=True, everyone=False, roles=False)
    salon_carte, message_id = duel.get("salon_id") or "", duel.get("message_id") or ""
    if salon_carte and message_id:
        salon = client.get_channel(int(salon_carte)) or await client.fetch_channel(int(salon_carte))
        await salon.get_partial_message(int(message_id)).edit(content=corps["content"] or None, embed=embed, view=vue)
        # Le résultat est aussi annoncé dans le salon des duels s'il n'est pas celui de la carte.
        salon_annonce = annonce.get("salon_id") or SALON_PAR_DEFAUT
        if annonce.get("evenement") == "resultat" and salon_annonce != salon_carte:
            autre = client.get_channel(int(salon_annonce)) or await client.fetch_channel(int(salon_annonce))
            await autre.send(content=corps["content"] or None, embed=embed, allowed_mentions=mentions)
    else:
        salon_id = annonce.get("salon_id") or SALON_PAR_DEFAUT
        salon = client.get_channel(int(salon_id)) or await client.fetch_channel(int(salon_id))
        m = await salon.send(content=corps["content"] or None, embed=embed, view=vue, allowed_mentions=mentions)
        await pb.maj("personus_duels", duel["id"], {"salon_id": str(salon_id), "message_id": str(m.id)})
    await pb.maj("personus_duel_annonces", annonce["id"], {"statut": "envoye", "erreur": ""})


async def boucle(client: discord.Client, pb: PocketBase):
    await client.wait_until_ready()
    while not client.is_closed():
        try:
            maintenant = datetime.now(timezone.utc)
            for a in sorted(await pb.lister("personus_duel_annonces", 'statut="a_envoyer"'), key=lambda x: str(x.get("created") or "")):
                quand = _date(a.get("created"))
                if quand and maintenant - quand > FRAICHEUR:
                    await pb.maj("personus_duel_annonces", a["id"], {"statut": "erreur", "erreur": "Trop ancienne : non postée."})
                    continue
                try:
                    await publier(client, pb, a)
                except Exception as e:
                    log.exception("Annonce de duel %s non postée", a.get("id"))
                    await pb.maj("personus_duel_annonces", a["id"], {"statut": "erreur", "erreur": str(e)[:500]})
        except Exception:
            log.exception("Erreur dans la boucle des duels Personus")
        await asyncio.sleep(ANNONCES_S)
