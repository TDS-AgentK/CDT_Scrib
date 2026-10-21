# Règles des fiches de jeu CDT

- `regles-cdt.json` : les tables de l'Excel « Event MJ - Joueurs » (races, métiers, classes, armures, armes, magie, artefacts, table des modificateurs) et les constantes des formules.
- `moteur.mjs` : `calculer(fiche, regles)` renvoie toutes les valeurs dérivées d'une fiche : caractéristiques, modificateurs, compétences, CA et jet de CA, PV, armes, sauvegardes, sorts et contrôles de création. JavaScript pur, sans dépendance.

Le moteur a été vérifié sur les 12 fiches de l'Excel : les 582 valeurs comparées sont identiques. Les données de test restent privées, dans le dossier partagé du projet.

Format d'une fiche (entrée de `calculer`) :

```json
{
  "race": "Humain", "metier": "Assassin", "classe": "Mage",
  "repartition": { "Force": 40, "Dextérité": 60, "Constitution": 55, "Intelligence": 60, "Sagesse": 55, "Charisme": 30 },
  "specialisations": ["Arcanes", "Investigation", "Discrétion"],
  "armure": "Etoffe Enchantée (Armure de mage)", "bouclier": false,
  "armes": [{ "nom": "Arme courte", "maitrise": true }, { "nom": "Pas d'armes", "maitrise": false }],
  "magie": { "energie": "Énergie ténébreuse", "ecole": "Magie des Ténèbres", "courant": "Ombromancie", "niveau": "Expert (15 ans)" },
  "degats_recus": 0,
  "artefacts": [{ "nom": "Fumerolle", "type": "Bijoux", "objet": "Bague", "emplacement_arme": 0,
    "bonus_degats": 0, "bonus_attaque": 0, "caracs": { "Intelligence": 2 }, "competences": { "Arcanes": 2, "Discrétion": 2 },
    "sauvegardes": {}, "sorts_attaque": {}, "sorts_degats": {}, "ca": 0 }]
}
```

Un jet est rendu sous la forme `{ n, faces, bonus }`, et `formule()` le transforme en texte (`1d29`, `1d4+2`).
