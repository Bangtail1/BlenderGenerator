"""Integrated Phase 6 & 7 workflow: QA <-> SQLite persistence, Reports, Human Approval, and Drive Sync."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from ..config import Settings
from ..database import (
    AssetRepository,
    CategoryRepository,
    ExportFormat,
    ImmutableApprovedVersionError,
    LogRepository,
    ProductionStatus,
    QAReportRecord,
    QAReportRepository,
    RobloxAssetRecord,
    RobloxDatabaseManager,
    StudioVerificationRecord,
    StudioVerificationRepository,
    VersionRepository,
)
from ..hashing import compute_sha256
from ..reports.report_generator import QAReportGenerator
from ..storage.sync_manager import RobloxDriveSyncManager
from .models import RobloxQAOutput
from .qa_engine import RobloxQAEngine

logger = logging.getLogger("blender_vault.roblox_qa.workflow")


class RobloxWorkflowService:
    """Orchestrates QA execution, SQLite persistence, HTML/JSON reporting, human approval, and Drive sync."""

    def __init__(
        self,
        settings: Settings,
        *,
        roblox_db: Optional[RobloxDatabaseManager] = None,
        qa_engine: Optional[RobloxQAEngine] = None,
        report_generator: Optional[QAReportGenerator] = None,
        sync_manager: Optional[RobloxDriveSyncManager] = None,
    ) -> None:
        self.settings = settings
        self.db = roblox_db or RobloxDatabaseManager(
            settings.resolved_roblox_db_path,
            backups_dir=settings.resolved_roblox_backups_dir,
        )
        self.categories = CategoryRepository(self.db)
        self.assets = AssetRepository(self.db)
        self.versions = VersionRepository(self.db)
        self.qa_reports = QAReportRepository(self.db)
        self.logs = LogRepository(self.db)
        self.studio_verifications = StudioVerificationRepository(self.db)

        inv_path = settings.roblox_master_inventory_path
        if inv_path.is_file() and len(self.categories.list_categories()) < 40:
            self.categories.seed_master_inventory(inv_path)

        self.qa_engine = qa_engine or RobloxQAEngine(settings)
        self.report_generator = report_generator or QAReportGenerator(settings.resolved_roblox_reports_dir)
        self.sync_manager = sync_manager

    def run_qa_for_asset(
        self,
        asset_id: str,
        *,
        version_number: Optional[int] = None,
        render_previews: bool = True,
        export_format: Optional[str] = None,
        tier_override: Optional[str] = None,
    ) -> dict[str, Any]:
        """Run full Roblox QA for an asset version, generate JSON/HTML reports, and update SQLite."""
        asset = self.assets.get_asset(asset_id)
        if not asset:
            raise KeyError(f"Asset '{asset_id}' not found in Roblox DB.")

        ver_num = version_number if version_number is not None else asset.current_version
        version = self.versions.get_asset_version(asset_id, ver_num)
        if not version:
            raise KeyError(f"Version v{ver_num:03d} not found for asset '{asset_id}'.")
        if version.is_approved:
            raise ImmutableApprovedVersionError(
                f"Version v{ver_num:03d} of '{asset_id}' is already APPROVED and immutable. "
                "Create a new version before re-running QA."
            )

        blend_str = version.blender_source_path or asset.source_blend_path
        if not blend_str or not Path(blend_str).is_file():
            raise FileNotFoundError(f"Blender source file missing for '{asset_id}' v{ver_num:03d}: {blend_str}")
        blend_path = Path(blend_str).resolve()

        # Safe status transition: -> QA_PENDING -> QA_RUNNING
        if asset.production_status != ProductionStatus.QA_PENDING:
            if asset.production_status != ProductionStatus.QA_RUNNING:
                self.assets.transition_status(
                    asset_id,
                    ProductionStatus.QA_PENDING,
                    process_name="qa_pipeline",
                    message=f"Queued v{ver_num:03d} for automated Roblox QA.",
                )
        if self.assets.get_asset(asset_id).production_status != ProductionStatus.QA_RUNNING:
            self.assets.transition_status(
                asset_id,
                ProductionStatus.QA_RUNNING,
                process_name="qa_pipeline",
                message=f"Running automated Roblox QA for v{ver_num:03d}.",
            )

        try:
            cat = self.categories.get_by_id(asset.category_id)
            dept_code = cat.category_code if cat else "PRP"
            fmt = (
                export_format
                or version.export_format
                or (asset.export_format.value if asset.export_format else None)
                or self.settings.roblox_default_export_format
                or "FBX"
            ).upper()

            # Place export alongside source blend or inside output/<asset_id>/v<ver>/
            if version.export_path and Path(version.export_path).suffix.upper() == f".{fmt}":
                target_export = Path(version.export_path)
            else:
                target_export = blend_path.parent / f"{blend_path.stem}.{fmt.lower()}"
                if "samples" in target_export.parts:
                    target_export = self.settings.resolved_roblox_output_dir / asset_id / f"v{ver_num:03d}" / f"{blend_path.stem}.{fmt.lower()}"

            qa_out = self.qa_engine.evaluate_blend(
                blend_path=blend_path,
                asset_id=asset_id,
                asset_name=asset.asset_name,
                asset_version=ver_num,
                department_code=dept_code,
                export_path=target_export,
                export_format=fmt,
                tier_override=tier_override,
                render_previews=render_previews,
            )

            # Generate QA_Report.json and QA_Report.html with previews
            existing_studio = self.studio_verifications.get_latest_verification(asset_id, version_id=version.version_id)
            report_paths = self.report_generator.generate_reports(
                qa_out,
                final_approval_status=(
                    "AWAITING_HUMAN_APPROVAL" if qa_out.final_status == "QA_PASSED" else "QA_FAILED"
                ),
                google_drive_file_id=version.google_drive_file_id,
                google_drive_url=version.google_drive_url,
                studio_verification=existing_studio.model_dump(mode="json") if existing_studio else None,
            )

            scores = qa_out.individual_scores
            target_status = (
                ProductionStatus.QA_PASSED
                if qa_out.final_status == "QA_PASSED"
                else ProductionStatus.QA_FAILED
            )
            export_actual = qa_out.export_validation.get("export_path")

            # Save version export path before saving QA report if not approved
            if not version.is_approved and export_actual:
                self.versions.update_version(
                    version.version_id,
                    export_path=export_actual,
                    export_format=fmt,
                )

            db_report = self.qa_reports.save_qa_report(
                asset_id=asset_id,
                version_id=version.version_id,
                overall_score=qa_out.overall_score,
                mesh_integrity_score=scores["mesh_integrity"],
                geometry_score=scores["geometry_topology"],
                polygon_efficiency_score=scores["polygon_efficiency"],
                uv_score=scores["uv_quality"],
                material_score=scores["materials_textures"],
                scale_score=scores["proportions_scale"],
                roblox_compatibility_score=scores["roblox_compatibility"],
                collision_score=scores["collision_usability"],
                visual_score=scores["visual_quality"],
                organisation_score=scores["organisation_naming"],
                final_status=qa_out.final_status,
                grade=qa_out.grade,
                failure_reasons=qa_out.mandatory_failures,
                recommendations=qa_out.recommendations,
                warnings=qa_out.warnings,
                report_json_path=str(report_paths["json"]),
                report_html_path=str(report_paths["html"]),
                details=qa_out.model_dump(),
                asset_metrics={
                    "triangle_count": qa_out.triangle_count,
                    "vertex_count": qa_out.vertex_count,
                    "polygon_count": qa_out.polygon_count,
                    "dimensions_x": qa_out.dimensions_meters[0] if len(qa_out.dimensions_meters) >= 1 else 0.0,
                    "dimensions_y": qa_out.dimensions_meters[1] if len(qa_out.dimensions_meters) >= 2 else 0.0,
                    "dimensions_z": qa_out.dimensions_meters[2] if len(qa_out.dimensions_meters) >= 3 else 0.0,
                    "texture_resolution": qa_out.texture_info.get("resolution", ""),
                    "material_count": qa_out.material_info.get("material_count", 0),
                    "uv_status": qa_out.uv_status,
                    "collision_status": qa_out.collision_assessment.collision_status,
                    "export_format": fmt,
                    "export_path": export_actual,
                },
                target_production_status=target_status,
                test_timestamp=qa_out.test_timestamp,
            )

            # Follow workflow: QA_PASSED -> AWAITING_HUMAN_APPROVAL
            if qa_out.final_status == "QA_PASSED":
                self.assets.transition_status(
                    asset_id,
                    ProductionStatus.AWAITING_HUMAN_APPROVAL,
                    process_name="qa_pipeline",
                    message=f"QA passed ({qa_out.overall_score:.1f}/100, Grade {qa_out.grade}); awaiting human approval.",
                )

            return {
                "asset": self.assets.get_asset(asset_id),
                "version": self.versions.get_version(version.version_id),
                "qa_output": qa_out,
                "qa_report": db_report,
                "report_paths": report_paths,
            }
        except Exception as exc:
            try:
                self.logs.log_event(
                    asset_id=asset_id,
                    process_name="qa_pipeline",
                    status="ERROR",
                    message=f"Unhandled error during Roblox QA for v{ver_num:03d}: {exc}",
                    error_details=repr(exc),
                )
                cur_asset = self.assets.get_asset(asset_id)
                if cur_asset and cur_asset.production_status == ProductionStatus.QA_RUNNING:
                    self.assets.transition_status(
                        asset_id,
                        ProductionStatus.QA_PENDING,
                        process_name="qa_pipeline",
                        message=f"Reverted v{ver_num:03d} to QA_PENDING after QA execution error.",
                        error_details=str(exc),
                    )
            except Exception as recovery_exc:
                logger.exception("Failed QA error recovery for %s: %s", asset_id, recovery_exc)
            raise

    def approve_asset(
        self,
        asset_id: str,
        *,
        version_number: Optional[int] = None,
        reviewer: str,
        notes: str = "",
        sync_to_drive: bool = True,
    ) -> dict[str, Any]:
        """Human-only final production acceptance gate; updates SQLite, reports, and Drive production folder."""
        clean_reviewer = (reviewer or "").strip()
        if not clean_reviewer:
            raise ValueError("Human reviewer identity is required for final approval.")

        asset = self.assets.get_asset(asset_id)
        if not asset:
            raise KeyError(f"Asset '{asset_id}' not found.")
        ver_num = version_number if version_number is not None else asset.current_version
        version = self.versions.get_asset_version(asset_id, ver_num)
        if not version:
            raise KeyError(f"Version v{ver_num:03d} not found for '{asset_id}'.")

        latest_qa = self.qa_reports.get_latest_report_for_asset(asset_id)
        if not latest_qa or latest_qa.version_id != version.version_id:
            raise ValueError(f"Cannot approve '{asset_id}' v{ver_num:03d} without completed QA for this version.")
        if latest_qa.final_status != "QA_PASSED" or latest_qa.grade not in {"A", "B"} or latest_qa.failure_reasons:
            raise ValueError(
                f"Cannot approve '{asset_id}' v{ver_num:03d}: QA failed or did not achieve Grade A/B with zero mandatory failures."
            )
        if asset.production_status not in {ProductionStatus.QA_PASSED, ProductionStatus.AWAITING_HUMAN_APPROVAL}:
            raise ValueError(
                f"Asset '{asset_id}' in status '{asset.production_status.value}' cannot be approved."
            )

        approved_ver = self.versions.mark_version_approved(
            asset_id,
            ver_num,
            approved_by=clean_reviewer,
        )
        if notes.strip():
            self.logs.log_event(
                asset_id=asset_id,
                process_name="human_approval",
                status="NOTE",
                message=f"Approval notes by {clean_reviewer}: {notes.strip()}",
            )

        # Regenerate QA reports reflecting APPROVED status
        qa_out = RobloxQAOutput.model_validate(latest_qa.details) if latest_qa.details else None
        existing_studio = self.studio_verifications.get_latest_verification(asset_id, version_id=approved_ver.version_id)
        report_paths: dict[str, Path] = {}
        if qa_out is not None:
            report_paths = self.report_generator.generate_reports(
                qa_out,
                final_approval_status="APPROVED",
                approved_by=approved_ver.approved_by,
                approved_at=approved_ver.approved_at,
                google_drive_file_id=approved_ver.google_drive_file_id,
                google_drive_url=approved_ver.google_drive_url,
                studio_verification=existing_studio.model_dump(mode="json") if existing_studio else None,
            )

        drive_result: Optional[dict[str, Any]] = None
        if sync_to_drive and self.sync_manager is not None:
            extra = []
            if "json" in report_paths:
                extra.append(("qa_report_json", report_paths["json"]))
            if "html" in report_paths:
                extra.append(("qa_report_html", report_paths["html"]))
            drive_result = self.sync_manager.sync_asset_version(
                asset_id,
                ver_num,
                require_approved_for_production=True,
                queue_on_failure=True,
                extra_files=extra,
            )

        return {
            "asset": self.assets.get_asset(asset_id),
            "version": self.versions.get_version(approved_ver.version_id),
            "report_paths": report_paths,
            "drive_sync": drive_result,
        }

    def reject_asset(
        self,
        asset_id: str,
        *,
        version_number: Optional[int] = None,
        reviewer: str,
        reason: str = "",
    ) -> RobloxAssetRecord:
        """Reject an asset in human review while preserving local development files intact."""
        asset = self.assets.get_asset(asset_id)
        if not asset:
            raise KeyError(f"Asset '{asset_id}' not found.")
        ver_num = version_number if version_number is not None else asset.current_version
        version = self.versions.get_asset_version(asset_id, ver_num)
        if not version:
            raise KeyError(f"Version v{ver_num:03d} not found for '{asset_id}'.")

        updated = self.assets.transition_status(
            asset_id,
            ProductionStatus.REJECTED,
            process_name="human_rejection",
            message=f"Rejected v{ver_num:03d} by {reviewer.strip() or 'Reviewer'}: {reason.strip() or 'Needs improvement'}.",
        )
        if not version.is_approved:
            self.versions.update_version(version.version_id, qa_status="REJECTED")
        latest_qa = next(
            (r for r in self.qa_reports.list_reports_for_asset(asset_id) if r.version_id == version.version_id),
            None,
        )
        if latest_qa and latest_qa.details:
            qa_out = RobloxQAOutput.model_validate(latest_qa.details)
            existing_studio = self.studio_verifications.get_latest_verification(asset_id, version_id=version.version_id)
            self.report_generator.generate_reports(
                qa_out,
                final_approval_status="REJECTED",
                google_drive_file_id=version.google_drive_file_id,
                google_drive_url=version.google_drive_url,
                studio_verification=existing_studio.model_dump(mode="json") if existing_studio else None,
            )
        return updated

    def record_studio_verification(
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
    ) -> dict[str, Any]:
        """Record actual human Roblox Studio verification checks for a specific asset version."""
        asset = self.assets.get_asset(asset_id)
        if not asset:
            raise KeyError(f"Asset '{asset_id}' not found.")
        ver_num = version_number if version_number is not None else asset.current_version
        version = self.versions.get_asset_version(asset_id, ver_num)
        if not version:
            raise KeyError(f"Version v{ver_num:03d} not found for '{asset_id}'.")

        rec = self.studio_verifications.record_verification(
            asset_id,
            version_number=ver_num,
            fbx_import_ok=fbx_import_ok,
            orientation_ok=orientation_ok,
            scale_ok=scale_ok,
            material_appearance_ok=material_appearance_ok,
            collision_test_ok=collision_test_ok,
            verified_by=verified_by,
            studio_warnings=studio_warnings,
            studio_errors=studio_errors,
            notes=notes,
        )

        # Refresh reports for this version if a QA report exists
        ver_report = next(
            (r for r in self.qa_reports.list_reports_for_asset(asset_id) if r.version_id == version.version_id),
            None,
        )
        report_paths: dict[str, Path] = {}
        if ver_report and ver_report.details:
            qa_out = RobloxQAOutput.model_validate(ver_report.details)
            if version.is_approved:
                approval_state = "APPROVED"
            elif version.qa_status == "REJECTED":
                approval_state = "REJECTED"
            elif ver_report.final_status == "QA_PASSED":
                approval_state = "AWAITING_HUMAN_APPROVAL"
            else:
                approval_state = "QA_FAILED"
            report_paths = self.report_generator.generate_reports(
                qa_out,
                final_approval_status=approval_state,
                approved_by=version.approved_by,
                approved_at=version.approved_at,
                google_drive_file_id=version.google_drive_file_id,
                google_drive_url=version.google_drive_url,
                studio_verification=rec.model_dump(mode="json"),
            )

        return {
            "asset": self.assets.get_asset(asset_id),
            "version": self.versions.get_version(version.version_id),
            "studio_verification": rec,
            "report_paths": report_paths,
        }

    def create_modified_version(
        self,
        asset_id: str,
        *,
        new_blend_path: Path,
        change_description: str,
        export_format: Optional[ExportFormat | str] = None,
    ) -> dict[str, Any]:
        """Create a new version for an existing asset without overwriting prior approved versions."""
        blend_file = Path(new_blend_path).resolve()
        if not blend_file.is_file():
            raise FileNotFoundError(f"New version .blend file not found: {blend_file}")
        sha = compute_sha256(blend_file)
        new_ver = self.versions.create_new_version(
            asset_id,
            blender_source_path=str(blend_file),
            export_format=export_format,
            change_description=change_description,
            sha256=sha,
            reset_status=ProductionStatus.MODELLING,
        )
        return {
            "asset": self.assets.get_asset(asset_id),
            "version": new_ver,
        }
