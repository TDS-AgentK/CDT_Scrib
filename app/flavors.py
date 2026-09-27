"""Variantes amusantes des messages de confirmation.
Ajoute des phrases dans ces listes pour enrichir le ton du bot — une est
choisie au hasard à chaque fois, {nom} est remplacé par l'identifiant de la fiche.
"""
import random

CREER = [
    "Fiche `{nom}` créée ! Un nouveau héros (ou monstre) vient de naître.",
    "`{nom}` est officiellement invoqué·e sur les registres des CDT.",
    "Nouvelle fiche `{nom}` ouverte. Que l'aventure commence !",
    "`{nom}` sort de l'œuf. Encore un peu fragile, mais ça viendra.",
]

MODIFIER = [
    "Section `{section}` de `{nom}` mise à jour. Beau travail, scribe !",
    "`{nom}` change de visage : la section `{section}` a été retouchée.",
    "Modification enregistrée sur `{nom}` (`{section}`). Le grimoire se souvient de tout.",
]

SUPPRIMER = [
    "Fiche `{nom}` supprimée. Repose en paix, cher personnage.",
    "`{nom}` a été effacé·e des registres. Poussière tu étais...",
    "`{nom}` disparaît dans les limbes. Adieu !",
]


def pick(templates: list[str], **kwargs) -> str:
    return random.choice(templates).format(**kwargs)
