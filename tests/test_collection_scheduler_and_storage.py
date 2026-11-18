"""Phase 4 & Phase 8 tests: daily 7-download cycle, allowance sharing, crash recovery, storage rebuild, Drive sync."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from googleapiclient.discovery import build_from_document

from blender_vault.blender_worker.runner import BlenderWorkerRunner
from blender_vault.config import Settings
from blender_vault.db import VaultDatabase
from blender_vault.models import DriveSyncStatus
from blender_vault.reference.blendswap_client import BlendSwapClient
from blender_vault.reference.collector import CollectionCycleService
from blender_vault.reference.grading_engine import QualityGradingEngine
from blender_vault.reference.inspector_service import ReferenceInspectorService
from blender_vault.reference.models import CollectionCycleRecord, DownloadQueueItem
from blender_vault.reference.ref_db import ReferenceDatabase
from blender_vault.reference.reference_drive_sync import ReferenceDriveSyncService
from blender_vault.reference.reference_storage import ReferenceStorage
from blender_vault.reference.scheduler import DailyCollectionScheduler, resolve_timezone
from blender_vault.services.gdrive_service import GoogleDriveSyncService, save_protected_token
from blender_vault.services.storage import VaultStorage
from tests.mock_blendswap_server import build_mock_blendswap_server, default_catalog_assets
from tests.test_gdrive_sync import _local_discovery_doc, build_local_drive_server


def _catalog_with_real_blend() -> list[dict]:
    real_blend = Path("samples/sample_wooden_chair/oak_dining_chair.blend").resolve().read_bytes()
    items = default_catalog_assets()
    for a in items:
        if a["files"][0]["format"] == "blender":
            a["_blob"] = real_blend
            a["size_bytes"] = len(real_blend)
            a["files"][0]["size_bytes"] = len(real_blend)
    return items


def test_full_seven_asset_cycle_storage_rebuild_and_drive_sync(tmp_path: Path) -> None:
    bs_server, bs_url, bs_state = build_mock_blendswap_server(
        free_downloads_remaining=7,
        credits=100,
        assets=_catalog_with_real_blend(),
    )
    drv_server, drv_url, _ = build_local_drive_server()
    try:
        import httplib2

        doc = _local_discovery_doc(drv_url)
        cfg = Settings(
            vault_data_dir=tmp_path / "vault",
            blendswap_key="bsk_test_valid_key",
            blendswap_base_url=f"{bs_url}/api/v1",
            gdrive_token_path=tmp_path / "creds" / "token.json",
            gdrive_client_secrets_path=tmp_path / "creds" / "client_secrets.json",
            blender_executable=Settings().blender_executable,
            allow_credit_spend=False,
        )
        cfg.ensure_directories()
        save_protected_token(
            cfg.gdrive_token_path,
            json.dumps(
                {
                    "token": "tok",
                    "refresh_token": "ref",
                    "client_id": "cid",
                    "client_secret": "csec",
                    "token_uri": f"{drv_url}/token",
                    "expiry": "2099-01-01T00:00:00Z",
                }
            ),
        )

        db = VaultDatabase(cfg.db_path)
        vault_storage = VaultStorage(cfg.assets_dir, cfg.imports_dir)
        gdrive = GoogleDriveSyncService(
            cfg,
            db,
            vault_storage,
            service_factory=lambda creds: build_from_document(doc, http=httplib2.Http(proxy_info=None)),
        )
        ref_db = ReferenceDatabase(db)
        ref_storage = ReferenceStorage(cfg.resolved_reference_library_dir)
        ref_drive = ReferenceDriveSyncService(gdrive, ref_db, ref_storage)
        client = BlendSwapClient(cfg, ref_db)
        runner = BlenderWorkerRunner(cfg.blender_executable, timeout_seconds=120)
        inspector = ReferenceInspectorService(cfg, runner)
        grader = QualityGradingEngine()
        collector = CollectionCycleService(cfg, ref_db, ref_storage, client, inspector, grader, ref_drive)

        # Scheduler runs due cycle for Europe/London
        scheduler = DailyCollectionScheduler(cfg, db, collector)
        assert resolve_timezone("Europe/London") is not None

        cycle = scheduler.check_and_run_due(
            now=datetime(2026, 11, 18, 10, 0, tzinfo=timezone.utc),
            trigger_type="scheduled",
        )
        assert cycle is not None
        assert cycle.status == "COMPLETED"
        assert cycle.downloaded_count == 7
        assert cycle.credits_spent == 0
        assert bs_state["free_remaining"] == 0
        assert bs_state["credits"] == 100  # Zero credits spent!

        # Verify DB + portable storage files + Drive sync
        assets = ref_db.list_reference_assets()
        assert len(assets) == 7
        for a in assets:
            assert a.primary_file_path and Path(a.primary_file_path).is_file()
            assert a.metadata_path and Path(a.metadata_path).is_file()
            assert a.attribution_path and Path(a.attribution_path).is_file()
            assert a.grade is not None and a.grade.total_score >= 80.0
            assert a.drive_sync_status == DriveSyncStatus.VERIFIED

        assert (ref_storage.database_dir / "reference_index.json").is_file()
        assert (ref_storage.attribution_dir / "attribution_registry.json").is_file()
        assert Path(cycle.log_path).is_file()

        # Running scheduler again on same London calendar day must NOT re-trigger
        second = scheduler.check_and_run_due(now=datetime(2026, 11, 18, 18, 0, tzinfo=timezone.utc))
        assert second is None

        # Pending Drive sync check (should find 0 pending -> no duplicate uploads)
        pending_res = ref_drive.sync_pending_assets()
        assert pending_res["attempted"] == 0

        # Delete SQLite ref_* rows and rebuild from portable reference_library/ JSON files
        with db.connect() as conn:
            conn.execute("DELETE FROM ref_assets")
        assert len(ref_db.list_reference_assets()) == 0
        rebuilt = ref_storage.rebuild_database_from_disk(ref_db)
        assert rebuilt["restored"] == 7
        assert len(ref_db.list_reference_assets()) == 7

        client.close()
    finally:
        bs_server.shutdown()
        drv_server.shutdown()


def test_partial_allowance_zero_allowance_and_crash_recovery(tmp_path: Path) -> None:
    # Start with only 2 remaining free downloads (simulating 5 used on blendswap.com website)
    bs_server, bs_url, bs_state = build_mock_blendswap_server(
        free_downloads_remaining=2,
        credits=50,
        assets=_catalog_with_real_blend(),
    )
    try:
        cfg = Settings(
            vault_data_dir=tmp_path / "vault",
            blendswap_key="bsk_test_valid_key",
            blendswap_base_url=f"{bs_url}/api/v1",
            blender_executable=Settings().blender_executable,
        )
        cfg.ensure_directories()
        db = VaultDatabase(cfg.db_path)
        ref_db = ReferenceDatabase(db)
        ref_storage = ReferenceStorage(cfg.resolved_reference_library_dir)
        client = BlendSwapClient(cfg, ref_db)
        runner = BlenderWorkerRunner(cfg.blender_executable, timeout_seconds=120)
        collector = CollectionCycleService(
            cfg, ref_db, ref_storage, client, ReferenceInspectorService(cfg, runner), QualityGradingEngine()
        )

        # Cycle 1: caps at 2 remaining free downloads (never spends credits)
        c1 = collector.run_daily_cycle(cycle_date="2026-11-18", sync_to_drive=False)
        assert c1.target_count == 2
        assert c1.downloaded_count == 2
        assert c1.credits_spent == 0
        assert bs_state["credits"] == 50
        assert bs_state["free_remaining"] == 0

        # Cycle 2: 0 remaining -> skips cleanly with SKIPPED_NO_ALLOWANCE
        c2 = collector.run_daily_cycle(cycle_date="2026-11-18", sync_to_drive=False)
        assert c2.status == "SKIPPED_NO_ALLOWANCE"
        assert c2.downloaded_count == 0

        # Pick an asset NOT yet downloaded in Cycle 1 for the interrupted PENDING item
        uncollected_id = next(
            aid for aid in (1001, 1002, 1003, 1004, 1005, 1006, 1007, 1008)
            if not ref_db.has_downloaded_asset(aid)
        )
        already_collected_id = next(
            aid for aid in (1001, 1002, 1003, 1004, 1005, 1006, 1007, 1008)
            if ref_db.has_downloaded_asset(aid)
        )

        # Simulate interrupted cycle recovery after crash with 2 fresh free downloads available
        bs_state["free_remaining"] = 2
        interrupted = CollectionCycleRecord(
            cycle_id="cyc_crash01",
            cycle_date="2026-11-19",
            status="RUNNING",
            target_count=2,
        )
        ref_db.create_cycle(interrupted)
        # Slot 1 was already COMPLETED before crash; Slot 2 was PENDING (uncollected)
        ref_db.enqueue_download_item(
            DownloadQueueItem(
                queue_id="q_done",
                cycle_id="cyc_crash01",
                blendswap_id=already_collected_id,
                file_id=already_collected_id * 10 + 1,
                slot_number=1,
                slot_reason="done before crash",
                selection_score=90.0,
                status="COMPLETED",
            )
        )
        ref_db.enqueue_download_item(
            DownloadQueueItem(
                queue_id="q_todo",
                cycle_id="cyc_crash01",
                blendswap_id=uncollected_id,
                file_id=uncollected_id * 10 + 1,
                slot_number=2,
                slot_reason="pending after crash",
                selection_score=85.0,
                status="PENDING",
            )
        )
        dl_calls_before = len(bs_state["download_calls"])
        recovered = collector.recover_incomplete_cycles(sync_to_drive=False)
        assert len(recovered) == 1
        assert recovered[0].status == "COMPLETED"
        assert recovered[0].downloaded_count == 2
        # Only 1 new POST /downloads call made (Slot 1 was NOT duplicated!)
        assert len(bs_state["download_calls"]) == dl_calls_before + 1
        assert bs_state["download_calls"][-1]["asset_id"] == uncollected_id
        client.close()
    finally:
        bs_server.shutdown()
