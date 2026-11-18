"""QA reports repository for storing 100-point Roblox QA scores and syncing asset summary state."""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Optional

from ..db_manager import RobloxDatabaseManager
from ..models import (
    AssetNotFoundError,
    ImmutableApprovedVersionError,
    ProductionStatus,
    QAReportRecord,
    utc_now_iso,
)
from .log_repo import LogRepository


class QAReportRepository:
    """Persists detailed 10-dimension QA reports and updates asset/version QA status atomically."""

    def __init__(self, db: RobloxDatabaseManager) -> None:
        self.db = db
        self.logs = LogRepository(db)

    @staticmethod
    def _row_to_report(row: sqlite3.Row) -> QAReportRecord:
        return QAReportRecord(
            report_id=int(row["report_id"]),
            asset_id=row["asset_id"],
            version_id=int(row["version_id"]),
            overall_score=float(row["overall_score"]),
            mesh_integrity_score=float(row["mesh_integrity_score"]),
            geometry_score=float(row["geometry_score"]),
            polygon_efficiency_score=float(row["polygon_efficiency_score"]),
            uv_score=float(row["uv_score"]),
            material_score=float(row["material_score"]),
            scale_score=float(row["scale_score"]),
            roblox_compatibility_score=float(row["roblox_compatibility_score"]),
            collision_score=float(row["collision_score"]),
            visual_score=float(row["visual_score"]),
            organisation_score=float(row["organisation_score"]),
            final_status=row["final_status"],
            grade=row["grade"],
            failure_reasons=json.loads(row["failure_reasons"] or "[]"),
            recommendations=json.loads(row["recommendations"] or "[]"),
            warnings=json.loads(row["warnings_json"] or "[]"),
            report_json_path=row["report_json_path"],
            report_html_path=row["report_html_path"],
            details=json.loads(row["details_json"] or "{}"),
            test_timestamp=row["test_timestamp"],
        )

    def save_qa_report(
        self,
        *,
        asset_id: str,
        version_id: int,
        overall_score: float,
        mesh_integrity_score: float,
        geometry_score: float,
        polygon_efficiency_score: float,
        uv_score: float,
        material_score: float,
        scale_score: float,
        roblox_compatibility_score: float,
        collision_score: float,
        visual_score: float,
        organisation_score: float,
        final_status: str,
        grade: Optional[str] = None,
        failure_reasons: Optional[list[str]] = None,
        recommendations: Optional[list[str]] = None,
        warnings: Optional[list[str]] = None,
        report_json_path: Optional[str] = None,
        report_html_path: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
        asset_metrics: Optional[dict[str, Any]] = None,
        target_production_status: Optional[ProductionStatus] = None,
        test_timestamp: Optional[str] = None,
    ) -> QAReportRecord:
        """Store a QA report and update asset & version QA summary fields transactionally."""
        ts = test_timestamp or utc_now_iso()
        fails = list(failure_reasons or [])
        recs = list(recommendations or [])
        warns = list(warnings or [])
        det = dict(details or {})
        metrics = dict(asset_metrics or {})

        with self.db.connect(immediate=True) as conn:
            a_row = conn.execute("SELECT * FROM assets WHERE asset_id = ?", (asset_id,)).fetchone()
            if not a_row:
                raise AssetNotFoundError(f"Asset '{asset_id}' not found.")
            v_row = conn.execute(
                "SELECT * FROM asset_versions WHERE version_id = ? AND asset_id = ?",
                (version_id, asset_id),
            ).fetchone()
            if not v_row:
                raise KeyError(f"Version ID {version_id} not found for asset '{asset_id}'.")
            if bool(v_row["is_approved"]):
                raise ImmutableApprovedVersionError(
                    f"Cannot overwrite QA report for already APPROVED version v{v_row['version_number']:03d}."
                )

            cur = conn.execute(
                """
                INSERT INTO qa_reports (
                    asset_id, version_id, overall_score,
                    mesh_integrity_score, geometry_score, polygon_efficiency_score,
                    uv_score, material_score, scale_score,
                    roblox_compatibility_score, collision_score, visual_score,
                    organisation_score, final_status, grade,
                    failure_reasons, recommendations, warnings_json,
                    report_json_path, report_html_path, details_json, test_timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    asset_id,
                    version_id,
                    round(float(overall_score), 2),
                    round(float(mesh_integrity_score), 2),
                    round(float(geometry_score), 2),
                    round(float(polygon_efficiency_score), 2),
                    round(float(uv_score), 2),
                    round(float(material_score), 2),
                    round(float(scale_score), 2),
                    round(float(roblox_compatibility_score), 2),
                    round(float(collision_score), 2),
                    round(float(visual_score), 2),
                    round(float(organisation_score), 2),
                    final_status,
                    grade,
                    json.dumps(fails),
                    json.dumps(recs),
                    json.dumps(warns),
                    report_json_path,
                    report_html_path,
                    json.dumps(det),
                    ts,
                ),
            )

            conn.execute(
                "UPDATE asset_versions SET qa_status = ?, qa_score = ? WHERE version_id = ?",
                (final_status, round(float(overall_score), 2), version_id),
            )

            new_prod_status = target_production_status.value if target_production_status else (
                ProductionStatus.AWAITING_HUMAN_APPROVAL.value
                if final_status in ("QA_PASSED", "AWAITING_HUMAN_APPROVAL")
                else ProductionStatus.QA_FAILED.value
            )

            conn.execute(
                """
                UPDATE assets SET
                    qa_score = ?,
                    qa_status = ?,
                    qa_grade = ?,
                    production_status = ?,
                    triangle_count = COALESCE(?, triangle_count),
                    vertex_count = COALESCE(?, vertex_count),
                    polygon_count = COALESCE(?, polygon_count),
                    dimensions_x = COALESCE(?, dimensions_x),
                    dimensions_y = COALESCE(?, dimensions_y),
                    dimensions_z = COALESCE(?, dimensions_z),
                    texture_resolution = COALESCE(?, texture_resolution),
                    material_count = COALESCE(?, material_count),
                    uv_status = COALESCE(?, uv_status),
                    collision_status = COALESCE(?, collision_status),
                    export_format = COALESCE(?, export_format),
                    export_path = COALESCE(?, export_path),
                    updated_at = ?
                WHERE asset_id = ?
                """,
                (
                    round(float(overall_score), 2),
                    final_status,
                    grade,
                    new_prod_status,
                    metrics.get("triangle_count"),
                    metrics.get("vertex_count"),
                    metrics.get("polygon_count"),
                    metrics.get("dimensions_x"),
                    metrics.get("dimensions_y"),
                    metrics.get("dimensions_z"),
                    metrics.get("texture_resolution"),
                    metrics.get("material_count"),
                    metrics.get("uv_status"),
                    metrics.get("collision_status"),
                    metrics.get("export_format"),
                    metrics.get("export_path"),
                    ts,
                    asset_id,
                ),
            )

            self.logs.log_event(
                asset_id=asset_id,
                process_name="qa_evaluation",
                status=final_status,
                message=f"QA score {overall_score:.1f}/100 (Grade {grade or '?'}) -> {final_status}.",
                error_details="; ".join(fails) if fails else None,
                conn=conn,
            )

            row = conn.execute(
                "SELECT * FROM qa_reports WHERE report_id = ?",
                (cur.lastrowid,),
            ).fetchone()
            return self._row_to_report(row)

    def get_report(self, report_id: int) -> Optional[QAReportRecord]:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM qa_reports WHERE report_id = ?", (report_id,)).fetchone()
            return self._row_to_report(row) if row else None

    def get_latest_report_for_asset(self, asset_id: str) -> Optional[QAReportRecord]:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM qa_reports WHERE asset_id = ? ORDER BY report_id DESC LIMIT 1",
                (asset_id,),
            ).fetchone()
            return self._row_to_report(row) if row else None

    def list_reports_for_asset(self, asset_id: str) -> list[QAReportRecord]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM qa_reports WHERE asset_id = ? ORDER BY report_id DESC",
                (asset_id,),
            ).fetchall()
            return [self._row_to_report(r) for r in rows]
