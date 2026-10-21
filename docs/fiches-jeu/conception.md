# Fiches de jeu des CDT : dés sur Discord, fiches sur le site

Source : Google Sheet « Event MJ - Joueurs », lu sans le modifier. L'export avec les formules est dans [Event MJ - Joueurs.xlsx](Event%20MJ%20-%20Joueurs.xlsx).
Maquettes : https://claude.ai/artifact/TsD9kBnjrNgPFRuE1hYo7z

## Décisions de K (8 octobre)

- **On édite uniquement sur le site.** Discord ne sert qu'à afficher les fiches et à lancer les dés.
- **Un petit dé dans le bandeau du site**, visible seulement des joueurs connectés :
  - dé doré (`https://cdn.discordapp.com/emojis/875447976900841473.webp`) quand le joueur a une fiche ;
  - dé gris (`https://cdn.discordapp.com/emojis/875447978226245652.webp`) quand il n'en a pas, avec la proposition d'en créer une.
- **Le dé ouvre la fiche**, inspirée de l'Excel et habillée au design CDT. Elle s'affiche en lecture seule ou en création/modification.
- **Agent K peut modifier les règles de calcul** depuis l'admin.
- **Relancer une session** : remet les PV au maximum et recharge les sorts.
- **Pas de verdict automatique, sauf en combat** : hors combat, le bot affiche le résultat brut et le MJ décide en direct pendant l'event. Le combat a ses propres règles, décrites dans la section « Combat (/attaque) ».
- **Rappel des degrés de difficulté de magie** : envoie sur Discord tous les DD d'un personnage.
- **Comptes** : les comptes du site sont déjà reliés à Discord.
- **Une fiche par personnage** : un joueur peut en avoir plusieurs.

## Création d'une fiche (décision de K)

- **Le bouton apparaît sur la page d'un personnage de la galerie**, et seulement pour le joueur connecté qui possède ce personnage :
  - un dé doré si la fiche de jeu existe ; un clic l'ouvre ;
  - un dé gris sinon ; un clic ouvre une fenêtre qui demande de confirmer la création.
- **Pas de liste de personnages** : on agit toujours sur la fiche visitée.
- **Pas d'étapes imposées** : la fiche s'ouvre directement en modification dans le Grimoire, et le logiciel calcule tout ce qu'il peut au fur et à mesure.
- **Prérequis d'équipement** : la comparaison est au sens large (≥ ou ≤), et non plus strict.
- **Les artefacts de la fiche se répercutent dans la liste des Artefacts du site.**

## Mises en page retenues (8 octobre)

- **Site** : la mise en page 1, Grimoire ouvert.
- **Discord** :
  - la fiche s'affiche en A1 : fiche complète, boutons et menu des compétences ;
  - les jets de compétence et de sort s'affichent en B1 :
    - **sans phrase d'ambiance** ;
    - on garde le bouton « Lancer les dégâts » ;
    - le rendu est le même, que le jet parte du site ou de Discord ;
  - le Rappel des DD s'affiche en C1 ;
  - Relancer une session existe en deux fonctionnalités distinctes, selon qui la lance :
    - D1, un personnage, lancé par le joueur pour sa fiche ;
    - D2, toute la table, lancé par le MJ.

## Le site

### Pour les joueurs connectés

- **Le dé dans le bandeau** (doré ou gris, voir plus haut) ouvre la fiche, ou propose d'en créer une.
- **En lecture seule**, chaque valeur lançable porte un bouton de dé. Un clic lance le jet côté serveur, et le résultat s'affiche sur le site et part dans le salon Discord avec la mention « lancé depuis le site ».
- **En création ou modification**, on retrouve la saisie de l'Excel avec ses contrôles :
  - 300 points à répartir, entre 30 et 70 par caractéristique ;
  - menus pour la race, le métier, la classe, l'armure, le bouclier, les 4 armes, l'énergie, l'école, le courant et le niveau de magie ;
  - 3 spécialisations au maximum ;
  - le prérequis d'armure est affiché ;
  - 3 artefacts.
