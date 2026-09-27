# CDT_Scrib

Bot Discord qui stocke des fiches d'information (embeds) via des commandes slash, avec formulaire de saisie.

## Architecture : HTTP Interactions (pas de Gateway)

Contrairement à un bot Discord classique (connexion WebSocket permanente), ce bot répond aux commandes via un **endpoint HTTP** (`/interactions`) : Discord envoie une requête à chaque interaction, le serveur répond, point.

Avantage principal : le serveur peut être **mis en veille entre deux usages** (ex: mode "Serverless" de Railway) et se réveille sur la première requête entrante, réduisant fortement le coût d'hébergement. La contrepartie : pas de détection de texte libre dans les messages (type `cdt-nomdelafiche`) — tout passe par des commandes slash.

## Commandes

- `/fiche ajouter nom:<nom>` — ouvre un formulaire (titre, description, couleur, image, footer).
- `/fiche modifier nom:<nom>` — même formulaire, pré-rempli avec le contenu existant (autocomplétion du nom).
- `/fiche supprimer nom:<nom>`
- `/fiche voir nom:<nom>` — affiche la fiche.
- `/fiche liste` — liste toutes les fiches enregistrées.

Noms de fiches : minuscules, lettres/chiffres/`-`/`_` uniquement. Aucune limite de nombre de fiches (elles ne sont pas enregistrées comme des commandes Discord, contrairement à la limite de 100 commandes slash).

Les données sont persistées dans une base SQLite (`data/fiches.db` par défaut).

## Installation

1. Créer une application sur le [Discord Developer Portal](https://discord.com/developers/applications).
2. Onglet **Bot** : créer un bot, récupérer le token.
3. Onglet **General Information** : récupérer l'`Application ID` et la `Public Key`.
4. Copier la config :
   ```bash
   cp .env.example .env
   ```
   puis renseigner `DISCORD_TOKEN`, `DISCORD_APPLICATION_ID`, `DISCORD_PUBLIC_KEY` (et `GUILD_ID` en dev pour un enregistrement instantané des commandes).
5. Installer les dépendances :
   ```bash
   pip install -r requirements.txt
   ```
6. Enregistrer les commandes slash auprès de Discord (à refaire uniquement si la liste des commandes change) :
   ```bash
   python -m scripts.register_commands
   ```
7. Lancer le serveur en local :
   ```bash
   uvicorn app.main:app --reload
   ```
8. Exposer le serveur publiquement (ex: `ngrok http 8000` en dev) et renseigner l'URL + `/interactions` dans **Interactions Endpoint URL** sur le Developer Portal. Discord vérifie l'URL avec un PING avant de l'accepter.
9. Inviter le bot sur le serveur avec les scopes `bot` + `applications.commands` (permission `Send Messages` + `Embed Links` suffit, aucun intent Gateway n'est nécessaire).

## Déploiement sur Railway

1. Créer un projet à partir de ce dépôt.
2. Ajouter les variables d'environnement `DISCORD_TOKEN` (pour lancer `register_commands` une fois, ou en local), `DISCORD_APPLICATION_ID`, `DISCORD_PUBLIC_KEY`, `DATABASE_PATH` dans l'onglet **Variables** du service (jamais dans le code).
3. Attacher un **Volume** monté sur le dossier de `DATABASE_PATH` (ex: `/data`) pour que la base SQLite survive aux redéploiements et réveils.
4. Une fois déployé, récupérer l'URL publique du service, et la renseigner (+ `/interactions`) dans **Interactions Endpoint URL** sur le Developer Portal.
5. Activer le mode veille : Service → **Settings → Deploy → Serverless**. Le service s'endort après ~10 min sans trafic sortant et se réveille sur la requête suivante (léger délai de démarrage à froid).

## Structure

```
app/
  main.py            # endpoint FastAPI /interactions, vérification de signature, routage
  verify.py          # vérification de signature Ed25519 (obligatoire côté Discord)
  discord_types.py   # constantes de l'API Discord + validation des noms
  embeds.py          # construction des embeds et des modals (JSON brut, sans état)
  handlers.py        # logique métier de chaque commande / soumission de modal
  database.py        # couche SQLite (aiosqlite, connexion paresseuse)
scripts/
  register_commands.py  # enregistre les commandes slash auprès de Discord (à lancer une fois)
```

Le formulaire (modal) encode l'action et le nom de la fiche directement dans son `custom_id`
(ex: `fiche_edit:regle-1`) plutôt que de garder un état en mémoire : le serveur peut être
redémarré entre l'ouverture du formulaire et sa soumission (cold start) sans rien perdre.
