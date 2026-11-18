"""Independent & pipeline-integrated Automated 3D Quality Assurance Engine for Roblox assets."""

from __future__ import annotations

import logging
import shutil
import uuid
from pathlib import Path
from typing import Any, Optional

from ..blender.export_manager import RobloxExportManager
from ..blender_worker.runner import BlenderWorkerError, BlenderWorkerRunner
from ..config import Settings
from ..database.models import utc_now_iso
from .collision_checks import evaluate_collision_usability
from .geometry_checks import evaluate_geometry_and_topology, evaluate_polygon_efficiency
from .material_checks import evaluate_materials_and_textures
from .mesh_checks import evaluate_mesh_integrity
from .models import (
    CheckFinding,
    CollisionAssessment,
    DimensionScoreResult,
    QARulesConfig,
    RobloxQAOutput,
)
from .roblox_checks import build_roblox_studio_checklist, evaluate_roblox_compatibility
from .scale_checks import evaluate_scale_and_transforms
from .scoring import compute_final_qa_verdict
from .uv_checks import evaluate_uv_quality
from .visual_previews import (
    evaluate_organisation_and_naming,
    evaluate_visual_inspection,
    generate_or_verify_previews,
)

logger = logging.getLogger("blender_vault.roblox_qa")


class RobloxQAEngine:
    """Runs Blender-backed 100-point Roblox QA checks independently or inside a production pipeline."""

    def __init__(
        self,
        settings: Settings,
        *,
        runner: Optional[BlenderWorkerRunner] = None,
        rules: Optional[QARulesConfig] = None,
    ) -> None:
        self.settings = settings
        self.runner = runner or BlenderWorkerRunner(
            settings.blender_executable,
            timeout_seconds=settings.blender_timeout_seconds,
        )
        self.export_manager = RobloxExportManager(self.runner)
        self.rules = rules or QARulesConfig(settings.roblox_qa_rules_path)

    def evaluate_blend(
        self,
        *,
        blend_path: Path,
        asset_id: str,
        asset_name: str,
        asset_version: int = 1,
        department_code: str = "FUR",
        export_path: Optional[Path] = None,
        export_format: Optional[str] = None,
        previews_dir: Optional[Path] = None,
        tier_override: Optional[str] = None,
        render_previews: bool = True,
    ) -> RobloxQAOutput:
        """Run full 10-category Roblox QA against a .blend file (standalone or pipeline)."""
        blend_file = Path(blend_path).resolve()
        weights = self.rules.scoring_weights
        limits = self.rules.roblox_limits
        policy = self.rules.resolve_department_policy(department_code, tier_override=tier_override)
        fmt = (export_format or (export_path.suffix.lstrip(".") if export_path else self.settings.roblox_default_export_format) or "FBX").upper()

        work_dir = (self.settings.tmp_dir / f"rbx_qa_{uuid.uuid4().hex[:10]}").resolve()
        work_dir.mkdir(parents=True, exist_ok=True)
        asset_out_dir = (self.settings.resolved_roblox_output_dir / asset_id / f"v{asset_version:03d}").resolve()
        existing_sibling_previews = blend_file.parent / "previews"
        if previews_dir is not None:
            resolved_previews_dir = Path(previews_dir).resolve()
        elif existing_sibling_previews.is_dir() and (existing_sibling_previews / "three_quarter.png").is_file():
            resolved_previews_dir = existing_sibling_previews.resolve()
        else:
            resolved_previews_dir = (asset_out_dir / "previews").resolve()

        try:
            # 1. Headless Blender deep inspection
            try:
                inspection = self.runner.run_script(
                    "inspect_roblox_qa.py",
                    {"blend_path": str(blend_file)},
                    work_dir / "inspect",
                )
            except BlenderWorkerError as exc:
                inspection = {
                    "ok": False,
                    "file_opened_ok": False,
                    "blender_version": "",
                    "objects": [],
                    "collision_objects": [],
                    "missing_object_references": [str(exc)],
                    "material_inspection": {},
                    "dimensions": [0.0, 0.0, 0.0],
                    "bbox_min": [0.0, 0.0, 0.0],
                    "total_tris": 0,
                    "total_vertices": 0,
                    "total_faces": 0,
                }

            # 2. Export & Round-trip validation (if export_path provided or generate into output/ dir)
            target_export = (
                Path(export_path).resolve()
                if export_path
                else (asset_out_dir / f"{blend_file.stem}.{fmt.lower()}").resolve()
            )
            export_validation: dict[str, Any] = {}
            if int(inspection.get("mesh_object_count", 0)) > 0:
                try:
                    export_validation = self.export_manager.export_and_validate(
                        blend_path=blend_file,
                        output_path=target_export,
                        export_format=fmt,
                        work_dir=work_dir / "export_rt",
                        verify_roundtrip=True,
                    )
                    if export_validation.get("export_path"):
                        target_export = Path(export_validation["export_path"])
                except Exception as exc:
                    export_validation = {"ok": False, "error": str(exc), "roundtrip": {"attempted": True, "success": False, "error": str(exc)}}
            else:
                export_validation = {"ok": False, "error": "Zero mesh objects", "roundtrip": {"attempted": False, "success": False}}

            # 3. Visual preview rendering / sanity verification
            render_paths: dict[str, str] = {}
            contact_sheet: Optional[Path] = None
            sanity_ok = False
            sanity_details: dict[str, Any] = {}
            if render_previews and int(inspection.get("mesh_object_count", 0)) > 0:
                try:
                    render_paths, contact_sheet, sanity_ok, sanity_details = generate_or_verify_previews(
                        blend_path=blend_file,
                        previews_dir=resolved_previews_dir,
                        runner=self.runner,
                        work_dir=work_dir,
                        render_engine=self.settings.blender_render_engine,
                        resolution=min(512, self.settings.blender_render_resolution),
                        samples=min(12, self.settings.blender_render_samples),
                    )
                except Exception as exc:
                    logger.warning("Preview rendering failed during QA for %s: %s", asset_id, exc)
                    sanity_details = {"error": str(exc)}

            # 4. Evaluate all 10 weighted scoring categories
            d_mesh = evaluate_mesh_integrity(inspection, policy, max_score=weights["mesh_integrity"])
            d_geom = evaluate_geometry_and_topology(inspection, limits, max_score=weights["geometry_topology"])
            d_poly = evaluate_polygon_efficiency(inspection, policy, max_score=weights["polygon_efficiency"])
            d_uv = evaluate_uv_quality(inspection, policy, max_score=weights["uv_quality"])
            d_mat = evaluate_materials_and_textures(inspection, policy, limits, max_score=weights["materials_textures"])
            d_scale = evaluate_scale_and_transforms(inspection, policy, limits, max_score=weights["proportions_scale"])
            d_roblox = evaluate_roblox_compatibility(
                asset_id=asset_id,
                export_format=fmt,
                export_path=target_export if target_export.is_file() else None,
                export_validation=export_validation,
                inspection=inspection,
                limits=limits,
                max_score=weights["roblox_compatibility"],
            )
            d_col, col_assessment = evaluate_collision_usability(
                inspection, policy, max_score=weights["collision_usability"]
            )
            if render_previews:
                d_vis = evaluate_visual_inspection(
                    render_paths=render_paths,
                    contact_sheet_path=contact_sheet,
                    sanity_ok=sanity_ok,
                    sanity_details=sanity_details,
                    max_score=weights["visual_quality"],
                )
            else:
                d_vis = DimensionScoreResult(
                    key="visual_quality",
                    label="Visual Quality (Preview Readiness)",
                    score=round(weights["visual_quality"] * 0.7, 2),
                    max_score=weights["visual_quality"],
                    warnings=["Visual preview rendering skipped in fast QA mode; human visual check pending."],
                    recommendations=["Generate multi-angle previews before final human approval."],
                )
            d_org = evaluate_organisation_and_naming(inspection, max_score=weights["organisation_naming"])

            dimensions = [d_mesh, d_geom, d_poly, d_uv, d_mat, d_scale, d_roblox, d_col, d_vis, d_org]
            verdict = compute_final_qa_verdict(dimensions, minimum_passing_score=self.rules.minimum_passing_score)

            dims_m = [round(float(x), 5) for x in inspection.get("dimensions", [0.0, 0.0, 0.0])]
            studs_per_m = float(limits.get("studs_per_meter", 3.57142857))
            dims_studs = [round(d * studs_per_m, 3) for d in dims_m]

            uv_status = "FAILED" if d_uv.failures else ("WARNING" if d_uv.warnings else "PASSED")
            mat_insp = inspection.get("material_inspection", {})
            tex_dims = mat_insp.get("image_dimensions", [])
            tex_res_str = (
                f"{max(d[0] for d in tex_dims)}x{max(d[1] for d in tex_dims)}"
                if tex_dims
                else ("Procedural" if mat_insp.get("procedural_material_count", 0) > 0 else "None")
            )

            all_findings: list[CheckFinding] = []
            for d in dimensions:
                all_findings.extend(d.findings)

            return RobloxQAOutput(
                asset_id=asset_id,
                asset_name=asset_name,
                asset_version=asset_version,
                department_code=policy["department_code"],
                budget_tier=policy["tier_name"],
                test_timestamp=utc_now_iso(),
                blender_version=str(inspection.get("blender_version", "")),
                polygon_count=int(inspection.get("total_faces", 0)),
                triangle_count=int(inspection.get("total_tris", 0)),
                vertex_count=int(inspection.get("total_vertices", 0)),
                object_count=int(inspection.get("mesh_object_count", 0)),
                dimensions_meters=dims_m,
                dimensions_studs=dims_studs,
                texture_info={
                    "resolution": tex_res_str,
                    "image_texture_count": mat_insp.get("image_texture_count", 0),
                    "image_dimensions": tex_dims,
                    "missing_textures": mat_insp.get("missing_textures", []),
                    "has_base_color_map": mat_insp.get("has_base_color_map", False),
                    "has_normal_map": mat_insp.get("has_normal_map", False),
                    "has_roughness_map": mat_insp.get("has_roughness_map", False),
                    "has_metallic_map": mat_insp.get("has_metallic_map", False),
                },
                material_info={
                    "material_count": mat_insp.get("material_count", 0),
                    "procedural_material_count": mat_insp.get("procedural_material_count", 0),
                    "materials": [
                        m["name"] if isinstance(m, dict) and "name" in m else str(m)
                        for m in mat_insp.get("materials", [])
                    ],
                    "material_details": mat_insp.get("materials", []),
                    "unsupported_configs": mat_insp.get("unsupported_configs", []),
                },
                export_validation={
                    "export_format": fmt,
                    "export_path": str(target_export) if target_export.is_file() else None,
                    "file_size_bytes": target_export.stat().st_size if target_export.is_file() else 0,
                    "roundtrip": export_validation.get("roundtrip", {}),
                },
                collision_assessment=col_assessment,
                uv_status=uv_status,
                individual_scores=verdict["individual_scores"],
                dimensions_breakdown=dimensions,
                overall_score=verdict["overall_score"],
                grade=verdict["grade"],
                final_status=verdict["final_status"],
                approval_eligible=verdict["approval_eligible"],
                human_approval_required=True,
                ai_visual_inspection_performed=False,
                roblox_studio_automation_available=False,
                roblox_studio_checklist=build_roblox_studio_checklist(asset_id, fmt),
                preview_images=render_paths,
                contact_sheet_path=str(contact_sheet) if contact_sheet else None,
                mandatory_failures=verdict["mandatory_failures"],
                warnings=verdict["warnings"],
                recommendations=verdict["recommendations"],
                findings=all_findings,
            )
        finally:
            if work_dir.is_dir():
                shutil.rmtree(work_dir, ignore_errors=True)
