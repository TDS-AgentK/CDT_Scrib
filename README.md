# CDT_Scrib

Bot Discord qui permet aux joueurs de créer et gérer eux-mêmes leurs fiches de personnage
(dans le style des embeds `cembed` utilisés sur les CDT), via des commandes slash et des
formulaires de saisie.

## Architecture : HTTP Interactions + Gateway (économie)

Contrairement à un bot Discord classique (connexion WebSocket permanente), ce bot répond aux commandes via un **endpoint HTTP** (`/interactions`) : Discord envoie une requête à chaque interaction, le serveur répond, point.

Les commandes `/fiche` passent par cet endpoint. L'**économie** (XP et Or par message, niveaux, boutiques, inventaire)
a besoin de lire les messages : le même processus ouvre donc aussi une connexion **Gateway** permanente
(discord.py), démarrée seulement si `DISCORD_TOKEN` et `PB_URL` sont définis. Le service ne doit **pas** être
mis en veille (pas de mode Serverless sur Railway).

## Économie (base PocketBase du site)

Tous les réglages viennent de la page « Économie du bot » du site des Chroniques du Temps (collections `eco_*`
de PocketBase), relus toutes les minutes : salons éligibles et boosters, XP min/max par message, rôles boosters
et rôle exclu, multiplicateurs temporaires, courbe de niveau (exponentielle Draftbot par défaut), récompenses de
niveau (Or, rôles, rôles temporaires retirés à échéance, objets, succès), boutiques (dont boutiques cachées
réservées à un rôle), articles, inventaires. Les joueurs sont reconnus par leur ID Discord (fiche `joueurs`),
ou à défaut par leur pseudo Discord (l'ID est alors enregistré automatiquement).

Commandes (préfixe `ECO_PREFIX`, `??` par défaut) : `??niveau [@membre]`, `??classement`, `??inventaire`,
`??boutique`, `??acheter <numéro ou nom>`.

Commandes slash de l'économie (à enregistrer avec `python -m scripts.register_commands`), dans l'esprit de Draftbot :
`/boutique` (Components V2 : choix de la boutique, articles paginés avec bouton « prix - Acheter », tri, fenêtre de
quantité), `/argent` et `/niveau` (cartes image), `/topargent`, `/topniveau` (paginés), `/inventaire` (grille),
`/payer` (Or, ou monnaie Rostheim si les échanges sont activés), `/donner`, `/vendre` (prix de revente de l'objet),
`/utiliser` (objets utilisables), `/echanger` (proposition acceptée ou refusée par bouton). Menus, boutons et
fenêtres (custom_id `eco:…`) arrivent par `/interactions` (`app/eco_interactions.py`).

Sessions de jeu des fiches du site (`app/sessions_jeu.py`, collection `sessions_jeu`), pour tout le monde :
`/session ouvrir [mj] [event]` (vous êtes MJ par défaut ; refusé si une session est déjà ouverte),
`/session rejoindre perso` (une fiche de jeu de vos personnages), `/session voir`, `/session clore` (MJ, personne qui
l'a ouverte ou administrateur du serveur). Les jets lancés depuis le site pendant la session y sont rattachés.

Combat des fiches de jeu (`app/combat.py`, collection `combats`) : `/attaque perso type cible` (listes lues dans la
base : vos fiches, leurs armes et sorts, les cibles ; ⚔ = à la table de la session). Comme Rostheim, tout passe par la
base, sans variable à poser : le bot dépose la demande (`/attaque`, Accepter/Refuser, votes d'égalité), le site
l'arbitre (jets d'attaque et de CA avec les états, égalité par vote puis 1d2, dégâts retirés des PV), puis le bot
affiche le défi et met à jour le même message à chaque étape, y compris pour les combats lancés depuis le site.

Événement Rostheim et commande RP (définis dans la base : `ros_commandes`, `ros_domaines`, `ros_paliers`,
`ros_recompenses`, `eco_commande_rp`) : commandes de gain (`??texte`, `??lore`…) avec rôle requis, salon autorisé et
limites ; conversions `??gold-<monnaie>` / `??xp-<monnaie>` ; boutiques de domaine `??dépenser-<monnaie> [numéro]` ;
`??boîte-à-rôle [numéro]` ; jauges collective et individuelle, paliers (palier marqué atteint au passage ; l'embed complet du palier — titre, citation, champs, image, auteur du domaine —
est posté quand K clique « Envoyer dans le salon » dans l'éditeur du site : demande dans `ros_envois`, traitée par le bot,
sous le nom et l'avatar des réglages Rostheim, renvoyable à volonté) ;
`??rp @partenaires…` (Or, XP et rôle, message dans le salon configuré). Chaque gain est tracé dans `eco_gains`.
Règle des boosters : entre plusieurs rôles, seul le plus fort compte ; il se multiplie avec le booster du salon
et les multiplicateurs temporaires.

Prérequis côté Discord (Developer Portal → Bot) : activer **Message Content Intent** et **Server Members Intent** ;
le bot doit avoir les permissions **Gérer les rôles** et **Gérer les messages** (suppression du message `--wut`) et son rôle doit être placé au-dessus des rôles qu'il distribue.

## Commandes

- `/fiche creer nom:<identifiant>` — crée une fiche vide, dont vous devenez propriétaire.
- `/fiche modifier nom:<identifiant> section:<architecture|identite|physique|apparence>` —
  ouvre le formulaire de la section choisie, pré-rempli avec le contenu existant.
  Un modal Discord étant limité à 5 champs, la fiche est découpée en 4 sections à remplir
  indépendamment, dans l'ordre voulu, petit à petit.
- `/fiche image nom:<identifiant> cible:<personnage|equipement> fichier:<pièce jointe>` —
  envoie directement une image depuis Discord (pas de lien à copier/héberger soi-même).
  Le bot la télécharge, l'héberge lui-même sur son propre volume, et met à jour le champ
  correspondant (`image_personnage_url` ou `equipement_image_url`) avec une URL stable.
  PNG/JPEG/WEBP/GIF, 8 Mo max.
- `/fiche supprimer nom:<identifiant>`
- `/fiche voir nom:<identifiant>` — affiche la fiche (et un second message avec les
  illustrations d'équipement, si un lien a été renseigné).
- `/fiche liste` — liste toutes les fiches enregistrées.

Seul le créateur d'une fiche peut la modifier ou la supprimer (`/fiche modifier` et
`/fiche supprimer` ne proposent en autocomplétion que ses propres fiches ; `/fiche voir`
propose toutes les fiches existantes).

### Champs par section

- **architecture** : couleur (code hexadécimal RGB, ex: `330033`, avec ou sans `#` — n'importe quel sélecteur de couleur en ligne convient),
  lien de l'image du personnage, nom de la skin (ex: `Sei.png`), lien vers la fiche CDT,
  lien de l'image des équipements (optionnel — déclenche le second message). Les deux liens
  d'image peuvent aussi être renseignés directement via `/fiche image` (pièce jointe), sans
  avoir à les héberger ailleurs au préalable.
- **identite** : nom, surnom, genre, race.
- **physique** : taille, poids, tranche d'âge physique, morphologie, couleur des yeux.
- **apparence** : couleur et longueur des cheveux, coiffure, tenue, armement & équipement.

Le titre affiché en haut de la fiche est toujours le nom du personnage ; le surnom apparaît
juste en-dessous (à la place d'un "..." par défaut si aucun surnom n'est renseigné), et le
footer combine les deux ("Nom, Surnom").

Tous les champs sont optionnels et peuvent être complétés ou vidés à tout moment : une fiche
partielle s'affiche simplement sans les champs manquants. Identifiants de fiche : minuscules,
lettres/chiffres/`-`/`_` uniquement. Aucune limite de nombre de fiches (elles ne sont pas
enregistrées comme des commandes Discord, contrairement à la limite de 100 commandes slash).

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
3. Attacher un **Volume** monté sur le dossier de `DATABASE_PATH` (ex: `/data`) pour que la base SQLite *et* les images envoyées via `/fiche image` (stockées par défaut dans `<dossier de DATABASE_PATH>/uploads`) survivent aux redéploiements et réveils.
4. Une fois déployé, récupérer l'URL publique du service, et la renseigner (+ `/interactions`) dans **Interactions Endpoint URL** sur le Developer Portal.
5. Ne **pas** activer le mode Serverless (veille) : la connexion Gateway de l'économie doit rester ouverte.
6. Pour l'économie, ajouter `DISCORD_TOKEN`, `PB_URL`, `PB_EMAIL`, `PB_PASSWORD` (compte superuser dédié au bot) et,
   pendant les tests, `ECO_GUILD_ID` (serveur de test) pour que le bot ne réagisse que là.

## Structure

```
app/
  main.py            # endpoint FastAPI /interactions, vérification de signature, routage
  verify.py          # vérification de signature Ed25519 (obligatoire côté Discord)
  discord_types.py   # constantes de l'API Discord + validation des noms
  embeds.py          # construction des embeds et des modals (JSON brut, sans état)
  handlers.py        # logique métier de chaque commande / soumission de modal
  database.py        # couche SQLite (aiosqlite, connexion paresseuse)
  economie.py        # économie (Gateway) : gains par message, niveaux, récompenses, commandes ??
  rostheim.py        # événement Rostheim et commande RP
  eco_vues.py        # embeds et composants (menus, boutons) de l'économie
  eco_interactions.py # commandes slash, clics, fenêtres et autocomplétion de l'économie
  eco_actions.py     # payer, donner, vendre, utiliser, échanger
  eco_cartes.py      # cartes image /argent et /niveau (Pillow)
  pocketbase.py      # accès à la base PocketBase du site
scripts/
  register_commands.py  # enregistre les commandes slash auprès de Discord (à lancer une fois)
```

Le formulaire (modal) encode l'action et le nom de la fiche directement dans son `custom_id`
(ex: `fiche_edit:regle-1`) plutôt que de garder un état en mémoire : le serveur peut être
redémarré entre l'ouverture du formulaire et sa soumission (cold start) sans rien perdre.
