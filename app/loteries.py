"""Loteries admin, dans l'esprit de Draftbot : un admin crée une loterie (lot, fin, nombre de gagnants), les joueurs
participent avec un bouton (gratuit ou ticket payant), le bot tire au sort à l'heure de fin et remet les lots.

Création : /loterie creer (réservé aux ID Discord de LOTERIE_ADMINS et de eco_reglages.loterie_admin_ids) ou page
Loteries du site (Agent K et Kyanite). Le bot publie toute loterie « ouverte » sans message, puis la clôt à sa fin.
Données : eco_loteries (lot, fin, salon, statut, gagnants), eco_loterie_tickets (un enregistrement par joueur).
"""
import asyncio
import logging
import random
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import discord

from app import eco_vues
from app.eco_actions import _gain, _n, changer_or
from app.pocketbase import echapper

log = logging.getLogger("cdt_scrib.loteries")
PARIS = ZoneInfo("Europe/Paris")
LOTERIE_ADMINS = {"293135204371922946", "286482667023892480"}
_verrou = asyncio.Lock()


def _pb_date(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.000Z")


def _fin(l: dict) -> datetime | None:
    try:
        return datetime.fromisoformat((l.get("fin") or "").replace(" ", "T").replace("Z", "+00:00"))
    except ValueError:
        return None


async def est_admin(eco, user_id: int) -> bool:
    reglages = (await eco.config())["reglages"]
    autres = {x.strip() for x in str(reglages.get("loterie_admin_ids") or "").replace(";", ",").split(",") if x.strip()}
    return str(user_id) in LOTERIE_ADMINS | autres


def lire_fin(texte: str) -> datetime | None:
    """« JJ/MM/AAAA HH:MM » (heure de Paris) ou ISO."""
    texte = (texte or "").strip()
    for fmt in ("%d/%m/%Y %H:%M", "%d/%m/%Y %Hh%M", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(texte, fmt).replace(tzinfo=PARIS)
        except ValueError:
            continue
    return None


def _lot_txt(l: dict, objet: dict | None, cfg: dict) -> str:
    morceaux = []
    if l.get("or_lot"):
        morceaux.append(f"**{_n(l['or_lot'])}** {eco_vues.or_txt(cfg)}")
    if objet:
        morceaux.append(f"**{l.get('quantite_objet') or 1} × {objet.get('emoji') or ''} {objet['nom']}**".replace("  ", " "))
    if l.get("succes"):
        morceaux.append("un succès")
    return " + ".join(morceaux) or "—"


async def _objet(eco, l: dict) -> dict | None:
    return await eco.pb.requete("GET", f'/api/collections/eco_objets/records/{l["objet"]}') if l.get("objet") else None


async def _embed_ouverte(eco, l: dict) -> discord.Embed:
    cfg = await eco.config()
    fin = _fin(l)
    gagnants = l.get("nombre_gagnants") or 1
    e = discord.Embed(title=l.get("titre") or "Loterie", description=l.get("description") or "", color=eco_vues.OR_DEFAUT)
    e.add_field(name="Lot" + (" (par gagnant)" if gagnants > 1 else ""), value=_lot_txt(l, await _objet(eco, l), cfg), inline=False)
    e.add_field(name="Gagnants", value=str(gagnants))
    prix = l.get("prix_ticket") or 0
    e.add_field(name="Participation", value=f"{_n(prix)} {eco_vues.or_txt(cfg)}" if prix else "Gratuite")
    e.add_field(name="Tickets par joueur", value=str(l.get("max_tickets") or 1))
    if fin:
        e.add_field(name="Tirage", value=f"<t:{int(fin.timestamp())}:F> (<t:{int(fin.timestamp())}:R>)", inline=False)
    return e


def _vue(loterie_id: str, desactive: bool = False) -> discord.ui.View:
    v = discord.ui.View(timeout=None)
    v.add_item(discord.ui.Button(custom_id=f"eco:lot:{loterie_id}", label="Participer", style=discord.ButtonStyle.primary, disabled=desactive))
    return v


async def _salon(eco, l: dict):
    if not l.get("salon_id"):
        return None
    return eco.client.get_channel(int(l["salon_id"])) or await eco.client.fetch_channel(int(l["salon_id"]))


async def creer(eco, membre: discord.Member, o: dict, salon_defaut: str | None) -> tuple[bool, str]:
    if not await est_admin(eco, membre.id):
        return False, "Seuls les administrateurs de loterie peuvent en créer."
    fin = lire_fin(o.get("fin"))
    if not fin or fin <= datetime.now(PARIS):
        return False, "Date de fin invalide ou passée. Format : JJ/MM/AAAA HH:MM (heure de Paris)."
    objet = None
    if o.get("objet"):
        from app.eco_actions import objet_par_nom
        objet = await objet_par_nom(eco, o["objet"])
        if not objet:
            return False, "Objet inconnu."
    if not (o.get("or") or objet):
        return False, "Indique un lot : de l'Or et/ou un objet."
    salon_id = str(o.get("salon") or salon_defaut or "")
    l = await eco.pb.creer("eco_loteries", {
        "titre": o.get("titre") or "Loterie", "description": o.get("description") or "", "salon_id": salon_id, "objet": objet["id"] if objet else "",
        "quantite_objet": int(o.get("quantite") or 1) if objet else 0, "or_lot": int(o.get("or") or 0), "nombre_gagnants": int(o.get("gagnants") or 1),
        "prix_ticket": int(o.get("prix_ticket") or 0), "max_tickets": int(o.get("max_tickets") or 1), "fin": _pb_date(fin),
        "statut": "ouverte", "message_id": "", "cree_par": str(membre.id)})
    await publier(eco, l)
    return True, f"Loterie « {l['titre']} » créée, tirage le {fin.strftime('%d/%m/%Y à %H:%M')} (heure de Paris)."


async def publier(eco, l: dict):
    salon = await _salon(eco, l)
    if not salon:
        log.warning("Loterie %s : salon introuvable", l["id"])
        return
    msg = await salon.send(embed=await _embed_ouverte(eco, l), view=_vue(l["id"]))
    await eco.pb.maj("eco_loteries", l["id"], {"message_id": str(msg.id)})


async def participer(eco, membre: discord.Member, loterie_id: str) -> tuple[bool, str]:
    joueur = await eco.joueur_de(membre)
    if not joueur:
        return False, "Aucune fiche joueur liée à ton compte Discord."
    cfg = await eco.config()
    async with eco._verrou(membre.id), _verrou:
        l = await eco.pb.requete("GET", f"/api/collections/eco_loteries/records/{loterie_id}")
        fin = _fin(l)
        if l.get("statut") != "ouverte" or (fin and fin <= datetime.now(timezone.utc)):
            return False, "Cette loterie est terminée."
        t = await eco.pb.premier("eco_loterie_tickets", f'loterie="{echapper(loterie_id)}" && joueur="{echapper(joueur["id"])}"')
        possede, maxi = (t or {}).get("quantite") or 0, l.get("max_tickets") or 1
        if possede >= maxi:
            return False, f"Tu as déjà {possede} ticket(s), le maximum pour cette loterie."
        prix = l.get("prix_ticket") or 0
        if prix:
            frais = await eco.pb.requete("GET", f'/api/collections/joueurs/records/{joueur["id"]}')
            if (frais.get("eco_or") or 0) < prix:
                return False, f"Il te manque {_n(prix - (frais.get('eco_or') or 0))} {eco_vues.or_txt(cfg)}."
            await changer_or(eco, joueur["id"], -prix)
            await _gain(eco, joueur["id"], f"loterie : {l.get('titre')}", "Or", -prix, "")
        if t:
            await eco.pb.maj("eco_loterie_tickets", t["id"], {"quantite": possede + 1})
        else:
            await eco.pb.creer("eco_loterie_tickets", {"loterie": loterie_id, "joueur": joueur["id"], "quantite": 1})
    return True, f"Tu participes à « {l.get('titre')} » ({possede + 1}/{maxi} ticket(s))."


async def tirer(eco, l: dict):
    """Tirage à la fin : lots remis, succès attribué, message clôturé."""
    async with _verrou:
        l = await eco.pb.requete("GET", f'/api/collections/eco_loteries/records/{l["id"]}')
        if l.get("statut") != "ouverte":
            return
        await eco.pb.maj("eco_loteries", l["id"], {"statut": "terminee"})  # d'abord : pas de double tirage
    tickets = await eco.pb.lister("eco_loterie_tickets", f'loterie="{echapper(l["id"])}"')
    pool = [t["joueur"] for t in tickets for _ in range(t.get("quantite") or 1)]
    gagnants: list[str] = []
    while pool and len(gagnants) < (l.get("nombre_gagnants") or 1):
        g = random.choice(pool)
        gagnants.append(g)
        pool = [x for x in pool if x != g]
    cfg, objet = await eco.config(), await _objet(eco, l)
    mentions = []
    for jid in gagnants:
        j = await eco.pb.requete("GET", f"/api/collections/joueurs/records/{jid}")
        if l.get("or_lot"):
            await changer_or(eco, jid, l["or_lot"])
            await _gain(eco, jid, f"loterie : {l.get('titre')}", "Or", l["or_lot"], "")
        if objet:
            await eco.ajouter_objet(jid, objet["id"], l.get("quantite_objet") or 1, "loterie", l.get("titre") or "")
        if l.get("succes"):
            await eco.donner_succes(j, l["succes"])
        mentions.append(f"<@{j['discord_id']}>" if j.get("discord_id") else (j.get("pseudo") or "un joueur"))
    await eco.pb.maj("eco_loteries", l["id"], {"gagnants_ids": ",".join(gagnants), "date_tirage": _pb_date(datetime.now(timezone.utc)),
                                              "nb_participants": len(tickets)})
    salon = await _salon(eco, l)
    if not salon:
        return
    fin = discord.Embed(title=l.get("titre") or "Loterie", color=eco_vues.OR_DEFAUT,
                        description=("Gagnant(s) : " + ", ".join(mentions) + f"\nLot : {_lot_txt(l, objet, cfg)}") if mentions else "Personne n'a participé.")
    try:
        if l.get("message_id"):
            msg = await salon.fetch_message(int(l["message_id"]))
            await msg.edit(embed=await _embed_ouverte(eco, l), view=_vue(l["id"], True))
        await salon.send(embed=fin, allowed_mentions=discord.AllowedMentions(users=True))
    except discord.HTTPException:
        log.warning("Annonce du tirage de la loterie %s impossible", l["id"])


async def boucle(eco):
    await eco.client.wait_until_ready()
    while not eco.client.is_closed():
        try:
            for l in await eco.pb.lister("eco_loteries", 'statut="ouverte" && message_id=""'):
                await publier(eco, l)
            for l in await eco.pb.lister("eco_loteries", f'statut="ouverte" && fin<="{_pb_date(datetime.now(timezone.utc))}"'):
                await tirer(eco, l)
        except Exception:
            log.exception("Erreur dans la boucle des loteries")
        await asyncio.sleep(20)
