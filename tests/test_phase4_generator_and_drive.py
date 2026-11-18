"""Phase 4 tests: generator integration with SQLite, FBX/OBJ export, and Drive failure retry queue."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from googleapiclient.discovery import build_from_document

from blender_vault.blender.export_manager import RobloxExportManager
from blender_vault.blender_worker.runner import BlenderWorkerRunner
from blender_vault.config import Settings
from blender_vault.database import LogRepository, ProductionStatus, RobloxAssetRegistrar
from blender_vault.db import VaultDatabase
from blender_vault.models import GenerateAssetRequest, HumanApprovalStatus, HumanReviewRequest
from blender_vault.services.gdrive_service import GoogleDriveSyncService, save_protected_token
from blender_vault.services.pipeline import AssetPipelineService
from blender_vault.services.storage import VaultStorage
from blender_vault.storage.sync_manager import RobloxDriveSyncManager
from tests.test_gdrive_sync import _local_discovery_doc, build_local_drive_server


def test_generator_registers_roblox_id_exports_fbx_and_handles_drive_retry(tmp_path: Path) -> None:
    base_cfg = Settings()
    cfg = Settings(
        vault_data_dir=tmp_path / "vault",
        gdrive_token_path=tmp_path / "creds" / "token.json",
        gdrive_client_secrets_path=tmp_path / "creds" / "client_secrets.json",
        blender_executable=base_cfg.blender_executable,
        blender_render_resolution=256,
        blender_render_samples=8,
        roblox_default_export_format="FBX",
    )
    cfg.ensure_directories()

    vdb = VaultDatabase(cfg.db_path)
    storage = VaultStorage(cfg.assets_dir, cfg.imports_dir)
    runner = BlenderWorkerRunner(cfg.blender_executable, timeout_seconds=120)
    gdrive_offline = GoogleDriveSyncService(cfg, vdb, storage)
    pipe = AssetPipelineService(cfg, vdb, storage, runner, gdrive_offline)

    # 1. Generate chair while Google Drive is NOT authenticated (simulates temporary outage)
    gen = pipe.generate_procedural_asset(
        GenerateAssetRequest(
            prompt="Create a detailed wooden chair with four legs, a curved backrest and a realistic wooden material.",
            name="Oak Dining Chair",
            export_format="FBX",
            sync_to_drive_pending=True,
        )
    )
    assert gen.roblox_asset_id == "FUR_00001_Wooden_Chair_Basic_LP_v001"
    assert gen.latest is not None
    assert Path(gen.latest.blend_path).is_file()
    fbx_file = Path(gen.latest.blend_path).with_suffix(".fbx")
    assert fbx_file.is_file() and fbx_file.stat().st_size > 1000

    rbx = pipe.roblox_registrar.assets.get_asset(gen.roblox_asset_id)
    assert rbx is not None
    assert rbx.export_format.value == "FBX"
    assert Path(rbx.export_path) == fbx_file
    assert rbx.production_status == ProductionStatus.AWAITING_HUMAN_APPROVAL

    # Drive failed gracefully: local files preserved, retry queued in drive_upload_queue
    logs_repo = LogRepository(pipe.roblox_registrar.db)
    pending = logs_repo.list_pending_drive_uploads()
    assert len(pending) == 1
    assert pending[0].asset_id == rbx.asset_id

    # 2. Failed defect asset must be blocked from approved Drive upload
    defect = pipe.generate_procedural_asset(
        GenerateAssetRequest(prompt="Defective test", name="Broken Chair", template_id="defect_zero_area_and_loose")
    )
    assert defect.roblox_asset_id is not None
    with pytest.raises(ValueError):
        pipe.roblox_sync.sync_asset_version(defect.roblox_asset_id, 1, queue_on_failure=False)

    # 3. Approve valid chair, bring local Drive server online, and retry queued upload
    pipe.submit_human_review(
        gen.asset_id,
        1,
        HumanReviewRequest(decision=HumanApprovalStatus.APPROVED, reviewer="Azlan", notes="Ready for Drive."),
    )
    server, base_url, _ = build_local_drive_server()
    try:
        import httplib2

        doc = _local_discovery_doc(base_url)
        save_protected_token(
            cfg.gdrive_token_path,
            json.dumps(
                {
                    "token": "tok",
                    "refresh_token": "ref",
                    "client_id": "cid",
                    "client_secret": "csec",
                    "token_uri": f"{base_url}/token",
                    "expiry": "2099-01-01T00:00:00Z",
                }
            ),
        )
        gdrive_online = GoogleDriveSyncService(
            cfg,
            vdb,
            storage,
            service_factory=lambda creds: build_from_document(doc, http=httplib2.Http(proxy_info=None)),
        )
        sync_online = RobloxDriveSyncManager(gdrive_online, pipe.roblox_registrar.db, vdb)
        retry_res = sync_online.retry_pending_uploads()
        assert retry_res["attempted"] == 1
        assert retry_res["succeeded"] == 1
        assert retry_res["remaining"] == 0

        synced_rbx = pipe.roblox_registrar.assets.get_asset(rbx.asset_id)
        assert synced_rbx is not None
        assert synced_rbx.google_drive_file_id is not None
        assert synced_rbx.google_drive_folder_id is not None
        assert synced_rbx.human_approved is True
    finally:
        server.shutdown()


def test_export_manager_fbx_and_obj_roundtrip_on_sample_chair(tmp_path: Path) -> None:
    cfg = Settings()
    runner = BlenderWorkerRunner(cfg.blender_executable, timeout_seconds=120)
    mgr = RobloxExportManager(runner)
    sample_blend = Path("samples/sample_wooden_chair/oak_dining_chair.blend").resolve()
    assert sample_blend.is_file()

    fbx_res = mgr.export_and_validate(
        blend_path=sample_blend,
        output_path=tmp_path / "chair.fbx",
        export_format="FBX",
        work_dir=tmp_path / "work_fbx",
        verify_roundtrip=True,
    )
    assert fbx_res["ok"] is True
    assert fbx_res["roundtrip"]["success"] is True
    assert fbx_res["roundtrip"]["dimension_delta_max"] < 0.05

    obj_res = mgr.export_and_validate(
        blend_path=sample_blend,
        output_path=tmp_path / "chair.obj",
        export_format="OBJ",
        work_dir=tmp_path / "work_obj",
        verify_roundtrip=True,
    )
    assert obj_res["ok"] is True
    assert obj_res["roundtrip"]["success"] is True
    assert obj_res["roundtrip"]["dimension_delta_max"] < 0.05
