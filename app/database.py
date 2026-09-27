import os
from dataclasses import dataclass
from typing import Optional

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS fiches (
    name TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    color INTEGER NOT NULL DEFAULT 0x5865F2,
    image_url TEXT,
    footer TEXT,
    created_by INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


@dataclass
class Fiche:
    name: str
    title: str
    description: str
    color: int
    image_url: Optional[str]
    footer: Optional[str]
    created_by: int
    created_at: str
    updated_at: str


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
            return Fiche(**dict(row)) if row else None

    async def list_names(self, prefix: str = "") -> list[str]:
        conn = await self._get_conn()
        async with conn.execute(
            "SELECT name FROM fiches WHERE name LIKE ? ORDER BY name",
            (f"{prefix.lower()}%",),
        ) as cursor:
            rows = await cursor.fetchall()
            return [row["name"] for row in rows]

    async def upsert(
        self,
        name: str,
        title: str,
        description: str,
        color: int,
        image_url: Optional[str],
        footer: Optional[str],
        author_id: int,
    ) -> None:
        conn = await self._get_conn()
        await conn.execute(
            """
            INSERT INTO fiches (name, title, description, color, image_url, footer, created_by)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                title = excluded.title,
                description = excluded.description,
                color = excluded.color,
                image_url = excluded.image_url,
                footer = excluded.footer,
                updated_at = datetime('now')
            """,
            (name.lower(), title, description, color, image_url, footer, author_id),
        )
        await conn.commit()

    async def delete(self, name: str) -> bool:
        conn = await self._get_conn()
        cursor = await conn.execute(
            "DELETE FROM fiches WHERE name = ?", (name.lower(),)
        )
        await conn.commit()
        return cursor.rowcount > 0
