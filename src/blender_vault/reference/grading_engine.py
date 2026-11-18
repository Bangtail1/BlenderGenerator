"""Category-specific 100-point technical quality grading engine for Branch 1 & Branch 2 assets."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Optional

from ..models import utc_now_iso
from .models import (
    DimensionScore,
    QualityGradeReport,
    ReferenceInspectionReport,
)


METHODOLOGY_VERSION = "2.0.0"


@dataclass(frozen=True)
class GradingProfileSpec:
    name: str
    allow_open_boundary: bool
    allow_triangulated_sculpt: bool
    target_quad_ratio: float
    max_ngon_ratio: float
    optimal_min_tris: int
    optimal_max_tris: int
    hard_cap_tris: int
    uv_required: bool
    allow_tiled_uvs: bool
    require_rig_checks: bool
    material_centric: bool = False


GRADING_PROFILES: dict[str, GradingProfileSpec] = {
    "character": GradingProfileSpec(
        name="character",
        allow_open_boundary=True,
        allow_triangulated_sculpt=False,
        target_quad_ratio=0.85,
        max_ngon_ratio=0.02,
        optimal_min_tris=1_500,
        optimal_max_tris=120_000,
        hard_cap_tris=450_000,
        uv_required=True,
        allow_tiled_uvs=False,
        require_rig_checks=False,
    ),
    "rigged": GradingProfileSpec(
        name="rigged",
        allow_open_boundary=True,
        allow_triangulated_sculpt=False,
        target_quad_ratio=0.85,
        max_ngon_ratio=0.02,
        optimal_min_tris=1_500,
        optimal_max_tris=140_000,
        hard_cap_tris=500_000,
        uv_required=True,
        allow_tiled_uvs=False,
        require_rig_checks=True,
    ),
    "furniture": GradingProfileSpec(
        name="furniture",
        allow_open_boundary=False,
        allow_triangulated_sculpt=False,
        target_quad_ratio=0.60,
        max_ngon_ratio=0.08,
        optimal_min_tris=200,
        optimal_max_tris=80_000,
        hard_cap_tris=300_000,
        uv_required=True,
        allow_tiled_uvs=True,
        require_rig_checks=False,
    ),
    "hard_surface": GradingProfileSpec(
        name="hard_surface",
        allow_open_boundary=False,
        allow_triangulated_sculpt=False,
        target_quad_ratio=0.50,
        max_ngon_ratio=0.08,
        optimal_min_tris=300,
        optimal_max_tris=150_000,
        hard_cap_tris=500_000,
        uv_required=True,
        allow_tiled_uvs=False,
        require_rig_checks=False,
    ),
    "organic_sculpted": GradingProfileSpec(
        name="organic_sculpted",
        allow_open_boundary=True,
        allow_triangulated_sculpt=True,
        target_quad_ratio=0.10,
        max_ngon_ratio=0.05,
        optimal_min_tris=5_000,
        optimal_max_tris=350_000,
        hard_cap_tris=1_200_000,
        uv_required=False,
        allow_tiled_uvs=False,
        require_rig_checks=False,
    ),
    "organic_nature": GradingProfileSpec(
        name="organic_nature",
        allow_open_boundary=True,
        allow_triangulated_sculpt=False,
        target_quad_ratio=0.55,
        max_ngon_ratio=0.08,
        optimal_min_tris=100,
        optimal_max_tris=150_000,
        hard_cap_tris=500_000,
        uv_required=True,
        allow_tiled_uvs=True,
        require_rig_checks=False,
    ),
    "architecture": GradingProfileSpec(
        name="architecture",
        allow_open_boundary=True,
        allow_triangulated_sculpt=False,
        target_quad_ratio=0.55,
        max_ngon_ratio=0.12,
        optimal_min_tris=100,
        optimal_max_tris=250_000,
        hard_cap_tris=900_000,
        uv_required=True,
        allow_tiled_uvs=True,
        require_rig_checks=False,
    ),
    "material_only": GradingProfileSpec(
        name="material_only",
        allow_open_boundary=True,
        allow_triangulated_sculpt=True,
        target_quad_ratio=0.25,
        max_ngon_ratio=0.15,
        optimal_min_tris=12,
        optimal_max_tris=100_000,
        hard_cap_tris=500_000,
        uv_required=False,
        allow_tiled_uvs=True,
        require_rig_checks=False,
        material_centric=True,
    ),
    "general": GradingProfileSpec(
        name="general",
        allow_open_boundary=False,
        allow_triangulated_sculpt=False,
        target_quad_ratio=0.60,
        max_ngon_ratio=0.08,
        optimal_min_tris=100,
        optimal_max_tris=120_000,
        hard_cap_tris=450_000,
        uv_required=True,
        allow_tiled_uvs=False,
        require_rig_checks=False,
    ),
}


def resolve_grading_profile(
    *,
    primary_category: str,
    technical_categories: Optional[list[str]] = None,
    asset_type: str = "model_3d",
    inspection: Optional[ReferenceInspectionReport] = None,
) -> GradingProfileSpec:
    """Choose the right category/modeling-style grading profile without a single universal formula."""
    tech = set(technical_categories or [])
    if asset_type == "material" or (inspection and inspection.mesh_object_count == 0 and inspection.material_count > 0):
        return GRADING_PROFILES["material_only"]
    if "rigged" in tech or (inspection and inspection.has_rig):
        return GRADING_PROFILES["rigged"]
    if "sculpted" in tech:
        return GRADING_PROFILES["organic_sculpted"]

    cat_map = {
        "Characters": "character",
        "Furniture": "furniture",
        "Architecture": "architecture",
        "Vehicles": "hard_surface",
        "Electronics": "hard_surface",
        "Weapons": "hard_surface",
        "Mechanical components": "hard_surface",
        "Mechanical Objects": "hard_surface",
        "Plants": "organic_nature",
        "Nature": "organic_nature",
        "Environments": "architecture",
        "Animals": "character",
        "Clothing": "organic_nature",
        "Food": "organic_nature",
        "Props and objects": "furniture",
        "Household Objects": "furniture",
    }
    key = cat_map.get(primary_category, "general")
    if key == "general" and "hard-surface" in tech:
        key = "hard_surface"
    elif key == "general" and "organic" in tech:
        key = "organic_nature"
    return GRADING_PROFILES[key]


def score_to_grade_letter(score: float) -> str:
    if score >= 95.0:
        return "A+"
    if score >= 90.0:
        return "A"
    if score >= 80.0:
        return "B"
    if score >= 70.0:
        return "C"
    if score >= 60.0:
        return "D"
    return "F"


class QualityGradingEngine:
    """Computes an objective 100-point technical quality grade strictly independent of popularity."""

    def grade(
        self,
        inspection: ReferenceInspectionReport,
        *,
        primary_category: str = "Other",
        technical_categories: Optional[list[str]] = None,
        asset_type: str = "model_3d",
        popularity_score: float = 0.0,
        blendswap_id: Optional[int] = None,
        local_asset_id: Optional[str] = None,
        explicit_profile: Optional[str] = None,
    ) -> QualityGradeReport:
        profile = (
            GRADING_PROFILES.get(explicit_profile, GRADING_PROFILES["general"])
            if explicit_profile
            else resolve_grading_profile(
                primary_category=primary_category,
                technical_categories=technical_categories,
                asset_type=asset_type,
                inspection=inspection,
            )
        )

        # If file failed to open or has zero meshes (unless material_only with materials)
        if not inspection.file_opened_ok or (inspection.mesh_object_count == 0 and not profile.material_centric):
            dims = [
                DimensionScore(dimension="Geometry integrity", score=0.0, max_score=20.0, percentage=0.0, findings=[inspection.error_message or "No valid mesh geometry opened."]),
                DimensionScore(dimension="Topology quality", score=0.0, max_score=20.0, percentage=0.0, findings=["Skipped due to missing/unopenable geometry."]),
                DimensionScore(dimension="Polygon efficiency", score=0.0, max_score=10.0, percentage=0.0, findings=[]),
                DimensionScore(dimension="UV quality", score=0.0, max_score=10.0, percentage=0.0, findings=[]),
                DimensionScore(dimension="Material quality", score=0.0, max_score=10.0, percentage=0.0, findings=[]),
                DimensionScore(dimension="Object organization", score=0.0, max_score=10.0, percentage=0.0, findings=[]),
                DimensionScore(dimension="Reusability", score=0.0, max_score=10.0, percentage=0.0, findings=[]),
                DimensionScore(dimension="Blender compatibility", score=0.0, max_score=5.0, percentage=0.0, findings=[]),
                DimensionScore(dimension="Presentation quality", score=0.0, max_score=5.0, percentage=0.0, findings=[]),
            ]
            return QualityGradeReport(
                grade_id=f"grd_{uuid.uuid4().hex[:12]}",
                blendswap_id=blendswap_id,
                local_asset_id=local_asset_id,
                methodology_version=METHODOLOGY_VERSION,
                grading_profile=profile.name,
                total_score=0.0,
                grade_letter="F",
                technical_quality_score=0.0,
                popularity_score=round(popularity_score, 2),
                reference_usefulness_score=0.0,
                dimensions=dims,
                strengths=[],
                weaknesses=[inspection.error_message or "Asset failed to open or contained no usable geometry."],
                graded_at=utc_now_iso(),
            )

        dimensions: list[DimensionScore] = [
            self._score_geometry_integrity(inspection, profile),
            self._score_topology_quality(inspection, profile),
            self._score_polygon_efficiency(inspection, profile),
            self._score_uv_quality(inspection, profile),
            self._score_material_quality(inspection, profile),
            self._score_organization(inspection),
            self._score_reusability(inspection, profile),
            self._score_compatibility(inspection),
            self._score_presentation(inspection),
        ]

        total = round(min(100.0, max(0.0, sum(d.score for d in dimensions))), 1)
        grade_letter = score_to_grade_letter(total)

        # Pure technical score (rescaled from the 95 technical points excluding presentation's 5 pts,
        # while total_score keeps the exact 100-point rubric sum)
        tech_95 = sum(d.score for d in dimensions if d.dimension != "Presentation quality")
        pure_tech_pct = round(min(100.0, (tech_95 / 95.0) * 100.0), 1)

        # Reference usefulness combines technical quality, reusability, topology, UV, and material clarity
        reusability = next(d.percentage for d in dimensions if d.dimension == "Reusability")
        topo_pct = next(d.percentage for d in dimensions if d.dimension == "Topology quality")
        usefulness = round(min(100.0, pure_tech_pct * 0.65 + reusability * 0.20 + topo_pct * 0.15), 1)

        strengths = [
            f"{d.dimension}: {d.score}/{d.max_score} ({d.percentage:.0f}%)"
            for d in dimensions
            if d.percentage >= 85.0
        ]
        weaknesses: list[str] = []
        for d in dimensions:
            if d.percentage < 75.0:
                if d.findings:
                    for reason in d.findings:
                        weaknesses.append(f"{d.dimension} ({d.score}/{d.max_score}): {reason}")
                else:
                    weaknesses.append(f"{d.dimension} ({d.score}/{d.max_score}): Below benchmark threshold.")

        return QualityGradeReport(
            grade_id=f"grd_{uuid.uuid4().hex[:12]}",
            blendswap_id=blendswap_id,
            local_asset_id=local_asset_id,
            methodology_version=METHODOLOGY_VERSION,
            grading_profile=profile.name,
            total_score=total,
            grade_letter=grade_letter,
            technical_quality_score=total,
            popularity_score=round(popularity_score, 2),
            reference_usefulness_score=usefulness,
            dimensions=dimensions,
            strengths=strengths,
            weaknesses=weaknesses,
            graded_at=utc_now_iso(),
        )

    # ------------------------------------------------------------------
    # Dimension Evaluators
    # ------------------------------------------------------------------
    @staticmethod
    def _score_geometry_integrity(insp: ReferenceInspectionReport, profile: GradingProfileSpec) -> DimensionScore:
        if profile.material_centric and insp.mesh_object_count == 0:
            return DimensionScore(dimension="Geometry integrity", score=16.0, max_score=20.0, percentage=80.0, findings=["Material-centric asset (shader showcase)."])
        score = 20.0
        findings: list[str] = []
        faces = max(insp.face_count, 1)
        verts = max(insp.vertex_count, 1)

        if insp.zero_area_faces > 0:
            pen = min(6.0, 1.5 + (insp.zero_area_faces / faces) * 35.0)
            score -= pen
            findings.append(f"{insp.zero_area_faces} zero-area face(s) detected.")
        elif insp.degenerate_faces > 0:
            pen = min(3.0, (insp.degenerate_faces / faces) * 20.0)
            score -= pen
            findings.append(f"{insp.degenerate_faces} tiny/sliver degenerate face(s).")

        if insp.multi_face_edges > 0:
            pen = min(5.0, 1.5 + insp.multi_face_edges * 0.4)
            score -= pen
            findings.append(f"{insp.multi_face_edges} multi-face non-manifold edge(s).")

        if insp.boundary_edges > 0 and not profile.allow_open_boundary:
            pen = min(4.0, 1.0 + (insp.boundary_edges / max(insp.edge_count, 1)) * 25.0)
            score -= pen
            findings.append(f"{insp.boundary_edges} open boundary edge(s) on closed-solid profile '{profile.name}'.")

        if insp.loose_verts > 0 or insp.loose_edges > 0:
            pen = min(3.5, 0.8 + (insp.loose_verts + insp.loose_edges) * 0.25)
            score -= pen
            findings.append(f"Loose geometry: {insp.loose_verts} verts, {insp.loose_edges} edges.")

        dup_ratio = insp.duplicate_verts / verts
        if dup_ratio > 0.005:
            pen = min(3.0, dup_ratio * 25.0)
            score -= pen
            findings.append(f"{insp.duplicate_verts} overlapping duplicate vertices ({dup_ratio:.1%}).")

        inv_ratio = insp.inverted_normals_count / faces
        if inv_ratio > 0.01:
            pen = min(4.5, 1.0 + inv_ratio * 15.0)
            score -= pen
            findings.append(f"{insp.inverted_normals_count} inverted face normal(s) ({inv_ratio:.1%}).")

        if max(insp.dimensions or [0.0]) <= 1e-4:
            score -= 5.0
            findings.append("Near-zero bounding box dimensions.")

        score = round(max(0.0, score), 2)
        if not findings:
            findings.append("Clean manifold geometry with zero defects.")
        return DimensionScore(
            dimension="Geometry integrity",
            score=score,
            max_score=20.0,
            percentage=round((score / 20.0) * 100.0, 1),
            findings=findings,
            metrics={"zero_area": insp.zero_area_faces, "multi_face_edges": insp.multi_face_edges, "boundary_edges": insp.boundary_edges, "inverted_normals": insp.inverted_normals_count},
        )

    @staticmethod
    def _score_topology_quality(insp: ReferenceInspectionReport, profile: GradingProfileSpec) -> DimensionScore:
        if profile.material_centric and insp.mesh_object_count == 0:
            return DimensionScore(dimension="Topology quality", score=16.0, max_score=20.0, percentage=80.0, findings=["Material-centric asset."])
        findings: list[str] = []

        if profile.allow_triangulated_sculpt:
            # Sculpted models judged on surface cleanliness & edge distribution rather than quads
            clean_part = (insp.mesh_cleanliness_score / 100.0) * 14.0
            density_ok = 6.0 if insp.triangle_count >= 2_000 else 3.5
            score = clean_part + density_ok
            findings.append(f"Sculpted topology evaluated on cleanliness ({insp.mesh_cleanliness_score:.0f}/100) rather than quad ratio.")
        else:
            # Quad ratio relative to profile target
            quad_score = min(1.0, insp.quad_ratio / max(profile.target_quad_ratio, 0.1)) * 10.0
            # N-gon penalty
            if insp.ngon_ratio <= profile.max_ngon_ratio:
                ngon_score = 4.0
            else:
                ngon_score = max(0.0, 4.0 - (insp.ngon_ratio - profile.max_ngon_ratio) * 16.0)
                findings.append(f"Elevated n-gon ratio ({insp.ngon_ratio:.1%} > {profile.max_ngon_ratio:.0%}).")

            # Pole / edge flow
            flow_score = (insp.edge_flow_score / 100.0) * 4.0
            subdiv_bonus = 2.0 if insp.subdivision_ready else (1.0 if insp.quad_ratio >= 0.5 else 0.0)
            score = quad_score + ngon_score + flow_score + subdiv_bonus
            if insp.quad_ratio < profile.target_quad_ratio * 0.7:
                findings.append(f"Low quad ratio ({insp.quad_ratio:.1%}) for '{profile.name}' profile (target >= {profile.target_quad_ratio:.0%}).")

        # Rigged deformation check
        if profile.require_rig_checks:
            if not insp.has_rig:
                score = max(0.0, score - 5.0)
                findings.append("Rigged profile lacks valid armature/bones.")
            elif insp.weighted_mesh_count == 0:
                score = max(0.0, score - 4.0)
                findings.append("Armature present but no weighted mesh deformations detected.")
            else:
                findings.append(f"Rigged topology verified ({insp.bone_count} bones, {insp.weighted_mesh_count} weighted mesh(es)).")

        score = round(min(20.0, max(0.0, score)), 2)
        if not findings:
            findings.append(f"Strong '{profile.name}' topology ({insp.quad_ratio:.0%} quads, {insp.ngon_ratio:.1%} n-gons).")
        return DimensionScore(
            dimension="Topology quality",
            score=score,
            max_score=20.0,
            percentage=round((score / 20.0) * 100.0, 1),
            findings=findings,
            metrics={"quad_ratio": insp.quad_ratio, "ngon_ratio": insp.ngon_ratio, "edge_flow_score": insp.edge_flow_score, "subdivision_ready": insp.subdivision_ready},
        )

    @staticmethod
    def _score_polygon_efficiency(insp: ReferenceInspectionReport, profile: GradingProfileSpec) -> DimensionScore:
        tris = insp.triangle_count
        findings: list[str] = []
        if tris == 0:
            score = 8.0 if profile.material_centric else 0.0
        elif tris < profile.optimal_min_tris:
            score = 6.5
            findings.append(f"Very low triangle count ({tris} tris); limited geometric detail.")
        elif tris <= profile.optimal_max_tris:
            score = 10.0
            findings.append(f"Well-balanced polygon budget ({tris:,} tris for '{profile.name}').")
        elif tris <= profile.hard_cap_tris:
            ratio = (tris - profile.optimal_max_tris) / max(profile.hard_cap_tris - profile.optimal_max_tris, 1)
            score = 10.0 - ratio * 4.5
            findings.append(f"High polygon count ({tris:,} tris > optimal {profile.optimal_max_tris:,}).")
        else:
            score = 3.0
            findings.append(f"Excessive polygon density ({tris:,} tris > hard cap {profile.hard_cap_tris:,}).")

        score = round(max(0.0, min(10.0, score)), 2)
        return DimensionScore(
            dimension="Polygon efficiency",
            score=score,
            max_score=10.0,
            percentage=round((score / 10.0) * 100.0, 1),
            findings=findings,
            metrics={"triangle_count": tris, "density_tris_per_m3": insp.mesh_density_tris_per_m3},
        )

    @staticmethod
    def _score_uv_quality(insp: ReferenceInspectionReport, profile: GradingProfileSpec) -> DimensionScore:
        findings: list[str] = []
        if not profile.uv_required and not insp.has_uv_map:
            return DimensionScore(dimension="UV quality", score=8.0, max_score=10.0, percentage=80.0, findings=[f"UVs optional for '{profile.name}' profile."])
        if not insp.has_uv_map:
            return DimensionScore(dimension="UV quality", score=0.0, max_score=10.0, percentage=0.0, findings=["Missing UV maps on all mesh objects."])

        mesh_ratio_pts = insp.uv_mapped_mesh_ratio * 3.5
        complete_pts = insp.uv_completeness_ratio * 3.5
        cov_pts = min(1.0, insp.uv_coverage_ratio / 0.45) * 2.0
        overlap_pts = 1.0 if (not insp.uv_overlapping_islands_detected or profile.allow_tiled_uvs) else max(0.0, 1.0 - insp.uv_overlap_cell_ratio * 3.0)

        score = round(min(10.0, mesh_ratio_pts + complete_pts + cov_pts + overlap_pts), 2)
        if insp.uv_overlapping_islands_detected and not profile.allow_tiled_uvs:
            findings.append(f"Overlapping UV islands detected ({insp.uv_overlap_cell_ratio:.0%} cell overlap).")
        if insp.uv_completeness_ratio < 0.85:
            findings.append(f"Incomplete UV mapping ({insp.uv_completeness_ratio:.0%} faces mapped).")
        if not findings:
            findings.append(f"Complete UV layout ({insp.uv_layer_count} layer(s), {insp.uv_coverage_ratio:.0%} coverage).")
        return DimensionScore(
            dimension="UV quality",
            score=score,
            max_score=10.0,
            percentage=round((score / 10.0) * 100.0, 1),
            findings=findings,
            metrics={"uv_layers": insp.uv_layer_count, "coverage": insp.uv_coverage_ratio, "completeness": insp.uv_completeness_ratio},
        )

    @staticmethod
    def _score_material_quality(insp: ReferenceInspectionReport, profile: GradingProfileSpec) -> DimensionScore:
        findings: list[str] = []
        if insp.material_count == 0:
            return DimensionScore(dimension="Material quality", score=1.0, max_score=10.0, percentage=10.0, findings=["No materials assigned."])

        score = 4.0  # Has materials
        if insp.principled_bsdf_count > 0 or insp.procedural_shader_count > 0:
            score += 2.5
        if insp.material_node_count_avg >= 4.0:
            score += 1.5
        elif insp.material_node_count_avg >= 2.0:
            score += 0.8

        if insp.missing_image_textures > 0:
            score -= min(4.0, 1.5 + insp.missing_image_textures * 1.0)
            findings.append(f"{insp.missing_image_textures} missing image texture(s).")
        elif insp.available_image_textures > 0 or insp.procedural_shader_count > 0:
            score += 2.0

        if insp.unassigned_material_faces > 0 and not profile.material_centric:
            unassigned_ratio = insp.unassigned_material_faces / max(insp.face_count, 1)
            if unassigned_ratio > 0.05:
                score -= min(2.0, unassigned_ratio * 4.0)
                findings.append(f"{insp.unassigned_material_faces} face(s) lack material slot assignment.")

        score = round(min(10.0, max(0.0, score)), 2)
        if not findings:
            findings.append(f"{insp.material_count} material(s), {insp.available_image_textures} packed/resolved texture(s), {insp.procedural_shader_count} procedural shader(s).")
        return DimensionScore(
            dimension="Material quality",
            score=score,
            max_score=10.0,
            percentage=round((score / 10.0) * 100.0, 1),
            findings=findings,
            metrics={"material_count": insp.material_count, "missing_textures": insp.missing_image_textures, "procedural_shaders": insp.procedural_shader_count},
        )

    @staticmethod
    def _score_organization(insp: ReferenceInspectionReport) -> DimensionScore:
        findings: list[str] = []
        naming_pts = insp.naming_consistency_ratio * 4.0
        col_pts = 2.5 if insp.collection_count > 0 else 0.8
        sep_pts = (insp.object_separation_score / 100.0) * 1.5
        scale_pts = 2.0 if not insp.unapplied_scale_objects else max(0.0, 2.0 - len(insp.unapplied_scale_objects) * 0.5)
        score = round(min(10.0, naming_pts + col_pts + sep_pts + scale_pts), 2)

        if insp.generic_named_objects:
            findings.append(f"Generic object names present: {', '.join(insp.generic_named_objects[:3])}.")
        if insp.unapplied_scale_objects:
            findings.append(f"Unapplied object scale on {len(insp.unapplied_scale_objects)} object(s).")
        if not findings:
            findings.append(f"Clean naming ({insp.naming_consistency_ratio:.0%}) and {insp.collection_count} collection(s).")
        return DimensionScore(
            dimension="Object organization",
            score=score,
            max_score=10.0,
            percentage=round((score / 10.0) * 100.0, 1),
            findings=findings,
        )

    @staticmethod
    def _score_reusability(insp: ReferenceInspectionReport, profile: GradingProfileSpec) -> DimensionScore:
        findings: list[str] = []
        score = 10.0
        if not insp.is_native_blender:
            score -= 1.5
            findings.append(f"Imported exchange format ({insp.source_format}) rather than native .blend.")
        if insp.missing_dependencies:
            score -= min(4.5, 1.5 + len(insp.missing_dependencies) * 1.0)
            findings.append(f"{len(insp.missing_dependencies)} missing external dependency/texture.")
        if insp.unapplied_scale_objects:
            score -= 1.5
        if insp.zero_area_faces > 0 or insp.multi_face_edges > 0:
            score -= 1.5
        if profile.uv_required and not insp.has_uv_map:
            score -= 1.5
        score = round(max(0.0, min(10.0, score)), 2)
        if not findings:
            findings.append("Self-contained, clean scale, zero missing dependencies — high reuse value.")
        return DimensionScore(dimension="Reusability", score=score, max_score=10.0, percentage=round((score / 10.0) * 100.0, 1), findings=findings)

    @staticmethod
    def _score_compatibility(insp: ReferenceInspectionReport) -> DimensionScore:
        findings: list[str] = []
        score = 5.0 if insp.file_opened_ok and insp.inspection_completed_ok else 0.0
        if insp.missing_dependencies:
            score -= min(2.0, len(insp.missing_dependencies) * 0.75)
            findings.append("External dependency warnings affect portability.")
        if not insp.is_native_blender:
            score -= 0.5
        score = round(max(0.0, score), 2)
        if not findings:
            findings.append(f"Opens cleanly in Blender {insp.blender_runtime_version or '5.x'} (source v{insp.blend_header_version or 'N/A'}).")
        return DimensionScore(dimension="Blender compatibility", score=score, max_score=5.0, percentage=round((score / 5.0) * 100.0, 1), findings=findings)

    @staticmethod
    def _score_presentation(insp: ReferenceInspectionReport) -> DimensionScore:
        """Presentation score (max 5 pts) kept strictly distinct from technical defect status."""
        score = 0.0
        notes: list[str] = []
        if insp.renderable:
            score += 2.0
        if insp.preview_image_count > 0:
            score += 1.5
            notes.append(f"{insp.preview_image_count} preview image(s)")
        if insp.has_camera:
            score += 0.75
            notes.append(f"{insp.camera_count} camera(s)")
        if insp.has_lighting:
            score += 0.75
            notes.append(f"{insp.light_count} light(s)")
        score = round(min(5.0, score), 2)
        if not notes:
            notes.append("No camera/lights in asset file (not treated as a technical defect).")
        return DimensionScore(
            dimension="Presentation quality",
            score=score,
            max_score=5.0,
            percentage=round((score / 5.0) * 100.0, 1),
            findings=notes,
        )
