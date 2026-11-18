"""Asset repository: central permanent ID generation, CRUD, status transitions, tags, and genre links."""

from __future__ import annotations

import sqlite3
from typing import Any, Optional

from ..db_manager import RobloxDatabaseManager
from ..models import (
    AssetNotFoundError,
    CategoryNotFoundError,
    CategoryRecord,
    DuplicateAssetIDError,
    ExportFormat,
    LibraryType,
    ProductionStatus,
    RobloxAssetRecord,
    ValidationStatus,
    VisualStyle,
    format_asset_id,
    utc_now_iso,
    validate_status_transition,
)
from .log_repo import LogRepository


class AssetRepository:
    """Manages permanent Roblox asset registration, lookup, updating, tags, and genre links."""

    def __init__(self, db: RobloxDatabaseManager) -> None:
        self.db = db
        self.logs = LogRepository(db)

    def _hydrate_asset(self, conn: sqlite3.Connection, row: sqlite3.Row) -> RobloxAssetRecord:
        aid = row["asset_id"]
        tag_rows = conn.execute(
            "SELECT tag_name FROM asset_tags WHERE asset_id = ? ORDER BY tag_name",
            (aid,),
        ).fetchall()
        genre_rows = conn.execute(
            "SELECT genre_category_id FROM asset_genre_relationships WHERE asset_id = ? ORDER BY genre_category_id",
            (aid,),
        ).fetchall()
        return RobloxAssetRecord(
            id=int(row["id"]),
            asset_id=aid,
            asset_name=row["asset_name"],
            display_name=row["display_name"],
            category_id=int(row["category_id"]),
            asset_description=row["asset_description"],
            visual_style=VisualStyle(row["visual_style"]),
            production_status=ProductionStatus(row["production_status"]),
            source_blend_path=row["source_blend_path"],
            google_drive_file_id=row["google_drive_file_id"],
            google_drive_folder_id=row["google_drive_folder_id"],
            google_drive_url=row["google_drive_url"],
            export_format=ExportFormat(row["export_format"]) if row["export_format"] else None,
            export_path=row["export_path"],
            triangle_count=int(row["triangle_count"]),
            vertex_count=int(row["vertex_count"]),
            polygon_count=int(row["polygon_count"]),
            dimensions_x=float(row["dimensions_x"]),
            dimensions_y=float(row["dimensions_y"]),
            dimensions_z=float(row["dimensions_z"]),
            texture_resolution=row["texture_resolution"],
            material_count=int(row["material_count"]),
            uv_status=row["uv_status"],
            collision_status=row["collision_status"],
            qa_score=float(row["qa_score"]) if row["qa_score"] is not None else None,
            qa_status=row["qa_status"],
            qa_grade=row["qa_grade"],
            human_approved=bool(row["human_approved"]),
            approved_by=row["approved_by"],
            approved_at=row["approved_at"],
            current_version=int(row["current_version"]),
            object_name=row["object_name"],
            variant_name=row["variant_name"],
            legacy_vault_id=row["legacy_vault_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            tags=[r["tag_name"] for r in tag_rows],
            genre_category_ids=[int(r["genre_category_id"]) for r in genre_rows],
        )

    def allocate_permanent_asset_id(
        self,
        *,
        category_id: int,
        object_name: str,
        variant: str = "Basic",
        visual_style: VisualStyle | str = VisualStyle.LP,
        version_num: int = 1,
    ) -> str:
        """Atomically allocate the next unique permanent ID for a category without ever reusing IDs."""
        style_enum = visual_style if isinstance(visual_style, VisualStyle) else VisualStyle(str(visual_style).upper())
        with self.db.connect(immediate=True) as conn:
            cat_row = conn.execute(
                "SELECT category_code FROM categories WHERE category_id = ?",
                (category_id,),
            ).fetchone()
            if not cat_row:
                raise CategoryNotFoundError(f"Category ID {category_id} not found.")
            cat_code = str(cat_row["category_code"]).upper()

            seq_row = conn.execute(
                "SELECT last_seq FROM id_sequences WHERE category_code = ?",
                (cat_code,),
            ).fetchone()
            next_seq = int(seq_row["last_seq"]) + 1 if seq_row else 1

            while True:
                candidate = format_asset_id(
                    cat_code,
                    next_seq,
                    object_name=object_name,
                    variant=variant,
                    visual_style=style_enum,
                    version_num=version_num,
                )
                exists = conn.execute(
                    "SELECT 1 FROM assets WHERE asset_id = ?",
                    (candidate,),
                ).fetchone()
                if not exists:
                    break
                next_seq += 1

            conn.execute(
                """
                INSERT INTO id_sequences (category_code, last_seq)
                VALUES (?, ?)
                ON CONFLICT(category_code) DO UPDATE SET last_seq = excluded.last_seq
                """,
                (cat_code, next_seq),
            )
            return candidate

    def register_asset(
        self,
        *,
        asset_name: str,
        category_id: int,
        display_name: Optional[str] = None,
        asset_description: str = "",
        visual_style: VisualStyle | str = VisualStyle.LP,
        production_status: ProductionStatus | str = ProductionStatus.PLANNED,
        object_name: Optional[str] = None,
        variant_name: str = "Basic",
        asset_id: Optional[str] = None,
        source_blend_path: Optional[str] = None,
        google_drive_file_id: Optional[str] = None,
        google_drive_folder_id: Optional[str] = None,
        google_drive_url: Optional[str] = None,
        export_format: Optional[ExportFormat | str] = None,
        export_path: Optional[str] = None,
        triangle_count: int = 0,
        vertex_count: int = 0,
        polygon_count: int = 0,
        dimensions_x: float = 0.0,
        dimensions_y: float = 0.0,
        dimensions_z: float = 0.0,
        texture_resolution: str = "",
        material_count: int = 0,
        uv_status: str = ValidationStatus.PENDING.value,
        collision_status: str = ValidationStatus.PENDING.value,
        qa_score: Optional[float] = None,
        qa_status: str = ValidationStatus.PENDING.value,
        qa_grade: Optional[str] = None,
        human_approved: bool = False,
        approved_by: Optional[str] = None,
        approved_at: Optional[str] = None,
        legacy_vault_id: Optional[str] = None,
        tags: Optional[list[str]] = None,
        genre_category_ids: Optional[list[int]] = None,
        change_description: str = "Initial asset registration",
        sha256: Optional[str] = None,
    ) -> RobloxAssetRecord:
        """Register an asset and its initial v1 version, tags, genres, and log atomically."""
        style_enum = visual_style if isinstance(visual_style, VisualStyle) else VisualStyle(str(visual_style).upper())
        status_enum = (
            production_status
            if isinstance(production_status, ProductionStatus)
            else ProductionStatus(str(production_status).upper())
        )
        fmt_enum: Optional[ExportFormat] = None
        if export_format is not None:
            fmt_enum = (
                export_format
                if isinstance(export_format, ExportFormat)
                else ExportFormat(str(export_format).upper())
            )

        clean_name = asset_name.strip()
        if not clean_name:
            raise ValueError("asset_name cannot be empty.")
        clean_display = (display_name or clean_name).strip()
        obj_token = (object_name or clean_name).strip()
        var_token = (variant_name or "Basic").strip()

        assigned_id = asset_id.strip() if asset_id else self.allocate_permanent_asset_id(
            category_id=category_id,
            object_name=obj_token,
            variant=var_token,
            visual_style=style_enum,
            version_num=1,
        )
        now = utc_now_iso()

        with self.db.connect(immediate=True) as conn:
            cat_row = conn.execute(
                "SELECT category_id FROM categories WHERE category_id = ?",
                (category_id,),
            ).fetchone()
            if not cat_row:
                raise CategoryNotFoundError(f"Category ID {category_id} not found.")

            dup = conn.execute("SELECT 1 FROM assets WHERE asset_id = ?", (assigned_id,)).fetchone()
            if dup:
                raise DuplicateAssetIDError(f"Asset ID '{assigned_id}' already exists.")

            conn.execute(
                """
                INSERT INTO assets (
                    asset_id, asset_name, display_name, category_id, asset_description,
                    visual_style, production_status, source_blend_path,
                    google_drive_file_id, google_drive_folder_id, google_drive_url,
                    export_format, export_path,
                    triangle_count, vertex_count, polygon_count,
                    dimensions_x, dimensions_y, dimensions_z,
                    texture_resolution, material_count, uv_status, collision_status,
                    qa_score, qa_status, qa_grade, human_approved, approved_by, approved_at,
                    current_version, object_name, variant_name, legacy_vault_id,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    assigned_id,
                    clean_name,
                    clean_display,
                    category_id,
                    asset_description.strip(),
                    style_enum.value,
                    status_enum.value,
                    source_blend_path,
                    google_drive_file_id,
                    google_drive_folder_id,
                    google_drive_url,
                    fmt_enum.value if fmt_enum else None,
                    export_path,
                    int(triangle_count),
                    int(vertex_count),
                    int(polygon_count),
                    float(dimensions_x),
                    float(dimensions_y),
                    float(dimensions_z),
                    texture_resolution,
                    int(material_count),
                    uv_status,
                    collision_status,
                    qa_score,
                    qa_status,
                    qa_grade,
                    1 if human_approved else 0,
                    approved_by,
                    approved_at,
                    1,
                    obj_token,
                    var_token,
                    legacy_vault_id,
                    now,
                    now,
                ),
            )

            conn.execute(
                """
                INSERT INTO asset_versions (
                    asset_id, version_number, blender_source_path,
                    google_drive_file_id, google_drive_folder_id, google_drive_url,
                    export_format, export_path, change_description,
                    created_at, qa_status, qa_score, is_approved, approved_by, approved_at, sha256
                ) VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    assigned_id,
                    source_blend_path,
                    google_drive_file_id,
                    google_drive_folder_id,
                    google_drive_url,
                    fmt_enum.value if fmt_enum else None,
                    export_path,
                    change_description,
                    now,
                    qa_status,
                    qa_score,
                    1 if human_approved else 0,
                    approved_by,
                    approved_at,
                    sha256,
                ),
            )

            for tag in sorted({t.strip().lower() for t in (tags or []) if t and t.strip()}):
                conn.execute(
                    "INSERT OR IGNORE INTO asset_tags (asset_id, tag_name, created_at) VALUES (?, ?, ?)",
                    (assigned_id, tag, now),
                )

            for gid in sorted({int(g) for g in (genre_category_ids or [])}):
                g_row = conn.execute("SELECT category_id FROM categories WHERE category_id = ?", (gid,)).fetchone()
                if not g_row:
                    raise CategoryNotFoundError(f"Genre category_id {gid} not found.")
                conn.execute(
                    "INSERT OR IGNORE INTO asset_genre_relationships (asset_id, genre_category_id, created_at) VALUES (?, ?, ?)",
                    (assigned_id, gid, now),
                )

            self.logs.log_event(
                asset_id=assigned_id,
                process_name="asset_registration",
                status=status_enum.value,
                message=f"Registered asset '{clean_name}' ({assigned_id}) v001.",
                conn=conn,
            )

            row = conn.execute("SELECT * FROM assets WHERE asset_id = ?", (assigned_id,)).fetchone()
            return self._hydrate_asset(conn, row)

    def get_asset(self, asset_id: str) -> Optional[RobloxAssetRecord]:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM assets WHERE asset_id = ?", (asset_id,)).fetchone()
            return self._hydrate_asset(conn, row) if row else None

    def get_by_legacy_vault_id(self, legacy_vault_id: str) -> Optional[RobloxAssetRecord]:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM assets WHERE legacy_vault_id = ?",
                (legacy_vault_id,),
            ).fetchone()
            return self._hydrate_asset(conn, row) if row else None

    def list_assets(
        self,
        *,
        category_id: Optional[int] = None,
        production_status: Optional[ProductionStatus | str] = None,
        qa_status: Optional[str] = None,
        visual_style: Optional[VisualStyle | str] = None,
        genre_category_id: Optional[int] = None,
        tag: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[RobloxAssetRecord]:
        clauses: list[str] = []
        params: list[Any] = []
        joins = ""

        if genre_category_id is not None:
            joins += " JOIN asset_genre_relationships agr ON agr.asset_id = a.asset_id "
            clauses.append("agr.genre_category_id = ?")
            params.append(int(genre_category_id))
        if tag is not None and tag.strip():
            joins += " JOIN asset_tags atg ON atg.asset_id = a.asset_id "
            clauses.append("LOWER(atg.tag_name) = LOWER(?)")
            params.append(tag.strip())
        if category_id is not None:
            clauses.append("a.category_id = ?")
            params.append(int(category_id))
        if production_status is not None:
            st = (
                production_status.value
                if isinstance(production_status, ProductionStatus)
                else str(production_status).upper()
            )
            clauses.append("a.production_status = ?")
            params.append(st)
        if qa_status is not None:
            clauses.append("a.qa_status = ?")
            params.append(qa_status)
        if visual_style is not None:
            vs = visual_style.value if isinstance(visual_style, VisualStyle) else str(visual_style).upper()
            clauses.append("a.visual_style = ?")
            params.append(vs)
        if search is not None and search.strip():
            like = f"%{search.strip()}%"
            clauses.append("(a.asset_id LIKE ? OR a.asset_name LIKE ? OR a.display_name LIKE ? OR a.asset_description LIKE ?)")
            params.extend([like, like, like, like])

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.extend([limit, offset])
        with self.db.connect() as conn:
            rows = conn.execute(
                f"SELECT DISTINCT a.* FROM assets a {joins} {where_sql} ORDER BY a.updated_at DESC, a.id DESC LIMIT ? OFFSET ?",
                tuple(params),
            ).fetchall()
            return [self._hydrate_asset(conn, r) for r in rows]

    def update_asset(self, asset_id: str, **fields: Any) -> RobloxAssetRecord:
        """Update allowed asset columns using strictly parameterized SQL."""
        allowed_cols = {
            "asset_name",
            "display_name",
            "category_id",
            "asset_description",
            "visual_style",
            "source_blend_path",
            "google_drive_file_id",
            "google_drive_folder_id",
            "google_drive_url",
            "export_format",
            "export_path",
            "triangle_count",
            "vertex_count",
            "polygon_count",
            "dimensions_x",
            "dimensions_y",
            "dimensions_z",
            "texture_resolution",
            "material_count",
            "uv_status",
            "collision_status",
            "qa_score",
            "qa_status",
            "qa_grade",
            "human_approved",
            "approved_by",
            "approved_at",
            "legacy_vault_id",
        }
        if "production_status" in fields:
            raise ValueError("Use transition_status() to change production_status safely.")
        unknown = set(fields.keys()) - allowed_cols
        if unknown:
            raise ValueError(f"Unsupported asset update field(s): {sorted(unknown)}")
        if not fields:
            existing = self.get_asset(asset_id)
            if not existing:
                raise AssetNotFoundError(f"Asset '{asset_id}' not found.")
            return existing

        clean_fields: dict[str, Any] = {}
        for k, v in fields.items():
            if k == "visual_style" and v is not None:
                clean_fields[k] = v.value if isinstance(v, VisualStyle) else str(v).upper()
            elif k == "export_format" and v is not None:
                clean_fields[k] = v.value if isinstance(v, ExportFormat) else str(v).upper()
            elif k == "human_approved" and v is not None:
                clean_fields[k] = 1 if bool(v) else 0
            else:
                clean_fields[k] = v
        clean_fields["updated_at"] = utc_now_iso()

        assignments = ", ".join(f"{col} = ?" for col in clean_fields.keys())
        values = list(clean_fields.values()) + [asset_id]

        with self.db.connect() as conn:
            cur = conn.execute(f"UPDATE assets SET {assignments} WHERE asset_id = ?", tuple(values))
            if cur.rowcount == 0:
                raise AssetNotFoundError(f"Asset '{asset_id}' not found.")
            row = conn.execute("SELECT * FROM assets WHERE asset_id = ?", (asset_id,)).fetchone()
            return self._hydrate_asset(conn, row)

    def transition_status(
        self,
        asset_id: str,
        target_status: ProductionStatus | str,
        *,
        process_name: str = "status_transition",
        message: str = "",
        error_details: Optional[str] = None,
    ) -> RobloxAssetRecord:
        target = (
            target_status
            if isinstance(target_status, ProductionStatus)
            else ProductionStatus(str(target_status).upper())
        )
        now = utc_now_iso()
        with self.db.connect(immediate=True) as conn:
            row = conn.execute("SELECT * FROM assets WHERE asset_id = ?", (asset_id,)).fetchone()
            if not row:
                raise AssetNotFoundError(f"Asset '{asset_id}' not found.")
            current = ProductionStatus(row["production_status"])
            validate_status_transition(current, target)
            conn.execute(
                "UPDATE assets SET production_status = ?, updated_at = ? WHERE asset_id = ?",
                (target.value, now, asset_id),
            )
            log_msg = message.strip() or f"Status transition: {current.value} -> {target.value}"
            self.logs.log_event(
                asset_id=asset_id,
                process_name=process_name,
                status=target.value,
                message=log_msg,
                error_details=error_details,
                conn=conn,
            )
            updated = conn.execute("SELECT * FROM assets WHERE asset_id = ?", (asset_id,)).fetchone()
            return self._hydrate_asset(conn, updated)

    # ------------------------------------------------------------------
    # Tag Management
    # ------------------------------------------------------------------
    def add_tags(self, asset_id: str, tags: list[str]) -> list[str]:
        now = utc_now_iso()
        clean = sorted({t.strip().lower() for t in tags if t and t.strip()})
        with self.db.connect() as conn:
            exists = conn.execute("SELECT 1 FROM assets WHERE asset_id = ?", (asset_id,)).fetchone()
            if not exists:
                raise AssetNotFoundError(f"Asset '{asset_id}' not found.")
            for tag in clean:
                conn.execute(
                    "INSERT OR IGNORE INTO asset_tags (asset_id, tag_name, created_at) VALUES (?, ?, ?)",
                    (asset_id, tag, now),
                )
            rows = conn.execute(
                "SELECT tag_name FROM asset_tags WHERE asset_id = ? ORDER BY tag_name",
                (asset_id,),
            ).fetchall()
            return [r["tag_name"] for r in rows]

    def remove_tag(self, asset_id: str, tag_name: str) -> list[str]:
        with self.db.connect() as conn:
            conn.execute(
                "DELETE FROM asset_tags WHERE asset_id = ? AND LOWER(tag_name) = LOWER(?)",
                (asset_id, tag_name.strip()),
            )
            rows = conn.execute(
                "SELECT tag_name FROM asset_tags WHERE asset_id = ? ORDER BY tag_name",
                (asset_id,),
            ).fetchall()
            return [r["tag_name"] for r in rows]

    def list_tags(self, asset_id: str) -> list[str]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT tag_name FROM asset_tags WHERE asset_id = ? ORDER BY tag_name",
                (asset_id,),
            ).fetchall()
            return [r["tag_name"] for r in rows]

    # ------------------------------------------------------------------
    # Multi-Genre Relationships (no model duplication)
    # ------------------------------------------------------------------
    def add_genre_relationship(self, asset_id: str, genre_category_id: int) -> list[CategoryRecord]:
        now = utc_now_iso()
        with self.db.connect() as conn:
            a_row = conn.execute("SELECT 1 FROM assets WHERE asset_id = ?", (asset_id,)).fetchone()
            if not a_row:
                raise AssetNotFoundError(f"Asset '{asset_id}' not found.")
            c_row = conn.execute("SELECT * FROM categories WHERE category_id = ?", (genre_category_id,)).fetchone()
            if not c_row:
                raise CategoryNotFoundError(f"Genre category_id {genre_category_id} not found.")
            if c_row["library_type"] != LibraryType.GENRE.value:
                raise ValueError(
                    f"Category '{c_row['category_code']}' has library_type '{c_row['library_type']}', expected 'GENRE'."
                )
            conn.execute(
                "INSERT OR IGNORE INTO asset_genre_relationships (asset_id, genre_category_id, created_at) VALUES (?, ?, ?)",
                (asset_id, genre_category_id, now),
            )
        return self.list_genres_for_asset(asset_id)

    def remove_genre_relationship(self, asset_id: str, genre_category_id: int) -> list[CategoryRecord]:
        with self.db.connect() as conn:
            conn.execute(
                "DELETE FROM asset_genre_relationships WHERE asset_id = ? AND genre_category_id = ?",
                (asset_id, genre_category_id),
            )
        return self.list_genres_for_asset(asset_id)

    def list_genres_for_asset(self, asset_id: str) -> list[CategoryRecord]:
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT c.* FROM categories c
                JOIN asset_genre_relationships agr ON agr.genre_category_id = c.category_id
                WHERE agr.asset_id = ?
                ORDER BY c.category_code
                """,
                (asset_id,),
            ).fetchall()
            return [
                CategoryRecord(
                    category_id=int(r["category_id"]),
                    category_code=r["category_code"],
                    category_name=r["category_name"],
                    parent_category_id=r["parent_category_id"],
                    category_description=r["category_description"],
                    library_type=LibraryType(r["library_type"]),
                    legacy_category=r["legacy_category"],
                    created_at=r["created_at"],
                )
                for r in rows
            ]
