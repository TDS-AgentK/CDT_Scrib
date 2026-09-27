"""Variantes amusantes des messages de confirmation.
Ajoute des phrases dans ces listes pour enrichir le ton du bot — une est
choisie au hasard à chaque fois, {nom} est remplacé par l'identifiant de la fiche.
"""
import random

CREER = [
    "`{nom}` a bien été créée. Le plus dur reste à faire.",
    "`{nom}` a bien été créée. Contrairement à sa fiche sur le site, je crois...",
    "Nouvelle fiche `{nom}` créée. Nous voilà dans les ennuis.",
    "`{nom}` pop sur le serveur. Si seulement ça se remplissait automatiquement...",
]

MODIFIER = [
    "Section `{section}` de `{nom}` mise à jour.",
    "`{nom}` change de visage : la section `{section}` a été retouchée.",
    "Modification enregistrée sur `{nom}` (`{section}`). Tant d'éléments ajoutés!",
]

SUPPRIMER = [
    "Fiche `{nom}` supprimée. Tu préférais le faire à la main ?",
    "`{nom}` a été effacé·e des registres. Sérieux, nettoyer ? Tu dois être Fyb.",
    "`{nom}` disparaît dans le couscous, babaye.",
]


def pick(templates: list[str], **kwargs) -> str:
    return random.choice(templates).format(**kwargs)
