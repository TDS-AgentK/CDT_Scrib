// Moteur de calcul des fiches de jeu CDT.
// Reproduit les formules de l'Excel « Event MJ - Joueurs » (fiches joueurs, Base V3.3 corrigée).
// Fonction pure : calculer(fiche, regles) -> valeurs dérivées. Aucune dépendance.

// ARRONDI d'Excel / Google Sheets : moitié éloignée de zéro.
export function arrondi(x) {
  const s = x < 0 ? -1 : 1;
  return s * Math.floor(Math.abs(x) + 0.5 + 1e-9);
}
const ent = (x) => Math.floor(x + 1e-9); // ENT d'Excel

export function modificateur(valeur, regles) {
  const t = regles.table_modificateurs;
  const v = Math.max(t[0][0], Math.min(t[t.length - 1][0], Math.round(valeur)));
  return t.find(([k]) => k === v)[1];
}

const trouver = (liste, nom, quoi) => {
  const x = liste.find((e) => e.nom === nom);
  if (!x) throw new Error(`${quoi} inconnu(e) : ${nom}`);
  return x;
};

// Un jet : n dés à f faces + bonus. f = 0 donne toujours 0 (« Pas d'armes »).
export const de = (n, faces, bonus = 0) => ({ n, faces, bonus });
export function formule({ n, faces, bonus }) {
  return `${n}d${faces}${bonus > 0 ? "+" + bonus : bonus < 0 ? bonus : ""}`;
}

