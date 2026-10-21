"""Drops automatiques : plannings créés sur le site (/gestion/loteries › Drops automatiques), exécutés par le bot.

Un planning (eco_drop_plannings) est quotidien ou hebdomadaire (jour choisi) avec une fenêtre horaire de Paris
(« 18:00 » → « 20:00 », fenêtre qui passe minuit acceptée, début = fin pour une heure fixe). Le bot tire une heure au
hasard dans la fenêtre et l'écrit dans « prochain » (et la fin de la fenêtre dans « prochain_fin »). À l'heure dite, il
écrit d'abord le créneau suivant et le créneau joué (« dernier », « dernier_fin ») puis lance le drop : un redémarrage
ne rejoue donc jamais un créneau. Un créneau dépassé de plus de 15 min (bot arrêté) est sauté.
Le site vide « prochain » à chaque modification ou réactivation : le bot recalcule à partir de max(maintenant,
dernier_fin), sans redonner de drop dans une fenêtre déjà servie.
"""
import asyncio
import logging
import random
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

log = logging.getLogger("cdt_scrib.drops_auto")
PARIS = ZoneInfo("Europe/Paris")
RETARD_MAX = timedelta(minutes=15)
FREQUENCES = ("quotidien", "hebdomadaire")


def _pb_date(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.000Z")


def lire_date(v) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace(" ", "T").replace("Z", "+00:00"))
    except ValueError:
        return None


def _heure(texte) -> time:
    h, _, m = str(texte or "").strip().replace("h", ":").partition(":")
    return time(int(h), int(m or 0))


def fenetre(p: dict, jour: date) -> tuple[datetime, datetime]:
    """Fenêtre du planning qui commence le jour donné (heure de Paris) ; fin ≤ début → la fin est le lendemain,
    sauf début = fin (heure fixe)."""
    debut = datetime.combine(jour, _heure(p.get("heure_debut")), PARIS)
    fin = datetime.combine(jour, _heure(p.get("heure_fin") or p.get("heure_debut")), PARIS)
    if fin < debut:
        fin = datetime.combine(jour + timedelta(days=1), fin.time(), PARIS)
    return debut, fin


def _jour_valide(p: dict, jour: date) -> bool:
    if p.get("frequence") == "hebdomadaire":
        return jour.weekday() == int(p.get("jour_semaine") or 0)  # 0 = lundi … 6 = dimanche
    return True


def prochain_creneau(p: dict, apres: datetime, rng: random.Random | None = None) -> tuple[datetime, datetime] | None:
    """Première heure tirée au hasard dans une fenêtre du planning, strictement après « apres » (une fenêtre déjà
    entamée n'est tirée que dans sa partie restante). Renvoie (heure, fin de la fenêtre), en UTC."""
    rng = rng or random
    if p.get("frequence") not in FREQUENCES:
        return None
    jour = apres.astimezone(PARIS).date() - timedelta(days=1)  # la fenêtre de la veille peut passer minuit
    for _ in range(16):
        if _jour_valide(p, jour):
            debut, fin = fenetre(p, jour)
            debut, fin = debut.astimezone(timezone.utc), fin.astimezone(timezone.utc)
            if debut == fin:
                if debut > apres:
                    return debut, fin
            else:
                depart = max(debut, apres)
                if depart < fin:
                    secondes = int((fin - depart).total_seconds())
                    return depart + timedelta(seconds=rng.randrange(secondes) if secondes > 0 else 0), fin
        jour += timedelta(days=1)
    return None


def planifier(p: dict, maintenant: datetime, rng: random.Random | None = None) -> tuple[dict, bool]:
    """Décision pour un planning actif à l'instant donné : (champs à écrire en base, lancer le drop maintenant ?).
    Les champs sont écrits AVANT le lancement : c'est ce qui empêche un doublon au redémarrage."""
    prochain, prochain_fin = lire_date(p.get("prochain")), lire_date(p.get("prochain_fin"))
    dernier_fin = lire_date(p.get("dernier_fin"))
    if not prochain:
        base = max(maintenant, dernier_fin) if dernier_fin else maintenant
        c = prochain_creneau(p, base, rng)
        return ({"prochain": _pb_date(c[0]), "prochain_fin": _pb_date(c[1])} if c else {"prochain": "", "prochain_fin": ""}), False
    if prochain > maintenant:
        return {}, False
    fin_jouee = prochain_fin or prochain
    c = prochain_creneau(p, max(maintenant, fin_jouee), rng)
    maj = {"prochain": _pb_date(c[0]) if c else "", "prochain_fin": _pb_date(c[1]) if c else "",
           "dernier": _pb_date(prochain), "dernier_fin": _pb_date(fin_jouee)}
    if maintenant - prochain > RETARD_MAX:
        maj["dernier_statut"] = "sauté (bot arrêté à l'heure prévue)"
        return maj, False
    return maj, True


async def executer(eco, p: dict) -> str:
    """Lance le drop du planning ; renvoie le statut à afficher sur le site."""
    from app import drop
    salon = eco.client.get_channel(int(p["salon_id"])) if p.get("salon_id") else None
    if salon is None:
        try:
            salon = await eco.client.fetch_channel(int(p["salon_id"]))
        except Exception:
            return "échec : salon introuvable"
    objet = await eco.pb.requete("GET", f'/api/collections/eco_objets/records/{p["objet"]}') if p.get("objet") else None
    or_ = int(p.get("or_montant") or 0)
    if not objet and or_ <= 0:
        return "échec : ni objet ni Or"
    q = max(1, int(p.get("quantite") or 1))
    duree = max(drop.DUREE_MIN, min(drop.DUREE_ADMIN_MAX, int(p.get("duree_s") or drop.DUREE_DEFAUT)))
    ok = await drop.creer_drop_cree(eco, salon, "", "", objet, q, or_, duree, planning_id=p["id"])
    return "lancé" if ok else "échec : impossible d'écrire dans le salon"


async def tour(eco, maintenant: datetime | None = None):
    maintenant = maintenant or datetime.now(timezone.utc)
    for p in await eco.pb.lister("eco_drop_plannings", "actif=true"):
        maj, lancer = planifier(p, maintenant)
        if not maj:
            continue
        await eco.pb.maj("eco_drop_plannings", p["id"], maj)  # d'abord : le créneau est consommé
        if lancer:
            try:
                statut = await executer(eco, p)
            except Exception:
                log.exception("Drop automatique %s impossible", p["id"])
                statut = "échec : erreur du bot"
            await eco.pb.maj("eco_drop_plannings", p["id"], {"dernier_statut": statut, "nb_drops": (p.get("nb_drops") or 0) + (statut == "lancé")})


async def boucle(eco):
    await eco.client.wait_until_ready()
    while not eco.client.is_closed():
        try:
            await tour(eco)
        except Exception:
            log.exception("Erreur dans la boucle des drops automatiques")
        await asyncio.sleep(20)
