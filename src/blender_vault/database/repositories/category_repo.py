"""Category and Master Inventory department repository."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Optional

from ..db_manager import RobloxDatabaseManager
from ..models import CategoryNotFoundError, CategoryRecord, LibraryType, utc_now_iso


class CategoryRepository:
    """Manages universal, genre, and game-specific hierarchical asset categories."""

    def __init__(self, db: RobloxDatabaseManager) -> None:
        self.db = db

    @staticmethod
    def _row_to_category(row: sqlite3.Row) -> CategoryRecord:
        return CategoryRecord(
            category_id=int(row["category_id"]),
            category_code=row["category_code"],
            category_name=row["category_name"],
            parent_category_id=row["parent_category_id"],
            category_description=row["category_description"],
            library_type=LibraryType(row["library_type"]),
            legacy_category=row["legacy_category"],
            created_at=row["created_at"],
        )

    def register_category(
        self,
        *,
        category_code: str,
        category_name: str,
        library_type: LibraryType | str = LibraryType.UNIVERSAL,
        parent_category_id: Optional[int] = None,
        category_description: str = "",
        legacy_category: Optional[str] = None,
    ) -> CategoryRecord:
        code = category_code.strip().upper()
        name = category_name.strip()
        if not code or not name:
            raise ValueError("category_code and category_name must be non-empty.")
        lib = library_type if isinstance(library_type, LibraryType) else LibraryType(str(library_type).upper())
        now = utc_now_iso()

        with self.db.connect() as conn:
            if parent_category_id is not None:
                parent = conn.execute(
                    "SELECT category_id FROM categories WHERE category_id = ?",
                    (parent_category_id,),
                ).fetchone()
                if not parent:
                    raise CategoryNotFoundError(f"Parent category_id {parent_category_id} not found.")

            existing = conn.execute(
                "SELECT * FROM categories WHERE category_code = ?",
                (code,),
            ).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE categories
                    SET category_name = ?, parent_category_id = ?, category_description = ?,
                        library_type = ?, legacy_category = ?
                    WHERE category_code = ?
                    """,
                    (name, parent_category_id, category_description.strip(), lib.value, legacy_category, code),
                )
                row = conn.execute("SELECT * FROM categories WHERE category_code = ?", (code,)).fetchone()
                return self._row_to_category(row)

            cur = conn.execute(
                """
                INSERT INTO categories (
                    category_code, category_name, parent_category_id,
                    category_description, library_type, legacy_category, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (code, name, parent_category_id, category_description.strip(), lib.value, legacy_category, now),
            )
            row = conn.execute(
                "SELECT * FROM categories WHERE category_id = ?",
                (cur.lastrowid,),
            ).fetchone()
            return self._row_to_category(row)

    def get_by_id(self, category_id: int) -> Optional[CategoryRecord]:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM categories WHERE category_id = ?", (category_id,)).fetchone()
            return self._row_to_category(row) if row else None

    def get_by_code(self, category_code: str) -> Optional[CategoryRecord]:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM categories WHERE category_code = ?",
                (category_code.strip().upper(),),
            ).fetchone()
            return self._row_to_category(row) if row else None

    def get_by_name(self, category_name: str) -> Optional[CategoryRecord]:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM categories WHERE LOWER(category_name) = LOWER(?)",
                (category_name.strip(),),
            ).fetchone()
            return self._row_to_category(row) if row else None

    def list_categories(
        self,
        *,
        library_type: Optional[LibraryType | str] = None,
        parent_category_id: Optional[int] = None,
    ) -> list[CategoryRecord]:
        clauses: list[str] = []
        params: list[Any] = []
        if library_type is not None:
            lt = library_type.value if isinstance(library_type, LibraryType) else str(library_type).upper()
            clauses.append("library_type = ?")
            params.append(lt)
        if parent_category_id is not None:
            clauses.append("parent_category_id = ?")
            params.append(parent_category_id)
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.db.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM categories {where_sql} ORDER BY library_type, category_id",
                tuple(params),
            ).fetchall()
            return [self._row_to_category(r) for r in rows]

    def get_children(self, parent_category_id: int) -> list[CategoryRecord]:
        return self.list_categories(parent_category_id=parent_category_id)

    def get_hierarchy_path(self, category_id: int) -> list[CategoryRecord]:
        """Return category chain from root ancestor down to category_id."""
        chain: list[CategoryRecord] = []
        seen: set[int] = set()
        current_id: Optional[int] = category_id
        while current_id is not None and current_id not in seen:
            seen.add(current_id)
            rec = self.get_by_id(current_id)
            if not rec:
                break
            chain.append(rec)
            current_id = rec.parent_category_id
        chain.reverse()
        return chain

    def seed_master_inventory(self, inventory_path: Path) -> dict[str, int]:
        """Idempotently register the 25 universal and 15 genre departments from master_inventory.json."""
        data = json.loads(Path(inventory_path).read_text(encoding="utf-8"))
        universal_count = 0
        genre_count = 0

        for entry in data.get("universal_departments", []):
            self.register_category(
                category_code=entry["code"],
                category_name=entry["name"],
                library_type=LibraryType.UNIVERSAL,
                category_description=entry.get("description", ""),
                legacy_category=entry.get("legacy_category"),
            )
            universal_count += 1

        for entry in data.get("genre_departments", []):
            self.register_category(
                category_code=entry["code"],
                category_name=entry["name"],
                library_type=LibraryType.GENRE,
                category_description=entry.get("description", ""),
            )
            genre_count += 1

        return {"universal_seeded": universal_count, "genre_seeded": genre_count}

    def resolve_department_for_legacy_category(
        self,
        legacy_category: str,
        *,
        asset_name: str = "",
        description: str = "",
    ) -> CategoryRecord:
        """Map a Branch 1 category or prompt text onto a registered Universal department."""
        text = f"{asset_name} {description}".lower()
        keyword_overrides = [
            (("sword", "axe", "blade", "gun", "rifle", "shield", "weapon", "bow"), "WPN"),
            (("chair", "table", "desk", "sofa", "bench", "stool", "cabinet", "shelf"), "FUR"),
            (("tree", "leaf", "plant", "foliage", "flower", "bush", "forest"), "ENV"),
            (("rock", "boulder", "cliff", "terrain", "crystal"), "TER"),
            (("car", "truck", "bike", "ship", "plane", "vehicle"), "VEH"),
            (("gear", "cog", "valve", "piston", "engine", "mechanical"), "MEC"),
            (("vase", "cup", "mug", "bowl", "plate", "lamp"), "HSH"),
            (("wall", "door", "window", "pillar", "arch", "stairs"), "ARC"),
            (("house", "building", "tower", "castle", "barn"), "BLD"),
            (("character", "humanoid", "avatar", "npc", "knight"), "CHR"),
        ]
        for keywords, code in keyword_overrides:
            if any(kw in text for kw in keywords):
                rec = self.get_by_code(code)
                if rec:
                    return rec

        legacy_map = {
            "Furniture": "FUR",
            "Nature": "ENV",
            "Vehicles": "VEH",
            "Mechanical Objects": "MEC",
            "Household Objects": "HSH",
            "Architecture": "ARC",
            "Characters": "CHR",
            "Miscellaneous": "PRP",
        }
        target_code = legacy_map.get(legacy_category, "PRP")
        rec = self.get_by_code(target_code)
        if rec:
            return rec
        all_univ = self.list_categories(library_type=LibraryType.UNIVERSAL)
        if all_univ:
            return all_univ[0]
        raise CategoryNotFoundError(
            f"No universal categories registered; cannot resolve '{legacy_category}'."
        )