- **Tous les dérivés se recalculent en direct** : totaux, modificateurs, compétences, CA, PV, attaques, dégâts, sorts, DD, sauvegardes.
- **Suivi pendant l'event** : dégâts reçus, PV restants, sorts restants pour chaque tier.
- **Deux actions sur la fiche** : « Relancer une session » et « Rappel des DD de magie ».

### Pour Agent K, dans /gestion

- **Les tables de règles sont éditables** : races (avec avantages et handicaps), métiers, classes (bonus, multiplicateurs, PV, CA par type d'armure), armures, armes, magie (énergies, écoles, courants, niveaux), types d'artefacts, table des modificateurs.
- **Les constantes des formules sont éditables** (voir « Règles de calcul » plus bas) : 6/10/20, 1,1/1,2/1,25, +15 de spécialisation, 300 points, 30 à 70, etc.
- **Toutes les fiches sont accessibles** : lecture, correction, relance de session pour une fiche ou pour toute la table.

### Quatre mises en page proposées (voir les maquettes)

1. **Grimoire ouvert** : deux pages. À gauche, les caractéristiques en médaillons, la défense et les sauvegardes. À droite, les compétences, l'armement et la magie.
2. **Planche** : inspirée de la fiche jointe par K. Portrait au centre, médaillons en arc, compétences à gauche, sauvegardes, artefacts et traits à droite, attaques et incantations sous le portrait.
3. **Codex à onglets** : en-tête avec portrait et barre de PV, puis des onglets Caractéristiques, Compétences, Combat, Magie et Artefacts. C'est la plus lisible sur mobile.
4. **Table de jeu** : chaque jet est une grande tuile cliquable, regroupée par Combat, Magie, Sauvegardes et Compétences. Pensée pour jouer pendant un event, sans rien lire d'autre.

## Discord (CDT_Scrib)

Aucune commande de modification sur Discord.

- `/perso voir nom:` affiche la fiche.
  - Variante A1 : fiche complète avec des boutons de jet et un menu des compétences.
  - Variante A2 : carte compacte avec 4 boutons.
- `/jet perso: action:` fait la même chose qu'un bouton. `/jet libre:2d6+3` lance un jet libre.
- **Résultats**, plusieurs rendus au choix :
  - B1 : embed doré, avec « Les dés roulent… » puis le résultat ;
  - B2 : une seule ligne ;
  - B3 : parchemin en bloc ANSI ;
  - B4 : jet lancé depuis le site.
- **Un jet de sort décompte automatiquement un lancer**, et un bouton « Lancer les dégâts » apparaît sous le résultat.
- `/perso dd nom:` donne le rappel des DD de magie.
  - Variante C1 : un personnage.
  - Variante C2 : toute la table, pour le MJ.
- `/perso relancer nom:` relance une session.
  - Variante D1 : un personnage.
  - Variante D2 : toute la table.

## Combat (/attaque)

C'est le seul cas où le bot tranche lui-même. L'attaquant lance toujours le combat.

**1. Déclaration**
- Commande : `/attaque type:<type d'attaque> cible:<personnage attaqué> hasard:<objet de hasard>`.
  - Le type d'attaque désigne une des armes de la fiche, ou un sort. Pour un sort, on prend son jet d'attaque ; son DD ne compte pas.
  - L'objet de hasard est facultatif : brise-garde, verre à moitié vide, verre à moitié plein, etc.
- Le bot poste le défi en mentionnant le joueur qui possède la cible.

**2. Acceptation**
- Seul le propriétaire de la cible peut répondre.
- Il ouvre un menu déroulant qui ne propose que les objets de son inventaire, en choisit éventuellement un, puis clique sur « Accepter » ou « Refuser ».
- Un refus annule le combat, et aucun objet n'est consommé.

**3. Jets**
- Le bot lance en même temps le jet d'attaque de l'attaquant et le jet de CA du défenseur.

**4. Résolution**

| Cas | Résultat affiché |
|---|---|
| ATK > DEF | Le bot lance les dégâts de l'attaquant : « L'attaque surpasse la défense. *x* points de dégâts. » |
| ATK < DEF | « L'attaque ne surpasse pas la défense. » Aucune phrase RP. |
| ATK = DEF | Deux boutons pour chaque joueur : « Avantage attaquant » ou « Avantage défenseur ». Si les deux choisissent la même chose, ce choix s'applique. En cas de désaccord, le bot lance 1d2 : 1, l'attaque passe ; 2, l'attaque échoue. |

**Maximums naturels** (le dé tombe sur sa face maximale, par exemple 29 sur `1d29+5`) :
- **attaque au maximum** : l'attaque passe et inflige automatiquement les **dégâts maximum** de l'arme ;
- **défense au maximum** : l'attaque échoue systématiquement ;
- **les deux au maximum** : on applique la règle ATK = DEF.

**Par défaut, en attendant la réponse de K** :
- les dégâts infligés sont retirés automatiquement des PV de la cible sur sa fiche ;
- un défi non accepté expire au bout de 10 minutes.

## Objets de hasard

### Où sont rangés les objets

- **Chaque personnage a un « sac de hasard »** sur sa fiche, avec la quantité de chaque objet. Le bot n'avait aucun inventaire de ce type : il est créé pour l'occasion.
- **Chaque objet est consommé** quand on l'utilise.
- **Le MJ de la session peut en donner**, et Agent K aussi, depuis /gestion. Monter de niveau en rapporte aussi (voir la section XP).

### Les huit objets

| Objet | Qui l'utilise | Quand | Effet |
|---|---|---|---|
| 🍀 Trèfle à 10 feuilles | Le lanceur, sur son propre jet | Avant le jet | +10 au total du jet |
| 🥛 Verre à moitié vide | Le défenseur | En acceptant l'attaque | Dégâts reçus divisés par 2 (arrondi à l'inférieur) |
| 🍷 Verre à moitié plein | L'attaquant | Dans `/attaque` | Dégâts multipliés par 1,5 (arrondi à l'inférieur) |
| 🛡️💥 Brise-garde | L'attaquant | Dans `/attaque` | Ignore la garde : le défenseur ne lance pas sa CA et l'attaque passe directement aux dégâts (confirmé par K). Le défenseur garde la possibilité de refuser l'attaque. |
| ⭐ Réussite critique | Le lanceur, sur son propre jet, en toute situation | Avant le jet | Le dé donne sa face maximale, et compte comme un maximum naturel (dégâts maximum en combat) |
| 💀 Échec critique | Un autre joueur, sur le jet de quelqu'un d'autre, en toute situation | **Après** le jet, par un bouton sous le résultat | Le dé ciblé passe à sa face minimale |
| ⚖️ Ça ne compte que pour un | Le lanceur | Avant tout jet | Lance deux dés et garde le meilleur |
| 🔁 Try again | Le lanceur | Après le jet | Relance son dernier jet, et le nouveau résultat remplace l'ancien |