export function calculer(fiche, regles) {
  const C = regles.constantes;
  const race = trouver(regles.races, fiche.race, "Race");
  const metier = trouver(regles.metiers, fiche.metier, "Métier");
  const classe = trouver(regles.classes, fiche.classe, "Classe");
  const armure = trouver(regles.armures, fiche.armure, "Armure");
  const arts = (fiche.artefacts || []).filter(Boolean);
  const somme = (f) => arts.reduce((s, a) => s + (f(a) || 0), 0);
  const armes = (fiche.armes || []).map((a) => ({
    ...a,
    def: trouver(regles.armes, a.nom || "Pas d'armes", "Arme"),
  }));

  // Caractéristiques finales et modificateurs
  const caracs = {}, mods = {};
  for (const c of regles.caracteristiques) {
    let v = (fiche.repartition[c] || 0) + (race.caracs[c] || 0) + (classe.caracs[c] || 0) + somme((a) => a.caracs?.[c]);
    if (c === "Dextérité") v += armure.malus_dex + armes.reduce((s, a) => s + (a.def.malus_dex || 0), 0);
    caracs[c] = v;
    mods[c] = modificateur(v, regles);
  }
  const repartis = regles.caracteristiques.reduce((s, c) => s + (fiche.repartition[c] || 0), 0);

  // Compétences
  const spes = new Set(fiche.specialisations || []);
  const competences = {};
  for (const k of regles.competences) {
    const m = mods[regles.competence_carac[k]];
    competences[k] = (race.competences[k] || 0) + (metier.competences[k] || 0) + (classe.competences[k] || 0)
      + (spes.has(k) ? m + C.bonus_specialisation : m) + somme((a) => a.competences?.[k]);
  }

  // Défense
  const caArmure = arrondi(armure.ca + (fiche.bouclier ? regles.bouclier.ca : 0) * classe.coef_bouclier);
  const statArmure = { "Armure de mage": mods.Intelligence / 2, "Armure légère": mods["Dextérité"] / 2,
    "Armure intermédiaire": (mods.Force + mods["Dextérité"]) / 2, "Armure lourde": mods.Constitution / 2 }[armure.type];
  const jetCA = de(1, arrondi(5 + caArmure + statArmure * classe.coef_armure[armure.type] + somme((a) => a.ca)));
  const prerequis = { carac: armure.prerequis_carac, valeur: armure.prerequis_valeur,
    rempli: (caracs[armure.prerequis_carac] ?? 0) >= armure.prerequis_valeur }; // ≥ (décision de K)
  const pvMax = classe.pv + C.pv_base + Math.max(mods.Constitution, 0);

  // Sauvegardes
  const save = (c, k) => de(1, 100, mods[c] + somme((a) => a.sauvegardes?.[k]));
  const sauvegardes = { "Réflexe": save("Dextérité", "Réflexe"), "Vigueur": save("Constitution", "Vigueur"), "Volonté": save("Sagesse", "Volonté") };

  // Armes (emplacements 1 à 4 ; un artefact peut être « implanté » sur un emplacement)
  const armesCalc = armes.map((a, i) => {
    const d = a.def, prop = d.propriete;
    const art = arts.find((x) => x.emplacement_arme === i + 1);
    const P = ent((d.degats || 0) * (prop === "De Jet" ? classe.mult_deg_jet : classe.mult_deg_arme));
    const multDeg = prop === "Arme légère" || prop === "De Jet" ? classe.mult_deg_jet : classe.mult_deg_arme;
    const faceAtk = Math.max(0, (prop === "De Jet" || prop === "Arme légère" ? mods["Dextérité"] : mods.Force) + P);
    const bonusAtk = (art?.bonus_attaque || 0) + (a.maitrise ? arrondi(P / 2) : 0);
    return { nom: d.nom, maitrise: !!a.maitrise, propriete: prop || null, action_bonus: d.action_bonus || 0,
      degats: de(1, ent(P * multDeg), art?.bonus_degats || 0), attaque: de(1, faceAtk, bonusAtk) };
  });

  // Magie
  let sorts = null;
  const niveau = fiche.magie?.niveau && regles.magie.niveaux.find((n) => n.nom === fiche.magie.niveau);
  if (niveau) {
    const I = mods.Intelligence, A = competences.Arcanes, S = C.sorts;
    const t = (k, faces, nbDes, faceDeg) => ({
      jet: de(1, faces, somme((a) => a.sorts_attaque?.[k])),
      dd: arrondi((S[k].dd_base + I + niveau.valeur) * S[k].dd_mult),
      degats: de(nbDes, faceDeg, somme((a) => a.sorts_degats?.[k])),
      lancers: niveau.lancers[k],
    });
    sorts = {
      mineur: t("mineur", arrondi(I * (A / 200 + 1)) + niveau.modif_maitrise + S.mineur.jet_bonus, S.mineur.des_degats,
        arrondi((S.mineur.jet_bonus / 2 + I * (A / 400 + 1)) / 4)),
      median: t("median", arrondi(I * (A / 100 + 1)) + niveau.modif_maitrise + S.median.jet_bonus, S.median.des_degats,
        arrondi((S.median.jet_bonus / 2 + I * (A / 100 + 0.6)) / 3)),
      majeur: t("majeur", arrondi(I * (A / 400 + 1)) + niveau.modif_maitrise + S.majeur.jet_bonus, S.majeur.des_degats,
        arrondi((S.majeur.jet_bonus / 2 + I * (A / 400 + 1)) / 3)),
      soin: fiche.magie.courant === "Guérison",
    };
  }

  return {
    caracs, mods, competences, specialisations: [...spes],
    controles: {
      points_restants: C.points_a_repartir - repartis,
      hors_bornes: regles.caracteristiques.filter((c) => fiche.repartition[c] < C.carac_min || fiche.repartition[c] > C.carac_max),
      trop_de_specialisations: spes.size > C.specialisations_max,
      prerequis_armure: prerequis,
    },
    armure: { nom: armure.nom, type: armure.type, ca: caArmure, jet: jetCA },
    pv: { max: pvMax, actuels: pvMax - (fiche.degats_recus || 0) },
    sauvegardes, armes: armesCalc, sorts,
    traits: race.traits, description_classe: classe.description,
  };
}
