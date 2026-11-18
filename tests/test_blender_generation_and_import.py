"""Integration tests for real Blender procedural generation and multi-format importing."""

from __future__ import annotations

from pathlib import Path

import pytest

from blender_vault.blender_worker.runner import BlenderWorkerRunner
from blender_vault.config import Settings
from blender_vault.db import VaultDatabase
from blender_vault.models import AssetStatus, GenerateAssetRequest, ProvenanceType, TechnicalQAStatus
from blender_vault.services.gdrive_service import GoogleDriveSyncService
from blender_vault.services.pipeline import AssetPipelineService, DuplicateAssetError, UnsupportedModelFormatError
from blender_vault.services.storage import VaultStorage


def _build_pipeline(tmp_path: Path) -> AssetPipelineService:
    base_cfg = Settings()
    cfg = Settings(
        vault_data_dir=tmp_path / "vault",
        blender_executable=base_cfg.blender_executable,
        blender_render_resolution=256,
        blender_render_samples=8,
    )
    cfg.ensure_directories()
    db = VaultDatabase(cfg.db_path)
    storage = VaultStorage(cfg.assets_dir, cfg.imports_dir)
    runner = BlenderWorkerRunner(cfg.blender_executable, timeout_seconds=120)
    gdrive = GoogleDriveSyncService(cfg, db, storage)
    return AssetPipelineService(cfg, db, storage, runner, gdrive)


def test_generate_test_cube_and_uv_sphere(tmp_path: Path) -> None:
    pipe = _build_pipeline(tmp_path)
    cube = pipe.generate_procedural_asset(
        GenerateAssetRequest(prompt="Create a calibrated test cube", name="Unit Test Cube", template_id="test_cube")
    )
    assert cube.latest is not None
    assert cube.latest.status == AssetStatus.AWAITING_HUMAN_APPROVAL
    assert cube.latest.technical_qa is not None
    assert cube.latest.technical_qa.status == TechnicalQAStatus.PASS
    assert Path(cube.latest.blend_path).is_file()
    assert Path(cube.latest.glb_path).is_file()

    sphere = pipe.generate_procedural_asset(
        GenerateAssetRequest(prompt="Create a UV sphere", name="Unit Test Sphere", template_id="test_uv_sphere")
    )
    assert sphere.latest is not None
    assert sphere.latest.technical_qa.status == TechnicalQAStatus.PASS


def test_unsupported_prompt_fails_honestly(tmp_path: Path) -> None:
    pipe = _build_pipeline(tmp_path)
    res = pipe.generate_procedural_asset(
        GenerateAssetRequest(prompt="Create a hyperrealistic medieval dragon with rigged wings", name="Dragon")
    )
    assert res.latest is not None
    assert res.latest.status == AssetStatus.FAILED
    assert "does not match any verified procedural template" in (res.latest.error_message or "")


def test_import_obj_glb_and_reject_unsupported_format(tmp_path: Path) -> None:
    pipe = _build_pipeline(tmp_path)

    # Unsupported extension must raise cleanly
    bad_file = tmp_path / "notes.txt"
    bad_file.write_text("not a 3d model", encoding="utf-8")
    with pytest.raises(UnsupportedModelFormatError):
        pipe.import_model_file(temp_upload_path=bad_file, original_filename="notes.txt")

    # Create a real minimal valid OBJ mesh
    obj_file = tmp_path / "crate.obj"
    obj_file.write_text(
        "o Test_Crate\n"
        "v -0.5 -0.5 0.0\nv 0.5 -0.5 0.0\nv 0.5 0.5 0.0\nv -0.5 0.5 0.0\n"
        "v -0.5 -0.5 1.0\nv 0.5 -0.5 1.0\nv 0.5 0.5 1.0\nv -0.5 0.5 1.0\n"
        "f 1 4 3 2\nf 5 6 7 8\nf 1 2 6 5\nf 2 3 7 6\nf 3 4 8 7\nf 4 1 5 8\n",
        encoding="utf-8",
    )
    imported = pipe.import_model_file(
        temp_upload_path=obj_file,
        original_filename="crate.obj",
        name="Imported Crate",
        category="Miscellaneous",
        provenance_type=ProvenanceType.EXTERNAL_IMPORT,
        author="QA Suite",
        license_name="CC0",
    )
    assert imported.latest is not None
    assert Path(imported.latest.source_original_path).is_file()
    assert Path(imported.latest.blend_path).is_file()
    assert imported.latest.technical_qa is not None
    assert imported.latest.technical_qa.file_opened_ok is True

    # Duplicate import detection
    with pytest.raises(DuplicateAssetError):
        pipe.import_model_file(temp_upload_path=obj_file, original_filename="crate.obj")

    # Generate a temporary .blend + .glb to test importing .glb and .blend cleanly
    src_gen = pipe.generate_procedural_asset(
        GenerateAssetRequest(prompt="Create a test sphere", name="Seed Sphere", template_id="test_uv_sphere")
    )
    assert src_gen.latest is not None

    glb_import = pipe.import_model_file(
        temp_upload_path=Path(src_gen.latest.glb_path),
        original_filename="imported_sphere.glb",
        name="Imported GLB Sphere",
        category="Miscellaneous",
        provenance_type=ProvenanceType.USER_UPLOADED,
    )
    assert glb_import.latest is not None
    assert glb_import.latest.technical_qa.file_opened_ok is True
    assert "glb" in glb_import.latest.file_formats

    blend_import = pipe.import_model_file(
        temp_upload_path=Path(src_gen.latest.blend_path),
        original_filename="imported_sphere.blend",
        name="Imported Blend Sphere",
        category="Miscellaneous",
        provenance_type=ProvenanceType.USER_UPLOADED,
        allow_duplicate=True,
    )
    assert blend_import.latest is not None
    assert blend_import.latest.technical_qa.status == TechnicalQAStatus.PASS
