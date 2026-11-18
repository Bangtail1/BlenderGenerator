"""Asset version repository: non-overwriting versioning and approved-version immutability."""

from __future__ import annotations

import sqlite3
from typing import Any, Optional

from ..db_manager import RobloxDatabaseManager
from ..models import (
    AssetNotFoundError,
    AssetVersionRecord,
    ExportFormat,
    ImmutableApprovedVersionError,
    ProductionStatus,
    ValidationStatus,
    utc_now_iso,
)
from .log_repo import LogRepository


class VersionRepository:
    """Manages immutable approved asset versions and sequential version registration."""

    def __init__(self, db: RobloxDatabaseManager) -> None:
        self.db = db
        self.logs = LogRepository(db)

    @staticmethod
    def _row_to_version(row: sqlite3.Row) -> AssetVersionRecord:
        return AssetVersionRecord(
            version_id=int(row["version_id"]),
            asset_id=row["asset_id"],
            version_number=int(row["version_number"]),
            blender_source_path=row["blender_source_path"],
            google_drive_file_id=row["google_drive_file_id"],
            google_drive_folder_id=row["google_drive_folder_id"],
            google_drive_url=row["google_drive_url"],
            export_format=row["export_format"],
            export_path=row["export_path"],
            change_description=row["change_description"],
            created_at=row["created_at"],
            qa_status=row["qa_status"],
            qa_score=float(row["qa_score"]) if row["qa_score"] is not None else None,
            is_approved=bool(row["is_approved"]),
            approved_by=row["approved_by"],
            approved_at=row["approved_at"],
            studio_verification_status=(
                row["studio_verification_status"]
                if "studio_verification_status" in row.keys() and row["studio_verification_status"]
                else "PENDING"
            ),
            sha256=row["sha256"],
        )

    def get_version(self, version_id: int) -> Optional[AssetVersionRecord]:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM asset_versions WHERE version_id = ?",
                (version_id,),
            ).fetchone()
            return self._row_to_version(row) if row else None

    def get_asset_version(self, asset_id: str, version_number: int) -> Optional[AssetVersionRecord]:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM asset_versions WHERE asset_id = ? AND version_number = ?",
                (asset_id, version_number),
            ).fetchone()
            return self._row_to_version(row) if row else None

    def get_latest_version(self, asset_id: str) -> Optional[AssetVersionRecord]:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM asset_versions WHERE asset_id = ? ORDER BY version_number DESC LIMIT 1",
                (asset_id,),
            ).fetchone()
            return self._row_to_version(row) if row else None

    def list_versions(self, asset_id: str) -> list[AssetVersionRecord]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM asset_versions WHERE asset_id = ? ORDER BY version_number ASC",
                (asset_id,),
            ).fetchall()
            return [self._row_to_version(r) for r in rows]

    def create_new_version(
        self,
        asset_id: str,
        *,
        blender_source_path: Optional[str] = None,
        google_drive_file_id: Optional[str] = None,
        google_drive_folder_id: Optional[str] = None,
        google_drive_url: Optional[str] = None,
        export_format: Optional[ExportFormat | str] = None,
        export_path: Optional[str] = None,
        change_description: str = "",
        sha256: Optional[str] = None,
        reset_status: ProductionStatus = ProductionStatus.MODELLING,
    ) -> AssetVersionRecord:
        """Register a new version retaining the permanent asset_id without overwriting prior versions."""
        now = utc_now_iso()
        fmt = (
            export_format.value
            if isinstance(export_format, ExportFormat)
            else (str(export_format).upper() if export_format else None)
        )
        with self.db.connect(immediate=True) as conn:
            a_row = conn.execute("SELECT * FROM assets WHERE asset_id = ?", (asset_id,)).fetchone()
            if not a_row:
                raise AssetNotFoundError(f"Asset '{asset_id}' not found.")

            max_row = conn.execute(
                "SELECT COALESCE(MAX(version_number), 0) AS max_ver FROM asset_versions WHERE asset_id = ?",
                (asset_id,),
            ).fetchone()
            next_ver = int(max_row["max_ver"]) + 1

            cur = conn.execute(
                """
                INSERT INTO asset_versions (
                    asset_id, version_number, blender_source_path,
                    google_drive_file_id, google_drive_folder_id, google_drive_url,
                    export_format, export_path, change_description,
                    created_at, qa_status, qa_score, is_approved, sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 0, ?)
                """,
                (
                    asset_id,
                    next_ver,
                    blender_source_path,
                    google_drive_file_id,
                    google_drive_folder_id,
                    google_drive_url,
                    fmt,
                    export_path,
                    change_description.strip() or f"Version v{next_ver:03d}",
                    now,
                    ValidationStatus.PENDING.value,
                    sha256,
                ),
            )

            # Update parent asset's active pointers while retaining permanent asset_id
            conn.execute(
                """
                UPDATE assets SET
                    current_version = ?,
                    production_status = ?,
                    source_blend_path = COALESCE(?, source_blend_path),
                    export_format = COALESCE(?, export_format),
                    export_path = COALESCE(?, export_path),
                    qa_status = 'PENDING',
                    qa_score = NULL,
                    qa_grade = NULL,
                    human_approved = 0,
                    approved_by = NULL,
                    approved_at = NULL,
                    updated_at = ?
                WHERE asset_id = ?
                """,
                (
                    next_ver,
                    reset_status.value,
                    blender_source_path,
                    fmt,
                    export_path,
                    now,
                    asset_id,
                ),
            )

            self.logs.log_event(
                asset_id=asset_id,
                process_name="version_registration",
                status="CREATED",
                message=f"Created new version v{next_ver:03d} for '{asset_id}': {change_description or 'update'}.",
                conn=conn,
            )
            row = conn.execute(
                "SELECT * FROM asset_versions WHERE version_id = ?",
                (cur.lastrowid,),
            ).fetchone()
            return self._row_to_version(row)

    def update_version(self, version_id: int, **fields: Any) -> AssetVersionRecord:
        """Update non-approved version metadata; blocks overwriting previously approved versions."""
        allowed = {
            "blender_source_path",
            "google_drive_file_id",
            "google_drive_folder_id",
            "google_drive_url",
            "export_format",
            "export_path",
            "change_description",
            "qa_status",
            "qa_score",
            "sha256",
        }
        unknown = set(fields.keys()) - allowed
        if unknown:
            raise ValueError(f"Unsupported version fields: {sorted(unknown)}")

        with self.db.connect(immediate=True) as conn:
            row = conn.execute(
                "SELECT * FROM asset_versions WHERE version_id = ?",
                (version_id,),
            ).fetchone()
            if not row:
                raise KeyError(f"Version ID {version_id} not found.")
            if bool(row["is_approved"]):
                # Allow attaching Drive metadata only if not overwriting existing file/source paths
                protected_keys = {"blender_source_path", "export_path", "qa_status", "qa_score", "sha256"}
                if any(k in protected_keys for k in fields):
                    raise ImmutableApprovedVersionError(
                        f"Version v{row['version_number']:03d} of '{row['asset_id']}' is already APPROVED and immutable. "
                        "Create a new version instead."
                    )

            if not fields:
                return self._row_to_version(row)

            assignments = ", ".join(f"{k} = ?" for k in fields.keys())
            values = list(fields.values()) + [version_id]
            conn.execute(f"UPDATE asset_versions SET {assignments} WHERE version_id = ?", tuple(values))
            updated = conn.execute(
                "SELECT * FROM asset_versions WHERE version_id = ?",
                (version_id,),
            ).fetchone()
            return self._row_to_version(updated)

    def mark_version_approved(
        self,
        asset_id: str,
        version_number: int,
        *,
        approved_by: str,
        approved_at: Optional[str] = None,
    ) -> AssetVersionRecord:
        """Human-only final approval gate; rejects any version that has not passed QA."""
        reviewer = approved_by.strip()
        if not reviewer:
            raise ValueError("approved_by (human reviewer identity) is required.")
        ts = approved_at or utc_now_iso()

        with self.db.connect(immediate=True) as conn:
            a_row = conn.execute("SELECT * FROM assets WHERE asset_id = ?", (asset_id,)).fetchone()
            if not a_row:
                raise AssetNotFoundError(f"Asset '{asset_id}' not found.")
            v_row = conn.execute(
                "SELECT * FROM asset_versions WHERE asset_id = ? AND version_number = ?",
                (asset_id, version_number),
            ).fetchone()
            if not v_row:
                raise KeyError(f"Version v{version_number:03d} not found for asset '{asset_id}'.")

            if v_row["qa_status"] not in ("QA_PASSED", "PASSED", "AWAITING_HUMAN_APPROVAL", "APPROVED"):
                raise ValueError(
                    f"Cannot approve '{asset_id}' v{version_number:03d}: QA status is '{v_row['qa_status']}'."
                )
            if a_row["production_status"] in ("QA_FAILED", "REJECTED"):
                raise ValueError(
                    f"Cannot approve '{asset_id}': asset production_status is '{a_row['production_status']}'."
                )

            conn.execute(
                """
                UPDATE asset_versions
                SET is_approved = 1, approved_by = ?, approved_at = ?, qa_status = 'APPROVED'
                WHERE version_id = ?
                """,
                (reviewer, ts, v_row["version_id"]),
            )
            conn.execute(
                """
                UPDATE assets
                SET human_approved = 1,
                    approved_by = ?,
                    approved_at = ?,
                    production_status = 'APPROVED',
                    qa_status = 'APPROVED',
                    current_version = ?,
                    updated_at = ?
                WHERE asset_id = ?
                """,
                (reviewer, ts, version_number, ts, asset_id),
            )
            self.logs.log_event(
                asset_id=asset_id,
                process_name="human_approval",
                status="APPROVED",
                message=f"Human approval recorded for v{version_number:03d} by '{reviewer}'.",
                conn=conn,
            )
            updated = conn.execute(
                "SELECT * FROM asset_versions WHERE version_id = ?",
                (v_row["version_id"],),
            ).fetchone()
            return self._row_to_version(updated)