### Règles d'usage proposées

- **Hors combat**, sur un bouton de jet (site ou Discord) :
  - un menu « Utiliser un objet » propose les objets qui se jouent avant le jet ;
  - un bouton « 🔁 Try again » apparaît sous le résultat si le personnage en possède un.
- **En combat** :
  - l'attaquant choisit son objet dans `/attaque` ;
  - le défenseur choisit le sien dans le menu, en acceptant l'attaque ;
  - après la résolution, chacun des deux peut encore utiliser Try again sur son propre jet, et le bot recalcule l'issue.
- **Échec critique** (décidé par K : en toute situation, et par un bouton sous le jet) :
  - sous chaque résultat de jet, un bouton « 💀 Échec critique » est cliquable par **les autres joueurs** de la session qui en ont un, jamais par le lanceur ;
  - un clic fait passer le dé à sa face minimale, et le message du jet est mis à jour avec la mention de qui l'a joué ;
  - le bouton reste actif **30 secondes**, pas plus ;
  - **un jet frappé par un Échec critique ne peut pas être relancé** : pas de Try again ;
  - si le jet avait utilisé une Réussite critique, les deux objets s'annulent et le dé est relancé normalement ;
  - en combat, chacun peut le jouer sur le jet de l'adversaire après la résolution, et le bot recalcule l'issue (confirmé par K).
