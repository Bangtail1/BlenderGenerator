"""UV quality checks (10 points) distinguishing intentional overlap from invalid/missing UVs."""

from __future__ import annotations

from typing import Any

from .models import CheckFinding, DimensionScoreResult


def evaluate_uv_quality(
    inspection: dict[str, Any],
    policy: dict[str, Any],
    max_score: float = 10.0,
) -> DimensionScoreResult:
    findings: list[CheckFinding] = []
    failures: list[str] = []
    warnings: list[str] = []
    recommendations: list[str] = []

    objects: list[dict[str, Any]] = list(inspection.get("objects", []))
    require_uv = bool(policy.get("require_uv", True))
    if not objects:
        return DimensionScoreResult(
            key="uv_quality",
            label="UV Quality",
            score=0.0,
            max_score=max_score,
            failures=["No visible meshes to evaluate UV mapping."],
        )

    no_uv_objs = [o["name"] for o in objects if not o.get("has_uv", False)]
    total_faces = sum(int(o.get("face_count", 0)) for o in objects)
    unmapped_faces = sum(int(o.get("uv_stats", {}).get("unmapped_faces", 0)) for o in objects)
    invalid_coords = sum(int(o.get("uv_stats", {}).get("invalid_coords", 0)) for o in objects)
    extreme_uv_loops = sum(int(o.get("uv_stats", {}).get("extreme_uv_loops", 0)) for o in objects)
    out_of_unit = sum(int(o.get("uv_stats", {}).get("out_of_unit_range_loops", 0)) for o in objects)
    avg_overlap = sum(float(o.get("uv_stats", {}).get("overlap_cell_ratio", 0.0)) for o in objects) / max(len(objects), 1)

    score = max_score

    if invalid_coords > 0:
        score -= 5.0
        msg = f"Detected {invalid_coords} invalid (NaN/Inf) UV coordinate(s)."
        failures.append(msg)
        findings.append(
            CheckFinding(
                check_id="uv_valid_coords",
                dimension="uv_quality",
                name="UV Coordinate Validity",
                status="FAIL",
                mandatory=True,
                measured=invalid_coords,
                threshold=0,
                message=msg,
                recommendation="Unwrap or reset corrupted UV layers cleanly.",
            )
        )
    else:
        findings.append(
            CheckFinding(
                check_id="uv_valid_coords",
                dimension="uv_quality",
                name="UV Coordinate Validity",
                status="PASS",
                measured=0,
                threshold=0,
                message="All UV coordinates are finite numbers.",
            )
        )

    if no_uv_objs:
        if require_uv:
            score -= 5.0
            msg = f"Visible mesh object(s) missing UV maps: {', '.join(no_uv_objs)}."
            failures.append(msg)
            findings.append(
                CheckFinding(
                    check_id="uv_presence",
                    dimension="uv_quality",
                    name="UV Map Presence",
                    status="FAIL",
                    mandatory=True,
                    measured=no_uv_objs,
                    threshold="All visible meshes UV mapped",
                    message=msg,
                    recommendation="Generate UV maps (Smart UV Project or box projection) on all visible meshes.",
                )
            )
        else:
            score -= 2.0
            msg = f"Optional UV maps missing on: {', '.join(no_uv_objs)}."
            warnings.append(msg)
            findings.append(
                CheckFinding(
                    check_id="uv_presence",
                    dimension="uv_quality",
                    name="UV Map Presence",
                    status="WARN",
                    measured=no_uv_objs,
                    threshold="Optional",
                    message=msg,
                )
            )
    else:
        findings.append(
            CheckFinding(
                check_id="uv_presence",
                dimension="uv_quality",
                name="UV Map Presence",
                status="PASS",
                measured=f"{len(objects)}/{len(objects)} meshes",
                threshold="All meshes",
                message="All visible mesh objects contain active UV layers.",
            )
        )

    if not no_uv_objs and total_faces > 0 and unmapped_faces > 0:
        unmapped_ratio = unmapped_faces / max(total_faces, 1)
        if unmapped_ratio > 0.10:
            score -= 3.0
            msg = f"{unmapped_faces}/{total_faces} faces ({unmapped_ratio:.1%}) have collapsed/zero-area UVs."
            failures.append(msg)
            findings.append(
                CheckFinding(
                    check_id="uv_coverage",
                    dimension="uv_quality",
                    name="Visible Face UV Coverage",
                    status="FAIL",
                    mandatory=True,
                    measured=unmapped_faces,
                    threshold="<= 10% unmapped",
                    message=msg,
                    recommendation="Re-unwrap faces with collapsed zero-area UVs.",
                )
            )
        else:
            score -= 1.0
            msg = f"Minor unmapped/collapsed UVs on {unmapped_faces} face(s)."
            warnings.append(msg)
            findings.append(
                CheckFinding(
                    check_id="uv_coverage",
                    dimension="uv_quality",
                    name="Visible Face UV Coverage",
                    status="WARN",
                    measured=unmapped_faces,
                    threshold=0,
                    message=msg,
                )
            )

    # Intentional mirrored/tiled UVs or out-of-[0,1] box projection are classified as WARN/INFO, NOT hard error
    if extreme_uv_loops > 0:
        score -= 1.0
        msg = f"{extreme_uv_loops} UV loop(s) exceed [-4, 4] range; verify intentional tiling."
        warnings.append(msg)
        recommendations.append("Keep UVs within [0, 1] unless using deliberate tileable Roblox SurfaceAppearance textures.")
        findings.append(
            CheckFinding(
                check_id="uv_range",
                dimension="uv_quality",
                name="UV Coordinate Range",
                status="WARN",
                measured=extreme_uv_loops,
                threshold="[-4.0, 4.0]",
                message=msg,
            )
        )
    elif out_of_unit > 0:
        score -= 0.25
        findings.append(
            CheckFinding(
                check_id="uv_range",
                dimension="uv_quality",
                name="UV Coordinate Range",
                status="INFO",
                measured=out_of_unit,
                threshold="[0, 1] preferred",
                message=f"{out_of_unit} UV loop(s) extend outside [0, 1] (acceptable for tiled/box UVs).",
            )
        )

    if avg_overlap > 0.25:
        score -= 0.75
        msg = f"Overlapping UV islands detected ({avg_overlap:.0%} average cell overlap); acceptable if mirrored/tiled, problematic for unique lightmap/texture baking."
        warnings.append(msg)
        recommendations.append("Pack UV islands without overlap if baking unique Roblox PBR texture maps.")
        findings.append(
            CheckFinding(
                check_id="uv_overlap",
                dimension="uv_quality",
                name="UV Island Overlap",
                status="WARN",
                measured=round(avg_overlap, 3),
                threshold="<= 0.25 for unique bakes",
                message=msg,
            )
        )
    else:
        findings.append(
            CheckFinding(
                check_id="uv_overlap",
                dimension="uv_quality",
                name="UV Island Overlap",
                status="PASS",
                measured=round(avg_overlap, 3),
                threshold="<= 0.25",
                message=f"Clean UV packing (overlap ratio {avg_overlap:.1%}).",
            )
        )

    return DimensionScoreResult(
        key="uv_quality",
        label="UV Quality",
        score=round(max(0.0, min(max_score, score)), 2),
        max_score=max_score,
        findings=findings,
        failures=failures,
        warnings=warnings,
        recommendations=recommendations,
    )
