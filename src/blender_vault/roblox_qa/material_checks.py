"""Material and texture checks (10 points) supporting both PBR textures and simple/procedural Roblox materials."""

from __future__ import annotations

from typing import Any

from .models import CheckFinding, DimensionScoreResult


def evaluate_materials_and_textures(
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
    mat_info: dict[str, Any] = dict(inspection.get("material_inspection", {}))
    mat_count = int(mat_info.get("material_count", 0))
    proc_count = int(mat_info.get("procedural_material_count", 0))
    img_count = int(mat_info.get("image_texture_count", 0))
    missing_tex: list[str] = list(mat_info.get("missing_textures", []))
    missing_libs: list[str] = list(mat_info.get("missing_libraries", []))
    unsupported: list[str] = list(mat_info.get("unsupported_configs", []))
    image_dims: list[list[int]] = list(mat_info.get("image_dimensions", []))

    unassigned = sum(int(o.get("unassigned_material_faces", 0)) for o in objects)
    max_tex_dim = int(limits.get("max_texture_dimension", 1024))
    min_tex_dim = int(limits.get("min_texture_dimension", 64))
    require_pbr_maps = bool(policy.get("require_pbr_maps", False))
    allow_procedural = bool(policy.get("allow_procedural_materials", True))

    score = max_score

    # 1. Material presence & face assignment
    if mat_count == 0:
        score = 0.0
        msg = "Asset has zero materials assigned."
        failures.append(msg)
        findings.append(
            CheckFinding(
                check_id="material_presence",
                dimension="materials_textures",
                name="Material Assignment",
                status="FAIL",
                mandatory=True,
                measured=0,
                threshold=">= 1 material",
                message=msg,
                recommendation="Assign at least one Principled BSDF or Roblox-compatible material.",
            )
        )
        return DimensionScoreResult(
            key="materials_textures",
            label="Materials & Textures",
            score=0.0,
            max_score=max_score,
            findings=findings,
            failures=failures,
            recommendations=["Assign materials to all visible mesh faces."],
        )

    if unassigned > 0:
        score -= 2.5
        msg = f"{unassigned} mesh face(s) lack an assigned material slot."
        warnings.append(msg)
        recommendations.append("Assign a valid material to all mesh faces.")
        findings.append(
            CheckFinding(
                check_id="material_presence",
                dimension="materials_textures",
                name="Material Assignment",
                status="WARN",
                measured={"materials": mat_count, "unassigned_faces": unassigned},
                threshold="0 unassigned faces",
                message=msg,
            )
        )
    else:
        findings.append(
            CheckFinding(
                check_id="material_presence",
                dimension="materials_textures",
                name="Material Assignment",
                status="PASS",
                measured={"materials": mat_count, "unassigned_faces": 0},
                threshold="All faces assigned",
                message=f"{mat_count} material(s) cleanly assigned across all faces.",
            )
        )

    # 2. Missing textures or missing external libraries
    if missing_tex or missing_libs:
        score -= 5.0
        msg = f"Broken texture/library references: {'; '.join(missing_tex + missing_libs)}."
        failures.append(msg)
        findings.append(
            CheckFinding(
                check_id="texture_references",
                dimension="materials_textures",
                name="Texture & External Dependency Integrity",
                status="FAIL",
                mandatory=True,
                measured=missing_tex + missing_libs,
                threshold=[],
                message=msg,
                recommendation="Pack textures or fix missing image file paths before export.",
            )
        )
    else:
        findings.append(
            CheckFinding(
                check_id="texture_references",
                dimension="materials_textures",
                name="Texture & External Dependency Integrity",
                status="PASS",
                measured=img_count,
                threshold="0 missing",
                message="Zero missing textures or broken linked libraries.",
            )
        )

    # 3. PBR texture map completeness vs simple/procedural Roblox materials
    if img_count > 0 or require_pbr_maps:
        missing_maps: list[str] = []
        if not mat_info.get("has_base_color_map"):
            missing_maps.append("Base Color")
        if require_pbr_maps:
            if not mat_info.get("has_normal_map"):
                missing_maps.append("Normal")
            if not mat_info.get("has_roughness_map"):
                missing_maps.append("Roughness")
            if not mat_info.get("has_metallic_map"):
                missing_maps.append("Metallic")

        if missing_maps:
            pen = 3.0 if require_pbr_maps else 1.0
            score -= pen
            msg = f"Missing expected PBR image map(s): {', '.join(missing_maps)}."
            if require_pbr_maps:
                failures.append(msg)
                findings.append(
                    CheckFinding(
                        check_id="pbr_maps",
                        dimension="materials_textures",
                        name="PBR Texture Map Completeness",
                        status="FAIL",
                        mandatory=True,
                        measured=missing_maps,
                        threshold="Base Color, Normal, Roughness, Metallic",
                        message=msg,
                        recommendation="Supply complete SurfaceAppearance PBR maps.",
                    )
                )
            else:
                warnings.append(msg)
                findings.append(
                    CheckFinding(
                        check_id="pbr_maps",
                        dimension="materials_textures",
                        name="PBR Texture Map Completeness",
                        status="WARN",
                        measured=missing_maps,
                        threshold="Base Color present",
                        message=msg,
                    )
                )
        else:
            findings.append(
                CheckFinding(
                    check_id="pbr_maps",
                    dimension="materials_textures",
                    name="PBR Texture Map Completeness",
                    status="PASS",
                    measured=img_count,
                    threshold="Valid PBR maps",
                    message="Required PBR texture maps verified.",
                )
            )
    else:
        # Procedural / simple solid Roblox material (allowed per spec 4.6)
        if proc_count > 0 and allow_procedural:
            score -= 0.5
            recommendations.append(
                "Procedural node shader detected; bake Base Color/Normal/Roughness to PNG if Roblox SurfaceAppearance fidelity is desired."
            )
            findings.append(
                CheckFinding(
                    check_id="pbr_maps",
                    dimension="materials_textures",
                    name="Roblox Material Mode",
                    status="PASS",
                    measured="Procedural Principled BSDF",
                    threshold="Simple/Procedural Roblox material allowed",
                    message="Simple/procedural Principled BSDF material permitted without external texture files.",
                )
            )
        else:
            findings.append(
                CheckFinding(
                    check_id="pbr_maps",
                    dimension="materials_textures",
                    name="Roblox Material Mode",
                    status="PASS",
                    measured="Simple Principled BSDF",
                    threshold="Allowed",
                    message="Simple Roblox-compatible material without external texture dependencies.",
                )
            )

    # 4. Texture dimensions check
    oversized = [d for d in image_dims if max(d) > max_tex_dim]
    undersized = [d for d in image_dims if min(d) < min_tex_dim]
    if oversized:
        score -= 1.0
        msg = f"Texture resolution exceeds Roblox 1024px cap: {oversized}."
        warnings.append(msg)
        recommendations.append(f"Downscale textures to <= {max_tex_dim}x{max_tex_dim} for Roblox memory limits.")
        findings.append(
            CheckFinding(
                check_id="texture_dimensions",
                dimension="materials_textures",
                name="Texture Resolution",
                status="WARN",
                measured=oversized,
                threshold=f"{min_tex_dim}–{max_tex_dim}px",
                message=msg,
            )
        )
    elif undersized:
        score -= 0.5
        warnings.append(f"Very low resolution texture detected: {undersized}.")

    # 5. Unsupported shader nodes
    if unsupported:
        score -= 1.5
        msg = f"Unsupported material configurations for Roblox export: {'; '.join(unsupported)}."
        warnings.append(msg)
        recommendations.append("Use Principled BSDF shaders for reliable FBX/GLB export to Roblox.")
        findings.append(
            CheckFinding(
                check_id="material_compatibility",
                dimension="materials_textures",
                name="Shader Compatibility",
                status="WARN",
                measured=unsupported,
                threshold="Principled BSDF",
                message=msg,
            )
        )

    return DimensionScoreResult(
        key="materials_textures",
        label="Materials & Textures",
        score=round(max(0.0, min(max_score, score)), 2),
        max_score=max_score,
        findings=findings,
        failures=failures,
        warnings=warnings,
        recommendations=recommendations,
    )
