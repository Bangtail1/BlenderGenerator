"""SQLite persistence layer for Branch 2 (BlendSwap Reference Intelligence Engine) extending vault.db."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from ..db import VaultDatabase
from ..models import DriveFileRecord, DriveSyncStatus, utc_now_iso
from .models import (
    BenchmarkReport,
    BlendSwapAuthor,
    BlendSwapCategoryInfo,
    BlendSwapCounts,
    BlendSwapFileInfo,
    CollectionCycleRecord,
    DimensionScore,
    DownloadQueueItem,
    LicenseAssessment,
    QualityGradeReport,
    ReferenceAssetRecord,
    ReferenceAssetStatus,
    ReferenceInspectionReport,
)


REF_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS ref_assets (
    blendswap_id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    url TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    asset_type TEXT NOT NULL DEFAULT 'model_3d',
    asset_type_label TEXT NOT NULL DEFAULT '3D Model',
    author_id INTEGER,
    author_username TEXT NOT NULL DEFAULT 'unknown',
    author_url TEXT NOT NULL DEFAULT '',
    license_json TEXT NOT NULL,
    license_key TEXT NOT NULL DEFAULT 'gal',
    license_category TEXT NOT NULL DEFAULT 'GAL',
    license_class TEXT NOT NULL DEFAULT 'F',
    blendswap_category_json TEXT NOT NULL DEFAULT '{}',
    primary_category TEXT NOT NULL DEFAULT 'Other',
    technical_categories_json TEXT NOT NULL DEFAULT '[]',
    quality_category TEXT NOT NULL DEFAULT '',
    tags_json TEXT NOT NULL DEFAULT '[]',
    original_tags_json TEXT NOT NULL DEFAULT '[]',
    blender_versions_json TEXT NOT NULL DEFAULT '[]',
    counts_json TEXT NOT NULL DEFAULT '{}',
    downloads_count INTEGER NOT NULL DEFAULT 0,
    likes_count INTEGER NOT NULL DEFAULT 0,
    comments_count INTEGER NOT NULL DEFAULT 0,
    explicit INTEGER NOT NULL DEFAULT 0,
    primary_format TEXT NOT NULL DEFAULT 'blender',
    is_native_blender INTEGER NOT NULL DEFAULT 1,
    file_size_bytes INTEGER NOT NULL DEFAULT 0,
    files_json TEXT NOT NULL DEFAULT '[]',
    local_dir TEXT,
    primary_file_path TEXT,
    blend_file_path TEXT,
    sha256 TEXT,
    md5 TEXT,
    preview_paths_json TEXT NOT NULL DEFAULT '[]',
    metadata_path TEXT,
    attribution_path TEXT,
    status TEXT NOT NULL DEFAULT 'DISCOVERED',
    error_message TEXT,
    popularity_score REAL NOT NULL DEFAULT 0.0,
    selection_score REAL NOT NULL DEFAULT 0.0,
    technical_quality_score REAL,
    reference_usefulness_score REAL,
    grade_letter TEXT,
    drive_sync_status TEXT NOT NULL DEFAULT 'NOT_SYNCED',
    drive_folder_id TEXT,
    drive_files_json TEXT NOT NULL DEFAULT '[]',
    blendswap_created_at TEXT,
    downloaded_at TEXT,
    inspected_at TEXT,
    graded_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ref_assets_primary_cat ON ref_assets(primary_category);
CREATE INDEX IF NOT EXISTS idx_ref_assets_status ON ref_assets(status);
CREATE INDEX IF NOT EXISTS idx_ref_assets_grade ON ref_assets(technical_quality_score DESC);
CREATE INDEX IF NOT EXISTS idx_ref_assets_downloads ON ref_assets(downloads_count DESC);
CREATE INDEX IF NOT EXISTS idx_ref_assets_license_key ON ref_assets(license_key);

CREATE TABLE IF NOT EXISTS ref_asset_files (
    file_id INTEGER PRIMARY KEY,
    blendswap_id INTEGER NOT NULL,
    format TEXT NOT NULL,
    format_label TEXT NOT NULL DEFAULT '',
    filename TEXT NOT NULL,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    software_version TEXT,
    render_engine TEXT,
    render_engine_label TEXT,
    position INTEGER NOT NULL DEFAULT 0,
    is_selected INTEGER NOT NULL DEFAULT 0,
    local_path TEXT,
    sha256 TEXT,
    md5 TEXT,
    FOREIGN KEY (blendswap_id) REFERENCES ref_assets(blendswap_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS ref_inspections (
    inspection_id TEXT PRIMARY KEY,
    blendswap_id INTEGER NOT NULL,
    methodology_version TEXT NOT NULL,
    blender_version TEXT NOT NULL DEFAULT '',
    file_opened_ok INTEGER NOT NULL DEFAULT 0,
    inspection_json TEXT NOT NULL,
    log_path TEXT,
    error_message TEXT,
    inspected_at TEXT NOT NULL,
    FOREIGN KEY (blendswap_id) REFERENCES ref_assets(blendswap_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_ref_inspections_asset ON ref_inspections(blendswap_id, inspected_at DESC);

CREATE TABLE IF NOT EXISTS ref_quality_grades (
    grade_id TEXT PRIMARY KEY,
    blendswap_id INTEGER NOT NULL,
    methodology_version TEXT NOT NULL,
    grading_profile TEXT NOT NULL,
    total_score REAL NOT NULL,
    grade_letter TEXT NOT NULL,
    popularity_score REAL NOT NULL,
    technical_quality_score REAL NOT NULL,
    reference_usefulness_score REAL NOT NULL,
    dimensions_json TEXT NOT NULL,
    grade_json TEXT NOT NULL,
    graded_at TEXT NOT NULL,
    FOREIGN KEY (blendswap_id) REFERENCES ref_assets(blendswap_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_ref_grades_asset ON ref_quality_grades(blendswap_id, graded_at DESC);

CREATE TABLE IF NOT EXISTS ref_collection_cycles (
    cycle_id TEXT PRIMARY KEY,
    cycle_date TEXT NOT NULL,
    timezone TEXT NOT NULL,
    trigger_type TEXT NOT NULL,
    status TEXT NOT NULL,
    target_count INTEGER NOT NULL DEFAULT 7,
    free_allowance_start INTEGER,
    free_allowance_end INTEGER,
    credits_start INTEGER,
    credits_end INTEGER,
    credits_spent INTEGER NOT NULL DEFAULT 0,
    downloaded_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0,
    shortage_count INTEGER NOT NULL DEFAULT 0,
    shortage_reason TEXT,
    log_path TEXT,
    error_message TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ref_cycles_date ON ref_collection_cycles(cycle_date DESC, started_at DESC);

CREATE TABLE IF NOT EXISTS ref_download_queue (
    queue_id TEXT PRIMARY KEY,
    cycle_id TEXT NOT NULL,
    blendswap_id INTEGER NOT NULL,
    file_id INTEGER,
    slot_number INTEGER NOT NULL,
    slot_reason TEXT NOT NULL DEFAULT '',
    selection_score REAL NOT NULL DEFAULT 0.0,
    status TEXT NOT NULL DEFAULT 'PENDING',
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    download_url TEXT,
    download_url_expires_at TEXT,
    last_error TEXT,
    next_retry_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (cycle_id) REFERENCES ref_collection_cycles(cycle_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_ref_queue_cycle ON ref_download_queue(cycle_id, slot_number);

CREATE TABLE IF NOT EXISTS ref_download_history (
    history_id TEXT PRIMARY KEY,
    cycle_id TEXT,
    blendswap_id INTEGER NOT NULL,
    file_id INTEGER,
    reason TEXT NOT NULL,
    charged INTEGER NOT NULL DEFAULT 0,
    credits_spent INTEGER NOT NULL DEFAULT 0,
    credits_remaining INTEGER,
    download_url_expires_at TEXT,
    local_path TEXT,
    sha256 TEXT,
    file_size_bytes INTEGER NOT NULL DEFAULT 0,
    success INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    downloaded_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ref_dl_history_asset ON ref_download_history(blendswap_id, downloaded_at DESC);

CREATE TABLE IF NOT EXISTS ref_download_tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    blendswap_id INTEGER NOT NULL,
    file_id INTEGER,
    reason TEXT NOT NULL,
    expires_at TEXT,
    granted_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ref_tickets_asset ON ref_download_tickets(blendswap_id, granted_at DESC);

CREATE TABLE IF NOT EXISTS ref_licenses_cache (
    license_key TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    url TEXT NOT NULL DEFAULT '',
    raw_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ref_categories_cache (
    slug TEXT PRIMARY KEY,
    category_id INTEGER,
    name TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ref_custom_categories (
    name TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    auto_created INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ref_benchmarks (
    benchmark_id TEXT PRIMARY KEY,
    branch1_asset_id TEXT NOT NULL,
    branch1_version INTEGER NOT NULL,
    branch1_name TEXT NOT NULL,
    branch1_category TEXT NOT NULL,
    grading_profile TEXT NOT NULL,
    methodology_version TEXT NOT NULL,
    local_score REAL NOT NULL,
    local_grade TEXT NOT NULL,
    reference_ids_json TEXT NOT NULL DEFAULT '[]',
    report_json TEXT NOT NULL,
    report_path TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ref_benchmarks_b1 ON ref_benchmarks(branch1_asset_id, branch1_version, created_at DESC);

CREATE TABLE IF NOT EXISTS ref_api_usage_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    method TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    status_code INTEGER,
    rate_limit_limit INTEGER,
    rate_limit_remaining INTEGER,
    rate_limit_reset INTEGER,
    duration_ms REAL,
    error_key TEXT,
    created_at TEXT NOT NULL
);
"""