- **Cumuls** :
  - **un seul objet par joueur avant chaque jet**, plus un Try again après ;
  - une Réussite critique et un Échec critique sur le même dé s'annulent, et le dé est relancé normalement ;
  - les deux Verres se cumulent : ×1,5 puis ÷2 ;
  - le Verre à moitié vide reste utilisable contre un Brise-garde.
- **Traçabilité** : chaque usage est inscrit dans l'historique et compte dans les statistiques.

## Sessions, MJ et XP (proposition)

### La session

- **Ouverture** : `/session ouvrir mj:@joueur`, ou bouton sur le site.
  - Le MJ est l'un des quatre joueurs. Il « dirige » la session et **ne joue pas de personnage** pendant celle-ci.
  - Agent K peut ouvrir une session et désigner n'importe qui.
- **La table** : les personnages qui rejoignent la session, avec `/session rejoindre perso:` ou automatiquement à leur premier jet.
- **Les pouvoirs du MJ, pendant sa session seulement** :
  - relancer toute la table (D2) ;
  - donner de l'XP et des objets ;
  - corriger des PV ;
  - trancher une égalité de combat bloquée.
- **Clôture** : `/session clore`, qui ouvre la distribution d'XP.
- **Lien avec les statistiques** : chaque jet est rattaché à sa session, ce qui donne des statistiques par session.

### L'XP

- **C'est une XP de personnage**, distincte de l'XP Dranig du Discord (économie) : chaque fiche a la sienne.
- **Les gains combinent les deux** (décidé par K) : objets de hasard **et** paliers qui améliorent le personnage.

- **Attribuée par le MJ à la clôture**, sur le site (formulaire qui liste la table) ou sur Discord (`/xp donner`). Il dispose de raccourcis :
  - présence : +10 ;
  - belle scène RP : +5 à +20 ;
  - objectif atteint : +15 ;
  - combat remporté : +5 ;
  - montant libre, avec un motif.
- **Le MJ gagne lui aussi de l'XP** pour avoir dirigé la session (+15 par défaut), versée au personnage de son choix. Cela encourage la rotation des MJ.
- **Les montants et les raccourcis sont réglables** par Agent K dans /gestion.

### Ce que l'XP apporte, en paliers

Valeurs par défaut, toutes réglables :

| Niveau | XP cumulée | Gain |
|---|---|---|
| 2 | 50 | 1 objet de hasard tiré au sort |
| 3 | 120 | +5 points de caractéristique à répartir |
| 4 | 200 | 1 objet de hasard + le maximum par caractéristique passe de 70 à 75 |
| 5 | 300 | 4e spécialisation |
| 6 | 420 | +5 points de caractéristique |
| 7 | 560 | Le niveau de magie monte d'un cran (Novice → Amateur → … → Maître) |
| 8 | 720 | 1 objet de hasard + 4e emplacement d'artefact |
| 9 | 900 | +5 points de caractéristique |
| 10 | 1100 | Titre « Légende » sur la fiche + 2 objets de hasard |


### Comment ça se voit

- **Sur le site** :
  - une barre d'XP et un badge de niveau sous le nom, dans le Grimoire ;
  - un journal d'XP sur la fiche : qui a donné combien, quand, pourquoi ;
  - une fenêtre « Niveau supérieur » pour dépenser les gains ;
  - un bandeau « Session dirigée par Kyanite » visible de tous les joueurs connectés pendant une session ;
  - un panneau MJ (table, Relancer la table, Donner XP ou objet, Clore) affiché seulement au MJ de la session.
- **Sur Discord** :
  - un message d'ouverture de session qui annonce le MJ et la table ;
  - un récapitulatif d'XP à la clôture ;
  - une annonce de montée de niveau.

## Historique et statistiques des jets

**Tous les jets sont conservés**, qu'ils partent du site ou de Discord.

