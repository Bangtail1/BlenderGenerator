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


def test_archive_extraction_limits_and_gltf_external_buffer_copy(tmp_path: Path) -> None:
    import json
    import struct
    import tarfile
    from io import BytesIO
    import pytest
    from blender_vault.reference.inspector_service import _safe_extract_tar, _safe_extract_zip

    # 1. ZIP and TAR member count & byte limits
    zip_many = tmp_path / "many.zip"
    with zipfile.ZipFile(zip_many, "w") as zf:
        zf.writestr("a.txt", b"12345")
        zf.writestr("b.txt", b"12345")
        zf.writestr("c.txt", b"12345")

    with pytest.raises(ValueError, match="member limit"):
        _safe_extract_zip(zip_many, tmp_path / "out_z1", max_members=2)
    with pytest.raises(ValueError, match="size budget"):
        _safe_extract_zip(zip_many, tmp_path / "out_z2", max_bytes=8)

    tar_many = tmp_path / "many.tar"
    with tarfile.open(tar_many, "w") as tf:
        for name in ("a.txt", "b.txt", "c.txt"):
            data = b"123456"
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tf.addfile(info, BytesIO(data))

    with pytest.raises(ValueError, match="member limit"):
        _safe_extract_tar(tar_many, tmp_path / "out_t1", max_members=2)
    with pytest.raises(ValueError, match="size budget"):
        _safe_extract_tar(tar_many, tmp_path / "out_t2", max_bytes=10)

    # 2. Standalone .gltf with external .bin buffer copies buffer into isolated workspace and imports cleanly
    gltf_dir = tmp_path / "gltf_src"
    buf_dir = gltf_dir / "buffers"
    buf_dir.mkdir(parents=True)
    # Single triangle: 3 float32 positions (36 bytes)
    bin_bytes = struct.pack("<9f", 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0)
    (buf_dir / "tri.bin").write_bytes(bin_bytes)
    gltf_doc = {
        "asset": {"version": "2.0"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
        "buffers": [{"uri": "buffers/tri.bin", "byteLength": len(bin_bytes)}],
        "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": len(bin_bytes), "target": 34962}],
        "accessors": [
            {
                "bufferView": 0,
                "componentType": 5126,
                "count": 3,
                "type": "VEC3",
                "min": [0.0, 0.0, 0.0],
                "max": [1.0, 1.0, 0.0],
            }
        ],
    }
    gltf_path = gltf_dir / "triangle.gltf"
    gltf_path.write_text(json.dumps(gltf_doc), encoding="utf-8")

    cfg = Settings(vault_data_dir=tmp_path / "vault")
    cfg.ensure_directories()
    runner = BlenderWorkerRunner(cfg.blender_executable, timeout_seconds=120)
    inspector = ReferenceInspectorService(cfg, runner)
    report = inspector.inspect_asset_file(source_file=gltf_path, blendswap_id=9001)
    assert report.file_opened_ok is True
    assert report.inspection_completed_ok is True
    assert report.vertex_count == 3
    assert report.triangle_count == 1
