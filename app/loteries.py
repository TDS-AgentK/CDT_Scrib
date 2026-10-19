"""Loteries admin, dans l'esprit de Draftbot : un admin crée une loterie (lot, fin, nombre de gagnants), les joueurs
participent avec un bouton (gratuit ou ticket payant), le bot tire au sort à l'heure de fin et remet les lots.

Création : /loterie creer (réservé aux ID Discord de LOTERIE_ADMINS et de eco_reglages.loterie_admin_ids) ou page
Loteries du site (Agent K et Kyanite). Le bot publie toute loterie « ouverte » sans message, puis la clôt à sa fin.
Données : eco_loteries (lot, fin, salon, statut, gagnants), eco_loterie_tickets (un enregistrement par joueur).
Une loterie peut être planifiée (« debut » futur : annonce publiée à cette heure) ; si le site change sa fin, il coche
« a_rafraichir » et le bot met l'annonce à jour. Modèles réutilisables : eco_loterie_modeles (durée en minutes),
relancés depuis le site ou par /loterie relancer ; /loterie creer enregistrer:Oui en crée un.
Lot possible : un rôle Discord (role_id, role_nom), définitif (role_heures = 0) ou temporaire (role_heures > 0). Il est
donné par Economie.donner_role ; un rôle temporaire est noté dans eco_roles_temporaires et retiré à l'échéance par
Economie.boucle_roles_temporaires (rattrapage après redémarrage). Un rôle impossible à donner (rôle supprimé, rôle
au-dessus de celui du bot) ne bloque pas le tirage : la raison est annoncée et notée dans « erreurs_lot ».
Gain personnalisé (« gain_perso », texte libre) : affiché dans l'annonce et le tirage, rien n'est distribué sur Discord ;
un administrateur le remet et le coche sur le site (« gains_remis »).
XP par gagnant (« xp_lot ») : créditée au tirage comme l'XP des messages (joueurs.eco_xp + ligne eco_gains « XP ») ;
une montée de niveau est annoncée dans le salon de la loterie, avec ses récompenses (Economie.monter_niveau).
"""
import re
import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
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


def _date_pb(v) -> datetime | None:
    try:
        return datetime.fromisoformat((v or "").replace(" ", "T").replace("Z", "+00:00"))
    except ValueError:
        return None


def _fin(l: dict) -> datetime | None:
    return _date_pb(l.get("fin"))


def a_publier(l: dict, maintenant: datetime) -> bool:
    """Loterie ouverte sans annonce, dont l'heure de publication (« debut », facultative) est arrivée."""
    debut = _date_pb(l.get("debut"))
    return l.get("statut") == "ouverte" and not l.get("message_id") and (debut is None or debut <= maintenant)


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


def lire_duree_role(texte) -> float | None:
    """Durée d'un rôle gagné : « » ou « 0 » = définitif (0), « 7j », « 12h », « 1j12h », « 3 » (jours). None si illisible."""
    t = str(texte or "").strip().lower().replace(" ", "").replace("d", "j")
    if t in ("", "0", "definitif", "définitif"):
        return 0
    if t.isdigit():
        return int(t) * 24
    g = re.fullmatch(r"(?:(\d+)j)?(?:(\d+)h)?", t)
    if not g or not (g.group(1) or g.group(2)):
        return None
    return int(g.group(1) or 0) * 24 + int(g.group(2) or 0)


def duree_role_txt(heures) -> str:
    heures = int(heures or 0)
    if not heures:
        return "définitif"
    j, h = divmod(heures, 24)
    return " ".join(x for x in (f"{j} j" if j else "", f"{h} h" if h else "") if x)


def _role_txt(l: dict) -> str:
    if not (l.get("role_id") or l.get("role_nom")):
        return ""
    nom = f"<@&{l['role_id']}>" if str(l.get("role_id") or "").isdigit() else f"« {l.get('role_nom')} »"
    return f"le rôle {nom} ({duree_role_txt(l.get('role_heures'))})"


