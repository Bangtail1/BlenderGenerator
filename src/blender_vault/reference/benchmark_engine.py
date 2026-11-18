"""Branch 1 <-> Branch 2 Benchmark Engine: reference matching and independent technical comparison."""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any, Optional

from ..db import VaultDatabase
from ..models import AssetRecord, AssetVersion, GeometryProfile, utc_now_iso
from .grading_engine import QualityGradingEngine
from .inspector_service import ReferenceInspectorService
from .models import (
    BenchmarkReport,
    QualityGradeReport,
    ReferenceAssetRecord,
    ReferenceAssetStatus,
    ReferenceComparisonItem,
    ReferenceInspectionReport,
)
from .ref_db import ReferenceDatabase
from .reference_drive_sync import ReferenceDriveSyncService
from .reference_storage import ReferenceStorage

logger = logging.getLogger("blender_vault.reference.benchmark")

_BRANCH1_TO_BRANCH2_CATEGORY: dict[str, str] = {
    "Architecture": "Architecture",
    "Furniture": "Furniture",
    "Characters": "Characters",
    "Mechanical Objects": "Mechanical components",
    "Vehicles": "Vehicles",
    "Household Objects": "Props and objects",
    "Nature": "Plants",
    "Miscellaneous": "Other",
}


class BenchmarkEngine:
    """Matches Branch 1 generated/imported models against Branch 2 references and evaluates quality."""

    def __init__(
        self,
        vault_db: VaultDatabase,
        ref_db: ReferenceDatabase,
        ref_storage: ReferenceStorage,
        inspector: ReferenceInspectorService,
        grader: QualityGradingEngine,
        drive_sync: Optional[ReferenceDriveSyncService] = None,
    ) -> None:
        self.vault_db = vault_db
        self.ref_db = ref_db
        self.ref_storage = ref_storage
        self.inspector = inspector
        self.grader = grader
        self.drive_sync = drive_sync

    def find_matching_references(
        self,
        asset: AssetRecord,
        *,
        local_inspection: Optional[ReferenceInspectionReport] = None,
        intended_usage: Optional[str] = None,
        modeling_style: Optional[str] = None,
        limit: int = 3,
    ) -> list[tuple[ReferenceAssetRecord, float, str]]:
        """Rank graded Branch 2 references by category, usage, modeling style, density, and quality."""
        candidates = self.ref_db.list_reference_assets(status=ReferenceAssetStatus.GRADED.value, include_details=True)
        if not candidates:
            candidates = self.ref_db.list_reference_assets(include_details=True)
        candidates = [c for c in candidates if c.inspection is not None and c.grade is not None]
        if not candidates:
            return []

        target_cat = _BRANCH1_TO_BRANCH2_CATEGORY.get(asset.category, asset.category)
        style_hint = modeling_style or self._infer_style_from_profile(asset.geometry_profile, asset)
        query_tokens = {
            tok
            for tok in f"{asset.name} {asset.description} {' '.join(asset.tags)} {intended_usage or ''}".lower()
            .replace(",", " ")
            .replace(".", " ")
            .split()
            if len(tok) >= 3
        }
        local_tris = local_inspection.triangle_count if local_inspection else (asset.latest.poly_count if asset.latest else 0)

        scored: list[tuple[ReferenceAssetRecord, float, str]] = []
        for ref in candidates:
            score = 0.0
            reasons: list[str] = []

            # 1. Category alignment
            if ref.primary_category.lower() == target_cat.lower():
                score += 40.0
                reasons.append(f"Category match ({ref.primary_category})")
            elif ref.primary_category.lower() in {"furniture", "props and objects"} and target_cat.lower() in {"furniture", "props and objects"}:
                score += 18.0
                reasons.append(f"Related category ({ref.primary_category})")

            # 2. Keyword / intended usage overlap (e.g., chair <-> Ergonomic Chair)
            ref_tokens = {
                tok
                for tok in f"{ref.title} {ref.description} {' '.join(ref.tags)}".lower()
                .replace(",", " ")
                .replace(".", " ")
                .split()
                if len(tok) >= 3
            }
            overlap = sorted(query_tokens.intersection(ref_tokens))
            if overlap:
                kw_pts = min(25.0, len(overlap) * 8.0)
                score += kw_pts
                reasons.append(f"Keyword match ({', '.join(overlap[:3])})")

            # 3. Modeling style alignment
            ref_tech = set(ref.technical_categories)
            if style_hint and style_hint in ref_tech:
                score += 15.0
                reasons.append(f"Style match ({style_hint})")
            elif asset.geometry_profile == GeometryProfile.SOLID_MECHANICAL and "hard-surface" in ref_tech:
                score += 12.0
                reasons.append("Hard-surface mechanical match")

            # 4. Technical quality suitability (reward high-quality benchmarks)
            tech_q = ref.grade.technical_quality_score if ref.grade else 0.0
            score += (tech_q / 100.0) * 15.0
            if tech_q >= 80.0:
                reasons.append(f"Grade {ref.grade.grade_letter} ({tech_q:.0f}/100)")

            # 5. Comparable polygon complexity tier (soft guidance only)
            if local_tris > 0 and ref.inspection and ref.inspection.triangle_count > 0:
                ratio = max(local_tris, ref.inspection.triangle_count) / max(min(local_tris, ref.inspection.triangle_count), 1)
                if ratio <= 4.0:
                    score += 5.0

            scored.append((ref, round(score, 2), "; ".join(reasons) or "General library fallback"))

        scored.sort(key=lambda item: (item[1], item[0].grade.technical_quality_score if item[0].grade else 0.0), reverse=True)
        return scored[:limit]

    def benchmark_branch1_asset(
        self,
        asset_id: str,
        *,
        version_num: Optional[int] = None,
        intended_usage: Optional[str] = None,
        modeling_style: Optional[str] = None,
        reference_limit: int = 3,
        sync_to_drive: bool = True,
    ) -> BenchmarkReport:
        """Independently inspect, grade, and compare a Branch 1 asset against top Branch 2 references."""
        asset = self.vault_db.get_asset(asset_id)
        if not asset:
            raise KeyError(f"Branch 1 asset '{asset_id}' not found.")
        version: Optional[AssetVersion] = (
            self.vault_db.get_version(asset_id, version_num)
            if version_num is not None
            else asset.latest
        )
        if not version:
            raise KeyError(f"Version not found for Branch 1 asset '{asset_id}'.")
        if not version.blend_path or not Path(version.blend_path).is_file():
            raise FileNotFoundError(f"Missing .blend file for Branch 1 asset '{asset_id}' v{version.version}.")

        # 1. Independent Blender technical inspection of Branch 1 .blend
        local_insp = self.inspector.inspect_asset_file(
            source_file=Path(version.blend_path),
            blendswap_id=0,
            preview_image_count=(
                len(version.visual_qa.render_paths)
                if version.visual_qa and version.visual_qa.render_paths
                else 0
            ),
        )

        target_cat = _BRANCH1_TO_BRANCH2_CATEGORY.get(asset.category, asset.category)
        tech_hints = list(asset.tags)
        if modeling_style:
            tech_hints.append(modeling_style)

        # 2. Independent 100-point technical grade (never depends on reference copy or popularity)
        local_grade = self.grader.grade(
            local_insp,
            primary_category=target_cat,
            technical_categories=tech_hints,
            asset_type="model_3d",
            popularity_score=0.0,
            local_asset_id=asset.asset_id,
        )

        # 3. Find matching Branch 2 reference benchmarks
        matches = self.find_matching_references(
            asset,
            local_inspection=local_insp,
            intended_usage=intended_usage,
            modeling_style=modeling_style,
            limit=reference_limit,
        )

        selected_refs: list[ReferenceComparisonItem] = []
        ref_inspections: list[ReferenceInspectionReport] = []
        for ref, _, reason in matches:
            assert ref.inspection is not None and ref.grade is not None
            ref_inspections.append(ref.inspection)
            selected_refs.append(
                ReferenceComparisonItem(
                    blendswap_id=ref.blendswap_id,
                    title=ref.title,
                    url=ref.url,
                    primary_category=ref.primary_category,
                    reference_grade_letter=ref.grade.grade_letter,
                    reference_technical_score=ref.grade.technical_quality_score,
                    reference_usefulness_score=ref.grade.reference_usefulness_score,
                    reference_triangles=ref.inspection.triangle_count,
                    reference_quad_ratio=ref.inspection.quad_ratio,
                    reference_uv_coverage=ref.inspection.uv_coverage_ratio,
                    reference_material_count=ref.inspection.material_count,
                    match_reason=reason,
                )
            )

        # 4. Comparative analysis (without blindly penalizing efficient lower-poly models!)
        geo_diffs, topo_weak, uv_weak, mat_weak, org_weak, recs, eff_note = self._compare_against_references(
            local_insp,
            local_grade,
            ref_inspections,
        )

        report = BenchmarkReport(
            benchmark_id=f"bmk_{uuid.uuid4().hex[:12]}",
            branch1_asset_id=asset.asset_id,
            branch1_version=version.version,
            branch1_name=asset.name,
            branch1_category=asset.category,
            grading_profile=local_grade.grading_profile,
            methodology_version=local_grade.methodology_version,
            local_grade=local_grade,
            selected_references=selected_refs,
            geometry_differences=geo_diffs,
            topology_weaknesses=topo_weak,
            uv_weaknesses=uv_weak,
            material_weaknesses=mat_weak,
            organization_weaknesses=org_weak,
            recommended_improvements=recs,
            efficiency_note=eff_note,
            created_at=utc_now_iso(),
        )

        report_path = self.ref_storage.write_benchmark_report(report)
        self.ref_db.save_benchmark_report(report)

        if sync_to_drive and self.drive_sync is not None:
            try:
                if self.drive_sync.gdrive.get_auth_status().get("authenticated"):
                    self.drive_sync.sync_benchmark_report(report_path)
            except Exception as exc:
                logger.warning("Could not sync benchmark report %s to Drive: %s", report.benchmark_id, exc)

        return report

    @staticmethod
    def _infer_style_from_profile(profile: GeometryProfile, asset: AssetRecord) -> str:
        text = f"{asset.name} {asset.description} {' '.join(asset.tags)}".lower()
        if "sculpt" in text:
            return "sculpted"
        if "rig" in text:
            return "rigged"
        if profile in (GeometryProfile.ORGANIC_NATURE, GeometryProfile.CHARACTER):
            return "organic"
        return "hard-surface"

    @staticmethod
    def _compare_against_references(
        local: ReferenceInspectionReport,
        grade: QualityGradeReport,
        refs: list[ReferenceInspectionReport],
    ) -> tuple[list[str], list[str], list[str], list[str], list[str], list[str], str]:
        geo_diffs: list[str] = []
        topo_weak: list[str] = []
        uv_weak: list[str] = []
        mat_weak: list[str] = []
        org_weak: list[str] = []
        recs: list[str] = []
        eff_note = ""

        # Local intrinsic checks first
        if local.zero_area_faces > 0:
            geo_diffs.append(f"Local asset has {local.zero_area_faces} zero-area face(s).")
            recs.append("Merge/dissolve zero-area degenerate faces before export.")
        if local.multi_face_edges > 0:
            geo_diffs.append(f"Local asset has {local.multi_face_edges} multi-face non-manifold edge(s).")
            recs.append("Clean multi-face non-manifold edges via Mesh > Clean Up.")
        if local.loose_verts > 0 or local.loose_edges > 0:
            geo_diffs.append(f"Local asset contains loose geometry ({local.loose_verts} verts, {local.loose_edges} edges).")
            recs.append("Delete loose vertices/edges.")
        if local.inverted_normals_count > 0:
            geo_diffs.append(f"Local asset has {local.inverted_normals_count} inverted face normal(s).")
            recs.append("Recalculate face normals consistently outward (Shift+N).")

        if local.ngon_ratio > 0.08:
            topo_weak.append(f"N-gon ratio is {local.ngon_ratio:.1%} (target <= 8%).")
            recs.append("Triangulate or retopologize high-valence n-gons into clean quads.")
        if local.quad_ratio < 0.55 and grade.grading_profile != "organic_sculpted":
            topo_weak.append(f"Quad ratio is {local.quad_ratio:.1%}, limiting clean edge loops/subdivision.")
            recs.append("Convert unnecessary triangle strips into quad edge loops where deformation/bevel flow matters.")

        if not local.has_uv_map:
            uv_weak.append("Local asset has no UV map.")
            recs.append("Unwrap clean non-overlapping UV islands (Smart UV Project or seam-based unwrap).")
        else:
            if local.uv_overlapping_islands_detected:
                uv_weak.append(f"Overlapping UV islands detected ({local.uv_overlap_cell_ratio:.0%} overlap).")
                recs.append("Repack UV islands with margin so lightmaps/textures do not bleed.")
            if local.uv_completeness_ratio < 0.90:
                uv_weak.append(f"Only {local.uv_completeness_ratio:.0%} of faces have non-zero UV area.")
                recs.append("Ensure all mesh faces receive valid UV coordinates.")

        if local.material_count == 0:
            mat_weak.append("Local asset has zero materials assigned.")
            recs.append("Assign Principled BSDF materials (or PBR textures) to all mesh surfaces.")
        if local.missing_image_textures > 0:
            mat_weak.append(f"{local.missing_image_textures} missing external image texture(s).")
            recs.append("Pack external image textures into the .blend file.")
        if local.unassigned_material_faces > 0:
            mat_weak.append(f"{local.unassigned_material_faces} face(s) have no assigned material slot.")
            recs.append("Assign valid material slots to all mesh polygons.")

        if local.generic_named_objects:
            org_weak.append(f"Generic object names remain: {', '.join(local.generic_named_objects[:4])}.")
            recs.append("Rename mesh objects descriptively (e.g., Part_Seat, Part_Frame).")
        if local.unapplied_scale_objects:
            org_weak.append(f"Unapplied non-unit scales on: {', '.join(local.unapplied_scale_objects[:4])}.")
            recs.append("Apply object rotation and scale transforms (Ctrl+A -> Rotation & Scale).")
        if local.collection_count == 0:
            org_weak.append("Objects reside in root Scene Collection without a dedicated asset collection.")
            recs.append("Group asset objects inside a dedicated named Blender collection.")

        if not refs:
            eff_note = "Evaluated independently (no graded references in Branch 2 library yet for comparative deltas)."
            if not recs:
                recs.append("Asset passed all technical quality benchmarks cleanly.")
            return geo_diffs, topo_weak, uv_weak, mat_weak, org_weak, recs, eff_note

        # Comparative metrics against matched Branch 2 references
        avg_tris = sum(r.triangle_count for r in refs) / len(refs)
        avg_quad = sum(r.quad_ratio for r in refs) / len(refs)
        avg_uv_cov = sum(r.uv_coverage_ratio for r in refs) / len(refs)
        avg_mats = sum(r.material_count for r in refs) / len(refs)
        avg_nodes = sum(r.material_node_count_avg for r in refs) / len(refs)
        avg_parts = sum(r.mesh_object_count for r in refs) / len(refs)

        geo_diffs.append(
            f"Dimensions: local {[round(d, 3) for d in local.dimensions]}m vs reference avg "
            f"{[round(sum(r.dimensions[i] for r in refs) / len(refs), 3) for i in range(3)]}m."
        )
        geo_diffs.append(
            f"Mesh parts: local {local.mesh_object_count} object(s) ({local.connected_components} component(s)) "
            f"vs reference avg {avg_parts:.1f} object(s)."
        )

        # Efficiency-aware polygon comparison (NEVER penalize fewer polys when technically sound!)
        if avg_tris > 0:
            poly_ratio = local.triangle_count / avg_tris
            if poly_ratio < 0.75 and grade.total_score >= 80.0:
                eff_note = (
                    f"Efficient geometry: local model uses {local.triangle_count:,} tris "
                    f"({(1.0 - poly_ratio):.0%} fewer than reference avg {avg_tris:,.0f} tris) "
                    f"while maintaining clean Grade {grade.grade_letter} technical integrity."
                )
            elif poly_ratio > 2.2:
                eff_note = (
                    f"Higher polygon footprint: local model uses {local.triangle_count:,} tris "
                    f"({poly_ratio:.1f}x reference avg {avg_tris:,.0f} tris)."
                )
                geo_diffs.append(eff_note)
                recs.append("Check whether bevel/subdivision segments can be optimized without losing silhouette fidelity.")
            else:
                eff_note = (
                    f"Comparable polygon budget: local {local.triangle_count:,} tris vs reference avg {avg_tris:,.0f} tris."
                )

        if local.quad_ratio + 0.12 < avg_quad:
            topo_weak.append(
                f"Lower quad proportion than matched references (local {local.quad_ratio:.1%} vs ref avg {avg_quad:.1%})."
            )
            if not any("quad" in r.lower() for r in recs):
                recs.append("Increase quad-loop consistency on primary forms to match top reference topology.")

        if local.has_uv_map and avg_uv_cov > 0.0 and local.uv_coverage_ratio + 0.18 < avg_uv_cov:
            uv_weak.append(
                f"UV space utilization is lower than references (local {local.uv_coverage_ratio:.0%} vs ref avg {avg_uv_cov:.0%})."
            )
            if not any("uv" in r.lower() for r in recs):
                recs.append("Scale and pack UV islands tightly to improve texel density utilization.")

        if avg_mats >= 2.0 and local.material_count <= 1 and local.material_node_count_avg + 2.0 < avg_nodes:
            mat_weak.append(
                f"Simpler shader/material setup than references (local {local.material_count} mat, "
                f"{local.material_node_count_avg:.1f} nodes/mat vs ref avg {avg_mats:.1f} mats, {avg_nodes:.1f} nodes)."
            )
            recs.append("Consider multi-material slot separation or richer PBR/procedural surface variation.")

        if not recs:
            recs.append("Technical metrics meet or exceed matched BlendSwap reference benchmarks.")

        return geo_diffs, topo_weak, uv_weak, mat_weak, org_weak, recs, eff_note
