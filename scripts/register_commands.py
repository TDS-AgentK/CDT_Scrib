"""Enregistre les commandes slash auprès de Discord.
À exécuter une fois (ou après modification des commandes) :
    python -m scripts.register_commands
"""
import os

import httpx
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.environ["DISCORD_TOKEN"]
APP_ID = os.environ["DISCORD_APPLICATION_ID"]
GUILD_ID = os.getenv("GUILD_ID") or None

SECTION_CHOICES = [
    {"name": "Architecture", "value": "architecture"},
    {"name": "Identité", "value": "identite"},
    {"name": "Physique", "value": "physique"},
    {"name": "Apparence", "value": "apparence"},
]

COMMANDS = [
    {
        "name": "fiche",
        "description": "Gérer les fiches de personnage",
        "options": [
            {
                "type": 1,  # SUB_COMMAND
                "name": "creer",
                "description": "Créer une nouvelle fiche de personnage",
                "options": [
                    {"type": 3, "name": "nom", "description": "Identifiant court (ex: elena)", "required": True}
                ],
            },
            {
                "type": 1,
                "name": "modifier",
                "description": "Modifier une section d'une de vos fiches",
                "options": [
                    {"type": 3, "name": "nom", "description": "Identifiant de votre fiche",
                     "required": True, "autocomplete": True},
                    {"type": 3, "name": "section", "description": "Section à modifier",
                     "required": True, "choices": SECTION_CHOICES},
                ],
            },
            {
                "type": 1,
                "name": "image",
                "description": "Envoyer directement une image (personnage ou équipement) pour une de vos fiches",
                "options": [
                    {"type": 3, "name": "nom", "description": "Identifiant de votre fiche",
                     "required": True, "autocomplete": True},
                    {"type": 3, "name": "cible", "description": "Quelle image remplacer", "required": True,
                     "choices": [
                         {"name": "Personnage", "value": "personnage"},
                         {"name": "Équipement", "value": "equipement"},
                     ]},
                    {"type": 11, "name": "fichier",
                     "description": "Image PNG/JPEG/WEBP/GIF, 8 Mo max", "required": True},
                ],
            },
            {
                "type": 1,
                "name": "supprimer",
                "description": "Supprimer une de vos fiches",
                "options": [
                    {"type": 3, "name": "nom", "description": "Identifiant de votre fiche",
                     "required": True, "autocomplete": True}
                ],
            },
            {
                "type": 1,
                "name": "voir",
                "description": "Afficher une fiche de personnage",
                "options": [
                    {"type": 3, "name": "nom", "description": "Identifiant de la fiche",
                     "required": True, "autocomplete": True}
                ],
            },
            {
                "type": 1,
                "name": "liste",
                "description": "Lister toutes les fiches disponibles",
            },
        ],
    },
    # Économie (app/eco_interactions.py)
    *[
        {"name": nom, "description": desc,
         "options": [{"type": 6, "name": "membre", "description": "Membre à afficher (vous par défaut)", "required": False}]}
        for nom, desc in (("argent", "Votre Or, votre place et votre record"),
                          ("niveau", "Votre niveau et votre progression"),
                          ("inventaire", "Votre Or, vos monnaies Rostheim et vos objets"))
    ],
    {"name": "topargent", "description": "Classement d'économie (Or)"},
    {"name": "topniveau", "description": "Classement des niveaux (XP)"},
    {"name": "boutique", "description": "Ouvrir la boutique et acheter des articles"},
    {
        "name": "payer", "description": "Donner de l'Or (ou une monnaie Rostheim) à un membre",
        "options": [
            {"type": 6, "name": "membre", "description": "Membre qui reçoit", "required": True},
            {"type": 4, "name": "montant", "description": "Montant", "required": True, "min_value": 1},
            {"type": 3, "name": "monnaie", "description": "Or par défaut", "required": False, "autocomplete": True},
        ],
    },
    {
        "name": "donner", "description": "Donner un objet de votre inventaire à un membre",
        "options": [
            {"type": 6, "name": "membre", "description": "Membre qui reçoit", "required": True},
            {"type": 3, "name": "objet", "description": "Objet de votre inventaire", "required": True, "autocomplete": True},
            {"type": 4, "name": "quantite", "description": "Quantité (1 par défaut)", "required": False, "min_value": 1},
        ],
    },
    {
        "name": "vendre", "description": "Vendre un objet de votre inventaire au Receleur contre de l'Or",
        "options": [
            {"type": 3, "name": "objet", "description": "Objet de votre inventaire", "required": True, "autocomplete": True},
            {"type": 4, "name": "quantite", "description": "Quantité (1 par défaut)", "required": False, "min_value": 1},
        ],
    },
    {
        "name": "recompense", "description": "Réclamer la récompense d'une action de Rostheim (texte, lore, quiz…)",
        "options": [
            {"type": 3, "name": "zone", "description": "Zone : Académie, Veille, Théâtre…", "required": True, "autocomplete": True},
            {"type": 3, "name": "type", "description": "Action réalisée dans cette zone", "required": True, "autocomplete": True},
        ],
    },
    {"name": "receleur", "description": "Voir ce que le Receleur reprend et vend, et vos ventes de la semaine", "options": []},
    {
        "name": "racheter", "description": "Acheter un objet au Receleur",
        "options": [
            {"type": 3, "name": "objet", "description": "Objet en vente chez le Receleur", "required": True, "autocomplete": True},
            {"type": 4, "name": "quantite", "description": "Quantité (1 par défaut)", "required": False, "min_value": 1},
        ],
    },
    {
        "name": "drop", "description": "Lâcher un objet dans le salon : le premier qui le ramasse le garde, sinon il file chez le Receleur",
        "options": [
            {"type": 3, "name": "objet", "description": "Objet de votre inventaire", "required": True, "autocomplete": True},
            {"type": 4, "name": "duree", "description": "Durée en secondes (5 à 120)", "required": True, "min_value": 5, "max_value": 120},
            {"type": 4, "name": "quantite", "description": "Quantité (1 par défaut)", "required": False, "min_value": 1},
        ],
    },
    {
        "name": "dropadmin", "description": "Drop d'un objet et/ou d'Or créés pour l'occasion (administrateurs de loterie)",
        "options": [
            {"type": 4, "name": "duree", "description": "Durée en secondes (5 à 3600)", "required": True, "min_value": 5, "max_value": 3600},
            {"type": 3, "name": "objet", "description": "Objet du catalogue", "required": False, "autocomplete": True},
            {"type": 4, "name": "quantite", "description": "Quantité de l'objet (1 par défaut)", "required": False, "min_value": 1},
            {"type": 4, "name": "or", "description": "Or à ramasser", "required": False, "min_value": 1},
        ],
    },
    {
        "name": "loterie", "description": "Loteries (administrateurs de loterie)",
        "options": [{
            "type": 1, "name": "creer", "description": "Créer une loterie",
            "options": [
                {"type": 3, "name": "titre", "description": "Titre de la loterie", "required": True},
                {"type": 3, "name": "fin", "description": "Fin du tirage : JJ/MM/AAAA HH:MM (heure de Paris)", "required": True},
                {"type": 4, "name": "gagnants", "description": "Nombre de gagnants (1 par défaut)", "required": False, "min_value": 1},
                {"type": 4, "name": "or", "description": "Or gagné par chaque gagnant", "required": False, "min_value": 1},
                {"type": 3, "name": "objet", "description": "Objet gagné par chaque gagnant", "required": False, "autocomplete": True},
                {"type": 4, "name": "quantite", "description": "Quantité de l'objet (1 par défaut)", "required": False, "min_value": 1},
                {"type": 4, "name": "prix_ticket", "description": "Prix d'un ticket en Or (0 = gratuit)", "required": False, "min_value": 0},
                {"type": 4, "name": "max_tickets", "description": "Tickets maximum par joueur (1 par défaut)", "required": False, "min_value": 1},
                {"type": 7, "name": "salon", "description": "Salon de l'annonce (le salon actuel par défaut)", "required": False},
            ],
        }],
    },
    {
        "name": "utiliser", "description": "Utiliser un objet de votre inventaire",
        "options": [{"type": 3, "name": "objet", "description": "Objet de votre inventaire", "required": True, "autocomplete": True}],
    },
    {
        "name": "echanger", "description": "Proposer un échange (objets, Or et/ou monnaies Rostheim) à un membre",
        "options": [
            {"type": 6, "name": "membre", "description": "Membre à qui proposer l'échange", "required": True},
            {"type": 3, "name": "donne_objet", "description": "Objet que vous donnez", "required": False, "autocomplete": True},
            {"type": 4, "name": "donne_quantite", "description": "Quantité donnée (1 par défaut)", "required": False, "min_value": 1},
            {"type": 4, "name": "donne_or", "description": "Or que vous donnez", "required": False, "min_value": 1},
            {"type": 3, "name": "recoit_objet", "description": "Objet que vous voulez recevoir", "required": False, "autocomplete": True},
            {"type": 4, "name": "recoit_quantite", "description": "Quantité reçue (1 par défaut)", "required": False, "min_value": 1},
            {"type": 4, "name": "recoit_or", "description": "Or que vous voulez recevoir", "required": False, "min_value": 1},
            {"type": 3, "name": "donne_monnaie", "description": "Monnaie Rostheim que vous donnez", "required": False, "autocomplete": True},
            {"type": 4, "name": "donne_montant", "description": "Montant de cette monnaie", "required": False, "min_value": 1},
            {"type": 3, "name": "recoit_monnaie", "description": "Monnaie Rostheim que vous voulez recevoir", "required": False, "autocomplete": True},
            {"type": 4, "name": "recoit_montant", "description": "Montant de cette monnaie", "required": False, "min_value": 1},
        ],
    },
]


def main():
    base = f"https://discord.com/api/v10/applications/{APP_ID}"
    url = f"{base}/guilds/{GUILD_ID}/commands" if GUILD_ID else f"{base}/commands"
    headers = {"Authorization": f"Bot {TOKEN}"}

    response = httpx.put(url, headers=headers, json=COMMANDS, timeout=30)
    if response.status_code >= 400:
        print(f"Erreur {response.status_code} de l'API Discord :")
        print(response.text)
        response.raise_for_status()
    scope = f"serveur {GUILD_ID}" if GUILD_ID else "global (propagation jusqu'à 1h)"
    print(f"{len(response.json())} commande(s) enregistrée(s) — portée : {scope}")


if __name__ == "__main__":
    main()