def _lot_txt(l: dict, objet: dict | None, cfg: dict) -> str:
    morceaux = []
    if l.get("or_lot"):
        morceaux.append(f"**{_n(l['or_lot'])}** {eco_vues.or_txt(cfg)}")
    if l.get("xp_lot"):
        morceaux.append(f"**{_n(l['xp_lot'])}** {eco_vues.xp_txt(cfg)}")
    if objet:
        morceaux.append(f"**{l.get('quantite_objet') or 1} × {objet.get('emoji') or ''} {objet['nom']}**".replace("  ", " "))
    if _role_txt(l):
        morceaux.append(_role_txt(l))
    if l.get("succes"):
        morceaux.append("un succès")
    if (l.get("gain_perso") or "").strip():
        morceaux.append(f"🎁 {l['gain_perso'].strip()}")
    return " + ".join(morceaux) or "—"


def role_donnable(guild: discord.Guild, role: discord.Role | None) -> str | None:
    """None si le bot peut donner ce rôle, sinon la raison (à corriger dans les réglages du serveur)."""
    if role is None:
        return "rôle introuvable sur le serveur"
    if role.is_default() or role.managed:
        return f"le rôle « {role.name} » ne peut pas être donné (rôle @everyone ou géré par une intégration)"
    moi = guild.me
    if not moi.guild_permissions.manage_roles:
        return "il me manque la permission « Gérer les rôles »"
    if role >= moi.top_role:
        return f"mon rôle doit être placé au-dessus de « {role.name} » dans les réglages du serveur"
    return None


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
    role, heures = None, 0
    if o.get("role"):
        role = membre.guild.get_role(int(o["role"])) if str(o["role"]).isdigit() else None
        raison = role_donnable(membre.guild, role)
        if raison:
            return False, f"Rôle impossible à donner : {raison}."
        heures = lire_duree_role(o.get("role_duree"))
        if heures is None:
            return False, "Durée du rôle illisible : « 7j », « 12h », « 1j12h » ou vide pour un rôle définitif."
    gain = (o.get("gain") or "").strip()[:500]
    if not (o.get("or") or o.get("xp") or objet or role or gain):
        return False, "Indique un lot : de l'Or, de l'XP, un objet, un rôle et/ou un gain personnalisé."
    salon_id = str(o.get("salon") or salon_defaut or "")
    l = await eco.pb.creer("eco_loteries", {
        "titre": o.get("titre") or "Loterie", "description": o.get("description") or "", "salon_id": salon_id, "objet": objet["id"] if objet else "",
        "quantite_objet": int(o.get("quantite") or 1) if objet else 0, "or_lot": int(o.get("or") or 0), "nombre_gagnants": int(o.get("gagnants") or 1),
        "prix_ticket": int(o.get("prix_ticket") or 0), "max_tickets": int(o.get("max_tickets") or 1), "fin": _pb_date(fin),
        "statut": "ouverte", "message_id": "", "cree_par": str(membre.id),
        "role_id": str(role.id) if role else "", "role_nom": role.name if role else "", "role_heures": heures, "gain_perso": gain,
        "xp_lot": int(o.get("xp") or 0)})
    await publier(eco, l)
    txt = f"Loterie « {l['titre']} » créée, tirage le {fin.strftime('%d/%m/%Y à %H:%M')} (heure de Paris)."
    if o.get("enregistrer"):
        minutes = max(1, round((fin - datetime.now(PARIS)).total_seconds() / 60))
        await eco.pb.creer("eco_loterie_modeles", {**_copie(l), "nom": l["titre"], "duree_minutes": minutes,
                                                   "cree_par": str(membre.id)})
        txt += f" Modèle « {l['titre']} » enregistré (durée {_duree_txt(minutes)})."
    return True, txt


CHAMPS_MODELE = ("titre", "description", "salon_id", "objet", "quantite_objet", "or_lot", "nombre_gagnants", "prix_ticket", "max_tickets", "succes",
                 "role_id", "role_nom", "role_heures", "gain_perso", "xp_lot")


def _copie(source: dict) -> dict:
    return {k: source[k] for k in CHAMPS_MODELE if source.get(k) is not None}


def _duree_txt(minutes: int) -> str:
    j, reste = divmod(int(minutes), 1440)
    h, m = divmod(reste, 60)
    return " ".join(x for x in (f"{j} j" if j else "", f"{h} h" if h else "", f"{m} min" if m else "") if x) or "0 min"


