"""Visual preview generation/sanity checks (10 points) and scene organisation/naming checks (5 points)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from ..blender_worker.runner import BlenderWorkerRunner
from ..services.vision_qa import (
    REQUIRED_INSPECTION_VIEWS,
    build_contact_sheet,
    verify_render_sanity,
)
from .models import CheckFinding, DimensionScoreResult


def generate_or_verify_previews(
    *,
    blend_path: Path,
    previews_dir: Path,
    runner: BlenderWorkerRunner,
    work_dir: Path,
    render_engine: str = "BLENDER_EEVEE",
    resolution: int = 512,
    samples: int = 12,
) -> tuple[dict[str, str], Optional[Path], bool, dict[str, Any]]:
    """Reuse complete valid previews if already rendered, or invoke render_views.py cleanly."""
    previews_dir.mkdir(parents=True, exist_ok=True)
    candidate_paths = {k: str(previews_dir / f"{k}.png") for k in REQUIRED_INSPECTION_VIEWS}
    all_exist = all(Path(p).is_file() and Path(p).stat().st_size > 0 for p in candidate_paths.values())

    if all_exist:
        sanity_ok, sanity_details = verify_render_sanity(candidate_paths)
        if sanity_ok:
            sheet = previews_dir / "contact_sheet.png"
            if not sheet.is_file():
                sheet = build_contact_sheet(candidate_paths, sheet)
            return candidate_paths, (sheet if sheet and sheet.is_file() else None), True, sanity_details

    ren_res = runner.run_script(
        "render_views.py",
        {
            "blend_path": str(Path(blend_path).resolve()),
            "output_dir": str(previews_dir.resolve()),
            "render_engine": render_engine,
            "resolution": resolution,
            "samples": samples,
            "turntable_frames": 0,
            "turntable_resolution": 256,
        },
        work_dir / "render_views",
    )
    render_paths: dict[str, str] = dict(ren_res.get("render_paths", {}))
    sheet_path = build_contact_sheet(render_paths, previews_dir / "contact_sheet.png")
    sanity_ok, sanity_details = verify_render_sanity(render_paths)
    return render_paths, sheet_path, sanity_ok, sanity_details


def evaluate_visual_inspection(
    *,
    render_paths: dict[str, str],
    contact_sheet_path: Optional[Path],
    sanity_ok: bool,
    sanity_details: dict[str, Any],
    max_score: float = 10.0,
) -> DimensionScoreResult:
    findings: list[CheckFinding] = []
    failures: list[str] = []
    warnings: list[str] = []
    recommendations: list[str] = []

    required_views = ("front", "right", "back", "three_quarter", "top")
    present_req = [v for v in required_views if render_paths.get(v) and Path(render_paths[v]).is_file()]

    score = 0.0
    if len(present_req) == len(required_views) and sanity_ok:
        score = max_score
        findings.append(
            CheckFinding(
                check_id="visual_previews",
                dimension="visual_quality",
                name="Standardized Visual Previews & Render Sanity",
                status="PASS",
                measured={"views_generated": sorted(render_paths.keys()), "contact_sheet": bool(contact_sheet_path)},
                threshold="front, side, rear, 3/4, top + non-blank contrast",
                message="All required preview angles rendered cleanly with non-blank visual contrast; ready for human visual inspection.",
            )
        )
    elif len(present_req) > 0:
        score = max(4.0, (len(present_req) / len(required_views)) * 8.0)
        msg = (
            f"Partial or low-contrast preview renders (present={present_req}, sanity_ok={sanity_ok})."
        )
        warnings.append(msg)
        recommendations.append("Re-render multi-angle previews and inspect lighting/framing manually.")
        findings.append(
            CheckFinding(
                check_id="visual_previews",
                dimension="visual_quality",
                name="Standardized Visual Previews & Render Sanity",
                status="WARN",
                measured={"present": present_req, "sanity_ok": sanity_ok, "details": sanity_details},
                threshold="All required views non-blank",
                message=msg,
            )
        )
    else:
        msg = "Failed to generate visual preview renders."
        failures.append(msg)
        findings.append(
            CheckFinding(
                check_id="visual_previews",
                dimension="visual_quality",
                name="Standardized Visual Previews & Render Sanity",
                status="FAIL",
                mandatory=True,
                measured=render_paths,
                threshold="Preview PNGs generated",
                message=msg,
                recommendation="Verify Blender EEVEE/Cycles rendering works in background mode.",
            )
        )

    recommendations.append("Human visual inspection required on generated preview renders before final approval.")

    return DimensionScoreResult(
        key="visual_quality",
        label="Visual Quality (Preview Readiness)",
        score=round(max(0.0, min(max_score, score)), 2),
        max_score=max_score,
        findings=findings,
        failures=failures,
        warnings=warnings,
        recommendations=recommendations,
    )


def evaluate_organisation_and_naming(
    inspection: dict[str, Any],
    max_score: float = 5.0,
) -> DimensionScoreResult:
    findings: list[CheckFinding] = []
    failures: list[str] = []
    warnings: list[str] = []
    recommendations: list[str] = []

    objects: list[dict[str, Any]] = list(inspection.get("objects", []))
    collections: list[str] = list(inspection.get("collections", []))
    score = max_score

    generic = [o["name"] for o in objects if o.get("generic_name")]
    root_col = [o["name"] for o in objects if o.get("collection") == "Scene Collection"]

    if generic:
        score -= min(2.5, 1.0 + len(generic) * 0.5)
        msg = f"Generic default Blender object name(s): {', '.join(generic)}."
        warnings.append(msg)
        recommendations.append("Rename generic primitives (e.g., Cube -> Chair_Seat) for clean Roblox hierarchy.")
        findings.append(
            CheckFinding(
                check_id="object_naming",
                dimension="organisation_naming",
                name="Descriptive Object Naming",
                status="WARN",
                measured=generic,
                threshold="Descriptive part names",
                message=msg,
            )
        )
    else:
        findings.append(
            CheckFinding(
                check_id="object_naming",
                dimension="organisation_naming",
                name="Descriptive Object Naming",
                status="PASS",
                measured=[o["name"] for o in objects],
                threshold="Descriptive part names",
                message=f"All {len(objects)} mesh object(s) have descriptive names.",
            )
        )

    if root_col:
        score -= 1.5
        msg = f"Object(s) placed in unorganized root Scene Collection: {', '.join(root_col)}."
        warnings.append(msg)
        recommendations.append("Organize asset meshes inside a dedicated Asset_<Name> collection.")
        findings.append(
            CheckFinding(
                check_id="collection_organisation",
                dimension="organisation_naming",
                name="Collection Hierarchy",
                status="WARN",
                measured=root_col,
                threshold="Dedicated asset collection",
                message=msg,
            )
        )
    else:
        findings.append(
            CheckFinding(
                check_id="collection_organisation",
                dimension="organisation_naming",
                name="Collection Hierarchy",
                status="PASS",
                measured=collections,
                threshold="Dedicated collection",
                message=f"Cleanly organized in collection(s): {', '.join(collections)}.",
            )
        )

    return DimensionScoreResult(
        key="organisation_naming",
        label="Organisation & Naming",
        score=round(max(0.0, min(max_score, score)), 2),
        max_score=max_score,
        findings=findings,
        failures=failures,
        warnings=warnings,
        recommendations=recommendations,
    )
