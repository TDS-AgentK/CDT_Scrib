"""Drop : un joueur lâche un objet de son inventaire dans un salon, pour 5 s à 2 min. Le premier qui clique sur
« Ramasser » le récupère ; sinon l'objet finit dans le stock du Receleur. Chaque drop est tracé dans eco_drops
(statut en_cours / ramasse / expire, lanceur, ramasseur, dates), l'objet dans eco_mouvements et rec_mouvements.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

import discord

from app import eco_vues, receleur
from app.eco_actions import objet_par_nom, quantite, retirer_objet

log = logging.getLogger("cdt_scrib.drop")
DUREE_MIN, DUREE_MAX = 5, 120
_verrous: dict[str, asyncio.Lock] = {}


def _verrou(drop_id: str) -> asyncio.Lock:
    return _verrous.setdefault(drop_id, asyncio.Lock())


def _date(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.000Z")


def _bouton(drop_id: str, desactive: bool = False) -> discord.ui.View:
    vue = discord.ui.View(timeout=None)
    vue.add_item(discord.ui.Button(custom_id=f"eco:drop:{drop_id}", label="Ramasser", style=discord.ButtonStyle.success, disabled=desactive))
    return vue


def _embed(lanceur: str, objet: dict, q: int, fin_ts: int | None = None, statut: str | None = None) -> discord.Embed:
    nom = f"**{q} × {objet.get('emoji') or ''} {objet['nom']}**".replace("  ", " ")
    if statut:
        texte = statut
    else:
        texte = f"{lanceur} lâche {nom}.\nLe premier à cliquer sur « Ramasser » le récupère. Fin <t:{fin_ts}:R>."
    return discord.Embed(description=texte, color=eco_vues.OR_DEFAUT)


async def lancer(eco, membre: discord.Member, objet_id: str, q: int, duree: int, channel_id: str | None) -> tuple[bool, str]:
    if q <= 0:
        return False, "La quantité doit être positive."
    if not DUREE_MIN <= duree <= DUREE_MAX:
        return False, f"La durée doit être comprise entre {DUREE_MIN} secondes et {DUREE_MAX} secondes (2 minutes)."
    joueur = await eco.joueur_de(membre)
    if not joueur:
        return False, "Aucune fiche joueur liée à ton compte Discord."
    objet = await objet_par_nom(eco, objet_id)
    if not objet:
        return False, "Objet inconnu."
    salon = eco.client.get_channel(int(channel_id)) if channel_id else None
    if salon is None:
        return False, "Salon introuvable pour ce drop."
    async with eco._verrou(membre.id):
        _, possede = await quantite(eco, joueur["id"], objet["id"])
        if possede < q:
            return False, f"Tu n'as que {possede} × {objet['nom']}."
        await retirer_objet(eco, joueur["id"], objet["id"], q, "drop", f"drop de {duree} s")
    fin = datetime.now(timezone.utc) + timedelta(seconds=duree)
    d = await eco.pb.creer("eco_drops", {"lanceur": joueur["id"], "objet": objet["id"], "quantite": q, "duree_s": duree, "salon_id": str(salon.id),
                                         "statut": "en_cours", "date": _date(datetime.now(timezone.utc)), "date_fin": _date(fin), "message_id": ""})
    try:
        msg = await salon.send(embed=_embed(membre.mention, objet, q, int(fin.timestamp())), view=_bouton(d["id"]))
    except discord.HTTPException:
        await eco.ajouter_objet(joueur["id"], objet["id"], q, "drop_annule", "message impossible")
        await eco.pb.maj("eco_drops", d["id"], {"statut": "annule"})
        return False, "Je ne peux pas écrire dans ce salon : drop annulé, l'objet t'est rendu."
    await eco.pb.maj("eco_drops", d["id"], {"message_id": str(msg.id)})
    asyncio.create_task(_attendre(eco, d["id"], duree))
    return True, f"Drop lancé pour {duree} s : {q} × {objet['nom']}."


async def _attendre(eco, drop_id: str, duree: int):
    await asyncio.sleep(duree + 1)
    try:
        await expirer(eco, drop_id)
    except Exception:
        log.exception("Expiration du drop %s impossible", drop_id)


async def _editer(eco, d: dict, embed: discord.Embed):
    try:
        salon = eco.client.get_channel(int(d["salon_id"])) or await eco.client.fetch_channel(int(d["salon_id"]))
        msg = await salon.fetch_message(int(d["message_id"]))
        await msg.edit(embed=embed, view=_bouton(d["id"], True))
    except (discord.HTTPException, ValueError, TypeError):
        log.warning("Message du drop %s non modifiable", d.get("id"))


async def _infos(eco, d: dict) -> tuple[dict, dict]:
    objet = await eco.pb.requete("GET", f'/api/collections/eco_objets/records/{d["objet"]}')
    lanceur = await eco.pb.requete("GET", f'/api/collections/joueurs/records/{d["lanceur"]}')
    return objet, lanceur


async def expirer(eco, drop_id: str):
    """Non ramassé à temps : l'objet entre dans le stock du Receleur."""
    async with _verrou(drop_id):
        d = await eco.pb.requete("GET", f"/api/collections/eco_drops/records/{drop_id}")
        if d.get("statut") != "en_cours":
            return
        await eco.pb.maj("eco_drops", drop_id, {"statut": "expire"})
        await receleur.ajouter_stock(eco, d["objet"], d["quantite"])
        await receleur._mouvement(eco, d["lanceur"], d["objet"], d["quantite"], 0, "drop", f"drop {drop_id}")
        objet, _ = await _infos(eco, d)
        await _editer(eco, d, _embed("", objet, d["quantite"], statut=f"Personne n'a ramassé **{d['quantite']} × {objet['nom']}** : l'objet file chez le Receleur."))


async def ramasser(eco, membre: discord.Member, drop_id: str) -> tuple[bool, str]:
    joueur = await eco.joueur_de(membre)
    if not joueur:
        return False, "Aucune fiche joueur liée à ton compte Discord."
    async with _verrou(drop_id):
        d = await eco.pb.requete("GET", f"/api/collections/eco_drops/records/{drop_id}")
        if d.get("statut") != "en_cours":
            return False, "Ce drop n'est plus disponible."
        await eco.pb.maj("eco_drops", drop_id, {"statut": "ramasse", "ramasse_par": joueur["id"], "date_ramasse": _date(datetime.now(timezone.utc))})
        await eco.ajouter_objet(joueur["id"], d["objet"], d["quantite"], "drop_ramasse", f"drop {drop_id}")
        objet, _ = await _infos(eco, d)
        await _editer(eco, d, _embed("", objet, d["quantite"], statut=f"{membre.mention} a ramassé **{d['quantite']} × {objet['nom']}**."))
    return True, f"Tu as ramassé {d['quantite']} × {objet['nom']}."


async def boucle(eco):
    """Rattrape les drops arrivés à échéance pendant un redémarrage du bot."""
    await eco.client.wait_until_ready()
    while not eco.client.is_closed():
        try:
            for d in await eco.pb.lister("eco_drops", f'statut="en_cours" && date_fin<="{_date(datetime.now(timezone.utc) - timedelta(seconds=5))}"'):
                await expirer(eco, d["id"])
        except Exception:
            log.exception("Erreur dans la boucle des drops")
        await asyncio.sleep(60)