Pour chaque jet, on enregistre :
- **qui** : le personnage et le joueur ;
- **quand** : la date et l'heure, la session et l'event ;
- **quoi** : le type de jet (compétence, sauvegarde, CA, attaque d'arme, dégâts, sort par tier, jet libre) et la formule ;
- **le tirage** : chaque dé, le bonus et le total ;
- **la face** : maximum naturel ou face minimale ;
- **d'où** : site ou Discord ;
- **pour un combat** : l'adversaire, l'issue (passe, échoue, égalité et comment elle a été tranchée), les dégâts infligés et les objets utilisés.

**Le résultat est un onglet « Statistiques » sur la fiche du site**, avec un filtre par période, par session ou event, et par type de jet. On y trouve :
- **sur tous les jets** :
  - le nombre de jets, la moyenne, la médiane, l'écart-type, le minimum et le maximum ;
  - la moyenne obtenue comparée à la moyenne théorique du dé, qui forme un indice de chance (+3,2 au-dessus de l'attendu, par exemple) ;
  - le nombre de maximums naturels et de faces minimales ;
- **pour les compétences** : la meilleure, la pire et la plus utilisée ;
- **en combat** :
  - les attaques lancées et le nombre réussi, en pourcentage, ainsi que les défenses réussies ;
  - les égalités et la façon dont elles ont été tranchées ;
  - les dégâts infligés et subis, au total et en moyenne, ainsi que le plus gros coup ;
  - l'adversaire le plus affronté, avec le bilan contre lui ;
  - les objets les plus utilisés ;
- **pour la magie** : les lancers par tier, la moyenne des jets d'attaque comparée au DD, et les dégâts moyens ;
- **en graphiques** :
  - la répartition des résultats ;
  - l'évolution de la moyenne dans le temps ;
  - une « série chaude » (meilleure suite de jets au-dessus de la moyenne) ;
  - la liste des derniers jets ;
- **côté MJ (/gestion)** : un classement des personnages (les plus chanceux, le plus de dégâts, le meilleur taux de réussite) et des statistiques par event.

## Architecture

Ces choix remplacent la première idée, où la base du bot était la source de vérité.

- **Le site et sa base PocketBase sont la source de vérité.** Les fiches, les règles, l'historique des jets, les sessions, l'XP et les sacs de hasard y vivent, puisque c'est là qu'on édite et que les comptes y sont déjà reliés à Discord.
- **Le bot CDT_Scrib lit et écrit par l'API de PocketBase.** Il gère les commandes et les boutons Discord, et enregistre chaque jet dans le même historique.
- **Un seul moteur de calcul**, `regles/moteur.mjs`, en JavaScript pur et sans dépendance, utilisé par le site. Le bot lit les valeurs déjà calculées, ou utilise un portage vérifié avec les mêmes tests.
- **Les règles de l'Excel sont exportées en JSON** dans `regles/regles-cdt.json`. Agent K pourra ensuite les modifier dans /gestion.

### Notes de la première version

- **Une seule source de vérité.** Les fiches et les tables de règles vivent à un seul endroit, et un seul moteur de calcul est partagé.
- **Le site appelle le bot** pour lancer un jet ou poster un message. Le bot fait le tirage, ce qui évite qu'un jet puisse être fabriqué côté navigateur.
- **Lien entre compte du site et compte Discord.** Il faut savoir quel compte Discord correspond à quel joueur du site (voir les questions).
- **Ce chantier reste à part dans le bot**, dans de nouveaux fichiers, pour ne pas croiser le chantier économie (PR 14 et suivantes).

## Règles de calcul (relevées dans les formules de « Base V3.3 »)

Notations : `mod(X)` est le modificateur de X dans la table de paliers (DATA_Stuff A39:B240). Classe, Race et Métier désignent les lignes de DATA_Reste. `art` désigne la somme des bonus des 3 artefacts.

- **Modificateur** : par paliers de 10. De 50 à 59 il vaut 0, de 60 à 69 +5, de 70 à 79 +10, et ainsi de suite jusqu'à +25 à partir de 100 et −25 en dessous de 10.
  - C'est la table de DATA_Stuff qui sert. Celle de DATA_Reste, aux paliers décalés de 5, n'est utilisée nulle part.
- **Caractéristique finale** = répartition + Race + Classe + art.
  - Pour la Dextérité, on ajoute aussi le malus de Dextérité de l'armure et ceux des armes. Les fiches joueurs le font correctement : seul l'onglet modèle « Base V3.3 » avait l'erreur.
- **Compétence** = Race + Métier + Classe + mod(caractéristique liée) + art, avec +15 si la compétence est une spécialisation (3 au maximum).
  - Caractéristique liée à chaque compétence :
    - Dextérité : Acrobaties, Escamotage, Discrétion ;
    - Force : Athlétisme ;
    - Intelligence : Arcanes, Histoire, Investigation, Nature, Religion ;
    - Sagesse : Dressage, Médecine, Perception, Perspicacité, Survie ;
    - Charisme : Tromperie, Intimidation, Représentation, Persuasion.
- **Sauvegardes** :
  - Réflexe = `1d100 + mod(Dex) + art` ;
  - Vigueur = `1d100 + mod(Con) + art` ;
  - Volonté = `1d100 + mod(Sag) + art`.
- **CA de l'armure** = arrondi(CA de l'armure + bouclier × coefficient « Malus CA » de la classe). Le bouclier vaut 2 et le coefficient est de 0,5 pour toutes les classes.
- **Jet de CA** = `1d` arrondi(5 + CA + (mod lié au type d'armure / 2) × coefficient de la classe pour ce type d'armure + art). Le coefficient ne multiplie que la part du modificateur.
  - Mod lié au type d'armure : Intelligence pour une armure de mage, Dextérité pour une légère, (Force + Dextérité) pour une intermédiaire, Constitution pour une lourde.
- **PV** = PV de la classe + 12 + mod(Con) si ce modificateur est positif − dégâts reçus.
- **Arme**, avec `P` = ent(dégâts de l'arme × multiplicateur de dégâts de la classe). Le multiplicateur est celui des armes de jet pour « De Jet », celui des armes sinon.
  - Dégâts = `1d` ent(P × multiplicateur) + bonus de dégâts de l'artefact posé sur cet emplacement. Le multiplicateur est appliqué une seconde fois, et pour une arme légère c'est celui des armes de jet (voir anomalie 4).
  - Attaque = `1d`(mod(Dex) + P pour une arme légère ou de jet, sinon mod(For) + P) + bonus d'attaque de l'artefact + arrondi(P/2) si l'arme est maîtrisée.
- **Magie**, avec `I` = mod(Int), `A` = Arcanes, `M` = modificateur de maîtrise du niveau (Novice 1 … Maître 5) et `V` = valeur du niveau (2, 5, 7, 10, 15) :

| Tier | Jet | DD | Dégâts | Lancers par niveau |
|---|---|---|---|---|
| Mineur | `1d`(arr(I×(A/200+1)) + M + 6) | > arr((10+I+V)×1,1) | `2d` arr((3 + I×(A/400+1))/4) | ∞ |
| Médian | `1d`(arr(I×(A/100+1)) + M + 10) | > arr((20+I+V)×1,2) | `3d` arr((5 + I×(A/100+0,6))/3) | 1, 3, 5, 8, 10 |
| Majeur | `1d`(arr(I×(A/400+1)) + M + 20) | > arr((30+I+V)×1,25) | `5d` arr((10 + I×(A/400+1))/3) | 0, 0, 1, 1, 1 |

  Avec le courant Guérison, les intitulés deviennent « Jet de soin » et « PV soignés ». Chaque tier reçoit aussi les bonus des artefacts.
- **Vérification** : le moteur (`regles/moteur.mjs` dans la branche `fiches-jeu` de CDT_Scrib) recalcule les 12 fiches de l'Excel. Les 582 valeurs comparées sont identiques : caractéristiques, modificateurs, compétences, CA, PV, armes, sauvegardes et sorts.

## Anomalies trouvées dans l'Excel (à trancher)

1. **« Arme longue aérienne »** : sa valeur d'Attaque est une date (1 janvier 2022), sans doute un « 1.1 » mal saisi. Toute la colonne « Attaque » des armes (2 / 1,1 / 1) n'est d'ailleurs utilisée par aucune formule.
2. ~~**Malus de Dextérité des armes**~~ : l'erreur n'existe que dans l'onglet modèle « Base V3.3 ». Les 12 fiches joueurs ont la bonne formule, ainsi que le bon bonus d'artefact sur Représentation. Le moteur suit les fiches joueurs.
3. **Prérequis d'armure** : il est affiché, mais rien n'empêche de porter une armure sans le remplir. Décision de K : la comparaison devient ≥ (et ≤ pour une borne haute) au lieu de strictement supérieur.
4. **Dégâts d'arme** : le multiplicateur de classe est appliqué deux fois. Exemple : un Guerrier avec une Arme tranchante 1M fait 7 × 1,5 = 10, puis 10 × 1,5 = `1d15`. De plus, une arme légère prend le multiplicateur des armes de jet pour ses dégâts, mais celui des armes pour son attaque.

Par défaut, je reproduis l'Excel tel quel, anomalies comprises, pour que les fiches existantes ne bougent pas. Chaque correction sera un réglage que K pourra activer dans /gestion.

## Questions pour K (mises à jour le 8 octobre)

Toujours ouvertes :

1. **Le MJ** : le MJ de la session peut-il aussi jouer un personnage ? Par défaut, non.
2. **Dégâts de combat** : faut-il les retirer automatiquement des PV de la cible ? Par défaut, oui.
3. **Défi non accepté** : le défi expire-t-il ? Par défaut, au bout de 10 minutes.
4. **Dégâts maximum d'un sort** : ils s'appliquent aussi quand l'attaque passe par un sort en maximum naturel ? Par défaut, oui (par exemple 5×9 = 45 pour un `5d9`).
5. **Validation** : une fiche créée est-elle jouable tout de suite, ou faut-il qu'Agent K la valide ?
6. **Visibilité** : un joueur voit-il les fiches des autres en lecture seule ?
7. **Salon Discord** : dans quel salon partent les jets lancés depuis le site ?
8. **Import** : faut-il reprendre les fiches de l'Excel ?
9. **Anomalies 1 à 4** : on garde le comportement de l'Excel, ou on corrige ?

Réponses déjà données : Brise-garde sans jet de CA, avec possibilité de refuser ; critiques utilisables en toute situation ; XP de personnage avec objets et paliers ; les comptes sont reliés ; une fiche par personnage ; les mises en page sont choisies ; Relancer une session existe en D1 pour le joueur et en D2 pour le MJ.

### Questions initiales (archive)

1. **Comptes** : les joueurs se connectent au site comment, et comment relie-t-on leur compte Discord ? Je propose que chacun renseigne son identifiant Discord dans son profil, ou qu'on passe par « Se connecter avec Discord ».
2. **Fiches par joueur** : une seule fiche de jeu par joueur, ou une par personnage, voire une par event ? Le dé du bandeau ouvre alors laquelle ?
3. **Validation** : une fiche créée par un joueur est-elle jouable tout de suite, ou faut-il qu'Agent K la valide, puis la verrouille ?
4. **Visibilité** : un joueur connecté voit-il les fiches des autres en lecture seule, ou seulement la sienne ? Agent K voit tout.
5. **Relancer une session** : réservé au MJ, ou chaque joueur peut le faire ? Pour une fiche à la fois, ou toute la table d'un coup ?
6. **Dégâts reçus et lancers de sorts** : qui les met à jour, le joueur, le MJ ou les deux ? Le décompte automatique d'un lancer à chaque jet de sort te convient-il ?
7. **Salon Discord** : un salon « dés » fixe pour les jets lancés depuis le site, ou le salon de l'event en cours ? Le rappel des DD doit-il être public ou visible seulement de celui qui le demande ?
8. **Mise en page** : quelle mise en page pour le site (1 à 4), et quels rendus Discord (A1/A2, B1 à B4, C1/C2, D1/D2) ? On peut aussi mélanger les variantes.
9. **Import** : faut-il reprendre les 12 fiches de l'Excel dans le site (Spy est en cours de retrait dans un autre fil), ou repartir de zéro ?
10. **Anomalies 1 à 4** : on garde le comportement de l'Excel, ou on corrige ?
