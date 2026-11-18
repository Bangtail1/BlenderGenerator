"""Category-aware Technical QA evaluation engine for Blender mesh inspection outputs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..models import (
    GeometryProfile,
    MeshObjectMetrics,
    QACheckItem,
    RoundTripReport,
    TechnicalQAReport,
    TechnicalQAStatus,
    utc_now_iso,
)

GENERIC_NAME_PATTERN = re.compile(
    r"^(cube|sphere|uvsphere|icosphere|plane|circle|cylinder|cone|torus|monkey|suzanne|object|mesh)(\.\d+)?$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ProfilePolicy:
    allow_open_boundary: bool
    boundary_is_fail: bool
    require_uv: bool
    require_material: bool
    soft_max_tris: int
    hard_max_tris: int
    check_signed_volume: bool


PROFILE_POLICIES: dict[GeometryProfile, ProfilePolicy] = {
    GeometryProfile.SOLID_MECHANICAL: ProfilePolicy(
        allow_open_boundary=False,
        boundary_is_fail=True,
        require_uv=False,
        require_material=True,
        soft_max_tris=120_000,
        hard_max_tris=400_000,
        check_signed_volume=True,
    ),
    GeometryProfile.FURNITURE_PROP: ProfilePolicy(
        allow_open_boundary=False,
        boundary_is_fail=False,
        require_uv=True,
        require_material=True,
        soft_max_tris=100_000,
        hard_max_tris=350_000,
        check_signed_volume=True,
    ),
    GeometryProfile.ORGANIC_NATURE: ProfilePolicy(
        allow_open_boundary=True,
        boundary_is_fail=False,
        require_uv=True,
        require_material=True,
        soft_max_tris=150_000,
        hard_max_tris=500_000,
        check_signed_volume=False,
    ),
    GeometryProfile.THIN_SHEET_CLOTH: ProfilePolicy(
        allow_open_boundary=True,
        boundary_is_fail=False,
        require_uv=True,
        require_material=True,
        soft_max_tris=150_000,
        hard_max_tris=500_000,
        check_signed_volume=False,
    ),
    GeometryProfile.ARCHITECTURE: ProfilePolicy(
        allow_open_boundary=True,
        boundary_is_fail=False,
        require_uv=True,
        require_material=True,
        soft_max_tris=300_000,
        hard_max_tris=1_000_000,
        check_signed_volume=False,
    ),
    GeometryProfile.CHARACTER: ProfilePolicy(
        allow_open_boundary=True,
        boundary_is_fail=False,
        require_uv=True,
        require_material=True,
        soft_max_tris=200_000,
        hard_max_tris=750_000,
        check_signed_volume=False,
    ),
    GeometryProfile.GENERAL: ProfilePolicy(
        allow_open_boundary=False,
        boundary_is_fail=False,
        require_uv=True,
        require_material=True,
        soft_max_tris=150_000,
        hard_max_tris=500_000,
        check_signed_volume=True,
    ),
}


def evaluate_technical_qa(
    raw_inspection: dict[str, Any],
    geometry_profile: GeometryProfile,
) -> TechnicalQAReport:
    """Evaluate raw Blender geometry metrics against profile-specific QA rules."""
    policy = PROFILE_POLICIES.get(geometry_profile, PROFILE_POLICIES[GeometryProfile.GENERAL])
    checks: list[QACheckItem] = []

    file_opened = bool(raw_inspection.get("file_opened_ok", False))
    mesh_count = int(raw_inspection.get("mesh_object_count", 0))
    total_verts = int(raw_inspection.get("total_vertices", 0))
    total_edges = int(raw_inspection.get("total_edges", 0))
    total_faces = int(raw_inspection.get("total_faces", 0))
    total_tris = int(raw_inspection.get("total_tris", 0))
    dims = [float(x) for x in raw_inspection.get("dimensions", [0.0, 0.0, 0.0])]
    bbox_min = [float(x) for x in raw_inspection.get("bbox_min", [0.0, 0.0, 0.0])]
    materials = list(raw_inspection.get("materials", []))
    collections = list(raw_inspection.get("collections", []))
    missing_deps = list(raw_inspection.get("missing_dependencies", []))
    obj_metrics = [MeshObjectMetrics.model_validate(o) for o in raw_inspection.get("objects", [])]
    round_trip = RoundTripReport.model_validate(raw_inspection.get("round_trip", {}))

    def add_check(check_id: str, name: str, category: str, status: str, measured: Any, threshold: Any, message: str) -> None:
        severity = "info" if status in ("PASS", "SKIP") else ("warning" if status == "WARN" else "error")
        checks.append(
            QACheckItem(
                check_id=check_id,
                name=name,
                category=category,
                status=status,
                severity=severity,
                measured=measured,
                threshold=threshold,
                message=message,
            )
        )

    # 1. File open & valid mesh presence
    add_check(
        "file_open",
        "Blender File Opens Cleanly",
        "structure",
        "PASS" if file_opened else "FAIL",
        file_opened,
        True,
        "Scene loaded cleanly in Blender." if file_opened else "Failed to open .blend file.",
    )
    add_check(
        "mesh_presence",
        "Valid Mesh Geometry Present",
        "geometry",
        "PASS" if (mesh_count > 0 and total_faces > 0 and total_verts > 0) else "FAIL",
        {"meshes": mesh_count, "verts": total_verts, "faces": total_faces},
        {"meshes": ">=1", "faces": ">=1"},
        f"Found {mesh_count} mesh object(s) with {total_verts} verts and {total_faces} faces."
        if mesh_count > 0 and total_faces > 0
        else "Asset contains no valid mesh polygons.",
    )

    if mesh_count == 0 or total_faces == 0:
        return TechnicalQAReport(
            status=TechnicalQAStatus.FAIL,
            geometry_profile=geometry_profile,
            blender_version=str(raw_inspection.get("blender_version", "")),
            file_opened_ok=file_opened,
            object_count=int(raw_inspection.get("object_count", 0)),
            mesh_object_count=mesh_count,
            total_vertices=total_verts,
            total_edges=total_edges,
            total_faces=total_faces,
            total_tris=total_tris,
            dimensions=dims,
            materials=materials,
            collections=collections,
            missing_dependencies=missing_deps,
            objects=obj_metrics,
            round_trip=round_trip,
            checks=checks,
            warnings_count=sum(1 for c in checks if c.status == "WARN"),
            failures_count=sum(1 for c in checks if c.status == "FAIL"),
            summary="Technical QA FAILED: no valid mesh geometry found.",
            inspected_at=utc_now_iso(),
        )

    # 2. Bounding box & dimensions sanity
    max_dim = max(dims) if dims else 0.0
    if max_dim <= 1e-4:
        dim_status = "FAIL"
        dim_msg = f"Mesh bounding box is effectively zero ({dims})."
    elif max_dim > 150.0:
        dim_status = "WARN"
        dim_msg = f"Asset dimensions ({[round(d, 3) for d in dims]}m) are unusually large; verify scene units."
    else:
        dim_status = "PASS"
        dim_msg = f"Bounding dimensions OK: {[round(d, 3) for d in dims]}m."
    add_check("dimensions_sanity", "Bounding Box Dimensions", "geometry", dim_status, dims, "0.0001m < max <= 150m", dim_msg)

    # 3. Object transforms & origin grounding
    bad_scales = [
        o.name
        for o in obj_metrics
        if any(s <= 0.0 for s in o.scale) or max(abs(s) for s in o.scale) / max(min(abs(s) for s in o.scale), 1e-6) > 4.0
    ]
    unapplied_scales = [o.name for o in obj_metrics if any(abs(s - 1.0) > 0.02 for s in o.scale)]
    if bad_scales:
        add_check(
            "object_scale",
            "Sensible Object Scale",
            "structure",
            "FAIL",
            bad_scales,
            "Uniform positive scale (1,1,1)",
            f"Extreme or negative unapplied object scale on: {', '.join(bad_scales)}.",
        )
    elif unapplied_scales:
        add_check(
            "object_scale",
            "Sensible Object Scale",
            "structure",
            "WARN",
            unapplied_scales,
            "Scale ~= (1,1,1)",
            f"Unapplied non-unit scale on: {', '.join(unapplied_scales)}.",
        )
    else:
        add_check("object_scale", "Sensible Object Scale", "structure", "PASS", [1.0, 1.0, 1.0], "(1,1,1)", "All object scales normalized to (1,1,1).")

    floor_offset = abs(bbox_min[2])
    ground_tol = max(0.05, max_dim * 0.25)
    add_check(
        "origin_grounding",
        "Origin & Ground Alignment",
        "structure",
        "PASS" if floor_offset <= ground_tol else "WARN",
        round(bbox_min[2], 4),
        f"|min_z| <= {round(ground_tol, 3)}m",
        f"Lowest point Z={round(bbox_min[2], 4)}m."
        + (" Well grounded near Z=0." if floor_offset <= ground_tol else " Asset sits far from world ground/origin."),
    )

    # 4. Normals consistency & signed volume
    inverted_total = sum(o.inverted_normals_count for o in obj_metrics)
    inside_out_closed = [
        o.name
        for o in obj_metrics
        if policy.check_signed_volume and o.boundary_edges == 0 and o.multi_face_edges == 0 and o.signed_volume < -1e-7
    ]
    if inverted_total > 0 or inside_out_closed:
        add_check(
            "face_normals",
            "Face Normals & Winding Consistency",
            "geometry",
            "FAIL",
            {"inverted_faces": inverted_total, "inside_out_closed_objects": inside_out_closed},
            0,
            f"Detected inconsistent/inverted normals ({inverted_total} faces; inside-out solids: {inside_out_closed}).",
        )
    else:
        add_check("face_normals", "Face Normals & Winding Consistency", "geometry", "PASS", 0, 0, "All face normals consistent and outward-facing.")

    # 5. Zero-area & degenerate faces
    zero_area_total = sum(o.zero_area_faces for o in obj_metrics)
    degen_total = sum(o.degenerate_faces for o in obj_metrics)
    if zero_area_total > 0:
        add_check(
            "degenerate_faces",
            "Degenerate & Zero-Area Faces",
            "geometry",
            "FAIL",
            {"zero_area": zero_area_total, "degenerate": degen_total},
            0,
            f"Found {zero_area_total} zero-area and {degen_total} degenerate faces.",
        )
    elif degen_total > 0:
        add_check(
            "degenerate_faces",
            "Degenerate & Zero-Area Faces",
            "geometry",
            "WARN",
            {"zero_area": 0, "degenerate": degen_total},
            0,
            f"Found {degen_total} near-degenerate tiny/sliver faces.",
        )
    else:
        add_check("degenerate_faces", "Degenerate & Zero-Area Faces", "geometry", "PASS", 0, 0, "No zero-area or degenerate faces.")

    # 6. Loose geometry (verts/edges)
    loose_v = sum(o.loose_verts for o in obj_metrics)
    loose_e = sum(o.loose_edges for o in obj_metrics)
    add_check(
        "loose_geometry",
        "Loose Vertices & Edges",
        "geometry",
        "FAIL" if (loose_v > 0 or loose_e > 0) else "PASS",
        {"loose_verts": loose_v, "loose_edges": loose_e},
        0,
        "No floating loose vertices or edges." if (loose_v == 0 and loose_e == 0) else f"Detected {loose_v} loose vertices and {loose_e} loose edges.",
    )

    # 7. Duplicate / overlapping vertices
    dup_verts = sum(o.duplicate_verts for o in obj_metrics)
    dup_ratio = dup_verts / max(total_verts, 1)
    if dup_verts == 0:
        dup_status = "PASS"
        dup_msg = "No overlapping duplicate vertices detected."
    elif dup_ratio > 0.05 or dup_verts > 50:
        dup_status = "FAIL"
        dup_msg = f"Excessive duplicate overlapping vertices: {dup_verts} ({dup_ratio:.1%})."
    else:
        dup_status = "WARN"
        dup_msg = f"Minor overlapping duplicate vertices: {dup_verts}."
    add_check("duplicate_verts", "Overlapping Duplicate Vertices", "geometry", dup_status, dup_verts, "0 (FAIL > 5%)", dup_msg)

    # 8. Non-manifold multi-face edges & profile-aware boundary edges
    multi_face = sum(o.multi_face_edges for o in obj_metrics)
    boundary = sum(o.boundary_edges for o in obj_metrics)
    add_check(
        "non_manifold_multi",
        "Non-Manifold Multi-Face Edges",
        "geometry",
        "FAIL" if multi_face > 0 else "PASS",
        multi_face,
        0,
        "No multi-face non-manifold edges." if multi_face == 0 else f"Found {multi_face} edge(s) shared by >2 faces.",
    )

    if boundary == 0:
        b_status = "PASS"
        b_msg = "Mesh is closed/watertight (0 boundary edges)."
    elif policy.allow_open_boundary:
        b_status = "PASS"
        b_msg = f"Open boundary edges ({boundary}) permitted for '{geometry_profile.value}' profile."
    elif policy.boundary_is_fail:
        b_status = "FAIL"
        b_msg = f"Found {boundary} open boundary edge(s); '{geometry_profile.value}' requires watertight manifold solids."
    else:
        b_status = "WARN"
        b_msg = f"Found {boundary} open boundary edge(s) (non-watertight)."
    add_check(
        "boundary_edges",
        "Open Boundary Edges / Watertightness",
        "geometry",
        b_status,
        boundary,
        "Allowed" if policy.allow_open_boundary else ("0 (strict)" if policy.boundary_is_fail else "0 (advisory)"),
        b_msg,
    )

    # 9. Mesh density
    if total_tris > policy.hard_max_tris:
        poly_status = "FAIL"
        poly_msg = f"Polygon density excessive ({total_tris} tris > hard cap {policy.hard_max_tris})."
    elif total_tris > policy.soft_max_tris:
        poly_status = "WARN"
        poly_msg = f"Polygon count high ({total_tris} tris > soft target {policy.soft_max_tris})."
    else:
        poly_status = "PASS"
        poly_msg = f"Appropriate triangle count ({total_tris} tris)."
    add_check("poly_density", "Mesh Density & Polycount", "structure", poly_status, total_tris, f"<= {policy.soft_max_tris}", poly_msg)

    # 10. Naming & Collection organization
    generic_objs = [o.name for o in obj_metrics if GENERIC_NAME_PATTERN.match(o.name.strip())]
    add_check(
        "object_naming",
        "Meaningful Object Names",
        "structure",
        "WARN" if generic_objs else "PASS",
        generic_objs if generic_objs else [o.name for o in obj_metrics],
        "Descriptive object names",
        f"Generic default names found: {', '.join(generic_objs)}." if generic_objs else "All mesh objects have descriptive names.",
    )
    root_col_objs = [o.name for o in obj_metrics if o.collection == "Scene Collection"]
    add_check(
        "collection_org",
        "Collection Organization",
        "structure",
        "WARN" if root_col_objs else "PASS",
        collections or ["Scene Collection"],
        "Dedicated asset collection",
        f"Objects sitting unorganized in root Scene Collection: {', '.join(root_col_objs)}." if root_col_objs else f"Organized in collections: {', '.join(collections)}.",
    )

    # 11. Materials, UVs, Modifiers & Dependencies
    unassigned_total = sum(o.unassigned_material_faces for o in obj_metrics)
    if not materials or unassigned_total > 0:
        mat_status = "FAIL" if (policy.require_material and not materials) else "WARN"
        mat_msg = f"Missing materials or {unassigned_total} face(s) lack material assignment."
    else:
        mat_status = "PASS"
        mat_msg = f"Assigned {len(materials)} material(s): {', '.join(materials)}."
    add_check("materials", "Material Assignment", "materials", mat_status, {"materials": materials, "unassigned_faces": unassigned_total}, "All faces assigned", mat_msg)

    no_uv_objs = [o.name for o in obj_metrics if not o.has_uv]
    if no_uv_objs and policy.require_uv:
        add_check("uv_mapping", "UV Map Coverage", "materials", "WARN", no_uv_objs, "UV layer present", f"Objects missing UV maps: {', '.join(no_uv_objs)}.")
    else:
        add_check("uv_mapping", "UV Map Coverage", "materials", "PASS", "All UVs present" if not no_uv_objs else "Optional", "UV layer", "UV layers present on all required meshes.")

    add_check(
        "external_dependencies",
        "External Textures & Linked Libraries",
        "materials",
        "FAIL" if missing_deps else "PASS",
        missing_deps,
        [],
        "All referenced textures and libraries resolved." if not missing_deps else f"Missing dependencies: {'; '.join(missing_deps)}",
    )

    high_subsurf = [
        f"{o.name}:{m['name']}(levels={m.get('levels')})"
        for o in obj_metrics
        for m in o.modifiers
        if m.get("type") == "SUBSURF" and (m.get("levels") or 0) > 3
    ]
    add_check(
        "modifiers",
        "Modifier Stack Sanity",
        "structure",
        "WARN" if high_subsurf else "PASS",
        high_subsurf or "Clean",
        "Subsurf levels <= 3",
        f"Excessive subdivision levels: {', '.join(high_subsurf)}." if high_subsurf else "Modifier stack clean and editable.",
    )

    # 12. GLB Export / Reimport Round-Trip Check
    if not round_trip.attempted:
        add_check("glb_round_trip", "GLB Export & Reimport Round-Trip", "round_trip", "FAIL", None, "Round-trip verified", round_trip.error or "GLB round-trip did not run.")
    elif not round_trip.success:
        add_check("glb_round_trip", "GLB Export & Reimport Round-Trip", "round_trip", "FAIL", round_trip.error, "Successful export & reimport", f"GLB round-trip failed: {round_trip.error}")
    else:
        dim_tol = max(0.02, 0.03 * max_dim)
        dim_ok = round_trip.dimension_delta_max <= dim_tol
        mat_ok = not (round_trip.material_count_before > 0 and round_trip.material_count_after == 0)
        rt_ok = dim_ok and mat_ok and round_trip.reimported_mesh_count > 0
        add_check(
            "glb_round_trip",
            "GLB Export & Reimport Round-Trip",
            "round_trip",
            "PASS" if rt_ok else "FAIL",
            {
                "reimported_meshes": round_trip.reimported_mesh_count,
                "dimension_delta_max": round_trip.dimension_delta_max,
                "materials_before": round_trip.material_count_before,
                "materials_after": round_trip.material_count_after,
            },
            f"dim_delta <= {round(dim_tol, 4)}m & materials preserved",
            f"GLB round-trip verified (max dim delta={round_trip.dimension_delta_max}m, mats {round_trip.material_count_before}->{round_trip.material_count_after})."
            if rt_ok
            else f"GLB round-trip mismatch (dim delta={round_trip.dimension_delta_max}m, tol={round(dim_tol, 4)}m, mats {round_trip.material_count_before}->{round_trip.material_count_after}).",
        )

    warnings_count = sum(1 for c in checks if c.status == "WARN")
    failures_count = sum(1 for c in checks if c.status == "FAIL")

    if failures_count > 0:
        final_status = TechnicalQAStatus.FAIL
        summary = f"Technical QA FAILED ({failures_count} failure(s), {warnings_count} warning(s))."
    elif warnings_count > 0:
        final_status = TechnicalQAStatus.PASS_WITH_WARNINGS
        summary = f"Technical QA PASSED WITH WARNINGS ({warnings_count} warning(s))."
    else:
        final_status = TechnicalQAStatus.PASS
        summary = f"Technical QA PASSED ({len(checks)}/{len(checks)} checks clean)."

    return TechnicalQAReport(
        status=final_status,
        geometry_profile=geometry_profile,
        blender_version=str(raw_inspection.get("blender_version", "")),
        file_opened_ok=file_opened,
        object_count=int(raw_inspection.get("object_count", 0)),
        mesh_object_count=mesh_count,
        total_vertices=total_verts,
        total_edges=total_edges,
        total_faces=total_faces,
        total_tris=total_tris,
        dimensions=dims,
        materials=materials,
        collections=collections,
        missing_dependencies=missing_deps,
        objects=obj_metrics,
        round_trip=round_trip,
        checks=checks,
        warnings_count=warnings_count,
        failures_count=failures_count,
        summary=summary,
        inspected_at=utc_now_iso(),
    )
