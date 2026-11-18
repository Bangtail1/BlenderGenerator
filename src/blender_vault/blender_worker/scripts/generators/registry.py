"""Procedural template registry, natural-language prompt resolver, and modular external Text-to-3D interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Callable, Optional

PUBLIC_TEMPLATE_METADATA: dict[str, dict[str, Any]] = {
    "wooden_chair": {
        "label": "Wooden Chair",
        "category": "Furniture",
        "default_prompt": "Create a detailed wooden chair with four tapered legs, a sculpted seat, and a curved slat backrest",
        "default_poly_budget": 16000,
    },
    "wooden_table": {
        "label": "Wooden Table",
        "category": "Furniture",
        "default_prompt": "Create a solid timber dining table with chamfered tabletop and tapered legs",
        "default_poly_budget": 8000,
    },
    "mechanical_gear": {
        "label": "Mechanical Gear",
        "category": "Mechanical Objects",
        "default_prompt": "Create a precision metallic spur gear with clean teeth and center hub",
        "default_poly_budget": 12000,
    },
    "ceramic_vase": {
        "label": "Ceramic Vase",
        "category": "Household Objects",
        "default_prompt": "Create a smooth glazed ceramic vase with tapered neck and flared rim",
        "default_poly_budget": 10000,
    },
    "stylized_leaf": {
        "label": "Stylized Leaf",
        "category": "Nature",
        "default_prompt": "Create a stylized botanical leaf with central stem and gentle arch",
        "default_poly_budget": 4000,
    },
    "test_cube": {
        "label": "Calibrated Test Cube",
        "category": "Miscellaneous",
        "default_prompt": "Create a beveled calibrated test cube with clean UVs and Principled BSDF material",
        "default_poly_budget": 1200,
    },
    "test_uv_sphere": {
        "label": "Calibrated UV Sphere",
        "category": "Miscellaneous",
        "default_prompt": "Create a clean UV sphere with proper seam unwrapping and Principled BSDF surface",
        "default_poly_budget": 4000,
    },
}

try:
    from .defective_fixtures import (
        generate_defect_empty_scene,
        generate_defect_inverted_normals,
        generate_defect_missing_texture,
        generate_defect_unapplied_scale_and_no_material,
        generate_defect_zero_area_and_loose,
    )
    from .primitive_test import (
        generate_ceramic_vase,
        generate_mechanical_gear,
        generate_stylized_leaf,
        generate_test_cube,
        generate_test_uv_sphere,
        generate_wooden_table,
    )
    from .wooden_chair import generate_wooden_chair
except ImportError:  # Host Python outside Blender lacks bpy/bmesh
    def _blender_only_stub(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("Procedural generator callables must execute inside Blender's Python runtime.")

    generate_defect_empty_scene = _blender_only_stub
    generate_defect_inverted_normals = _blender_only_stub
    generate_defect_missing_texture = _blender_only_stub
    generate_defect_unapplied_scale_and_no_material = _blender_only_stub
    generate_defect_zero_area_and_loose = _blender_only_stub
    generate_ceramic_vase = _blender_only_stub
    generate_mechanical_gear = _blender_only_stub
    generate_stylized_leaf = _blender_only_stub
    generate_test_cube = _blender_only_stub
    generate_test_uv_sphere = _blender_only_stub
    generate_wooden_table = _blender_only_stub
    generate_wooden_chair = _blender_only_stub


class UnsupportedGenerationRequestError(ValueError):
    """Raised when a prompt does not match a verified procedural template and no external 3D generator is configured."""


class ExternalTextTo3DProvider(ABC):
    """Modular interface for plugging in genuine external text-to-3D mesh generation services."""

    provider_name: str = "abstract"

    @abstractmethod
    def is_configured(self) -> bool:
        ...

    @abstractmethod
    def generate_mesh_file(self, prompt: str, output_dir: Path, params: dict[str, Any]) -> Path:
        """Generate a real 3D mesh file (.glb/.obj/.blend) from prompt. Must never fake 3D from 2D."""


class DisabledExternalTextTo3DProvider(ExternalTextTo3DProvider):
    provider_name = "none"

    def is_configured(self) -> bool:
        return False

    def generate_mesh_file(self, prompt: str, output_dir: Path, params: dict[str, Any]) -> Path:
        raise UnsupportedGenerationRequestError(
            "No external text-to-3D provider is configured. "
            "Supported built-in procedural templates: "
            + ", ".join(sorted(PUBLIC_TEMPLATES.keys()))
        )


PUBLIC_TEMPLATES: dict[str, Callable[[str, dict[str, Any]], dict[str, Any]]] = {
    "wooden_chair": generate_wooden_chair,
    "wooden_table": generate_wooden_table,
    "mechanical_gear": generate_mechanical_gear,
    "ceramic_vase": generate_ceramic_vase,
    "stylized_leaf": generate_stylized_leaf,
    "test_cube": generate_test_cube,
    "test_uv_sphere": generate_test_uv_sphere,
}

INTERNAL_DEFECT_TEMPLATES: dict[str, Callable[[str, dict[str, Any]], dict[str, Any]]] = {
    "defect_empty_scene": generate_defect_empty_scene,
    "defect_zero_area_and_loose": generate_defect_zero_area_and_loose,
    "defect_inverted_normals": generate_defect_inverted_normals,
    "defect_unapplied_scale_and_no_material": generate_defect_unapplied_scale_and_no_material,
    "defect_missing_texture": generate_defect_missing_texture,
}


def resolve_prompt_to_template(
    prompt: str,
    *,
    explicit_template_id: Optional[str] = None,
    params: Optional[dict[str, Any]] = None,
) -> tuple[str, dict[str, Any]]:
    """Map a user prompt or template_id into a concrete procedural generator and parameters."""
    merged_params = dict(params or {})

    if explicit_template_id:
        if explicit_template_id in PUBLIC_TEMPLATES or explicit_template_id in INTERNAL_DEFECT_TEMPLATES:
            return explicit_template_id, merged_params
        raise UnsupportedGenerationRequestError(
            f"Unknown procedural template_id '{explicit_template_id}'. "
            f"Available templates: {', '.join(sorted(PUBLIC_TEMPLATES.keys()))}"
        )

    text = f"{prompt or ''} {merged_params.get('additional_instructions', '')}".strip().lower()
    if not text:
        raise UnsupportedGenerationRequestError("Prompt cannot be empty.")

    complexity = str(merged_params.get("complexity", "medium")).lower()
    poly_budget = merged_params.get("poly_budget")
    if complexity == "low" or (isinstance(poly_budget, int) and 0 < poly_budget <= 8000):
        merged_params.setdefault("optimize_topology", True)
        merged_params.setdefault("clean_uv_pack", True)
    elif complexity == "high":
        merged_params.setdefault("optimize_topology", False)
        merged_params.setdefault("clean_uv_pack", True)

    # Extract wood tone hints if mentioned
    for tone in ("walnut", "cherry", "pine", "ebony", "oak"):
        if tone in text and "wood_tone" not in merged_params:
            merged_params["wood_tone"] = tone
            break

    if "chair" in text:
        if "slat" in text and "5" in text:
            merged_params.setdefault("slat_count", 5)
        elif "solid" in text or "single" in text:
            merged_params.setdefault("slat_count", 1)
        return "wooden_chair", merged_params

    if "table" in text or "desk" in text:
        return "wooden_table", merged_params

    if "gear" in text or "cog" in text or "sprocket" in text:
        return "mechanical_gear", merged_params

    if "vase" in text or "pot" in text or "urn" in text:
        return "ceramic_vase", merged_params

    if "leaf" in text or "foliage" in text:
        return "stylized_leaf", merged_params

    if "sphere" in text or "ball" in text:
        return "test_uv_sphere", merged_params

    if "cube" in text or "box" in text:
        return "test_cube", merged_params

    raise UnsupportedGenerationRequestError(
        f"Prompt '{prompt}' does not match any verified procedural template, and no external text-to-3D service is configured. "
        f"Supported procedural templates: {', '.join(sorted(PUBLIC_TEMPLATES.keys()))}."
    )


def run_procedural_generator(
    *,
    asset_name: str,
    prompt: str,
    template_id: Optional[str] = None,
    params: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    resolved_id, resolved_params = resolve_prompt_to_template(
        prompt,
        explicit_template_id=template_id,
        params=params,
    )
    fn = PUBLIC_TEMPLATES.get(resolved_id) or INTERNAL_DEFECT_TEMPLATES[resolved_id]
    return fn(asset_name, resolved_params)
