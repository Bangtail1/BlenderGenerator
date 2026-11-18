"""Tests for Technical QA inspection, defective asset detection, and category-aware geometry rules."""

from __future__ import annotations

from pathlib import Path

import pytest

from blender_vault.blender_worker.runner import BlenderWorkerRunner
from blender_vault.config import Settings
from blender_vault.models import GeometryProfile, TechnicalQAStatus
from blender_vault.services.qa_rules import evaluate_technical_qa


def _generate_and_inspect(tmp_path: Path, template_id: str, profile: GeometryProfile):
    cfg = Settings()
    runner = BlenderWorkerRunner(cfg.blender_executable, timeout_seconds=120)
    work = tmp_path / template_id
    work.mkdir(parents=True, exist_ok=True)
    blend = work / f"{template_id}.blend"
    glb = work / f"{template_id}.glb"

    runner.run_script(
        "generate.py",
        {
            "asset_name": template_id,
            "prompt": template_id,
            "template_id": template_id,
            "output_blend": str(blend.resolve()),
            "output_glb": str(glb.resolve()),
        },
        work / "gen",
    )
    raw = runner.run_script(
        "inspect_qa.py",
        {
            "blend_path": str(blend.resolve()),
            "roundtrip_glb": str((work / "rt.glb").resolve()),
        },
        work / "qa",
    )
    return evaluate_technical_qa(raw, profile)


@pytest.mark.parametrize(
    "defect_template,expected_failed_check",
    [
        ("defect_empty_scene", "mesh_presence"),
        ("defect_zero_area_and_loose", "degenerate_faces"),
        ("defect_inverted_normals", "face_normals"),
        ("defect_unapplied_scale_and_no_material", "object_scale"),
        ("defect_missing_texture", "external_dependencies"),
    ],
)
def test_defective_models_are_caught(tmp_path: Path, defect_template: str, expected_failed_check: str) -> None:
    report = _generate_and_inspect(tmp_path, defect_template, GeometryProfile.GENERAL)
    assert report.status == TechnicalQAStatus.FAIL
    failed_ids = {c.check_id for c in report.checks if c.status == "FAIL"}
    assert expected_failed_check in failed_ids


def test_category_aware_watertight_rules(tmp_path: Path) -> None:
    # Open-boundary leaf passes under ORGANIC_NATURE, fails under SOLID_MECHANICAL
    leaf_nature = _generate_and_inspect(tmp_path / "nature", "stylized_leaf", GeometryProfile.ORGANIC_NATURE)
    assert leaf_nature.status == TechnicalQAStatus.PASS

    leaf_mech = _generate_and_inspect(tmp_path / "mech", "stylized_leaf", GeometryProfile.SOLID_MECHANICAL)
    assert leaf_mech.status == TechnicalQAStatus.FAIL
    assert any(c.check_id == "boundary_edges" and c.status == "FAIL" for c in leaf_mech.checks)

    # Precision spur gear passes under SOLID_MECHANICAL cleanly
    gear_mech = _generate_and_inspect(tmp_path / "gear", "mechanical_gear", GeometryProfile.SOLID_MECHANICAL)
    assert gear_mech.status == TechnicalQAStatus.PASS
    assert gear_mech.round_trip.success is True
