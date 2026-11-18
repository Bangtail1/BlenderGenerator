"""Geometry & topology checks (10 pts) and category-specific polygon efficiency checks (10 pts)."""

from __future__ import annotations

from typing import Any

from .models import CheckFinding, DimensionScoreResult


def evaluate_geometry_and_topology(
    inspection: dict[str, Any],
    limits: dict[str, Any],
    max_score: float = 10.0,
) -> DimensionScoreResult:
    findings: list[CheckFinding] = []
    failures: list[str] = []
    warnings: list[str] = []
    recommendations: list[str] = []

    objects: list[dict[str, Any]] = list(inspection.get("objects", []))
    total_verts = int(inspection.get("total_vertices", 0))
    total_edges = int(inspection.get("total_edges", 0))
    total_faces = int(inspection.get("total_faces", 0))
    total_tris = int(inspection.get("total_tris", 0))
    max_part_tris = int(limits.get("max_tris_per_mesh_part", 20000))

    score = max_score

    # 1. Applied scale & rotation checks
    unapplied_scale = [
        o["name"] for o in objects if any(abs(float(s) - 1.0) > 0.02 for s in o.get("scale", [1, 1, 1]))
    ]
    unapplied_rot = [
        o["name"] for o in objects if any(abs(float(r)) > 0.02 for r in o.get("rotation_euler", [0, 0, 0]))
    ]
    if unapplied_scale:
        score -= 2.5
        msg = f"Unapplied non-unit object scale on: {', '.join(unapplied_scale)}."
        warnings.append(msg)
        recommendations.append("Apply Scale (Ctrl+A > Scale) before exporting for Roblox.")
        findings.append(
            CheckFinding(
                check_id="applied_transforms",
                dimension="geometry_topology",
                name="Applied Object Transforms",
                status="WARN",
                measured={"unapplied_scale": unapplied_scale, "unapplied_rotation": unapplied_rot},
                threshold="Scale=(1,1,1), Rot=(0,0,0)",
                message=msg,
            )
        )
    elif unapplied_rot:
        score -= 1.0
        msg = f"Unapplied object rotation on: {', '.join(unapplied_rot)}."
        warnings.append(msg)
        recommendations.append("Apply Rotation (Ctrl+A > Rotation) for consistent Roblox pivot axes.")
        findings.append(
            CheckFinding(
                check_id="applied_transforms",
                dimension="geometry_topology",
                name="Applied Object Transforms",
                status="WARN",
                measured={"unapplied_rotation": unapplied_rot},
                threshold="Rot=(0,0,0)",
                message=msg,
            )
        )
    else:
        findings.append(
            CheckFinding(
                check_id="applied_transforms",
                dimension="geometry_topology",
                name="Applied Object Transforms",
                status="PASS",
                measured={"scale": [1, 1, 1], "rotation": [0, 0, 0]},
                threshold="Applied",
                message="All mesh objects have clean applied transforms.",
            )
        )

    # 2. Per-MeshPart Roblox 20k triangle ceiling
    over_part_cap = [f"{o['name']}({o.get('tri_count', 0)})" for o in objects if int(o.get("tri_count", 0)) > max_part_tris]
    if over_part_cap:
        score -= 4.5
        msg = f"Mesh object(s) exceed Roblox per-MeshPart limit ({max_part_tris:,} tris): {', '.join(over_part_cap)}."
        failures.append(msg)
        findings.append(
            CheckFinding(
                check_id="per_part_tri_limit",
                dimension="geometry_topology",
                name="Per-MeshPart Triangle Limit",
                status="FAIL",
                mandatory=True,
                measured=over_part_cap,
                threshold=f"<= {max_part_tris:,} tris per mesh object",
                message=msg,
                recommendation="Decimate or split large mesh parts below 20,000 triangles for Roblox MeshPart import.",
            )
        )
    else:
        findings.append(
            CheckFinding(
                check_id="per_part_tri_limit",
                dimension="geometry_topology",
                name="Per-MeshPart Triangle Limit",
                status="PASS",
                measured=max((int(o.get("tri_count", 0)) for o in objects), default=0),
                threshold=f"<= {max_part_tris:,}",
                message=f"All {len(objects)} mesh object(s) fit within Roblox's {max_part_tris:,}-triangle per-part limit.",
            )
        )

    # 3. Topology quality (N-gon ratio & object count sanity)
    total_ngons = sum(int(o.get("ngon_count", 0)) for o in objects)
    ngon_ratio = total_ngons / max(total_faces, 1)
    if ngon_ratio > 0.15:
        score -= 2.0
        msg = f"High N-gon ratio ({ngon_ratio:.1%}, {total_ngons} faces >4 verts); triangulation artifacts possible."
        warnings.append(msg)
        recommendations.append("Convert large N-gons to quads/tris before Roblox export.")
        findings.append(
            CheckFinding(
                check_id="topology_ngons",
                dimension="geometry_topology",
                name="Polygon & Edge Topology",
                status="WARN",
                measured={"verts": total_verts, "edges": total_edges, "faces": total_faces, "tris": total_tris, "ngon_ratio": round(ngon_ratio, 4)},
                threshold="ngon_ratio <= 0.15",
                message=msg,
            )
        )
    else:
        findings.append(
            CheckFinding(
                check_id="topology_ngons",
                dimension="geometry_topology",
                name="Polygon & Edge Topology",
                status="PASS",
                measured={"verts": total_verts, "edges": total_edges, "faces": total_faces, "tris": total_tris, "ngon_ratio": round(ngon_ratio, 4)},
                threshold="ngon_ratio <= 0.15",
                message=f"Clean quad/tri topology ({total_verts} verts, {total_edges} edges, {total_faces} polys, {total_tris} tris).",
            )
        )

    return DimensionScoreResult(
        key="geometry_topology",
        label="Geometry & Topology",
        score=round(max(0.0, min(max_score, score)), 2),
        max_score=max_score,
        findings=findings,
        failures=failures,
        warnings=warnings,
        recommendations=recommendations,
    )