class ReferenceDatabase:
    """Thread-safe SQLite repository for all Branch 2 reference library data."""

    def __init__(self, vault_db: VaultDatabase) -> None:
        self.db = vault_db
        self._init_schema()

    def _init_schema(self) -> None:
        with self.db.connect() as conn:
            conn.executescript(REF_SCHEMA_SQL)

    # ------------------------------------------------------------------
    # API Usage & Caches
    # ------------------------------------------------------------------
    def log_api_call(
        self,
        *,
        method: str,
        endpoint: str,
        status_code: Optional[int],
        rate_limit_limit: Optional[int] = None,
        rate_limit_remaining: Optional[int] = None,
        rate_limit_reset: Optional[int] = None,
        duration_ms: float = 0.0,
        error_key: Optional[str] = None,
    ) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO ref_api_usage_log (
                    method, endpoint, status_code, rate_limit_limit,
                    rate_limit_remaining, rate_limit_reset, duration_ms, error_key, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    method.upper(),
                    endpoint,
                    status_code,
                    rate_limit_limit,
                    rate_limit_remaining,
                    rate_limit_reset,
                    round(duration_ms, 2),
                    error_key,
                    utc_now_iso(),
                ),
            )

    def get_api_usage_summary(self) -> dict[str, Any]:
        with self.db.connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM ref_api_usage_log").fetchone()[0]
            errors = conn.execute("SELECT COUNT(*) FROM ref_api_usage_log WHERE status_code >= 400 OR error_key IS NOT NULL").fetchone()[0]
            latest = conn.execute(
                "SELECT * FROM ref_api_usage_log ORDER BY id DESC LIMIT 1"
            ).fetchone()
            recent = conn.execute(
                "SELECT method, endpoint, status_code, duration_ms, error_key, created_at FROM ref_api_usage_log ORDER BY id DESC LIMIT 20"
            ).fetchall()
            return {
                "total_calls": total,
                "error_calls": errors,
                "latest_rate_limit": (
                    {
                        "limit": latest["rate_limit_limit"],
                        "remaining": latest["rate_limit_remaining"],
                        "reset": latest["rate_limit_reset"],
                        "at": latest["created_at"],
                    }
                    if latest
                    else None
                ),
                "recent": [dict(r) for r in recent],
            }

    def upsert_licenses_cache(self, licenses: list[dict[str, Any]]) -> None:
        now = utc_now_iso()
        with self.db.connect() as conn:
            for item in licenses:
                key = str(item.get("key") or "").strip().lower()
                if not key:
                    continue
                conn.execute(
                    """
                    INSERT INTO ref_licenses_cache (license_key, name, url, raw_json, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(license_key) DO UPDATE SET
                        name = excluded.name,
                        url = excluded.url,
                        raw_json = excluded.raw_json,
                        updated_at = excluded.updated_at
                    """,
                    (key, str(item.get("name") or key), str(item.get("url") or ""), json.dumps(item), now),
                )

    def list_licenses_cache(self) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute("SELECT * FROM ref_licenses_cache ORDER BY license_key").fetchall()
            return [json.loads(r["raw_json"]) for r in rows]

    def upsert_categories_cache(self, categories: list[dict[str, Any]]) -> None:
        now = utc_now_iso()
        with self.db.connect() as conn:
            for item in categories:
                slug = str(item.get("slug") or "").strip().lower()
                if not slug:
                    continue
                conn.execute(
                    """
                    INSERT INTO ref_categories_cache (slug, category_id, name, raw_json, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(slug) DO UPDATE SET
                        category_id = excluded.category_id,
                        name = excluded.name,
                        raw_json = excluded.raw_json,
                        updated_at = excluded.updated_at
                    """,
                    (slug, item.get("id"), str(item.get("name") or slug), json.dumps(item), now),
                )

    def list_categories_cache(self) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute("SELECT * FROM ref_categories_cache ORDER BY name").fetchall()
            return [json.loads(r["raw_json"]) for r in rows]

    def register_custom_category(self, name: str, kind: str = "primary") -> None:
        clean = name.strip()
        if not clean:
            return
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO ref_custom_categories (name, kind, auto_created, created_at)
                VALUES (?, ?, 1, ?)
                ON CONFLICT(name) DO NOTHING
                """,
                (clean, kind, utc_now_iso()),
            )

    def list_custom_categories(self, kind: Optional[str] = None) -> list[str]:
        with self.db.connect() as conn:
            if kind:
                rows = conn.execute("SELECT name FROM ref_custom_categories WHERE kind = ? ORDER BY name", (kind,)).fetchall()
            else:
                rows = conn.execute("SELECT name FROM ref_custom_categories ORDER BY name").fetchall()
            return [r["name"] for r in rows]

    # ------------------------------------------------------------------
    # Reference Assets & Files
    # ------------------------------------------------------------------
    def upsert_reference_asset(self, record: ReferenceAssetRecord, *, selected_file_id: Optional[int] = None) -> None:
        record.updated_at = utc_now_iso()
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO ref_assets (
                    blendswap_id, title, url, description, asset_type, asset_type_label,
                    author_id, author_username, author_url,
                    license_json, license_key, license_category, license_class,
                    blendswap_category_json, primary_category, technical_categories_json,
                    quality_category, tags_json, original_tags_json, blender_versions_json,
                    counts_json, downloads_count, likes_count, comments_count, explicit,
                    primary_format, is_native_blender, file_size_bytes, files_json,
                    local_dir, primary_file_path, blend_file_path, sha256, md5,
                    preview_paths_json, metadata_path, attribution_path,
                    status, error_message, popularity_score, selection_score,
                    technical_quality_score, reference_usefulness_score, grade_letter,
                    drive_sync_status, drive_folder_id, drive_files_json,
                    blendswap_created_at, downloaded_at, inspected_at, graded_at,
                    created_at, updated_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                ON CONFLICT(blendswap_id) DO UPDATE SET
                    title = excluded.title,
                    url = excluded.url,
                    description = excluded.description,
                    asset_type = excluded.asset_type,
                    asset_type_label = excluded.asset_type_label,
                    author_id = excluded.author_id,
                    author_username = excluded.author_username,
                    author_url = excluded.author_url,
                    license_json = excluded.license_json,
                    license_key = excluded.license_key,
                    license_category = excluded.license_category,
                    license_class = excluded.license_class,
                    blendswap_category_json = excluded.blendswap_category_json,
                    primary_category = excluded.primary_category,
                    technical_categories_json = excluded.technical_categories_json,
                    quality_category = excluded.quality_category,
                    tags_json = excluded.tags_json,
                    original_tags_json = excluded.original_tags_json,
                    blender_versions_json = excluded.blender_versions_json,
                    counts_json = excluded.counts_json,
                    downloads_count = excluded.downloads_count,
                    likes_count = excluded.likes_count,
                    comments_count = excluded.comments_count,
                    explicit = excluded.explicit,
                    primary_format = excluded.primary_format,
                    is_native_blender = excluded.is_native_blender,
                    file_size_bytes = excluded.file_size_bytes,
                    files_json = excluded.files_json,
                    local_dir = COALESCE(excluded.local_dir, ref_assets.local_dir),
                    primary_file_path = COALESCE(excluded.primary_file_path, ref_assets.primary_file_path),
                    blend_file_path = COALESCE(excluded.blend_file_path, ref_assets.blend_file_path),
                    sha256 = COALESCE(excluded.sha256, ref_assets.sha256),
                    md5 = COALESCE(excluded.md5, ref_assets.md5),
                    preview_paths_json = CASE WHEN excluded.preview_paths_json != '[]' THEN excluded.preview_paths_json ELSE ref_assets.preview_paths_json END,
                    metadata_path = COALESCE(excluded.metadata_path, ref_assets.metadata_path),
                    attribution_path = COALESCE(excluded.attribution_path, ref_assets.attribution_path),
                    status = excluded.status,
                    error_message = excluded.error_message,
                    popularity_score = excluded.popularity_score,
                    selection_score = excluded.selection_score,
                    technical_quality_score = COALESCE(excluded.technical_quality_score, ref_assets.technical_quality_score),
                    reference_usefulness_score = COALESCE(excluded.reference_usefulness_score, ref_assets.reference_usefulness_score),
                    grade_letter = COALESCE(excluded.grade_letter, ref_assets.grade_letter),
                    drive_sync_status = excluded.drive_sync_status,
                    drive_folder_id = COALESCE(excluded.drive_folder_id, ref_assets.drive_folder_id),
                    drive_files_json = CASE WHEN excluded.drive_files_json != '[]' THEN excluded.drive_files_json ELSE ref_assets.drive_files_json END,
                    blendswap_created_at = COALESCE(excluded.blendswap_created_at, ref_assets.blendswap_created_at),
                    downloaded_at = COALESCE(excluded.downloaded_at, ref_assets.downloaded_at),
                    inspected_at = COALESCE(excluded.inspected_at, ref_assets.inspected_at),
                    graded_at = COALESCE(excluded.graded_at, ref_assets.graded_at),
                    updated_at = excluded.updated_at
                """,
                (
                    record.blendswap_id,
                    record.title,
                    record.url,
                    record.description,
                    record.asset_type,
                    record.asset_type_label,
                    record.author.id,
                    record.author.username,
                    record.author.url,
                    record.license_assessment.model_dump_json(),
                    record.license_assessment.license_key,
                    record.license_assessment.license_category.value,
                    record.license_assessment.primary_class.value,
                    record.blendswap_category.model_dump_json(),
                    record.primary_category,
                    json.dumps(record.technical_categories),
                    record.quality_category,
                    json.dumps(record.tags),
                    json.dumps(record.original_tags),
                    json.dumps(record.blender_versions),
                    record.counts.model_dump_json(),
                    record.counts.downloads,
                    record.counts.likes,
                    record.counts.comments,
                    1 if record.explicit else 0,
                    record.primary_format,
                    1 if record.is_native_blender else 0,
                    record.file_size_bytes,
                    json.dumps([f.model_dump() for f in record.files]),
                    record.local_dir,
                    record.primary_file_path,
                    record.blend_file_path,
                    record.sha256,
                    record.md5,
                    json.dumps(record.preview_paths),
                    record.metadata_path,
                    record.attribution_path,
                    record.status.value,
                    record.error_message,
                    record.popularity_score,
                    record.selection_score,
                    record.technical_quality_score,
                    record.reference_usefulness_score,
                    record.grade_letter,
                    record.drive_sync_status.value,
                    record.drive_folder_id,
                    json.dumps([df.model_dump() for df in record.drive_files]),
                    record.blendswap_created_at,
                    record.downloaded_at,
                    record.inspected_at,
                    record.graded_at,
                    record.created_at,
                    record.updated_at,
                ),
            )

            for f in record.files:
                is_sel = 1 if (selected_file_id is not None and f.id == selected_file_id) else 0
                conn.execute(
                    """
                    INSERT INTO ref_asset_files (
                        file_id, blendswap_id, format, format_label, filename,
                        size_bytes, software_version, render_engine, render_engine_label,
                        position, is_selected, local_path, sha256, md5
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(file_id) DO UPDATE SET
                        format = excluded.format,
                        format_label = excluded.format_label,
                        filename = excluded.filename,
                        size_bytes = excluded.size_bytes,
                        software_version = excluded.software_version,
                        render_engine = excluded.render_engine,
                        render_engine_label = excluded.render_engine_label,
                        position = excluded.position,
                        is_selected = CASE WHEN excluded.is_selected = 1 THEN 1 ELSE ref_asset_files.is_selected END
                    """,
                    (
                        f.id,
                        record.blendswap_id,
                        f.format,
                        f.format_label,
                        f.filename,
                        f.size_bytes,
                        f.software_version,
                        f.render_engine,
                        f.render_engine_label,
                        f.position,
                        is_sel,
                        record.primary_file_path if is_sel else None,
                        record.sha256 if is_sel else None,
                        record.md5 if is_sel else None,
                    ),
                )

    def get_reference_asset(self, blendswap_id: int) -> Optional[ReferenceAssetRecord]:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM ref_assets WHERE blendswap_id = ?", (blendswap_id,)).fetchone()
            if not row:
                return None
            insp_row = conn.execute(
                "SELECT inspection_json FROM ref_inspections WHERE blendswap_id = ? ORDER BY inspected_at DESC LIMIT 1",
                (blendswap_id,),
            ).fetchone()
            grade_row = conn.execute(
                "SELECT grade_json FROM ref_quality_grades WHERE blendswap_id = ? ORDER BY graded_at DESC LIMIT 1",
                (blendswap_id,),
            ).fetchone()
            return self._row_to_reference_asset(
                row,
                ReferenceInspectionReport.model_validate_json(insp_row["inspection_json"]) if insp_row else None,
                QualityGradeReport.model_validate_json(grade_row["grade_json"]) if grade_row else None,
            )

    def list_reference_assets(
        self,
        *,
        search: Optional[str] = None,
        primary_category: Optional[str] = None,
        technical_category: Optional[str] = None,
        license_key: Optional[str] = None,
        grade_letter: Optional[str] = None,
        status: Optional[str] = None,
        sort_by: str = "updated_at",
        limit: Optional[int] = None,
        include_details: bool = True,
    ) -> list[ReferenceAssetRecord]:
        order_map = {
            "updated_at": "updated_at DESC",
            "downloads": "downloads_count DESC",
            "technical_score": "COALESCE(technical_quality_score, -1) DESC",
            "usefulness": "COALESCE(reference_usefulness_score, -1) DESC",
            "newest": "created_at DESC",
            "title": "title COLLATE NOCASE ASC",
        }
        order_sql = order_map.get(sort_by, "updated_at DESC")
        with self.db.connect() as conn:
            rows = conn.execute(f"SELECT * FROM ref_assets ORDER BY {order_sql}").fetchall()
            results: list[ReferenceAssetRecord] = []
            q = search.strip().lower() if search and search.strip() else None

            for row in rows:
                if primary_category and primary_category != "All" and row["primary_category"] != primary_category:
                    continue
                if license_key and license_key != "All" and row["license_key"] != license_key:
                    continue
                if grade_letter and grade_letter != "All" and (row["grade_letter"] or "") != grade_letter:
                    continue
                if status and status != "All" and row["status"] != status:
                    continue

                tech_cats = json.loads(row["technical_categories_json"])
                if technical_category and technical_category != "All" and technical_category not in tech_cats:
                    continue

                if q:
                    haystack = " ".join(
                        [
                            str(row["blendswap_id"]),
                            row["title"],
                            row["description"],
                            row["author_username"],
                            row["primary_category"],
                            row["license_key"],
                            " ".join(json.loads(row["tags_json"])),
                            " ".join(tech_cats),
                        ]
                    ).lower()
                    if q not in haystack:
                        continue

                insp = None
                grade = None
                if include_details:
                    insp_row = conn.execute(
                        "SELECT inspection_json FROM ref_inspections WHERE blendswap_id = ? ORDER BY inspected_at DESC LIMIT 1",
                        (row["blendswap_id"],),
                    ).fetchone()
                    grade_row = conn.execute(
                        "SELECT grade_json FROM ref_quality_grades WHERE blendswap_id = ? ORDER BY graded_at DESC LIMIT 1",
                        (row["blendswap_id"],),
                    ).fetchone()
                    if insp_row:
                        insp = ReferenceInspectionReport.model_validate_json(insp_row["inspection_json"])
                    if grade_row:
                        grade = QualityGradeReport.model_validate_json(grade_row["grade_json"])

                results.append(self._row_to_reference_asset(row, insp, grade))
                if limit is not None and len(results) >= limit:
                    break
            return results

    def has_downloaded_asset(self, blendswap_id: int) -> bool:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT status, primary_file_path FROM ref_assets WHERE blendswap_id = ?",
                (blendswap_id,),
            ).fetchone()
            if not row:
                return False
            return row["status"] in (
                ReferenceAssetStatus.DOWNLOADED.value,
                ReferenceAssetStatus.INSPECTED.value,
                ReferenceAssetStatus.GRADED.value,
            ) or bool(row["primary_file_path"])

    def find_reference_by_sha256(self, sha256: str) -> Optional[int]:
        if not sha256:
            return None
        with self.db.connect() as conn:
            row = conn.execute("SELECT blendswap_id FROM ref_assets WHERE sha256 = ? LIMIT 1", (sha256,)).fetchone()
            return int(row["blendswap_id"]) if row else None

    def get_category_distribution(self) -> dict[str, int]:
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT primary_category, COUNT(*) AS cnt
                FROM ref_assets
                WHERE status IN ('DOWNLOADED', 'INSPECTED', 'GRADED')
                GROUP BY primary_category
                """
            ).fetchall()
            return {r["primary_category"]: int(r["cnt"]) for r in rows}

    def update_drive_sync_state(
        self,
        blendswap_id: int,
        *,
        status: DriveSyncStatus,
        folder_id: Optional[str] = None,
        drive_files: Optional[list[DriveFileRecord]] = None,
    ) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE ref_assets SET
                    drive_sync_status = ?,
                    drive_folder_id = COALESCE(?, drive_folder_id),
                    drive_files_json = COALESCE(?, drive_files_json),
                    updated_at = ?
                WHERE blendswap_id = ?
                """,
                (
                    status.value,
                    folder_id,
                    json.dumps([df.model_dump() for df in drive_files]) if drive_files is not None else None,
                    utc_now_iso(),
                    blendswap_id,
                ),
            )

    # ------------------------------------------------------------------
    # Inspections & Grades
    # ------------------------------------------------------------------
    def save_inspection(self, report: ReferenceInspectionReport) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO ref_inspections (
                    inspection_id, blendswap_id, methodology_version, blender_version,
                    file_opened_ok, inspection_json, log_path, error_message, inspected_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(inspection_id) DO UPDATE SET
                    inspection_json = excluded.inspection_json,
                    file_opened_ok = excluded.file_opened_ok,
                    error_message = excluded.error_message
                """,
                (
                    report.inspection_id,
                    report.blendswap_id,
                    report.methodology_version,
                    report.blender_runtime_version or report.blend_header_version,
                    1 if report.file_opened_ok else 0,
                    report.model_dump_json(),
                    report.log_path,
                    report.error_message,
                    report.inspected_at,
                ),
            )
            conn.execute(
                """
                UPDATE ref_assets SET
                    status = ?,
                    inspected_at = ?,
                    error_message = ?,
                    updated_at = ?
                WHERE blendswap_id = ?
                """,
                (
                    ReferenceAssetStatus.INSPECTED.value if report.inspection_completed_ok else ReferenceAssetStatus.FAILED.value,
                    report.inspected_at,
                    report.error_message,
                    utc_now_iso(),
                    report.blendswap_id,
                ),
            )

    def save_quality_grade(
        self,
        grade: QualityGradeReport,
        *,
        quality_category: str = "",
        technical_categories: Optional[list[str]] = None,
    ) -> None:
        if grade.blendswap_id is None:
            raise ValueError("Reference grade requires blendswap_id.")
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO ref_quality_grades (
                    grade_id, blendswap_id, methodology_version, grading_profile,
                    total_score, grade_letter, popularity_score, technical_quality_score,
                    reference_usefulness_score, dimensions_json, grade_json, graded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    grade.grade_id,
                    grade.blendswap_id,
                    grade.methodology_version,
                    grade.grading_profile,
                    grade.total_score,
                    grade.grade_letter,
                    grade.popularity_score,
                    grade.technical_quality_score,
                    grade.reference_usefulness_score,
                    json.dumps([d.model_dump() for d in grade.dimensions]),
                    grade.model_dump_json(),
                    grade.graded_at,
                ),
            )
            conn.execute(
                """
                UPDATE ref_assets SET
                    status = ?,
                    technical_quality_score = ?,
                    reference_usefulness_score = ?,
                    popularity_score = ?,
                    grade_letter = ?,
                    quality_category = COALESCE(NULLIF(?, ''), quality_category),
                    technical_categories_json = COALESCE(?, technical_categories_json),
                    graded_at = ?,
                    updated_at = ?
                WHERE blendswap_id = ?
                """,
                (
                    ReferenceAssetStatus.GRADED.value,
                    grade.technical_quality_score,
                    grade.reference_usefulness_score,
                    grade.popularity_score,
                    grade.grade_letter,
                    quality_category,
                    json.dumps(technical_categories) if technical_categories is not None else None,
                    grade.graded_at,
                    utc_now_iso(),
                    grade.blendswap_id,
                ),
            )

    # ------------------------------------------------------------------
    # Collection Cycles, Download Queue & History
    # ------------------------------------------------------------------
    def create_cycle(self, cycle: CollectionCycleRecord) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO ref_collection_cycles (
                    cycle_id, cycle_date, timezone, trigger_type, status, target_count,
                    free_allowance_start, free_allowance_end, credits_start, credits_end,
                    credits_spent, downloaded_count, failed_count, shortage_count,
                    shortage_reason, log_path, error_message, started_at, completed_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cycle.cycle_id,
                    cycle.cycle_date,
                    cycle.timezone,
                    cycle.trigger_type,
                    cycle.status,
                    cycle.target_count,
                    cycle.free_allowance_start,
                    cycle.free_allowance_end,
                    cycle.credits_start,
                    cycle.credits_end,
                    cycle.credits_spent,
                    cycle.downloaded_count,
                    cycle.failed_count,
                    cycle.shortage_count,
                    cycle.shortage_reason,
                    cycle.log_path,
                    cycle.error_message,
                    cycle.started_at,
                    cycle.completed_at,
                    cycle.updated_at,
                ),
            )

    def update_cycle(self, cycle: CollectionCycleRecord) -> None:
        cycle.updated_at = utc_now_iso()
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE ref_collection_cycles SET
                    status = ?, target_count = ?, free_allowance_start = ?, free_allowance_end = ?,
                    credits_start = ?, credits_end = ?, credits_spent = ?,
                    downloaded_count = ?, failed_count = ?, shortage_count = ?,
                    shortage_reason = ?, log_path = ?, error_message = ?,
                    completed_at = ?, updated_at = ?
                WHERE cycle_id = ?
                """,
                (
                    cycle.status,
                    cycle.target_count,
                    cycle.free_allowance_start,
                    cycle.free_allowance_end,
                    cycle.credits_start,
                    cycle.credits_end,
                    cycle.credits_spent,
                    cycle.downloaded_count,
                    cycle.failed_count,
                    cycle.shortage_count,
                    cycle.shortage_reason,
                    cycle.log_path,
                    cycle.error_message,
                    cycle.completed_at,
                    cycle.updated_at,
                    cycle.cycle_id,
                ),
            )

    def get_cycle(self, cycle_id: str) -> Optional[CollectionCycleRecord]:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM ref_collection_cycles WHERE cycle_id = ?", (cycle_id,)).fetchone()
            return CollectionCycleRecord(**dict(row)) if row else None

    def get_latest_cycle(self, cycle_date: Optional[str] = None) -> Optional[CollectionCycleRecord]:
        with self.db.connect() as conn:
            if cycle_date:
                row = conn.execute(
                    "SELECT * FROM ref_collection_cycles WHERE cycle_date = ? ORDER BY started_at DESC LIMIT 1",
                    (cycle_date,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM ref_collection_cycles ORDER BY started_at DESC LIMIT 1"
                ).fetchone()
            return CollectionCycleRecord(**dict(row)) if row else None

    def list_cycles(self, limit: int = 30) -> list[CollectionCycleRecord]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM ref_collection_cycles ORDER BY started_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [CollectionCycleRecord(**dict(r)) for r in rows]

    def get_incomplete_cycles(self) -> list[CollectionCycleRecord]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM ref_collection_cycles WHERE status IN ('PENDING', 'RUNNING') ORDER BY started_at ASC"
            ).fetchall()
            return [CollectionCycleRecord(**dict(r)) for r in rows]

    def enqueue_download_item(self, item: DownloadQueueItem) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO ref_download_queue (
                    queue_id, cycle_id, blendswap_id, file_id, slot_number, slot_reason,
                    selection_score, status, attempts, max_attempts,
                    download_url, download_url_expires_at, last_error, next_retry_at,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.queue_id,
                    item.cycle_id,
                    item.blendswap_id,
                    item.file_id,
                    item.slot_number,
                    item.slot_reason,
                    item.selection_score,
                    item.status,
                    item.attempts,
                    item.max_attempts,
                    item.download_url,
                    item.download_url_expires_at,
                    item.last_error,
                    item.next_retry_at,
                    item.created_at,
                    item.updated_at,
                ),
            )

    def update_queue_item(self, item: DownloadQueueItem) -> None:
        item.updated_at = utc_now_iso()
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE ref_download_queue SET
                    file_id = ?, status = ?, attempts = ?, download_url = ?,
                    download_url_expires_at = ?, last_error = ?, next_retry_at = ?, updated_at = ?
                WHERE queue_id = ?
                """,
                (
                    item.file_id,
                    item.status,
                    item.attempts,
                    item.download_url,
                    item.download_url_expires_at,
                    item.last_error,
                    item.next_retry_at,
                    item.updated_at,
                    item.queue_id,
                ),
            )

    def get_queue_for_cycle(self, cycle_id: str) -> list[DownloadQueueItem]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM ref_download_queue WHERE cycle_id = ? ORDER BY slot_number ASC",
                (cycle_id,),
            ).fetchall()
            return [DownloadQueueItem(**dict(r)) for r in rows]

    def record_download_history(
        self,
        *,
        history_id: str,
        cycle_id: Optional[str],
        blendswap_id: int,
        file_id: Optional[int],
        reason: str,
        charged: bool,
        credits_spent: int,
        credits_remaining: Optional[int],
        download_url_expires_at: Optional[str],
        local_path: Optional[str],
        sha256: Optional[str],
        file_size_bytes: int,
        success: bool,
        error_message: Optional[str] = None,
    ) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO ref_download_history (
                    history_id, cycle_id, blendswap_id, file_id, reason, charged,
                    credits_spent, credits_remaining, download_url_expires_at,
                    local_path, sha256, file_size_bytes, success, error_message, downloaded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    history_id,
                    cycle_id,
                    blendswap_id,
                    file_id,
                    reason,
                    1 if charged else 0,
                    credits_spent,
                    credits_remaining,
                    download_url_expires_at,
                    local_path,
                    sha256,
                    file_size_bytes,
                    1 if success else 0,
                    error_message,
                    utc_now_iso(),
                ),
            )

    def record_ticket_grant(
        self,
        blendswap_id: int,
        file_id: Optional[int],
        reason: str,
        expires_at: Optional[str],
    ) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO ref_download_tickets (blendswap_id, file_id, reason, expires_at, granted_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (blendswap_id, file_id, reason, expires_at, utc_now_iso()),
            )

    def has_free_repeat_window(self, blendswap_id: int, file_id: Optional[int], hours: int = 23) -> bool:
        """Return True if a free/repeat ticket or successful download occurred within the 24h repeat window."""
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).replace(microsecond=0).isoformat()
        with self.db.connect() as conn:
            if file_id is not None:
                row = conn.execute(
                    """
                    SELECT 1 FROM ref_download_tickets
                    WHERE blendswap_id = ? AND (file_id = ? OR file_id IS NULL)
                      AND reason IN ('free_allowance', 'repeat', 'own_asset')
                      AND granted_at >= ?
                    LIMIT 1
                    """,
                    (blendswap_id, file_id, cutoff),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT 1 FROM ref_download_tickets
                    WHERE blendswap_id = ? AND reason IN ('free_allowance', 'repeat', 'own_asset')
                      AND granted_at >= ?
                    LIMIT 1
                    """,
                    (blendswap_id, cutoff),
                ).fetchone()
            if row is not None:
                return True
        return self.was_file_downloaded_within_hours(blendswap_id, file_id, hours=hours)

    def was_file_downloaded_within_hours(self, blendswap_id: int, file_id: Optional[int], hours: int = 24) -> bool:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).replace(microsecond=0).isoformat()
        with self.db.connect() as conn:
            if file_id is not None:
                row = conn.execute(
                    """
                    SELECT 1 FROM ref_download_history
                    WHERE blendswap_id = ? AND file_id = ? AND success = 1 AND downloaded_at >= ?
                    LIMIT 1
                    """,
                    (blendswap_id, file_id, cutoff),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT 1 FROM ref_download_history
                    WHERE blendswap_id = ? AND success = 1 AND downloaded_at >= ?
                    LIMIT 1
                    """,
                    (blendswap_id, cutoff),
                ).fetchone()
            return row is not None

    def list_download_history(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM ref_download_history ORDER BY downloaded_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Benchmarks
    # ------------------------------------------------------------------
    def save_benchmark_report(self, report: BenchmarkReport) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO ref_benchmarks (
                    benchmark_id, branch1_asset_id, branch1_version, branch1_name,
                    branch1_category, grading_profile, methodology_version,
                    local_score, local_grade, reference_ids_json, report_json,
                    report_path, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(benchmark_id) DO UPDATE SET
                    report_json = excluded.report_json,
                    report_path = excluded.report_path
                """,
                (
                    report.benchmark_id,
                    report.branch1_asset_id,
                    report.branch1_version,
                    report.branch1_name,
                    report.branch1_category,
                    report.grading_profile,
                    report.methodology_version,
                    report.local_grade.total_score,
                    report.local_grade.grade_letter,
                    json.dumps([r.blendswap_id for r in report.selected_references]),
                    report.model_dump_json(),
                    report.report_path,
                    report.created_at,
                ),
            )

    def list_benchmark_reports(self, branch1_asset_id: Optional[str] = None, limit: int = 50) -> list[BenchmarkReport]:
        with self.db.connect() as conn:
            if branch1_asset_id:
                rows = conn.execute(
                    "SELECT report_json FROM ref_benchmarks WHERE branch1_asset_id = ? ORDER BY created_at DESC LIMIT ?",
                    (branch1_asset_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT report_json FROM ref_benchmarks ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [BenchmarkReport.model_validate_json(r["report_json"]) for r in rows]

    def get_benchmark_report(self, benchmark_id: str) -> Optional[BenchmarkReport]:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT report_json FROM ref_benchmarks WHERE benchmark_id = ?",
                (benchmark_id,),
            ).fetchone()
            return BenchmarkReport.model_validate_json(row["report_json"]) if row else None

    # ------------------------------------------------------------------
    # Row Deserializer
    # ------------------------------------------------------------------
    def _row_to_reference_asset(
        self,
        row: sqlite3.Row,
        inspection: Optional[ReferenceInspectionReport],
        grade: Optional[QualityGradeReport],
    ) -> ReferenceAssetRecord:
        return ReferenceAssetRecord(
            blendswap_id=int(row["blendswap_id"]),
            title=row["title"],
            url=row["url"],
            description=row["description"],
            asset_type=row["asset_type"],
            asset_type_label=row["asset_type_label"],
            author=BlendSwapAuthor(
                id=row["author_id"],
                username=row["author_username"],
                url=row["author_url"],
            ),
            license_assessment=LicenseAssessment.model_validate_json(row["license_json"]),
            blendswap_category=BlendSwapCategoryInfo.model_validate_json(row["blendswap_category_json"]),
            primary_category=row["primary_category"],
            technical_categories=json.loads(row["technical_categories_json"]),
            quality_category=row["quality_category"],
            tags=json.loads(row["tags_json"]),
            original_tags=json.loads(row["original_tags_json"]),
            blender_versions=json.loads(row["blender_versions_json"]),
            counts=BlendSwapCounts.model_validate_json(row["counts_json"]),
            explicit=bool(row["explicit"]),
            primary_format=row["primary_format"],
            is_native_blender=bool(row["is_native_blender"]),
            file_size_bytes=int(row["file_size_bytes"] or 0),
            files=[BlendSwapFileInfo.model_validate(f) for f in json.loads(row["files_json"])],
            local_dir=row["local_dir"],
            primary_file_path=row["primary_file_path"],
            blend_file_path=row["blend_file_path"],
            sha256=row["sha256"],
            md5=row["md5"],
            preview_paths=json.loads(row["preview_paths_json"]),
            metadata_path=row["metadata_path"],
            attribution_path=row["attribution_path"],
            status=ReferenceAssetStatus(row["status"]),
            error_message=row["error_message"],
            popularity_score=float(row["popularity_score"] or 0.0),
            selection_score=float(row["selection_score"] or 0.0),
            technical_quality_score=row["technical_quality_score"],
            reference_usefulness_score=row["reference_usefulness_score"],
            grade_letter=row["grade_letter"],
            inspection=inspection,
            grade=grade,
            drive_sync_status=DriveSyncStatus(row["drive_sync_status"]),
            drive_folder_id=row["drive_folder_id"],
            drive_files=[DriveFileRecord.model_validate(df) for df in json.loads(row["drive_files_json"])],
            blendswap_created_at=row["blendswap_created_at"],
            downloaded_at=row["downloaded_at"],
            inspected_at=row["inspected_at"],
            graded_at=row["graded_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