async def relancer(eco, membre: discord.Member, modele_id: str, fin_txt: str | None, salon: str | None) -> tuple[bool, str]:
    """Nouvelle loterie à partir d'un modèle : fin donnée, sinon maintenant + durée du modèle."""
    if not await est_admin(eco, membre.id):
        return False, "Seuls les administrateurs de loterie peuvent relancer une loterie."
    try:
        m = await eco.pb.requete("GET", f"/api/collections/eco_loterie_modeles/records/{quote(modele_id or '', safe='')}")
    except Exception:
        m = None
    if not m or not m.get("id"):
        return False, "Modèle inconnu."
    if fin_txt:
        fin = lire_fin(fin_txt)
        if not fin or fin <= datetime.now(PARIS):
            return False, "Date de fin invalide ou passée. Format : JJ/MM/AAAA HH:MM (heure de Paris)."
    else:
        fin = datetime.now(PARIS) + timedelta(minutes=int(m.get("duree_minutes") or 60))
    salon_id = str(salon or m.get("salon_id") or "")
    if not salon_id:
        return False, "Ce modèle n'a pas de salon : précise-le."
    l = await eco.pb.creer("eco_loteries", {**_copie(m), "titre": m.get("titre") or m.get("nom") or "Loterie",
                                            "salon_id": salon_id, "fin": _pb_date(fin), "statut": "ouverte", "message_id": "",
                                            "cree_par": str(membre.id), "modele": m["id"]})
    await publier(eco, l)
    return True, f"Loterie « {l['titre']} » relancée, tirage le {fin.astimezone(PARIS).strftime('%d/%m/%Y à %H:%M')} (heure de Paris)."


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
        if not a_publier({**l, "message_id": ""}, datetime.now(timezone.utc)):
            return False, "Cette loterie n'a pas encore commencé."
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
    try:
        salon = await _salon(eco, l)
    except discord.HTTPException:
        salon = None
    guild = salon.guild if salon is not None and getattr(salon, "guild", None) else (eco.client.get_guild(eco.guild_id) if eco.guild_id else None)
    mentions, erreurs = [], []
    for jid in gagnants:
        j = await eco.pb.requete("GET", f"/api/collections/joueurs/records/{jid}")
        if l.get("or_lot"):
            await changer_or(eco, jid, l["or_lot"])
            await _gain(eco, jid, f"loterie : {l.get('titre')}", "Or", l["or_lot"], "")
        if l.get("xp_lot"):
            await _crediter_xp(eco, guild, salon, j, l["xp_lot"], l.get("titre") or "")
        if objet:
            await eco.ajouter_objet(jid, objet["id"], l.get("quantite_objet") or 1, "loterie", l.get("titre") or "")
        if l.get("succes"):
            await eco.donner_succes(j, l["succes"])
        mention = f"<@{j['discord_id']}>" if j.get("discord_id") else (j.get("pseudo") or "un joueur")
        if l.get("role_id") or l.get("role_nom"):
            raison = await _donner_role(eco, guild, j, l)
            if raison:
                erreurs.append(f"{mention} : {raison}")
                log.warning("Loterie %s : rôle non donné à %s (%s)", l["id"], jid, raison)
        mentions.append(mention)
    await eco.pb.maj("eco_loteries", l["id"], {"gagnants_ids": ",".join(gagnants), "date_tirage": _pb_date(datetime.now(timezone.utc)),
                                              "nb_participants": len(tickets), "erreurs_lot": "\n".join(erreurs)})
    if not salon:
        return
    texte = ("Gagnant(s) : " + ", ".join(mentions) + f"\nLot : {_lot_txt(l, objet, cfg)}") if mentions else "Personne n'a participé."
    if mentions and (l.get("gain_perso") or "").strip():
        texte += f"\n\n🎁 Gain personnalisé : {l['gain_perso'].strip()} — remis par un administrateur."
    if erreurs:
        texte += "\n\n⚠️ Rôle non donné — un administrateur s'en chargera :\n" + "\n".join(erreurs)
    fin = discord.Embed(title=l.get("titre") or "Loterie", color=eco_vues.OR_DEFAUT, description=texte)
    try:
        if l.get("message_id"):
            msg = await salon.fetch_message(int(l["message_id"]))
            await msg.edit(embed=await _embed_ouverte(eco, l), view=_vue(l["id"], True))
        await salon.send(embed=fin, allowed_mentions=discord.AllowedMentions(users=True))
    except discord.HTTPException:
        log.warning("Annonce du tirage de la loterie %s impossible", l["id"])


