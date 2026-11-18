"""Phase 5 tests: independent 100-point Roblox QA engine across all 10 checks."""

from __future__ import annotations

from pathlib import Path

import pytest

from blender_vault.blender_worker.runner import BlenderWorkerRunner
from blender_vault.config import Settings
from blender_vault.roblox_qa import RobloxQAEngine
from blender_vault.roblox_qa.uv_checks import evaluate_uv_quality


def test_independent_qa_on_existing_sample_chair(tmp_path: Path) -> None:
    cfg = Settings(
        vault_data_dir=tmp_path / "vault",
        roblox_output_dir=tmp_path / "output",
        blender_render_resolution=256,
        blender_render_samples=8,
    )
    cfg.ensure_directories()
    engine = RobloxQAEngine(cfg)

    sample_blend = Path("samples/sample_wooden_chair/oak_dining_chair.blend").resolve()
    assert sample_blend.is_file()

    report = engine.evaluate_blend(
        blend_path=sample_blend,
        asset_id="FUR_00001_Wooden_Chair_Basic_LP_v001",
        asset_name="Oak Dining Chair",
        asset_version=1,
        department_code="FUR",
        export_path=tmp_path / "export" / "oak_dining_chair.fbx",
        export_format="FBX",
        previews_dir=tmp_path / "previews",
        render_previews=True,
    )

    assert report.overall_score >= 85.0
    assert report.grade in {"A", "B"}
    assert report.final_status == "QA_PASSED"
    assert report.approval_eligible is True
    assert report.human_approval_required is True
    # Honest disclaimers
    assert report.ai_visual_inspection_performed is False
    assert report.roblox_studio_automation_available is False
    assert len(report.roblox_studio_checklist) >= 4

    # All 10 categories scored
    assert len(report.individual_scores) == 10
    assert report.individual_scores["mesh_integrity"] == 15.0  # Multi-part 9 objects not penalized!
    assert report.individual_scores["roblox_compatibility"] >= 14.0
    assert report.export_validation["roundtrip"]["success"] is True
    assert Path(report.export_validation["export_path"]).is_file()
    assert Path(report.contact_sheet_path).is_file()
    for view in ("front", "back", "left", "right", "top", "three_quarter"):
        assert Path(report.preview_images[view]).is_file()


@pytest.mark.parametrize(
    "defect_template",
    [
        "defect_empty_scene",
        "defect_zero_area_and_loose",
        "defect_inverted_normals",
        "defect_unapplied_scale_and_no_material",
        "defect_missing_texture",
    ],
)
def test_roblox_qa_rejects_defective_assets_with_mandatory_failures(tmp_path: Path, defect_template: str) -> None:
    cfg = Settings(
        vault_data_dir=tmp_path / "vault",
        roblox_output_dir=tmp_path / "output",
        blender_render_resolution=256,
        blender_render_samples=8,
    )
    cfg.ensure_directories()
    runner = BlenderWorkerRunner(cfg.blender_executable, timeout_seconds=120)
    blend = tmp_path / f"{defect_template}.blend"
    runner.run_script(
        "generate.py",
        {
            "asset_name": defect_template,
            "prompt": defect_template,
            "template_id": defect_template,
            "output_blend": str(blend.resolve()),
        },
        tmp_path / f"gen_{defect_template}",
    )

    engine = RobloxQAEngine(cfg, runner=runner)
    report = engine.evaluate_blend(
        blend_path=blend,
        asset_id=f"CAL_00001_{defect_template}_LP_v001",
        asset_name=defect_template,
        department_code="CAL",
        render_previews=False,
    )
    assert report.final_status == "QA_FAILED"
    assert report.approval_eligible is False
    assert len(report.mandatory_failures) >= 1