def evaluate_polygon_efficiency(
    inspection: dict[str, Any],
    policy: dict[str, Any],
    max_score: float = 10.0,
) -> DimensionScoreResult:
    findings: list[CheckFinding] = []
    failures: list[str] = []
    warnings: list[str] = []
    recommendations: list[str] = []

    tris = int(inspection.get("total_tris", 0))
    dept = policy.get("department_code", "PRP")
    tier = policy.get("tier_name", "medium_prop")
    min_tris = int(policy.get("min_tris", 12))
    opt_min = int(policy.get("optimal_min_tris", 200))
    opt_max = int(policy.get("optimal_max_tris", 8000))
    warn_max = int(policy.get("warning_max_tris", max(opt_max * 2, 16000)))
    hard_max = int(policy.get("hard_max_tris", max(warn_max * 2, 35000)))

    if tris <= 0 or tris < min_tris:
        score = 2.0 if tris > 0 else 0.0
        msg = f"Triangle count ({tris}) is below minimum threshold ({min_tris}) for {dept}/{tier}."
        failures.append(msg)
        findings.append(
            CheckFinding(
                check_id="polygon_budget",
                dimension="polygon_efficiency",
                name="Category Polygon Budget",
                status="FAIL",
                mandatory=True,
                measured=tris,
                threshold=f"{opt_min:,}–{opt_max:,} optimal (min {min_tris}, hard max {hard_max:,})",
                message=msg,
                recommendation="Verify mesh was not accidentally decimated or empty.",
            )
        )
    elif tris > hard_max:
        score = 2.0
        msg = f"Triangle count ({tris:,}) exceeds hard budget cap ({hard_max:,}) for {dept}/{tier}."
        failures.append(msg)
        findings.append(
            CheckFinding(
                check_id="polygon_budget",
                dimension="polygon_efficiency",
                name="Category Polygon Budget",
                status="FAIL",
                mandatory=True,
                measured=tris,
                threshold=f"<= {hard_max:,} tris",
                message=msg,
                recommendation=f"Decimate or optimize geometry below {opt_max:,}–{hard_max:,} tris for {dept}.",
            )
        )
    elif tris <= opt_max:
        if tris < opt_min:
            score = 8.5
            msg = f"Low triangle count ({tris:,} tris < {opt_min:,} optimal lower range for {dept}/{tier}); very lightweight."
            findings.append(
                CheckFinding(
                    check_id="polygon_budget",
                    dimension="polygon_efficiency",
                    name="Category Polygon Budget",
                    status="PASS",
                    measured=tris,
                    threshold=f"{opt_min:,}–{opt_max:,}",
                    message=msg,
                )
            )
        else:
            score = max_score
            findings.append(
                CheckFinding(
                    check_id="polygon_budget",
                    dimension="polygon_efficiency",
                    name="Category Polygon Budget",
                    status="PASS",
                    measured=tris,
                    threshold=f"{opt_min:,}–{opt_max:,}",
                    message=f"Triangle count ({tris:,} tris) sits cleanly within {dept} ({tier}) budget ({opt_min:,}–{opt_max:,}).",
                )
            )
    else:
        # Between opt_max and hard_max: graceful deduction with optimization note
        ratio = (tris - opt_max) / max(hard_max - opt_max, 1)
        score = max(5.0, max_score - ratio * 4.5)
        msg = f"Triangle count ({tris:,} tris) exceeds optimal {dept} target ({opt_max:,} tris) but stays under hard cap ({hard_max:,})."
        warnings.append(msg)
        recommendations.append(f"Consider reducing bevel/subdivision density closer to {opt_max:,} tris for optimal Roblox streaming.")
        findings.append(
            CheckFinding(
                check_id="polygon_budget",
                dimension="polygon_efficiency",
                name="Category Polygon Budget",
                status="WARN",
                measured=tris,
                threshold=f"<= {opt_max:,} optimal (hard max {hard_max:,})",
                message=msg,
                recommendation=recommendations[-1],
            )
        )

    return DimensionScoreResult(
        key="polygon_efficiency",
        label="Polygon Efficiency",
        score=round(max(0.0, min(max_score, score)), 2),
        max_score=max_score,
        findings=findings,
        failures=failures,
        warnings=warnings,
        recommendations=recommendations,
    )
