"""Collision geometry evaluation (5 points) with category-aware requirements."""

from __future__ import annotations

from typing import Any

from .models import CheckFinding, CollisionAssessment, DimensionScoreResult


def evaluate_collision_usability(
    inspection: dict[str, Any],
    policy: dict[str, Any],
    max_score: float = 5.0,
) -> tuple[DimensionScoreResult, CollisionAssessment]:
    findings: list[CheckFinding] = []
    failures: list[str] = []
    warnings: list[str] = []
    recommendations: list[str] = []

    collision_objs: list[dict[str, Any]] = list(inspection.get("collision_objects", []))
    visible_objs: list[dict[str, Any]] = list(inspection.get("objects", []))
    total_vis_tris = int(inspection.get("total_tris", 0))
    dept = policy.get("department_code", "PRP")
    requires_collision = bool(policy.get("collision_required", False))
    rec_fidelity = str(policy.get("recommended_collision", "Hull"))

    col_count = len(collision_objs)
    col_tris = sum(int(o.get("tri_count", 0)) for o in collision_objs)

    score = max_score
    notes: list[str] = []

    if col_count > 0:
        # Dedicated collision meshes (UCX_ / COL_) exist
        max_col_tris = 2500
        if col_tris > max_col_tris:
            score -= 2.0
            msg = f"Dedicated collision geometry is excessively complex ({col_tris:,} tris > {max_col_tris:,})."
            warnings.append(msg)
            notes.append(msg)
            recommendations.append("Simplify UCX/COL collision hulls below 2,500 triangles for fast physics.")
            col_status = "WARNING"
            findings.append(
                CheckFinding(
                    check_id="collision_complexity",
                    dimension="collision_usability",
                    name="Collision Proxy Complexity",
                    status="WARN",
                    measured={"collision_meshes": col_count, "collision_tris": col_tris},
                    threshold=f"<= {max_col_tris:,} tris",
                    message=msg,
                )
            )
        else:
            col_status = "PASSED"
            msg = f"Dedicated collision proxy present ({col_count} mesh(es), {col_tris} tris); recommended fidelity: {rec_fidelity}."
            notes.append(msg)
            findings.append(
                CheckFinding(
                    check_id="collision_complexity",
                    dimension="collision_usability",
                    name="Dedicated Collision Geometry",
                    status="PASS",
                    measured={"collision_meshes": col_count, "collision_tris": col_tris},
                    threshold="Clean convex/box collider",
                    message=msg,
                )
            )
    elif requires_collision:
        # Walkable / architectural / structural assets benefit from collision proxies or clean hull decomposition
        if total_vis_tris > 15000:
            score -= 2.0
            col_status = "WARNING"
            msg = (
                f"{dept} asset requires reliable collision ({rec_fidelity}) and has {total_vis_tris:,} tris "
                "without dedicated UCX_/COL_ proxy meshes."
            )
            warnings.append(msg)
            notes.append(msg)
            recommendations.append(f"Add lightweight UCX_/COL_ proxy geometry or set CollisionFidelity={rec_fidelity} in Roblox Studio.")
            findings.append(
                CheckFinding(
                    check_id="collision_suitability",
                    dimension="collision_usability",
                    name="Structural Collision Suitability",
                    status="WARN",
                    measured={"collision_meshes": 0, "visible_tris": total_vis_tris},
                    threshold="UCX_ proxy or <= 15,000 tris",
                    message=msg,
                )
            )
        else:
            score -= 0.5
            col_status = "PASSED"
            msg = f"No separate UCX_ mesh; {dept} geometry ({total_vis_tris:,} tris) suitable for Roblox '{rec_fidelity}' CollisionFidelity."
            notes.append(msg)
            findings.append(
                CheckFinding(
                    check_id="collision_suitability",
                    dimension="collision_usability",
                    name="Structural Collision Suitability",
                    status="PASS",
                    measured={"recommended_fidelity": rec_fidelity, "visible_tris": total_vis_tris},
                    threshold=rec_fidelity,
                    message=msg,
                )
            )
    else:
        # Decorative / standard props do NOT require custom collision meshes
        col_status = "NOT_REQUIRED" if dept in {"DEC", "VFX"} else "PASSED"
        if dept in {"DEC", "VFX"}:
            rec_fidelity = "Box (CanCollide=false recommended for decorative/VFX)"
            msg = f"Custom collision mesh not required for {dept}; recommend CanCollide=false or Box collision."
        elif len(visible_objs) > 1 and total_vis_tris > 12000:
            rec_fidelity = "Hull"
            msg = f"Multi-part {dept} model suitable for Roblox '{rec_fidelity}' / 'Default' CollisionFidelity without dedicated UCX_ meshes."
        else:
            msg = f"Geometry suitable for Roblox '{rec_fidelity}' CollisionFidelity."
        notes.append(msg)
        findings.append(
            CheckFinding(
                check_id="collision_suitability",
                dimension="collision_usability",
                name="Collision Usability",
                status="PASS",
                measured={"collision_meshes": 0, "recommended_fidelity": rec_fidelity},
                threshold="Optional for category",
                message=msg,
            )
        )

    assessment = CollisionAssessment(
        requires_collision=requires_collision,
        has_dedicated_collision=col_count > 0,
        collision_mesh_count=col_count,
        collision_tri_count=col_tris,
        recommended_fidelity=rec_fidelity,
        collision_status=col_status,
        notes=notes,
    )
    dim_score = DimensionScoreResult(
        key="collision_usability",
        label="Collision Usability",
        score=round(max(0.0, min(max_score, score)), 2),
        max_score=max_score,
        findings=findings,
        failures=failures,
        warnings=warnings,
        recommendations=recommendations,
    )
    return dim_score, assessment
