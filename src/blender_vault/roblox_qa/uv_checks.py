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
    avg_self_overlap = sum(float(o.get("uv_stats", {}).get("self_overlap_cell_ratio", 0.0)) for o in objects) / max(len(objects), 1)
    layout_modes = [str(o.get("uv_stats", {}).get("uv_layout_mode", "UNKNOWN")) for o in objects]
    mat_info = dict(inspection.get("material_inspection", {}))
    img_count = int(mat_info.get("image_texture_count", 0))
    require_pbr_maps = bool(policy.get("require_pbr_maps", False))
    uses_image_textures = img_count > 0 or require_pbr_maps

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

    # Intentional mirrored/tiled UVs or out-of-[0,1] box projection on procedural assets are INFO (0 penalty)
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
        if uses_image_textures:
            score -= 0.25
        findings.append(
            CheckFinding(
                check_id="uv_range",
                dimension="uv_quality",
                name="UV Coordinate Range",
                status="INFO",
                measured=out_of_unit,
                threshold="[0, 1] preferred for unique bakes",
                message=(
                    f"{out_of_unit} UV loop(s) extend outside [0, 1] "
                    + ("(acceptable for seamless/tiled image textures)." if uses_image_textures else "(valid for procedural shaders / tiled projection).")
                ),
            )
        )

    if avg_self_overlap > 0.12:
        score -= 2.0
        msg = f"Folded/self-overlapping faces detected inside UV islands ({avg_self_overlap:.0%}); texture artifacts likely."
        warnings.append(msg)
        recommendations.append("Re-unwrap folded UV islands cleanly with Smart UV Project or marked seams.")
        findings.append(
            CheckFinding(
                check_id="uv_overlap",
                dimension="uv_quality",
                name="UV Island Overlap & Layout",
                status="WARN",
                measured={"overlap": round(avg_overlap, 3), "self_overlap": round(avg_self_overlap, 3), "modes": sorted(set(layout_modes))},
                threshold="self_overlap <= 0.12",
                message=msg,
            )
        )
    elif avg_overlap > 0.20:
        intentional_mode = any(
            m in {"INTENTIONAL_BOX_OR_TILED", "MIRRORED_OR_STACKED"} for m in layout_modes
        )
        if not uses_image_textures:
            recommendations.append(
                "Note: pack non-overlapping [0, 1] UVs only if you later bake unique PBR SurfaceAppearance maps or lightmaps."
            )
            findings.append(
                CheckFinding(
                    check_id="uv_overlap",
                    dimension="uv_quality",
                    name="UV Island Overlap & Layout",
                    status="INFO",
                    measured={"overlap": round(avg_overlap, 3), "modes": sorted(set(layout_modes)), "image_textures": img_count},
                    threshold="Non-penalty for procedural/tiled materials",
                    message=(
                        f"Intentional box-projected/tiled UV island overlap ({avg_overlap:.0%}, mode={','.join(sorted(set(layout_modes)))}); "
                        "valid for procedural materials without distortion (not intended for unique texture baking)."
                    ),
                )
            )
        elif intentional_mode:
            score -= 0.5
            msg = (
                f"Mirrored/tiled UV island overlap ({avg_overlap:.0%}) with image texture(s); "
                "valid for symmetrical/seamless textures, unsuitable for unique lightmap or asymmetrical baking."
            )
            warnings.append(msg)
            recommendations.append("Pack UVs without overlap if baking unique asymmetrical PBR textures or lightmaps.")
            findings.append(
                CheckFinding(
                    check_id="uv_overlap",
                    dimension="uv_quality",
                    name="UV Island Overlap & Layout",
                    status="WARN",
                    measured={"overlap": round(avg_overlap, 3), "modes": sorted(set(layout_modes)), "image_textures": img_count},
                    threshold="<= 0.20 for unique bakes",
                    message=msg,
                )
            )
        else:
            score -= 1.5
            msg = (
                f"Unpacked overlapping UV islands ({avg_overlap:.0%}) detected alongside {img_count} image texture(s); "
                "unique texture/lightmap baking will cause cross-island bleeding."
            )
            warnings.append(msg)
            recommendations.append("Pack UV islands inside [0, 1] without overlap for Roblox SurfaceAppearance PBR maps.")
            findings.append(
                CheckFinding(
                    check_id="uv_overlap",
                    dimension="uv_quality",
                    name="UV Island Overlap & Layout",
                    status="WARN",
                    measured={"overlap": round(avg_overlap, 3), "modes": sorted(set(layout_modes)), "image_textures": img_count},
                    threshold="<= 0.20",
                    message=msg,
                )
            )
    else:
        findings.append(
            CheckFinding(
                check_id="uv_overlap",
                dimension="uv_quality",
                name="UV Island Overlap & Layout",
                status="PASS",
                measured={"overlap": round(avg_overlap, 3), "modes": sorted(set(layout_modes))},
                threshold="<= 0.20",
                message=f"Clean UV layout ({avg_overlap:.1%} overlap, mode={','.join(sorted(set(layout_modes)))}); suitable for unique PBR baking.",
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