async def _crediter_xp(eco, guild, salon, j: dict, xp: int, titre: str):
    """XP gagnée à une loterie : même traitement que l'XP des messages (niveau recalculé, montée annoncée et récompensée)."""
    from app.economie import niveau_de
    frais = await eco.pb.requete("GET", f"/api/collections/joueurs/records/{j['id']}")
    ancien = frais.get("eco_xp") or 0
    maj = await eco.pb.maj("joueurs", j["id"], {"eco_xp": ancien + int(xp)})
    await _gain(eco, j["id"], f"loterie : {titre}", "XP", int(xp), "")
    courbe = (await eco.config()).get("courbe") or {}
    n0, n1 = niveau_de(courbe, ancien)[0], niveau_de(courbe, (maj or {}).get("eco_xp", ancien + int(xp)))[0]
    if n1 <= n0 or salon is None or guild is None or not str(j.get("discord_id") or "").isdigit():
        return
    try:
        membre = guild.get_member(int(j["discord_id"])) or await guild.fetch_member(int(j["discord_id"]))
        await eco.monter_niveau(salon, membre, maj, n0, n1)
    except discord.HTTPException:
        log.warning("Loterie : montée de niveau de %s non annoncée", j["id"])


async def rafraichir(eco, l: dict):
    """Fin modifiée sur le site : l'annonce affiche la nouvelle heure de tirage."""
    await eco.pb.maj("eco_loteries", l["id"], {"a_rafraichir": False})
    salon = await _salon(eco, l)
    if not salon or not l.get("message_id"):
        return
    try:
        msg = await salon.fetch_message(int(l["message_id"]))
        await msg.edit(embed=await _embed_ouverte(eco, l), view=_vue(l["id"]))
    except discord.HTTPException:
        log.warning("Annonce de la loterie %s non modifiable", l["id"])


async def _donner_role(eco, guild, j: dict, l: dict, origine: str | None = None) -> str | None:
    """Rôle du lot pour un gagnant ; renvoie la raison d'un échec, sans jamais lever (le tirage continue).
    Sert aussi aux rôles gagnés sur le site (app/roles_site.py), avec leur propre `origine`."""
    try:
        if guild is None:
            return "serveur Discord introuvable"
        if not j.get("discord_id"):
            return "pas de compte Discord lié à sa fiche"
        membre = guild.get_member(int(j["discord_id"]))
        if membre is None:
            try:
                membre = await guild.fetch_member(int(j["discord_id"]))
            except discord.NotFound:
                return "n'est plus sur le serveur"
        role = guild.get_role(int(l["role_id"])) if str(l.get("role_id") or "").isdigit() else eco._role_discord(guild, None, l.get("role_nom"))
        raison = role_donnable(guild, role)
        if raison:
            return raison
        return await eco.donner_role(membre, j, str(role.id), role.name, (l.get("role_heures") or 0) / 24, origine=origine or f"loterie : {l.get('titre') or ''}")
    except Exception as exc:
        log.exception("Loterie %s : rôle du gagnant %s", l.get("id"), j.get("id"))
        return f"erreur inattendue ({exc.__class__.__name__})"


async def boucle(eco):
    await eco.client.wait_until_ready()
    while not eco.client.is_closed():
        try:
            maintenant = datetime.now(timezone.utc)
            for l in await eco.pb.lister("eco_loteries", 'statut="ouverte"'):
                if a_publier(l, maintenant):
                    await publier(eco, l)
                elif l.get("message_id") and l.get("a_rafraichir") and not (_fin(l) and _fin(l) <= maintenant):
                    await rafraichir(eco, l)
            for l in await eco.pb.lister("eco_loteries", f'statut="ouverte" && fin<="{_pb_date(datetime.now(timezone.utc))}"'):
                await tirer(eco, l)
        except Exception:
            log.exception("Erreur dans la boucle des loteries")
        await asyncio.sleep(20)
