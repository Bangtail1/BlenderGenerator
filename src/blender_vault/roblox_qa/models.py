"""Data models and rule loader for the Roblox Automated 3D Quality Assurance engine."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

from ..database.models import utc_now_iso


class CheckFinding(BaseModel):
    check_id: str
    dimension: str
    name: str
    status: str  # PASS | WARN | FAIL | INFO
    mandatory: bool = False
    measured: Any = None
    threshold: Any = None
    message: str
    recommendation: Optional[str] = None


class DimensionScoreResult(BaseModel):
    key: str
    label: str
    score: float
    max_score: float
    findings: list[CheckFinding] = Field(default_factory=list)
    failures: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)


class CollisionAssessment(BaseModel):
    requires_collision: bool = False
    has_dedicated_collision: bool = False
    collision_mesh_count: int = 0
    collision_tri_count: int = 0
    recommended_fidelity: str = "Hull"
    collision_status: str = "PASSED"  # PASSED | WARNING | FAILED | NOT_REQUIRED
    notes: list[str] = Field(default_factory=list)


class RobloxStudioChecklistItem(BaseModel):
    step: int
    check: str
    expected: str
    automated_in_blender: bool = False
    manual_studio_verification_required: bool = True


class RobloxQAOutput(BaseModel):
    asset_id: str
    asset_name: str
    asset_version: int
    department_code: str
    budget_tier: str
    test_timestamp: str = Field(default_factory=utc_now_iso)
    blender_version: str = ""
    polygon_count: int = 0
    triangle_count: int = 0
    vertex_count: int = 0
    object_count: int = 0
    dimensions_meters: list[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    dimensions_studs: list[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    texture_info: dict[str, Any] = Field(default_factory=dict)
    material_info: dict[str, Any] = Field(default_factory=dict)
    export_validation: dict[str, Any] = Field(default_factory=dict)
    collision_assessment: CollisionAssessment = Field(default_factory=CollisionAssessment)
    uv_status: str = "PASSED"
    individual_scores: dict[str, float] = Field(default_factory=dict)
    dimensions_breakdown: list[DimensionScoreResult] = Field(default_factory=list)
    overall_score: float = 0.0
    grade: str = "D"
    final_status: str = "QA_FAILED"  # QA_PASSED | QA_FAILED
    approval_eligible: bool = False
    human_approval_required: bool = True
    ai_visual_inspection_performed: bool = False
    visual_inspection_note: str = (
        "Automated preview rendering & pixel sanity verified; human visual inspection is required before final approval."
    )
    roblox_studio_automation_available: bool = False
    roblox_studio_limitation_note: str = (
        "Direct Roblox Studio import automation is not available in this headless environment; "
        "complete the manual Roblox Studio import checklist before publishing."
    )
    roblox_studio_checklist: list[RobloxStudioChecklistItem] = Field(default_factory=list)
    preview_images: dict[str, str] = Field(default_factory=dict)
    contact_sheet_path: Optional[str] = None
    mandatory_failures: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    findings: list[CheckFinding] = Field(default_factory=list)


class QARulesConfig:
    """Loads configurable Roblox QA budgets, scoring weights, and department policies."""

    def __init__(self, rules_path: Optional[Path] = None) -> None:
        self.rules_path = rules_path or Path("config/qa_rules.json")
        if self.rules_path.is_file():
            self.raw: dict[str, Any] = json.loads(self.rules_path.read_text(encoding="utf-8"))
        else:
            self.raw = {}

    @property
    def scoring_weights(self) -> dict[str, float]:
        defaults = {
            "mesh_integrity": 15.0,
            "geometry_topology": 10.0,
            "polygon_efficiency": 10.0,
            "uv_quality": 10.0,
            "materials_textures": 10.0,
            "proportions_scale": 10.0,
            "roblox_compatibility": 15.0,
            "collision_usability": 5.0,
            "visual_quality": 10.0,
            "organisation_naming": 5.0,
        }
        defaults.update(self.raw.get("scoring_weights", {}))
        return {k: float(v) for k, v in defaults.items()}

    @property
    def roblox_limits(self) -> dict[str, Any]:
        defaults = {
            "max_tris_per_mesh_part": 20000,
            "max_texture_dimension": 1024,
            "min_texture_dimension": 64,
            "supported_export_formats": ["FBX", "GLB", "OBJ"],
            "studs_per_meter": 3.57142857,
            "require_applied_scale": True,
            "max_uv_coordinate_abs": 4.0,
            "standard_uv_range": [0.0, 1.0],
        }
        defaults.update(self.raw.get("roblox_limits", {}))
        return defaults

    @property
    def minimum_passing_score(self) -> float:
        return float(self.raw.get("minimum_passing_score", 80.0))

    def resolve_department_policy(self, department_code: str, *, tier_override: Optional[str] = None) -> dict[str, Any]:
        code = (department_code or "PRP").strip().upper()
        dept_rules = self.raw.get("department_rules", {})
        dept = dict(
            dept_rules.get(
                code,
                {
                    "tier": "medium_prop",
                    "allow_open_boundary": False,
                    "allow_multi_part": True,
                    "require_uv": True,
                    "require_pbr_maps": False,
                    "allow_procedural_materials": True,
                    "expected_height_m": [0.05, 10.0],
                    "recommended_collision": "Hull",
                },
            )
        )
        tier_name = tier_override or dept.get("tier", "medium_prop")
        tiers = self.raw.get("budget_tiers", {})
        tier = dict(
            tiers.get(
                tier_name,
                {
                    "min_tris": 24,
                    "optimal_min_tris": 200,
                    "optimal_max_tris": 4000,
                    "warning_max_tris": 12000,
                    "hard_max_tris": 25000,
                    "expected_max_dim_m": [0.1, 6.0],
                    "collision_required": False,
                    "recommended_collision": "Hull",
                },
            )
        )
        merged = {**tier, **dept, "tier_name": tier_name, "department_code": code}
        return merged
