"""Mesh integrity checks (15 points) distinguishing multi-part assemblies from accidental defects."""

from __future__ import annotations

from typing import Any

from .models import CheckFinding, DimensionScoreResult


def evaluate_mesh_integrity(inspection: dict[str, Any], policy: dict[str, Any], max_score: float = 15.0) -> DimensionScoreResult:
    findings: list[CheckFinding] = []
    failures: list[str] = []
    warnings: list[str] = []
    recommendations: list[str] = []

    file_ok = bool(inspection.get("file_opened_ok", False))
    objects: list[dict[str, Any]] = list(inspection.get("objects", []))
    missing_refs: list[str] = list(inspection.get("missing_object_references", []))
    total_faces = int(inspection.get("total_faces", 0))
    total_verts = int(inspection.get("total_vertices", 0))
    allow_open_boundary = bool(policy.get("allow_open_boundary", False))
    allow_multi_part = bool(policy.get("allow_multi_part", True))

    if not file_ok or len(objects) == 0 or total_faces == 0 or total_verts == 0:
        msg = "Scene failed to open or contains empty/missing mesh geometry."
        findings.append(
            CheckFinding(
                check_id="mesh_presence",
                dimension="mesh_integrity",
                name="Valid Non-Empty Mesh Geometry",
                status="FAIL",
                mandatory=True,
                measured={"file_opened_ok": file_ok, "objects": len(objects), "faces": total_faces},
                threshold=">= 1 mesh object with faces",
                message=msg,
                recommendation="Ensure the .blend file contains at least one valid visible mesh object with faces.",
            )
        )
        return DimensionScoreResult(
            key="mesh_integrity",
            label="Mesh Integrity",
            score=0.0,
            max_score=max_score,
            findings=findings,
            failures=[msg],
            recommendations=["Re-generate or fix the .blend file so it exports valid mesh geometry."],
        )

    score = max_score

    # 1. Empty individual mesh objects or missing object references
    empty_objs = [o["name"] for o in objects if o.get("face_count", 0) == 0 or o.get("vertex_count", 0) == 0]
    if empty_objs or missing_refs:
        msg = f"Empty meshes ({empty_objs}) or missing object references ({missing_refs})."
        score -= 6.0
        failures.append(msg)
        findings.append(
            CheckFinding(
                check_id="empty_or_missing_refs",
                dimension="mesh_integrity",
                name="Empty Meshes & Missing References",
                status="FAIL",
                mandatory=True,
                measured={"empty_meshes": empty_objs, "missing_refs": missing_refs},
                threshold="none",
                message=msg,
                recommendation="Remove empty mesh containers and fix broken modifier/object references.",
            )
        )
    else:
        findings.append(
            CheckFinding(
                check_id="empty_or_missing_refs",
                dimension="mesh_integrity",
                name="Empty Meshes & Missing References",
                status="PASS",
                measured=0,
                threshold=0,
                message=f"All {len(objects)} mesh object(s) contain valid geometry with zero missing references.",
            )
        )

    # 2. Zero-area & degenerate faces
    zero_area = sum(int(o.get("zero_area_faces", 0)) for o in objects)
    degen = sum(int(o.get("degenerate_faces", 0)) for o in objects)
    if zero_area > 0:
        msg = f"Detected {zero_area} zero-area face(s) ({degen} total degenerate)."
        score -= 4.5
        failures.append(msg)
        findings.append(
            CheckFinding(
                check_id="zero_area_faces",
                dimension="mesh_integrity",
                name="Degenerate & Zero-Area Faces",
                status="FAIL",
                mandatory=True,
                measured={"zero_area": zero_area, "degenerate": degen},
                threshold=0,
                message=msg,
                recommendation="Run Mesh > Clean Up > Degenerate Dissolve in Blender.",
            )
        )
    elif degen > 0:
        msg = f"Detected {degen} tiny/sliver near-degenerate face(s)."
        score -= 1.0
        warnings.append(msg)
        recommendations.append("Merge tiny sliver faces (< 1e-8 area) for cleaner Roblox shading.")
        findings.append(
            CheckFinding(
                check_id="zero_area_faces",
                dimension="mesh_integrity",
                name="Degenerate & Zero-Area Faces",
                status="WARN",
                measured={"zero_area": 0, "degenerate": degen},
                threshold=0,
                message=msg,
            )
        )
    else:
        findings.append(
            CheckFinding(
                check_id="zero_area_faces",
                dimension="mesh_integrity",
                name="Degenerate & Zero-Area Faces",
                status="PASS",
                measured=0,
                threshold=0,
                message="Zero degenerate or zero-area faces.",
            )
        )

    # 3. Duplicate vertices & duplicate faces
    dup_verts = sum(int(o.get("duplicate_verts", 0)) for o in objects)
    dup_faces = sum(int(o.get("duplicate_faces", 0)) for o in objects)
    dup_ratio = dup_verts / max(total_verts, 1)
    if dup_faces > 0 or dup_ratio > 0.05 or dup_verts > 50:
        msg = f"Excessive duplicate geometry: {dup_verts} duplicate verts ({dup_ratio:.1%}), {dup_faces} duplicate faces."
        score -= 3.0
        failures.append(msg)
        findings.append(
            CheckFinding(
                check_id="duplicate_geometry",
                dimension="mesh_integrity",
                name="Duplicate Vertices & Faces",
                status="FAIL",
                mandatory=True,
                measured={"duplicate_verts": dup_verts, "duplicate_faces": dup_faces},
                threshold="0 faces, <=5% verts",
                message=msg,
                recommendation="Run Merge By Distance and delete overlapping duplicate faces.",
            )
        )
    elif dup_verts > 0:
        msg = f"Minor overlapping duplicate vertices ({dup_verts})."
        score -= 0.5
        warnings.append(msg)
        findings.append(
            CheckFinding(
                check_id="duplicate_geometry",
                dimension="mesh_integrity",
                name="Duplicate Vertices & Faces",
                status="WARN",
                measured={"duplicate_verts": dup_verts, "duplicate_faces": 0},
                threshold=0,
                message=msg,
            )
        )
    else:
        findings.append(
            CheckFinding(
                check_id="duplicate_geometry",
                dimension="mesh_integrity",
                name="Duplicate Vertices & Faces",
                status="PASS",
                measured=0,
                threshold=0,
                message="No duplicate vertices or duplicate faces.",
            )
        )

    # 4. Non-manifold edges (multi-face vs open boundary)
    multi_face = sum(int(o.get("multi_face_edges", 0)) for o in objects)
    boundary = sum(int(o.get("boundary_edges", 0)) for o in objects)
    if multi_face > 0:
        msg = f"Detected {multi_face} multi-face non-manifold edge(s) shared by >2 faces."
        score -= 3.5
        failures.append(msg)
        findings.append(
            CheckFinding(
                check_id="non_manifold_edges",
                dimension="mesh_integrity",
                name="Manifold Integrity",
                status="FAIL",
                mandatory=True,
                measured={"multi_face_edges": multi_face, "boundary_edges": boundary},
                threshold=0,
                message=msg,
                recommendation="Remove interior faces causing >2-face edge junctions.",
            )
        )
    elif boundary > 0 and not allow_open_boundary:
        msg = f"Mesh has {boundary} open boundary edge(s) (non-watertight)."
        score -= 1.5
        warnings.append(msg)
        recommendations.append("Cap open boundary edges if the asset is intended as a watertight solid.")
        findings.append(
            CheckFinding(
                check_id="non_manifold_edges",
                dimension="mesh_integrity",
                name="Manifold Integrity",
                status="WARN",
                measured={"multi_face_edges": 0, "boundary_edges": boundary},
                threshold=0,
                message=msg,
            )
        )
    else:
        findings.append(
            CheckFinding(
                check_id="non_manifold_edges",
                dimension="mesh_integrity",
                name="Manifold Integrity",
                status="PASS",
                measured={"multi_face_edges": 0, "boundary_edges": boundary},
                threshold=0,
                message="Clean manifold topology.",
            )
        )

    # 5. Normals (inverted / NaN / inside-out closed solids)
    inv_norm = sum(int(o.get("inverted_normals_count", 0)) for o in objects)
    bad_norm = sum(int(o.get("invalid_normals_count", 0)) for o in objects)
    inside_out = [
        o["name"]
        for o in objects
        if o.get("boundary_edges", 0) == 0 and o.get("multi_face_edges", 0) == 0 and float(o.get("signed_volume", 0.0)) < -1e-7
    ]
    if inv_norm > 0 or bad_norm > 0 or inside_out:
        msg = f"Invalid/inverted normals: {inv_norm} flipped, {bad_norm} invalid, inside-out={inside_out}."
        score -= 4.0
        failures.append(msg)
        findings.append(
            CheckFinding(
                check_id="normals_integrity",
                dimension="mesh_integrity",
                name="Face Normals Integrity",
                status="FAIL",
                mandatory=True,
                measured={"inverted": inv_norm, "invalid": bad_norm, "inside_out": inside_out},
                threshold=0,
                message=msg,
                recommendation="Recalculate Outside Normals (Shift+N) in Blender before exporting to Roblox.",
            )
        )
    else:
        findings.append(
            CheckFinding(
                check_id="normals_integrity",
                dimension="mesh_integrity",
                name="Face Normals Integrity",
                status="PASS",
                measured=0,
                threshold=0,
                message="All face normals valid and consistently outward-facing.",
            )
        )

    # 6. Loose geometry & accidental disconnected shards (vs intentional multi-part)
    loose_v = sum(int(o.get("loose_verts", 0)) for o in objects)
    loose_e = sum(int(o.get("loose_edges", 0)) for o in objects)
    stray_islands: list[str] = []
    for o in objects:
        comp_faces = list(o.get("component_face_counts", []))
        if len(comp_faces) > 1:
            # Tiny 1..3-face island sitting inside a much larger object (>20 faces) indicates stray shard
            if comp_faces[0] >= 20 and any(c <= 3 for c in comp_faces[1:]):
                stray_islands.append(f"{o['name']} (islands={comp_faces})")

    if loose_v > 0 or loose_e > 0:
        msg = f"Detected loose geometry: {loose_v} loose vertex(es), {loose_e} loose edge(s)."
        score -= 3.0
        failures.append(msg)
        findings.append(
            CheckFinding(
                check_id="loose_and_disconnected",
                dimension="mesh_integrity",
                name="Loose Geometry & Stray Islands",
                status="FAIL",
                mandatory=True,
                measured={"loose_verts": loose_v, "loose_edges": loose_e, "stray_islands": stray_islands},
                threshold=0,
                message=msg,
                recommendation="Run Mesh > Clean Up > Delete Loose in Blender.",
            )
        )
    elif stray_islands and not allow_multi_part:
        msg = f"Unexpected tiny disconnected islands: {', '.join(stray_islands)}."
        score -= 1.0
        warnings.append(msg)
        findings.append(
            CheckFinding(
                check_id="loose_and_disconnected",
                dimension="mesh_integrity",
                name="Loose Geometry & Stray Islands",
                status="WARN",
                measured=stray_islands,
                threshold="Clean parts",
                message=msg,
            )
        )
    else:
        total_parts = sum(max(1, int(o.get("connected_components", 1))) for o in objects)
        findings.append(
            CheckFinding(
                check_id="loose_and_disconnected",
                dimension="mesh_integrity",
                name="Loose Geometry & Stray Islands",
                status="PASS",
                measured={"mesh_objects": len(objects), "total_parts": total_parts},
                threshold="No loose verts/edges",
                message=f"No loose vertices/edges; {len(objects)} mesh object(s) / {total_parts} intentional component(s).",
            )
        )

    return DimensionScoreResult(
        key="mesh_integrity",
        label="Mesh Integrity",
        score=round(max(0.0, min(max_score, score)), 2),
        max_score=max_score,
        findings=findings,
        failures=failures,
        warnings=warnings,
        recommendations=recommendations,
    )
