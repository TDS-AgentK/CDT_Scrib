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

COMMANDS = [
    {
        "name": "fiche",
        "description": "Gérer les fiches",
        "options": [
            {
                "type": 1,  # SUB_COMMAND
                "name": "ajouter",
                "description": "Créer une nouvelle fiche",
                "options": [
                    {"type": 3, "name": "nom", "description": "Identifiant court (ex: regle-1)", "required": True}
                ],
            },
            {
                "type": 1,
                "name": "modifier",
                "description": "Modifier une fiche existante",
                "options": [
                    {"type": 3, "name": "nom", "description": "Identifiant de la fiche",
                     "required": True, "autocomplete": True}
                ],
            },
            {
                "type": 1,
                "name": "supprimer",
                "description": "Supprimer une fiche",
                "options": [
                    {"type": 3, "name": "nom", "description": "Identifiant de la fiche",
                     "required": True, "autocomplete": True}
                ],
            },
            {
                "type": 1,
                "name": "voir",
                "description": "Afficher une fiche",
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
    response.raise_for_status()
    scope = f"serveur {GUILD_ID}" if GUILD_ID else "global (propagation jusqu'à 1h)"
    print(f"{len(response.json())} commande(s) enregistrée(s) — portée : {scope}")


if __name__ == "__main__":
    main()
