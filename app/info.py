"""/info : résumé de toutes les commandes du bot, construit depuis scripts/register_commands.py (les descriptions
restent donc à jour toutes seules). Une commande qui n'est rangée dans aucune rubrique apparaît dans « Autres »."""
from app import eco_vues

RUBRIQUES = [
    ("📜 Fiches de personnage", ["fiche"]),
    ("💰 Économie", ["argent", "niveau", "inventaire", "topargent", "topniveau", "boutique", "payer", "donner", "utiliser", "echanger"]),
    ("🕯️ Receleur et drops", ["receleur", "vendre", "racheter", "drop"]),
    ("🏰 Rostheim", ["recompense", "boite-a-role"]),
    ("⚔️ Jeu de rôle (fiches du site)", ["session", "attaque"]),
    ("🛡️ Administration", ["dropadmin", "loterie"]),
]

# Ajoutée sous la rubrique des fiches : deux sortes de fiches cohabitent (app/fiches_site.py).
NOTE_FICHES = ("*Les fiches créées ici (et celles qui existaient déjà) restent sur le bot : on les modifie avec /fiche modifier. "
               "Celles créées sur le site (logo contrat de la page du personnage, une par apparence) se modifient et s'envoient "
               "depuis le site. /fiche voir affiche les deux, et le message s'efface au bout de 24 h.*")


def _ligne(c: dict) -> str:
    sous = [o["name"] for o in c.get("options", []) if o.get("type") in (1, 2)]
    nom = f'**/{c["name"]}**' + (f' ({" · ".join(sous)})' if sous else "")
    return f'{nom} : {c.get("description", "")}'


def _champs(titre: str, lignes: list[str]) -> list[dict]:
    """Un champ par rubrique, découpé si besoin (1 024 caractères au plus par champ)."""
    champs, bloc = [], ""
    for l in lignes:
        if bloc and len(bloc) + len(l) + 1 > 1024:
            champs.append({"name": titre, "value": bloc, "inline": False})
            titre, bloc = "​", ""
        bloc = f"{bloc}\n{l}" if bloc else l
    if bloc:
        champs.append({"name": titre, "value": bloc, "inline": False})
    return champs


def vue_info(prefixe: str = "??") -> dict:
    from scripts.register_commands import COMMANDS
    par_nom = {c["name"]: c for c in COMMANDS}
    ranges = {n for _t, noms in RUBRIQUES for n in noms}
    champs = []
    for titre, noms in RUBRIQUES:
        lignes = [_ligne(par_nom[n]) for n in noms if n in par_nom]
        champs += _champs(titre, lignes + ([NOTE_FICHES] if "fiche" in noms else []))
    autres = [_ligne(c) for c in COMMANDS if c["name"] not in ranges and c["name"] != "info"]
    if autres:
        champs += _champs("✨ Autres", autres)
    champs.append({"name": "⌨️ Commandes à taper", "inline": False, "value": (
        f"`{prefixe}rp @partenaires` après un RP · `{prefixe}niveau` · `{prefixe}classement` · `{prefixe}inventaire` · "
        f"`{prefixe}boutique` · `{prefixe}acheter <numéro ou nom>`")})
    return {"title": "Commandes du Scribe", "color": eco_vues.OR_DEFAUT, "fields": champs[:25],
            "description": "Toutes les commandes du bot. Tapez `/` puis le nom pour voir leurs options."}