def test_decorative_asset_collision_and_open_leaf_rules(tmp_path: Path) -> None:
    cfg = Settings(
        vault_data_dir=tmp_path / "vault",
        roblox_output_dir=tmp_path / "output",
    )
    cfg.ensure_directories()
    runner = BlenderWorkerRunner(cfg.blender_executable, timeout_seconds=120)
    leaf_blend = tmp_path / "leaf.blend"
    runner.run_script(
        "generate.py",
        {
            "asset_name": "Botanical Leaf",
            "prompt": "Create a stylized leaf",
            "template_id": "stylized_leaf",
            "output_blend": str(leaf_blend.resolve()),
        },
        tmp_path / "gen_leaf",
    )
    engine = RobloxQAEngine(cfg, runner=runner)
    report = engine.evaluate_blend(
        blend_path=leaf_blend,
        asset_id="ENV_00001_Botanical_Leaf_Basic_LP_v001",
        asset_name="Botanical Leaf",
        department_code="ENV",
        export_format="OBJ",
        render_previews=False,
    )
    # Open boundary leaf permitted under ENV, no collision required
    assert report.collision_assessment.requires_collision is False
    assert report.individual_scores["collision_usability"] == 5.0
    assert report.export_validation["export_format"] == "OBJ"
    assert report.export_validation["roundtrip"]["success"] is True


def test_optimized_chair_variant_cuts_triangles_and_packs_clean_uvs(tmp_path: Path) -> None:
    cfg = Settings(
        vault_data_dir=tmp_path / "vault",
        roblox_output_dir=tmp_path / "output",
        blender_render_resolution=256,
        blender_render_samples=8,
    )
    cfg.ensure_directories()
    runner = BlenderWorkerRunner(cfg.blender_executable, timeout_seconds=120)
    opt_blend = tmp_path / "oak_chair_optimized_v2.blend"
    runner.run_script(
        "generate.py",
        {
            "asset_name": "Oak Dining Chair Optimized",
            "prompt": "Create a wooden chair",
            "template_id": "wooden_chair",
            "params": {"optimize_topology": True, "clean_uv_pack": True},
            "output_blend": str(opt_blend.resolve()),
        },
        tmp_path / "gen_opt",
    )

    engine = RobloxQAEngine(cfg, runner=runner)
    rep = engine.evaluate_blend(
        blend_path=opt_blend,
        asset_id="FUR_00001_Wooden_Chair_Basic_LP_v002",
        asset_name="Oak Dining Chair",
        asset_version=2,
        department_code="FUR",
        export_format="FBX",
        previews_dir=tmp_path / "previews_v2",
        render_previews=True,
    )
    assert rep.triangle_count < 6000
    assert rep.individual_scores["polygon_efficiency"] == 10.0
    assert rep.individual_scores["uv_quality"] == 10.0
    assert rep.uv_status == "PASSED"
    assert rep.final_status == "QA_PASSED"
    assert rep.grade == "A"


def test_uv_classifier_distinguishes_procedural_overlap_vs_image_texture_overlap() -> None:
    policy = {"require_uv": True, "require_pbr_maps": False}
    base_uv = {
        "has_uv": True,
        "mapped_faces": 100,
        "unmapped_faces": 0,
        "invalid_coords": 0,
        "out_of_unit_range_loops": 200,
        "extreme_uv_loops": 0,
        "overlap_cell_ratio": 0.72,
        "self_overlap_cell_ratio": 0.0,
        "uv_layout_mode": "INTENTIONAL_BOX_OR_TILED",
    }
    proc_inspection = {
        "objects": [{"name": "Chair_Seat", "has_uv": True, "face_count": 100, "uv_stats": base_uv}],
        "material_inspection": {"image_texture_count": 0, "procedural_material_count": 1},
    }
    proc_res = evaluate_uv_quality(proc_inspection, policy, max_score=10.0)
    assert proc_res.score == 10.0
    assert not proc_res.warnings

    img_uv = dict(base_uv, out_of_unit_range_loops=0, uv_layout_mode="UNPACKED_OVERLAP")
    img_inspection = {
        "objects": [{"name": "Prop_Mesh", "has_uv": True, "face_count": 100, "uv_stats": img_uv}],
        "material_inspection": {"image_texture_count": 1, "procedural_material_count": 0},
    }
    img_res = evaluate_uv_quality(img_inspection, policy, max_score=10.0)
    assert img_res.score < 10.0
    assert len(img_res.warnings) >= 1
