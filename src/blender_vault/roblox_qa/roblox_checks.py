"""Roblox compatibility & export validation checks (15 points) + manual Studio verification checklist."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

from .models import CheckFinding, DimensionScoreResult, RobloxStudioChecklistItem

_SAFE_FILENAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def build_roblox_studio_checklist(asset_id: str, export_format: str) -> list[RobloxStudioChecklistItem]:
    """Return an explicit manual Roblox Studio import verification checklist."""
    return [
        RobloxStudioChecklistItem(
            step=1,
            check="Import Asset via Roblox Studio Asset Manager / Mesh Importer",
            expected=f"Import {asset_id}.{export_format.lower()} without 'Mesh exceeds 20,000 triangles' or format errors.",
        ),
        RobloxStudioChecklistItem(
            step=2,
            check="Verify Orientation & Scale in Studs",
            expected="Front face points -Z, upright +Y in Studio, and character proportion matches R15 reference dummy.",
        ),
        RobloxStudioChecklistItem(
            step=3,
            check="Inspect SurfaceAppearance / Material Rendering",
            expected="Materials, roughness, and textures render cleanly under Studio Future/ShadowMap lighting.",
        ),
        RobloxStudioChecklistItem(
            step=4,
            check="Test CollisionFidelity in Playtest",
            expected="Verify CollisionFidelity (Box/Hull/Precise) matches gameplay walkthrough and physics behavior.",
        ),
    ]


def evaluate_roblox_compatibility(
    *,
    asset_id: str,
    export_format: str,
    export_path: Optional[Path],
    export_validation: dict[str, Any],
    inspection: dict[str, Any],
    limits: dict[str, Any],
    max_score: float = 15.0,
) -> DimensionScoreResult:
    findings: list[CheckFinding] = []
    failures: list[str] = []
    warnings: list[str] = []
    recommendations: list[str] = []

    fmt = (export_format or "").upper()
    supported = set(limits.get("supported_export_formats", ["FBX", "GLB", "OBJ"]))
    max_part_tris = int(limits.get("max_tris_per_mesh_part", 20000))
    score = max_score

    # 1. Supported format & file existence/readability
    if fmt not in supported:
        score = 0.0
        msg = f"Unsupported Roblox export format '{fmt}'. Supported: {sorted(supported)}."
        failures.append(msg)
        findings.append(
            CheckFinding(
                check_id="roblox_export_format",
                dimension="roblox_compatibility",
                name="Roblox Export Format Support",
                status="FAIL",
                mandatory=True,
                measured=fmt,
                threshold=sorted(supported),
                message=msg,
                recommendation="Export asset as FBX, GLB, or OBJ.",
            )
        )
        return DimensionScoreResult(
            key="roblox_compatibility",
            label="Roblox Compatibility",
            score=0.0,
            max_score=max_score,
            findings=findings,
            failures=failures,
            recommendations=["Export in FBX, GLB, or OBJ format."],
        )

    if not export_path or not export_path.is_file() or export_path.stat().st_size == 0:
        score = 0.0
        msg = f"Exported {fmt} file missing or empty: {export_path}."
        failures.append(msg)
        findings.append(
            CheckFinding(
                check_id="roblox_export_file",
                dimension="roblox_compatibility",
                name="Export File Existence & Size",
                status="FAIL",
                mandatory=True,
                measured=str(export_path),
                threshold="Non-empty file",
                message=msg,
                recommendation="Re-run Blender export stage to generate a valid export file.",
            )
        )
        return DimensionScoreResult(
            key="roblox_compatibility",
            label="Roblox Compatibility",
            score=0.0,
            max_score=max_score,
            findings=findings,
            failures=failures,
            recommendations=["Re-export asset to FBX/GLB/OBJ."],
        )

    findings.append(
        CheckFinding(
            check_id="roblox_export_file",
            dimension="roblox_compatibility",
            name="Export File Existence & Format",
            status="PASS",
            measured={"format": fmt, "bytes": export_path.stat().st_size, "filename": export_path.name},
            threshold=f"Valid non-empty {fmt}",
            message=f"{fmt} export exists ({export_path.stat().st_size:,} bytes).",
        )
    )

    # 2. Export readability & Blender round-trip comparison
    rt = export_validation.get("roundtrip", {}) if isinstance(export_validation, dict) else {}
    if not rt.get("attempted"):
        score -= 3.0
        msg = f"{fmt} export re-import round-trip check did not run."
        warnings.append(msg)
        findings.append(
            CheckFinding(
                check_id="roblox_export_roundtrip",
                dimension="roblox_compatibility",
                name="Export Readability & Mesh Comparison",
                status="WARN",
                measured=rt,
                threshold="Round-trip verified",
                message=msg,
            )
        )
    elif not rt.get("success"):
        score -= 6.0
        msg = f"Exported {fmt} failed re-import readability check: {rt.get('error', 'unknown error')}."
        failures.append(msg)
        findings.append(
            CheckFinding(
                check_id="roblox_export_roundtrip",
                dimension="roblox_compatibility",
                name="Export Readability & Mesh Comparison",
                status="FAIL",
                mandatory=True,
                measured=rt,
                threshold="Re-imports cleanly",
                message=msg,
                recommendation="Fix mesh/material issues preventing clean FBX/GLB/OBJ re-import.",
            )
        )
    else:
        before = rt.get("before", {})
        after = rt.get("after", {})
        dim_delta = float(rt.get("dimension_delta_max", 0.0))
        max_dim = max([float(x) for x in before.get("dimensions", [0.0, 0.0, 0.0])] or [0.0])
        dim_tol = max(0.03, 0.05 * max_dim)
        mats_ok = bool(rt.get("materials_preserved", True))
        uvs_ok = bool(rt.get("uvs_preserved", True))
        if dim_delta > dim_tol or not mats_ok or not uvs_ok:
            score -= 5.0
            msg = (
                f"Exported {fmt} diverges from source .blend: dim_delta={dim_delta:.4f}m (tol {dim_tol:.4f}m), "
                f"mats_preserved={mats_ok}, uvs_preserved={uvs_ok}."
            )
            failures.append(msg)
            findings.append(
                CheckFinding(
                    check_id="roblox_export_roundtrip",
                    dimension="roblox_compatibility",
                    name="Export Readability & Mesh Comparison",
                    status="FAIL",
                    mandatory=True,
                    measured={"dim_delta": dim_delta, "mats_preserved": mats_ok, "uvs_preserved": uvs_ok},
                    threshold=f"dim_delta <= {dim_tol:.4f}m, mats & UVs preserved",
                    message=msg,
                    recommendation="Verify export axis/scale settings and material/UV export flags.",
                )
            )
        else:
            findings.append(
                CheckFinding(
                    check_id="roblox_export_roundtrip",
                    dimension="roblox_compatibility",
                    name="Export Readability & Mesh Comparison",
                    status="PASS",
                    measured={
                        "meshes_before": before.get("mesh_count"),
                        "meshes_after": after.get("mesh_count"),
                        "tris_before": before.get("tri_count"),
                        "tris_after": after.get("tri_count"),
                        "dim_delta": dim_delta,
                    },
                    threshold="Geometry, dimensions, UVs & materials preserved",
                    message=(
                        f"{fmt} re-import verified: {after.get('mesh_count')} mesh(es), "
                        f"{after.get('tri_count')} tris, dim_delta={dim_delta:.5f}m."
                    ),
                )
            )

    # 3. Exported mesh complexity (per-part <= 20k Roblox MeshPart ceiling)
    objects: list[dict[str, Any]] = list(inspection.get("objects", []))
    heavy_parts = [o["name"] for o in objects if int(o.get("tri_count", 0)) > max_part_tris]
    if heavy_parts:
        score -= 4.0
        msg = f"Part(s) exceed Roblox 20,000-triangle MeshPart import limit: {', '.join(heavy_parts)}."
        failures.append(msg)
        findings.append(
            CheckFinding(
                check_id="roblox_meshpart_cap",
                dimension="roblox_compatibility",
                name="Roblox MeshPart Triangle Limit",
                status="FAIL",
                mandatory=True,
                measured=heavy_parts,
                threshold=f"<= {max_part_tris:,} tris/part",
                message=msg,
                recommendation="Split or decimate meshes so each part stays <= 20,000 triangles.",
            )
        )

    # 4. File naming & hierarchy cleanliness
    if not _SAFE_FILENAME_RE.match(export_path.name):
        score -= 1.0
        msg = f"Export filename '{export_path.name}' contains spaces or special characters."
        warnings.append(msg)
        recommendations.append("Use ASCII alphanumeric/underscore filenames for Roblox Asset Manager imports.")
        findings.append(
            CheckFinding(
                check_id="roblox_file_naming",
                dimension="roblox_compatibility",
                name="Roblox Safe File Naming",
                status="WARN",
                measured=export_path.name,
                threshold="ASCII alphanumeric + _ . -",
                message=msg,
            )
        )
    else:
        findings.append(
            CheckFinding(
                check_id="roblox_file_naming",
                dimension="roblox_compatibility",
                name="Roblox Safe File Naming",
                status="PASS",
                measured=export_path.name,
                threshold="Safe filename",
                message=f"Export filename '{export_path.name}' is Roblox-safe.",
            )
        )

    # 5. Texture availability during export
    missing_tex = list(inspection.get("material_inspection", {}).get("missing_textures", []))
    if missing_tex:
        score -= 3.0
        msg = f"Missing external texture(s) during export: {missing_tex}."
        failures.append(msg)
        findings.append(
            CheckFinding(
                check_id="roblox_export_textures",
                dimension="roblox_compatibility",
                name="Export Texture Availability",
                status="FAIL",
                mandatory=True,
                measured=missing_tex,
                threshold="0 missing",
                message=msg,
            )
        )

    # Note: Honesty notice — Roblox Studio direct import cannot run headless in this environment
    recommendations.append(
        "Complete the Roblox Studio Manual Import Verification Checklist (Studio CLI import is not available in this environment)."
    )

    return DimensionScoreResult(
        key="roblox_compatibility",
        label="Roblox Compatibility",
        score=round(max(0.0, min(max_score, score)), 2),
        max_score=max_score,
        findings=findings,
        failures=failures,
        warnings=warnings,
        recommendations=recommendations,
    )
