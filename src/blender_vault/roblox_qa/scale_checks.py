"""Scale, orientation, grounding, and unit checks (10 points) with category-aware expectations."""

from __future__ import annotations

from typing import Any

from .models import CheckFinding, DimensionScoreResult


def evaluate_scale_and_transforms(
    inspection: dict[str, Any],
    policy: dict[str, Any],
    limits: dict[str, Any],
    max_score: float = 10.0,
) -> DimensionScoreResult:
    findings: list[CheckFinding] = []
    failures: list[str] = []
    warnings: list[str] = []
    recommendations: list[str] = []

    objects: list[dict[str, Any]] = list(inspection.get("objects", []))
    dims = [float(x) for x in inspection.get("dimensions", [0.0, 0.0, 0.0])]
    bbox_min = [float(x) for x in inspection.get("bbox_min", [0.0, 0.0, 0.0])]
    unit_system = str(inspection.get("unit_system", "METRIC"))
    unit_scale = float(inspection.get("unit_scale_length", 1.0))
    studs_per_meter = float(limits.get("studs_per_meter", 3.57142857))
    dims_studs = [round(d * studs_per_meter, 3) for d in dims]

    expected_height = policy.get("expected_height_m", [0.05, 10.0])
    expected_max_dim = policy.get("expected_max_dim_m", [0.05, 10.0])
    min_h, max_h = float(expected_height[0]), float(expected_height[1])
    min_d, max_d = float(expected_max_dim[0]), float(expected_max_dim[1])
    dept = policy.get("department_code", "PRP")

    score = max_score

    # 1. Negative or degenerate scale (mandatory fail)
    neg_scale = [o["name"] for o in objects if any(float(s) <= 0.0 for s in o.get("scale", [1, 1, 1]))]
    extreme_aspect = [
        o["name"]
        for o in objects
        if all(abs(float(s)) > 0 for s in o.get("scale", [1, 1, 1]))
        and (max(abs(float(s)) for s in o.get("scale", [1, 1, 1])) / min(abs(float(s)) for s in o.get("scale", [1, 1, 1]))) > 4.0
    ]
    if neg_scale or extreme_aspect:
        score -= 5.0
        msg = f"Invalid negative or extreme object scale (neg={neg_scale}, extreme={extreme_aspect})."
        failures.append(msg)
        findings.append(
            CheckFinding(
                check_id="negative_scale",
                dimension="proportions_scale",
                name="Scale Sign & Uniformity",
                status="FAIL",
                mandatory=True,
                measured={"negative": neg_scale, "extreme": extreme_aspect},
                threshold="Positive applied scale (1,1,1)",
                message=msg,
                recommendation="Apply object transforms (Ctrl+A > Scale) and recalculate normals if negative scale was used.",
            )
        )
    else:
        findings.append(
            CheckFinding(
                check_id="negative_scale",
                dimension="proportions_scale",
                name="Scale Sign & Uniformity",
                status="PASS",
                measured="Positive",
                threshold="Positive scale",
                message="No negative or extreme anisotropic scales detected.",
            )
        )

    # 2. Bounding box dimensions vs category expectations
    height_z = dims[2] if len(dims) == 3 else 0.0
    max_dim = max(dims) if dims else 0.0
    if max_dim <= 1e-4 or max_dim > 500.0:
        score -= 5.0
        msg = f"Extreme bounding box dimensions {[round(d, 4) for d in dims]}m ({dims_studs} studs)."
        failures.append(msg)
        findings.append(
            CheckFinding(
                check_id="category_dimensions",
                dimension="proportions_scale",
                name="Category Dimensions & Proportions",
                status="FAIL",
                mandatory=True,
                measured={"meters": dims, "studs": dims_studs},
                threshold=f"height {min_h}–{max_h}m, max {min_d}–{max_d}m",
                message=msg,
                recommendation="Resize asset to realistic metric proportions before Roblox Studio export.",
            )
        )
    elif height_z < min_h * 0.5 or height_z > max_h * 1.5 or max_dim < min_d * 0.5 or max_dim > max_d * 1.5:
        score -= 2.5
        msg = f"Asset dimensions {[round(d, 3) for d in dims]}m fall outside typical {dept} expectations (height {min_h}–{max_h}m)."
        warnings.append(msg)
        recommendations.append(f"Verify real-world scale for {dept} ({dims_studs} Roblox studs).")
        findings.append(
            CheckFinding(
                check_id="category_dimensions",
                dimension="proportions_scale",
                name="Category Dimensions & Proportions",
                status="WARN",
                measured={"meters": dims, "studs": dims_studs},
                threshold=f"height {min_h}–{max_h}m",
                message=msg,
            )
        )
    else:
        findings.append(
            CheckFinding(
                check_id="category_dimensions",
                dimension="proportions_scale",
                name="Category Dimensions & Proportions",
                status="PASS",
                measured={"meters": [round(d, 3) for d in dims], "studs": dims_studs},
                threshold=f"height {min_h}–{max_h}m",
                message=f"Dimensions {[round(d, 3) for d in dims]}m ({dims_studs} studs) fit {dept} expectations.",
            )
        )

    # 3. Ground alignment & origin check
    floor_offset = abs(bbox_min[2])
    ground_tol = max(0.05, max_dim * 0.25)
    if floor_offset > ground_tol:
        score -= 1.0
        msg = f"Lowest mesh point Z={round(bbox_min[2], 3)}m sits away from ground plane Z=0."
        warnings.append(msg)
        recommendations.append("Align asset base/origin near Z=0 for predictable Roblox floor placement.")
        findings.append(
            CheckFinding(
                check_id="origin_grounding",
                dimension="proportions_scale",
                name="Origin & Grounding",
                status="WARN",
                measured=round(bbox_min[2], 4),
                threshold=f"|min_z| <= {round(ground_tol, 3)}m",
                message=msg,
            )
        )
    else:
        findings.append(
            CheckFinding(
                check_id="origin_grounding",
                dimension="proportions_scale",
                name="Origin & Grounding",
                status="PASS",
                measured=round(bbox_min[2], 4),
                threshold=f"|min_z| <= {round(ground_tol, 3)}m",
                message=f"Asset properly grounded near Z=0 (min_z={round(bbox_min[2], 4)}m).",
            )
        )

    # 4. Units sanity
    if unit_system != "METRIC" or abs(unit_scale - 1.0) > 1e-3:
        score -= 1.0
        msg = f"Non-standard Blender scene units ({unit_system}, scale={unit_scale}); expected METRIC 1.0."
        warnings.append(msg)
        recommendations.append("Use Metric unit system with Unit Scale 1.0 for predictable Roblox Studio scaling.")
        findings.append(
            CheckFinding(
                check_id="scene_units",
                dimension="proportions_scale",
                name="Blender Scene Units",
                status="WARN",
                measured={"system": unit_system, "scale_length": unit_scale},
                threshold="METRIC / 1.0",
                message=msg,
            )
        )
    else:
        findings.append(
            CheckFinding(
                check_id="scene_units",
                dimension="proportions_scale",
                name="Blender Scene Units",
                status="PASS",
                measured={"system": unit_system, "scale_length": unit_scale},
                threshold="METRIC / 1.0",
                message="Standard Metric 1.0 unit configuration verified.",
            )
        )

    return DimensionScoreResult(
        key="proportions_scale",
        label="Proportions & Scale",
        score=round(max(0.0, min(max_score, score)), 2),
        max_score=max_score,
        findings=findings,
        failures=failures,
        warnings=warnings,
        recommendations=recommendations,
    )
