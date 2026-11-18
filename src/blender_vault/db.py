"""SQLite persistence layer for the Asset Vault catalogue, versions, jobs, and Drive state."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

from .models import (
    AssetManifest,
    AssetRecord,
    AssetStatus,
    AssetVersion,
    DriveFileRecord,
    DriveSyncStatus,
    GeometryProfile,
    HumanReviewRecord,
    ProvenanceInfo,
    TechnicalQAReport,
    VisualQAReport,
    utc_now_iso,
)


SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS assets (
    asset_id TEXT PRIMARY KEY,
    slug TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT 'Miscellaneous',
    geometry_profile TEXT NOT NULL DEFAULT 'general',
    tags_json TEXT NOT NULL DEFAULT '[]',
    provenance_json TEXT NOT NULL,
    current_version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS asset_versions (
    asset_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    status TEXT NOT NULL,
    blend_path TEXT,
    glb_path TEXT,
    source_original_path TEXT,
    blender_version TEXT NOT NULL DEFAULT '',
    file_formats_json TEXT NOT NULL DEFAULT '[]',
    file_size_bytes INTEGER NOT NULL DEFAULT 0,
    sha256 TEXT,
    md5 TEXT,
    poly_count INTEGER NOT NULL DEFAULT 0,
    vertex_count INTEGER NOT NULL DEFAULT 0,
    dimensions_json TEXT NOT NULL DEFAULT '[0.0, 0.0, 0.0]',
    materials_json TEXT NOT NULL DEFAULT '[]',
    technical_qa_json TEXT,
    visual_qa_json TEXT,
    human_review_json TEXT NOT NULL,
    drive_sync_status TEXT NOT NULL DEFAULT 'NOT_SYNCED',
    drive_folder_id TEXT,
    drive_files_json TEXT NOT NULL DEFAULT '[]',
    error_message TEXT,
    failed_stage TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (asset_id, version),
    FOREIGN KEY (asset_id) REFERENCES assets(asset_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_versions_sha256 ON asset_versions(sha256);
CREATE INDEX IF NOT EXISTS idx_versions_status ON asset_versions(status);
CREATE INDEX IF NOT EXISTS idx_assets_category ON assets(category);

CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    job_type TEXT NOT NULL,
    status TEXT NOT NULL,
    stage TEXT NOT NULL DEFAULT '',
    params_json TEXT NOT NULL DEFAULT '{}',
    stages_json TEXT NOT NULL DEFAULT '[]',
    started_at TEXT,
    completed_at TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    log_path TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


class VaultDatabase:
    """Thread-safe SQLite catalogue manager."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys = ON;")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA_SQL)
            existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
            migrations = {
                "params_json": "TEXT NOT NULL DEFAULT '{}'",
                "stages_json": "TEXT NOT NULL DEFAULT '[]'",
                "started_at": "TEXT",
                "completed_at": "TEXT",
                "cancel_requested": "INTEGER NOT NULL DEFAULT 0",
            }
            for col, col_def in migrations.items():
                if col not in existing_cols:
                    conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} {col_def}")

    def create_asset_with_version(self, asset: AssetRecord, version: AssetVersion) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO assets (
                    asset_id, slug, name, description, category, geometry_profile,
                    tags_json, provenance_json, current_version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    asset.asset_id,
                    asset.slug,
                    asset.name,
                    asset.description,
                    asset.category,
                    asset.geometry_profile.value,
                    json.dumps(asset.tags),
                    asset.provenance.model_dump_json(),
                    version.version,
                    asset.created_at,
                    asset.updated_at,
                ),
            )
            self._insert_version_row(conn, version)

    def add_version(self, version: AssetVersion) -> None:
        with self.connect() as conn:
            self._insert_version_row(conn, version)
            conn.execute(
                "UPDATE assets SET current_version = ?, updated_at = ? WHERE asset_id = ?",
                (version.version, utc_now_iso(), version.asset_id),
            )

    def _insert_version_row(self, conn: sqlite3.Connection, v: AssetVersion) -> None:
        conn.execute(
            """
            INSERT INTO asset_versions (
                asset_id, version, status, blend_path, glb_path, source_original_path,
                blender_version, file_formats_json, file_size_bytes, sha256, md5,
                poly_count, vertex_count, dimensions_json, materials_json,
                technical_qa_json, visual_qa_json, human_review_json,
                drive_sync_status, drive_folder_id, drive_files_json,
                error_message, failed_stage, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                v.asset_id,
                v.version,
                v.status.value,
                v.blend_path,
                v.glb_path,
                v.source_original_path,
                v.blender_version,
                json.dumps(v.file_formats),
                v.file_size_bytes,
                v.sha256,
                v.md5,
                v.poly_count,
                v.vertex_count,
                json.dumps(v.dimensions),
                json.dumps(v.materials),
                v.technical_qa.model_dump_json() if v.technical_qa else None,
                v.visual_qa.model_dump_json() if v.visual_qa else None,
                v.human_review.model_dump_json(),
                v.drive_sync_status.value,
                v.drive_folder_id,
                json.dumps([df.model_dump() for df in v.drive_files]),
                v.error_message,
                v.failed_stage,
                v.created_at,
                v.updated_at,
            ),
        )

    def update_version(self, v: AssetVersion) -> None:
        v.updated_at = utc_now_iso()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE asset_versions SET
                    status = ?,
                    blend_path = ?,
                    glb_path = ?,
                    source_original_path = ?,
                    blender_version = ?,
                    file_formats_json = ?,
                    file_size_bytes = ?,
                    sha256 = ?,
                    md5 = ?,
                    poly_count = ?,
                    vertex_count = ?,
                    dimensions_json = ?,
                    materials_json = ?,
                    technical_qa_json = ?,
                    visual_qa_json = ?,
                    human_review_json = ?,
                    drive_sync_status = ?,
                    drive_folder_id = ?,
                    drive_files_json = ?,
                    error_message = ?,
                    failed_stage = ?,
                    updated_at = ?
                WHERE asset_id = ? AND version = ?
                """,
                (
                    v.status.value,
                    v.blend_path,
                    v.glb_path,
                    v.source_original_path,
                    v.blender_version,
                    json.dumps(v.file_formats),
                    v.file_size_bytes,
                    v.sha256,
                    v.md5,
                    v.poly_count,
                    v.vertex_count,
                    json.dumps(v.dimensions),
                    json.dumps(v.materials),
                    v.technical_qa.model_dump_json() if v.technical_qa else None,
                    v.visual_qa.model_dump_json() if v.visual_qa else None,
                    v.human_review.model_dump_json(),
                    v.drive_sync_status.value,
                    v.drive_folder_id,
                    json.dumps([df.model_dump() for df in v.drive_files]),
                    v.error_message,
                    v.failed_stage,
                    v.updated_at,
                    v.asset_id,
                    v.version,
                ),
            )
            conn.execute(
                "UPDATE assets SET updated_at = ? WHERE asset_id = ?",
                (v.updated_at, v.asset_id),
            )

    def update_asset_metadata(
        self,
        asset_id: str,
        *,
        name: Optional[str] = None,
        description: Optional[str] = None,
        category: Optional[str] = None,
        tags: Optional[list[str]] = None,
        provenance: Optional[ProvenanceInfo] = None,
    ) -> None:
        now = utc_now_iso()
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM assets WHERE asset_id = ?", (asset_id,)).fetchone()
            if not row:
                raise KeyError(f"Asset not found: {asset_id}")
            conn.execute(
                """
                UPDATE assets SET name = ?, description = ?, category = ?, tags_json = ?, provenance_json = ?, updated_at = ?
                WHERE asset_id = ?
                """,
                (
                    name if name is not None else row["name"],
                    description if description is not None else row["description"],
                    category if category is not None else row["category"],
                    json.dumps(tags) if tags is not None else row["tags_json"],
                    provenance.model_dump_json() if provenance is not None else row["provenance_json"],
                    now,
                    asset_id,
                ),
            )

    def get_asset(self, asset_id: str) -> Optional[AssetRecord]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM assets WHERE asset_id = ?", (asset_id,)).fetchone()
            if not row:
                return None
            v_rows = conn.execute(
                "SELECT * FROM asset_versions WHERE asset_id = ? ORDER BY version DESC",
                (asset_id,),
            ).fetchall()
            versions = [self._row_to_version(vr) for vr in v_rows]
            return self._row_to_asset(row, versions)

    def get_version(self, asset_id: str, version: int) -> Optional[AssetVersion]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM asset_versions WHERE asset_id = ? AND version = ?",
                (asset_id, version),
            ).fetchone()
            return self._row_to_version(row) if row else None

    def list_assets(
        self,
        *,
        search: Optional[str] = None,
        category: Optional[str] = None,
        status: Optional[str] = None,
    ) -> list[AssetRecord]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM assets ORDER BY updated_at DESC").fetchall()
            results: list[AssetRecord] = []
            q = search.strip().lower() if search and search.strip() else None
            for row in rows:
                v_rows = conn.execute(
                    "SELECT * FROM asset_versions WHERE asset_id = ? ORDER BY version DESC",
                    (row["asset_id"],),
                ).fetchall()
                versions = [self._row_to_version(vr) for vr in v_rows]
                asset = self._row_to_asset(row, versions)
                if category and category != "All" and asset.category != category:
                    continue
                if status and status != "All":
                    if not asset.latest or asset.latest.status.value != status:
                        continue
                if q:
                    haystack = " ".join(
                        [
                            asset.asset_id,
                            asset.name,
                            asset.description,
                            asset.category,
                            " ".join(asset.tags),
                            asset.provenance.license_name,
                        ]
                    ).lower()
                    if q not in haystack:
                        continue
                results.append(asset)
            return results

    def find_version_by_sha256(self, sha256: str) -> Optional[tuple[AssetRecord, AssetVersion]]:
        if not sha256:
            return None
        with self.connect() as conn:
            v_row = conn.execute(
                "SELECT * FROM asset_versions WHERE sha256 = ? ORDER BY created_at DESC LIMIT 1",
                (sha256,),
            ).fetchone()
            if not v_row:
                return None
            asset = self.get_asset(v_row["asset_id"])
            if not asset:
                return None
            return asset, self._row_to_version(v_row)

    def upsert_from_manifest(self, manifest: AssetManifest) -> None:
        """Reconstruct or synchronize a local database entry from an AssetManifest."""
        with self.connect() as conn:
            existing = conn.execute(
                "SELECT current_version FROM assets WHERE asset_id = ?",
                (manifest.asset_id,),
            ).fetchone()
            v = manifest.version
            if not existing:
                conn.execute(
                    """
                    INSERT INTO assets (
                        asset_id, slug, name, description, category, geometry_profile,
                        tags_json, provenance_json, current_version, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        manifest.asset_id,
                        manifest.slug,
                        manifest.name,
                        manifest.description,
                        manifest.category,
                        manifest.geometry_profile.value,
                        json.dumps(manifest.tags),
                        manifest.provenance.model_dump_json(),
                        v.version,
                        v.created_at,
                        v.updated_at,
                    ),
                )
            else:
                max_ver = max(existing["current_version"], v.version)
                conn.execute(
                    """
                    UPDATE assets SET
                        slug = ?, name = ?, description = ?, category = ?,
                        geometry_profile = ?, tags_json = ?, provenance_json = ?,
                        current_version = ?, updated_at = ?
                    WHERE asset_id = ?
                    """,
                    (
                        manifest.slug,
                        manifest.name,
                        manifest.description,
                        manifest.category,
                        manifest.geometry_profile.value,
                        json.dumps(manifest.tags),
                        manifest.provenance.model_dump_json(),
                        max_ver,
                        utc_now_iso(),
                        manifest.asset_id,
                    ),
                )
            v_existing = conn.execute(
                "SELECT 1 FROM asset_versions WHERE asset_id = ? AND version = ?",
                (v.asset_id, v.version),
            ).fetchone()
            if v_existing:
                conn.execute(
                    "DELETE FROM asset_versions WHERE asset_id = ? AND version = ?",
                    (v.asset_id, v.version),
                )
            self._insert_version_row(conn, v)

    def record_job(
        self,
        *,
        job_id: str,
        asset_id: str,
        version: int,
        job_type: str,
        status: str,
        stage: str = "",
        error_message: Optional[str] = None,
        log_path: Optional[str] = None,
        params: Optional[dict[str, Any]] = None,
        stages: Optional[list[dict[str, Any]]] = None,
        started_at: Optional[str] = None,
        completed_at: Optional[str] = None,
    ) -> None:
        now = utc_now_iso()
        with self.connect() as conn:
            existing = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE jobs SET
                        status = ?,
                        stage = ?,
                        error_message = ?,
                        log_path = ?,
                        params_json = ?,
                        stages_json = ?,
                        started_at = COALESCE(?, started_at),
                        completed_at = COALESCE(?, completed_at),
                        updated_at = ?
                    WHERE job_id = ?
                    """,
                    (
                        status,
                        stage,
                        error_message,
                        log_path,
                        json.dumps(params) if params is not None else existing["params_json"],
                        json.dumps(stages) if stages is not None else existing["stages_json"],
                        started_at,
                        completed_at,
                        now,
                        job_id,
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO jobs (
                        job_id, asset_id, version, job_type, status, stage,
                        params_json, stages_json, started_at, completed_at, cancel_requested,
                        error_message, log_path, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)
                    """,
                    (
                        job_id,
                        asset_id,
                        version,
                        job_type,
                        status,
                        stage,
                        json.dumps(params or {}),
                        json.dumps(stages or []),
                        started_at,
                        completed_at,
                        error_message,
                        log_path,
                        now,
                        now,
                    ),
                )

    def _hydrate_job_row(self, row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        try:
            d["params"] = json.loads(d.pop("params_json", "{}") or "{}")
        except json.JSONDecodeError:
            d["params"] = {}
        try:
            d["stages"] = json.loads(d.pop("stages_json", "[]") or "[]")
        except json.JSONDecodeError:
            d["stages"] = []
        d["cancel_requested"] = bool(d.get("cancel_requested", 0))
        return d

    def get_job(self, job_id: str) -> Optional[dict[str, Any]]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            return self._hydrate_job_row(row) if row else None

    def request_job_cancel(self, job_id: str) -> Optional[dict[str, Any]]:
        now = utc_now_iso()
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            if not row:
                return None
            if row["status"] in ("COMPLETED", "FAILED", "CANCELLED"):
                return self._hydrate_job_row(row)
            if row["status"] == "QUEUED":
                conn.execute(
                    "UPDATE jobs SET status = 'CANCELLED', cancel_requested = 1, completed_at = ?, updated_at = ? WHERE job_id = ?",
                    (now, now, job_id),
                )
            else:
                conn.execute(
                    "UPDATE jobs SET cancel_requested = 1, updated_at = ? WHERE job_id = ?",
                    (now, job_id),
                )
            updated = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            return self._hydrate_job_row(updated)

    def list_jobs(self, asset_id: Optional[str] = None, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as conn:
            if asset_id:
                rows = conn.execute(
                    "SELECT * FROM jobs WHERE asset_id = ? ORDER BY created_at DESC LIMIT ?",
                    (asset_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [self._hydrate_job_row(r) for r in rows]

    def set_setting(self, key: str, value: str) -> None:
        now = utc_now_iso()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO app_settings (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (key, value, now),
            )

    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else default

    def _row_to_asset(self, row: sqlite3.Row, versions: list[AssetVersion]) -> AssetRecord:
        latest = versions[0] if versions else None
        return AssetRecord(
            asset_id=row["asset_id"],
            slug=row["slug"],
            name=row["name"],
            description=row["description"],
            category=row["category"],
            geometry_profile=GeometryProfile(row["geometry_profile"]),
            tags=json.loads(row["tags_json"]),
            provenance=ProvenanceInfo.model_validate_json(row["provenance_json"]),
            current_version=row["current_version"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            latest=latest,
            versions=versions,
        )

    def _row_to_version(self, row: sqlite3.Row) -> AssetVersion:
        return AssetVersion(
            asset_id=row["asset_id"],
            version=row["version"],
            status=AssetStatus(row["status"]),
            blend_path=row["blend_path"],
            glb_path=row["glb_path"],
            source_original_path=row["source_original_path"],
            blender_version=row["blender_version"],
            file_formats=json.loads(row["file_formats_json"]),
            file_size_bytes=row["file_size_bytes"],
            sha256=row["sha256"],
            md5=row["md5"],
            poly_count=row["poly_count"],
            vertex_count=row["vertex_count"],
            dimensions=json.loads(row["dimensions_json"]),
            materials=json.loads(row["materials_json"]),
            technical_qa=(
                TechnicalQAReport.model_validate_json(row["technical_qa_json"])
                if row["technical_qa_json"]
                else None
            ),
            visual_qa=(
                VisualQAReport.model_validate_json(row["visual_qa_json"])
                if row["visual_qa_json"]
                else None
            ),
            human_review=HumanReviewRecord.model_validate_json(row["human_review_json"]),
            drive_sync_status=DriveSyncStatus(row["drive_sync_status"]),
            drive_folder_id=row["drive_folder_id"],
            drive_files=[DriveFileRecord.model_validate(item) for item in json.loads(row["drive_files_json"])],
            error_message=row["error_message"],
            failed_stage=row["failed_stage"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
