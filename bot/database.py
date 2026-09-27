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
    def __init__(self, path: str):
        self.path = path
        self._conn: Optional[aiosqlite.Connection] = None

    async def connect(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute(SCHEMA)
        await self._conn.commit()

    async def close(self):
        if self._conn:
            await self._conn.close()

    async def get(self, name: str) -> Optional[Fiche]:
        async with self._conn.execute(
            "SELECT * FROM fiches WHERE name = ?", (name.lower(),)
        ) as cursor:
            row = await cursor.fetchone()
            return Fiche(**dict(row)) if row else None

    async def list_names(self) -> list[str]:
        async with self._conn.execute(
            "SELECT name FROM fiches ORDER BY name"
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
        await self._conn.execute(
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
        await self._conn.commit()

    async def delete(self, name: str) -> bool:
        cursor = await self._conn.execute(
            "DELETE FROM fiches WHERE name = ?", (name.lower(),)
        )
        await self._conn.commit()
        return cursor.rowcount > 0
