import os
from dataclasses import dataclass, fields as dataclass_fields
from typing import Optional

import aiosqlite

SECTION_COLUMNS = {
    "architecture": ["couleur", "image_personnage_url", "nom_skin", "lien_cdt", "equipement_image_url"],
    "identite": ["nom", "surnom", "genre", "race"],
    "physique": ["taille", "poids", "tranche_age", "morphologie", "couleur_yeux"],
    "apparence": ["couleur_cheveux", "coiffure", "tenue", "armement_equipement"],
}

# "titre" n'est plus éditable via un formulaire (le titre affiché est désormais
# toujours le nom), mais la colonne reste en base pour compatibilité avec les
# fiches existantes et le dataclass Fiche.
TEXT_COLUMNS = ["titre"] + [c for cols in SECTION_COLUMNS.values() for c in cols if c != "couleur"]

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS fiches (
    name TEXT PRIMARY KEY,
    owner_id INTEGER NOT NULL,
    couleur INTEGER,
    {", ".join(f"{c} TEXT" for c in TEXT_COLUMNS)},
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


@dataclass
class Fiche:
    name: str
    owner_id: int
    couleur: Optional[int]
    image_personnage_url: Optional[str]
    nom_skin: Optional[str]
    lien_cdt: Optional[str]
    equipement_image_url: Optional[str]
    nom: Optional[str]
    surnom: Optional[str]
    titre: Optional[str]
    genre: Optional[str]
    race: Optional[str]
    taille: Optional[str]
    poids: Optional[str]
    tranche_age: Optional[str]
    morphologie: Optional[str]
    couleur_cheveux: Optional[str]
    coiffure: Optional[str]
    couleur_yeux: Optional[str]
    tenue: Optional[str]
    armement_equipement: Optional[str]
    created_at: str
    updated_at: str


FICHE_FIELD_NAMES = {f.name for f in dataclass_fields(Fiche)}


class Database:
    """Connexion paresseuse : ouverte au premier appel, réutilisée tant que le
    process reste chaud, rouverte automatiquement après un réveil (cold start)."""

    def __init__(self, path: str):
        self.path = path
        self._conn: Optional[aiosqlite.Connection] = None

    async def _get_conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            self._conn = await aiosqlite.connect(self.path)
            self._conn.row_factory = aiosqlite.Row
            await self._conn.execute(SCHEMA)
            await self._conn.commit()
        return self._conn

    async def close(self):
        if self._conn:
            await self._conn.close()
            self._conn = None

    async def get(self, name: str) -> Optional[Fiche]:
        conn = await self._get_conn()
        async with conn.execute(
            "SELECT * FROM fiches WHERE name = ?", (name.lower(),)
        ) as cursor:
            row = await cursor.fetchone()
            return Fiche(**{k: row[k] for k in FICHE_FIELD_NAMES}) if row else None

    async def list_names(self, prefix: str = "") -> list[str]:
        conn = await self._get_conn()
        async with conn.execute(
            "SELECT name FROM fiches WHERE name LIKE ? ORDER BY name",
            (f"{prefix.lower()}%",),
        ) as cursor:
            rows = await cursor.fetchall()
            return [row["name"] for row in rows]

    async def list_names_by_owner(self, owner_id: int, prefix: str = "") -> list[str]:
        conn = await self._get_conn()
        async with conn.execute(
            "SELECT name FROM fiches WHERE owner_id = ? AND name LIKE ? ORDER BY name",
            (owner_id, f"{prefix.lower()}%"),
        ) as cursor:
            rows = await cursor.fetchall()
            return [row["name"] for row in rows]

    async def create(self, name: str, owner_id: int) -> None:
        conn = await self._get_conn()
        await conn.execute(
            "INSERT INTO fiches (name, owner_id) VALUES (?, ?)", (name.lower(), owner_id)
        )
        await conn.commit()

    async def update_section(self, name: str, values: dict) -> None:
        conn = await self._get_conn()
        columns = list(values.keys())
        assignments = ", ".join(f"{c} = ?" for c in columns)
        await conn.execute(
            f"UPDATE fiches SET {assignments}, updated_at = datetime('now') WHERE name = ?",
            (*values.values(), name.lower()),
        )
        await conn.commit()

    async def delete(self, name: str) -> bool:
        conn = await self._get_conn()
        cursor = await conn.execute(
            "DELETE FROM fiches WHERE name = ?", (name.lower(),)
        )
        await conn.commit()
        return cursor.rowcount > 0
