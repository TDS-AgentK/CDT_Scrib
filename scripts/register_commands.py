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
    }
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
