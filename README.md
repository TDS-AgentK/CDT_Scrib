# CDT_Scrib

Bot Discord qui stocke des fiches d'information (embeds) et les restitue à la demande via un préfixe texte (`cdt-nomdelafiche`).

## Fonctionnement

- **Consultation** : taper `cdt-nomdelafiche` dans un salon renvoie l'embed correspondant, s'il existe.
- **Gestion** (commandes slash) :
  - `/fiche ajouter nom:<nom>` — ouvre un formulaire (titre, description, couleur, image, footer).
  - `/fiche modifier nom:<nom>` — même formulaire, pré-rempli avec le contenu existant.
  - `/fiche supprimer nom:<nom>`
  - `/fiche voir nom:<nom>` — affiche la fiche sans passer par le préfixe.
  - `/fiche liste` — liste toutes les fiches enregistrées.

Les noms de fiches sont en minuscules, lettres/chiffres/`-`/`_` uniquement, et bénéficient de l'autocomplétion. Le nombre de fiches n'est pas limité (contrairement aux commandes slash, plafonnées à 100 par Discord) puisqu'elles ne sont pas enregistrées comme des commandes.

Les données sont persistées dans une base SQLite (`data/fiches.db` par défaut).

## Installation

1. Créer une application et un bot sur le [Discord Developer Portal](https://discord.com/developers/applications), récupérer le token, et activer l'intent **Message Content**.
2. Inviter le bot sur le serveur avec les scopes `bot` + `applications.commands` et la permission `Send Messages` (+ `Embed Links`).
3. Copier la config :
   ```bash
   cp .env.example .env
   ```
   puis renseigner `DISCORD_TOKEN` (et `GUILD_ID` en dev pour une synchronisation instantanée des commandes).
4. Installer les dépendances :
   ```bash
   pip install -r requirements.txt
   ```
5. Lancer le bot :
   ```bash
   python -m bot.main
   ```

## Structure

```
bot/
  main.py            # point d'entrée, connexion et synchronisation des commandes
  config.py          # chargement de la configuration (.env)
  database.py        # couche SQLite (aiosqlite)
  cogs/fiches.py      # commandes slash + modal de saisie + listener du préfixe cdt-
  utils/embed_builder.py
```
