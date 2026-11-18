"""Phase 6 & Phase 7 tests: isolated Blender 5.3 reference inspection and category-specific 100-pt grading."""

from __future__ import annotations

import zipfile
from pathlib import Path

from blender_vault.blender_worker.runner import BlenderWorkerRunner
from blender_vault.config import Settings
from blender_vault.hashing import compute_sha256
from blender_vault.reference.grading_engine import QualityGradingEngine
from blender_vault.reference.inspector_service import ReferenceInspectorService
from blender_vault.reference.models import ReferenceInspectionReport


def test_real_blender_inspection_preserves_original_and_grades_chair(tmp_path: Path) -> None:
    cfg = Settings(vault_data_dir=tmp_path / "vault")
    cfg.ensure_directories()
    runner = BlenderWorkerRunner(cfg.blender_executable, timeout_seconds=120)
    inspector = ReferenceInspectorService(cfg, runner)
    grader = QualityGradingEngine()

    sample_blend = Path("samples/sample_wooden_chair/oak_dining_chair.blend").resolve()
    assert sample_blend.is_file()
    orig_sha = compute_sha256(sample_blend)

    # Also test inspecting inside a .zip archive
    zip_path = tmp_path / "chair_bundle.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.write(sample_blend, arcname="nested/oak_dining_chair.blend")

    report = inspector.inspect_asset_file(source_file=zip_path, blendswap_id=1001, preview_image_count=2)
    assert compute_sha256(sample_blend) == orig_sha  # Original file untouched

    assert report.file_opened_ok is True
    assert report.inspection_completed_ok is True
    assert report.is_native_blender is True
    assert report.mesh_object_count == 9
    assert report.vertex_count > 1000
    assert report.triangle_count > 5000
    assert report.connected_components >= 9
    assert report.has_uv_map is True
    assert report.uv_coverage_ratio > 0.0
    assert report.material_count == 1
    assert report.procedural_shader_count >= 1
    assert report.has_camera is False  # No camera in chair .blend

    # Grade must stay high (A/A+) despite lacking camera/lights in .blend
    grade = grader.grade(
        report,
        primary_category="Furniture",
        technical_categories=["hard-surface", "mid-poly", "procedural"],
        popularity_score=10.0,  # Low popularity must not penalize technical score
        blendswap_id=1001,
    )
    assert grade.total_score >= 90.0
    assert grade.grade_letter in {"A", "A+"}
    assert grade.methodology_version == "2.0.0"
    assert grade.grading_profile == "furniture"
    assert sum(d.max_score for d in grade.dimensions) == 100.0


def test_real_blender_detects_defects_and_decouples_popularity_from_quality(tmp_path: Path) -> None:
    cfg = Settings(vault_data_dir=tmp_path / "vault")
    cfg.ensure_directories()
    runner = BlenderWorkerRunner(cfg.blender_executable, timeout_seconds=120)
    inspector = ReferenceInspectorService(cfg, runner)
    grader = QualityGradingEngine()

    bad_blend = tmp_path / "bad_mesh.blend"
    runner.run_script(
        "generate.py",
        {
            "asset_name": "Bad Popular Mesh",
            "prompt": "defect",
            "template_id": "defect_zero_area_and_loose",
            "output_blend": str(bad_blend),
        },
        tmp_path / "gen_bad",
    )
    orig_sha = compute_sha256(bad_blend)

    report = inspector.inspect_asset_file(source_file=bad_blend, blendswap_id=777)
    assert compute_sha256(bad_blend) == orig_sha
    assert report.file_opened_ok is True
    assert report.zero_area_faces >= 1
    assert report.loose_verts >= 2
    assert report.loose_edges >= 1
    assert report.has_uv_map is False

    # High popularity (99.0) MUST NOT inflate technical grade!
    grade = grader.grade(
        report,
        primary_category="Furniture",
        technical_categories=["hard-surface"],
        popularity_score=99.0,
        blendswap_id=777,
    )
    assert grade.popularity_score == 99.0
    assert grade.total_score < 70.0
    assert grade.grade_letter in {"D", "F"}


def test_category_specific_grading_profiles_diverge_appropriately() -> None:
    grader = QualityGradingEngine()
    triangulated_sculpt = ReferenceInspectionReport(
        inspection_id="s1",
        blendswap_id=888,
        file_opened_ok=True,
        inspection_completed_ok=True,
        mesh_object_count=1,
        object_count=1,
        vertex_count=15000,
        edge_count=45000,
        face_count=30000,
        polygon_count=30000,
        triangle_count=30000,
        quad_ratio=0.05,
        triangle_ratio=0.95,
        ngon_ratio=0.0,
        mesh_cleanliness_score=98.0,
        edge_flow_score=30.0,
        has_uv_map=False,
        material_count=1,
        principled_bsdf_count=1,
        procedural_shader_count=1,
        naming_consistency_ratio=1.0,
        collection_count=1,
        object_separation_score=85.0,
        renderable=True,
    )
    sculpt_grade = grader.grade(
        triangulated_sculpt,
        primary_category="Characters",
        technical_categories=["sculpted", "organic"],
    )
    char_grade = grader.grade(
        triangulated_sculpt,
        primary_category="Characters",
        technical_categories=["organic"],
    )
    assert sculpt_grade.grading_profile == "organic_sculpted"
    assert char_grade.grading_profile == "character"
    assert sculpt_grade.total_score > char_grade.total_score + 15.0

    # Rigged profile checks armature + weights
    unrigged_as_rigged = grader.grade(
        triangulated_sculpt,
        primary_category="Characters",
        technical_categories=["rigged"],
    )
    assert unrigged_as_rigged.grading_profile == "rigged"
    assert any("armature" in w.lower() for w in unrigged_as_rigged.weaknesses)
