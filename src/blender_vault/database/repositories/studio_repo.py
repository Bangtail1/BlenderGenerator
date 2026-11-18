"""Roblox Studio manual verification history repository."""

from __future__ import annotations

import json
import sqlite3
from typing import Optional

from ..db_manager import RobloxDatabaseManager
from ..models import (
    AssetNotFoundError,
    StudioVerificationRecord,
    StudioVerificationStatus,
    utc_now_iso,
)
from .log_repo import LogRepository


class StudioVerificationRepository:
    """Persists manual Roblox Studio import, scale, orientation, material & collision checks per version."""

    def __init__(self, db: RobloxDatabaseManager) -> None:
        self.db = db
        self.logs = LogRepository(db)

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> StudioVerificationRecord:
        ver_num = int(row["version_number"]) if "version_number" in row.keys() and row["version_number"] is not None else None
        return StudioVerificationRecord(
            verification_id=int(row["verification_id"]),
            asset_id=row["asset_id"],
            version_id=int(row["version_id"]),
            version_number=ver_num,
            status=StudioVerificationStatus(row["status"]),
            fbx_import_ok=bool(row["fbx_import_ok"]),
            orientation_ok=bool(row["orientation_ok"]),
            scale_ok=bool(row["scale_ok"]),
            material_appearance_ok=bool(row["material_appearance_ok"]),
            collision_test_ok=bool(row["collision_test_ok"]),
            studio_warnings=json.loads(row["studio_warnings"] or "[]"),
            studio_errors=json.loads(row["studio_errors"] or "[]"),
            notes=row["notes"] or "",
            verified_by=row["verified_by"],
            verified_at=row["verified_at"],
        )

    def record_verification(
        self,
        asset_id: str,
        *,
        version_number: Optional[int] = None,
        fbx_import_ok: bool,
        orientation_ok: bool,
        scale_ok: bool,
        material_appearance_ok: bool,
        collision_test_ok: bool,
        verified_by: str,
        studio_warnings: Optional[list[str]] = None,
        studio_errors: Optional[list[str]] = None,
        notes: str = "",
    ) -> StudioVerificationRecord:
        """Record actual human Roblox Studio verification results against the exact asset version."""
        clean_verifier = (verified_by or "").strip()
        if not clean_verifier:
            raise ValueError("verified_by (human tester identity) is required for Roblox Studio verification.")

        warnings = [w.strip() for w in (studio_warnings or []) if w and w.strip()]
        errors = [e.strip() for e in (studio_errors or []) if e and e.strip()]
        all_checks_ok = all(
            (fbx_import_ok, orientation_ok, scale_ok, material_appearance_ok, collision_test_ok)
        )

        if not all_checks_ok or errors:
            status = StudioVerificationStatus.FAILED
        elif warnings:
            status = StudioVerificationStatus.PASSED_WITH_WARNINGS
        else:
            status = StudioVerificationStatus.PASSED

        ts = utc_now_iso()
        with self.db.connect(immediate=True) as conn:
            a_row = conn.execute("SELECT current_version FROM assets WHERE asset_id = ?", (asset_id,)).fetchone()
            if not a_row:
                raise AssetNotFoundError(f"Asset '{asset_id}' not found.")
            target_ver = version_number if version_number is not None else int(a_row["current_version"])
            v_row = conn.execute(
                "SELECT version_id, version_number FROM asset_versions WHERE asset_id = ? AND version_number = ?",
                (asset_id, target_ver),
            ).fetchone()
            if not v_row:
                raise KeyError(f"Version v{target_ver:03d} not found for asset '{asset_id}'.")
            version_id = int(v_row["version_id"])

            cur = conn.execute(
                """
                INSERT INTO roblox_studio_verifications (
                    asset_id, version_id, status,
                    fbx_import_ok, orientation_ok, scale_ok,
                    material_appearance_ok, collision_test_ok,
                    studio_warnings, studio_errors, notes, verified_by, verified_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    asset_id,
                    version_id,
                    status.value,
                    1 if fbx_import_ok else 0,
                    1 if orientation_ok else 0,
                    1 if scale_ok else 0,
                    1 if material_appearance_ok else 0,
                    1 if collision_test_ok else 0,
                    json.dumps(warnings),
                    json.dumps(errors),
                    (notes or "").strip(),
                    clean_verifier,
                    ts,
                ),
            )
            conn.execute(
                "UPDATE asset_versions SET studio_verification_status = ? WHERE version_id = ?",
                (status.value, version_id),
            )
            self.logs.log_event(
                asset_id=asset_id,
                process_name="roblox_studio_verification",
                status=status.value,
                message=(
                    f"Roblox Studio manual verification for v{target_ver:03d} by '{clean_verifier}': "
                    f"{status.value} (fbx={fbx_import_ok}, orient={orientation_ok}, scale={scale_ok}, "
                    f"mat={material_appearance_ok}, col={collision_test_ok})."
                ),
                error_details="; ".join(errors) if errors else None,
                conn=conn,
            )
            row = conn.execute(
                """
                SELECT rsv.*, av.version_number
                FROM roblox_studio_verifications rsv
                JOIN asset_versions av ON av.version_id = rsv.version_id
                WHERE rsv.verification_id = ?
                """,
                (cur.lastrowid,),
            ).fetchone()
            return self._row_to_record(row)

    def list_verifications(self, asset_id: str, *, version_id: Optional[int] = None) -> list[StudioVerificationRecord]:
        with self.db.connect() as conn:
            if version_id is not None:
                rows = conn.execute(
                    """
                    SELECT rsv.*, av.version_number
                    FROM roblox_studio_verifications rsv
                    JOIN asset_versions av ON av.version_id = rsv.version_id
                    WHERE rsv.asset_id = ? AND rsv.version_id = ?
                    ORDER BY rsv.verification_id DESC
                    """,
                    (asset_id, version_id),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT rsv.*, av.version_number
                    FROM roblox_studio_verifications rsv
                    JOIN asset_versions av ON av.version_id = rsv.version_id
                    WHERE rsv.asset_id = ?
                    ORDER BY rsv.verification_id DESC
                    """,
                    (asset_id,),
                ).fetchall()
            return [self._row_to_record(r) for r in rows]

    def get_latest_verification(self, asset_id: str, *, version_id: Optional[int] = None) -> Optional[StudioVerificationRecord]:
        items = self.list_verifications(asset_id, version_id=version_id)
        return items[0] if items else None
