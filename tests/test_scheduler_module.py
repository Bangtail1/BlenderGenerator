"""Comprehensive tests for Automated Collection Scheduler module (simulated clocks, mock BlendSwap API, zero real downloads)."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from blender_vault.blender_worker.runner import BlenderWorkerRunner
from blender_vault.config import Settings
from blender_vault.db import VaultDatabase
from blender_vault.reference.blendswap_client import BlendSwapClient
from blender_vault.reference.collector import CollectionCycleService
from blender_vault.reference.grading_engine import QualityGradingEngine
from blender_vault.reference.inspector_service import ReferenceInspectorService
from blender_vault.reference.models import CollectionCycleRecord, DownloadQueueItem, ReferenceInspectionReport
from blender_vault.reference.ref_db import ReferenceDatabase
from blender_vault.reference.reference_storage import ReferenceStorage
from blender_vault.reference.scheduler import DailyCollectionScheduler, _LondonTzInfo
from blender_vault.reference.windows_task_scheduler import (
    DEFAULT_WINDOWS_TASK_NAME,
    WindowsTaskSchedulerManager,
    build_windows_task_xml,
)
from blender_vault.web.app import create_app
from tests.mock_blendswap_server import build_mock_blendswap_server, default_catalog_assets


def _catalog_with_real_blend() -> list[dict]:
    real_blend = Path("samples/sample_wooden_chair/oak_dining_chair.blend").resolve().read_bytes()
    items = default_catalog_assets()
    for asset in items:
        if asset["files"][0]["format"] == "blender":
            asset["_blob"] = real_blend
            asset["size_bytes"] = len(real_blend)
            asset["files"][0]["size_bytes"] = len(real_blend)
    return items


def _large_catalog(count: int = 30) -> list[dict]:
    base = _catalog_with_real_blend()
    out = list(base)
    sample_blob = base[0]["_blob"]
    categories = [
        ("furniture", "Furniture"),
        ("characters", "Characters"),
        ("vehicles", "Vehicles"),
        ("architecture", "Architecture"),
        ("mechanical", "Mechanical"),
        ("plants", "Plants"),
        ("electronics", "Electronics"),
    ]
    for idx in range(len(out), count):
        aid = 2000 + idx
        cslug, cname = categories[idx % len(categories)]
        out.append(
            {
                "id": aid,
                "title": f"Mock Diverse Asset {aid}",
                "url": f"https://blendswap.com/blend/{aid}",
                "description": f"Synthetic benchmark asset {aid} ({cname}).",
                "asset_type": "model_3d",
                "asset_type_label": "3D Model",
                "license": {
                    "key": "cc0",
                    "name": "CC0",
                    "url": "https://creativecommons.org/publicdomain/zero/1.0/",
                    "license_source": "uploader_declared",
                },
                "license_source": "uploader_declared",
                "tags": [cslug, "hard-surface", "mid-poly"],
                "category": {"id": (idx % 7) + 1, "name": cname, "slug": cslug},
                "author": {"id": 800 + idx, "username": f"artist_{idx}", "url": f"https://blendswap.com/profile/{800 + idx}"},
                "counts": {"downloads": 5000 - idx * 50, "likes": 100 - idx, "comments": 10},
                "explicit": False,
                "blender_versions": ["4.2"],
                "size_bytes": len(sample_blob),
                "created_at": "2026-06-01T12:00:00Z",
                "files": [
                    {
                        "id": aid * 10 + 1,
                        "format": "blender",
                        "format_label": "BLENDER",
                        "size_bytes": len(sample_blob),
                        "software_version": "4.2",
                        "render_engine": "cycles",
                        "render_engine_label": "Cycles",
                        "render_engine_version": None,
                        "filename": f"asset_{aid}.blend",
                        "position": 0,
                    }
                ],
                "_blob": sample_blob,
            }
        )
    return out


class _FastStubInspector:
    """Fast deterministic inspector stub for high-frequency clock/DST scheduler tests (without skipping real HTTP/DB/queue)."""

    def inspect_asset_file(
        self,
        *,
        source_file: Path,
        blendswap_id: int = 0,
        preview_image_count: int = 0,
    ) -> ReferenceInspectionReport:
        return ReferenceInspectionReport(
            inspection_id=f"insp_{blendswap_id}",
            blendswap_id=blendswap_id,
            blender_runtime_version="5.3.0",
            file_opened_ok=True,
            inspection_completed_ok=True,
            mesh_object_count=4,
            object_count=5,
            vertex_count=2400,
            edge_count=4800,
            face_count=2400,
            polygon_count=2400,
            triangle_count=4800,
            connected_components=2,
            is_manifold=True,
            dimensions=[0.8, 0.8, 1.0],
            quad_ratio=0.9,
            triangle_ratio=0.1,
            ngon_ratio=0.0,
            edge_flow_score=90.0,
            subdivision_ready=True,
            mesh_cleanliness_score=96.0,
            has_uv_map=True,
            uv_layer_count=1,
            uv_mapped_mesh_ratio=1.0,
            uv_coverage_ratio=0.72,
            uv_completeness_ratio=1.0,
            material_count=2,
            principled_bsdf_count=2,
            procedural_shader_count=1,
            material_node_count_avg=5.0,
            naming_consistency_ratio=1.0,
            collection_count=1,
            object_separation_score=92.0,
            scene_organization_score=95.0,
            renderable=True,
            preview_image_count=preview_image_count,
        )


def _make_scheduler(
    tmp_path: Path,
    bs_url: str,
    *,
    fast_inspector: bool = True,
    collection_time: str = "03:00",
    reset_recheck_minutes: int = 15,
    resume_missed_jobs: bool = True,
) -> tuple[Settings, VaultDatabase, ReferenceDatabase, BlendSwapClient, CollectionCycleService, DailyCollectionScheduler]:
    cfg = Settings(
        vault_data_dir=tmp_path / "vault",
        blendswap_key="bsk_test_valid_key",
        blendswap_base_url=f"{bs_url}/api/v1",
        blender_executable=Settings().blender_executable,
        collection_schedule_time=collection_time,
        collection_timezone="Europe/London",
        collection_daily_target=7,
        collection_scheduler_enabled=True,
        collection_reset_recheck_minutes=reset_recheck_minutes,
        collection_resume_missed_jobs=resume_missed_jobs,
        allow_credit_spend=False,
        reference_drive_sync_enabled=False,
    )
    cfg.ensure_directories()
    db = VaultDatabase(cfg.db_path)
    ref_db = ReferenceDatabase(db)
    ref_storage = ReferenceStorage(cfg.resolved_reference_library_dir)
    client = BlendSwapClient(cfg, ref_db)
    inspector = (
        _FastStubInspector()
        if fast_inspector
        else ReferenceInspectorService(cfg, BlenderWorkerRunner(cfg.blender_executable, timeout_seconds=120))
    )
    collector = CollectionCycleService(cfg, ref_db, ref_storage, client, inspector, QualityGradingEngine())
    scheduler = DailyCollectionScheduler(
        cfg,
        db,
        collector,
        ref_db=ref_db,
        reset_recheck_minutes=reset_recheck_minutes,
    )
    return cfg, db, ref_db, client, collector, scheduler


def test_default_0300_schedule_and_json_config_persistence(tmp_path: Path) -> None:
    bs_server, bs_url, _ = build_mock_blendswap_server(free_downloads_remaining=7, credits=50, assets=_catalog_with_real_blend())
    try:
        cfg, db, ref_db, client, collector, sched = _make_scheduler(tmp_path, bs_url)
        try:
            state = sched.get_schedule_config(now=datetime(2026, 11, 18, 1, 0, tzinfo=timezone.utc))
            assert state["enabled"] is True
            assert state["frequency"] == "daily"
            assert state["collection_time"] == "03:00"
            assert state["timezone"] == "Europe/London"
            assert state["target_downloads"] == 7
            assert state["free_downloads_only"] is True
            assert state["automatic_credit_spending"] is False
            assert state["resume_missed_jobs"] is True
            assert state["next_run_at"].startswith("2026-11-18T03:00:00")

            # Verify portable JSON config file matches Section 7 schema
            cfg_file = Path(state["config_path"])
            assert cfg_file.is_file()
            raw_json = json.loads(cfg_file.read_text(encoding="utf-8"))
            assert raw_json == {
                "enabled": True,
                "frequency": "daily",
                "collection_time": "03:00",
                "timezone": "Europe/London",
                "target_downloads": 7,
                "free_downloads_only": True,
                "automatic_credit_spending": False,
                "resume_missed_jobs": True,
            }

            # Simulate restart and change collection time
            sched.update_schedule_config(collection_time="04:15", enabled=False)
            sched2 = DailyCollectionScheduler(cfg, VaultDatabase(cfg.db_path), collector, ref_db=ref_db)
            reloaded = sched2.get_schedule_config(now=datetime(2026, 11, 18, 1, 0, tzinfo=timezone.utc))
            assert reloaded["collection_time"] == "04:15"
            assert reloaded["enabled"] is False
            assert reloaded["scheduler_state"] == "PAUSED"
        finally:
            client.close()
    finally:
        bs_server.shutdown()


def test_daily_execution_and_reset_detection_after_postponement(tmp_path: Path) -> None:
    """Do NOT assume midnight reset: if allowance is 0 at 03:00, postpone and retry periodically until replenished."""
    bs_server, bs_url, bs_state = build_mock_blendswap_server(
        free_downloads_remaining=0,
        credits=100,
        assets=_catalog_with_real_blend(),
    )
    try:
        cfg, db, ref_db, client, collector, sched = _make_scheduler(tmp_path, bs_url, reset_recheck_minutes=15)
        try:
            # 02:59 GMT -> too early
            assert sched.check_and_run_due(now=datetime(2026, 11, 18, 2, 59, tzinfo=timezone.utc)) is None

            # 03:00 GMT -> allowance is 0 -> postpones cleanly WITHOUT marking day completed
            res_0300 = sched.check_and_run_due(now=datetime(2026, 11, 18, 3, 0, tzinfo=timezone.utc))
            assert res_0300 is None
            cfg_0300 = sched.get_schedule_config(now=datetime(2026, 11, 18, 3, 0, tzinfo=timezone.utc))
            assert cfg_0300["scheduler_state"] == "POSTPONED_WAITING_RESET"
            assert cfg_0300["postponed_date"] == "2026-11-18"
            assert cfg_0300["last_cycle_date"] is None
            assert cfg_0300["last_allowance_seen"] == 0
            assert cfg_0300["next_recheck_at"].startswith("2026-11-18T03:15:00")
            assert len(bs_state["download_calls"]) == 0

            latest_evt = sched.get_latest_notification()
            assert latest_evt is not None
            assert latest_evt.status == "POSTPONED_WAITING_RESET"
            assert latest_evt.free_allowance_remaining == 0

            # 03:05 GMT -> still inside 15-min backoff window -> should NOT spam /me
            calls_before_0305 = ref_db.get_api_usage_summary()["total_calls"]
            assert sched.check_and_run_due(now=datetime(2026, 11, 18, 3, 5, tzinfo=timezone.utc)) is None
            assert ref_db.get_api_usage_summary()["total_calls"] == calls_before_0305

            # 03:15 GMT -> backoff elapsed, still 0 remaining -> reschedules recheck to 03:30
            assert sched.check_and_run_due(now=datetime(2026, 11, 18, 3, 15, tzinfo=timezone.utc)) is None
            assert ref_db.get_api_usage_summary()["total_calls"] > calls_before_0305
            assert sched.get_schedule_config(now=datetime(2026, 11, 18, 3, 15, tzinfo=timezone.utc))["next_recheck_at"].startswith("2026-11-18T03:30:00")

            # BlendSwap resets allowance at 11:20 GMT -> periodic check detects reset and executes 7 downloads!
            bs_state["free_remaining"] = 7
            cycle = sched.check_and_run_due(now=datetime(2026, 11, 18, 11, 20, tzinfo=timezone.utc))
            assert cycle is not None
            assert cycle.status == "COMPLETED"
            assert cycle.downloaded_count == 7
            assert cycle.credits_spent == 0
            assert bs_state["credits"] == 100
            assert bs_state["free_remaining"] == 0

            cfg_after = sched.get_schedule_config(now=datetime(2026, 11, 18, 11, 25, tzinfo=timezone.utc))
            assert cfg_after["last_cycle_date"] == "2026-11-18"
            assert cfg_after["allowance_reset_detected_at"] is not None
            assert cfg_after["postponed_date"] is None
            assert cfg_after["next_run_at"].startswith("2026-11-19T03:00:00")

            # Duplicate prevention: later check on same day returns None even if allowance somehow resets again
            bs_state["free_remaining"] = 7
            assert sched.check_and_run_due(now=datetime(2026, 11, 18, 22, 0, tzinfo=timezone.utc)) is None
            assert len(bs_state["download_calls"]) == 7

            # Notification log contains required telemetry fields
            done_evt = sched.get_latest_notification()
            assert done_evt is not None
            assert done_evt.status == "COMPLETED"
            assert done_evt.downloaded_count == 7
            assert done_evt.failed_count == 0
            assert done_evt.free_allowance_remaining == 0
            assert len(done_evt.assets_selected) == 7
            assert done_evt.scheduled_time.startswith("2026-11-18T03:00:00")
            assert done_evt.next_scheduled_run.startswith("2026-11-19T03:00:00")
        finally:
            client.close()
    finally:
        bs_server.shutdown()


def test_missed_run_and_interrupted_restart_recovery(tmp_path: Path) -> None:
    """Recover missed day runs and resume interrupted queues across restart without duplicate downloads."""
    bs_server, bs_url, bs_state = build_mock_blendswap_server(
        free_downloads_remaining=7,
        credits=50,
        assets=_large_catalog(30),
    )
    try:
        cfg, db, ref_db, client, collector, sched = _make_scheduler(tmp_path, bs_url)
        try:
            # 1. Completed cycle on Nov 17
            bs_state["free_remaining"] = 7
            c_nov17 = sched.check_and_run_due(now=datetime(2026, 11, 17, 3, 5, tzinfo=timezone.utc))
            assert c_nov17 is not None and c_nov17.cycle_date == "2026-11-17"

            # 2. PC turned off all of Nov 18; wakes up Nov 19 at 01:30 GMT (before 03:00 GMT) -> recovers Nov 18!
            bs_state["free_remaining"] = 7
            c_missed = sched.check_and_run_due(now=datetime(2026, 11, 19, 1, 30, tzinfo=timezone.utc))
            assert c_missed is not None
            assert c_missed.cycle_date == "2026-11-18"
            assert c_missed.trigger_type == "missed_run_recovery"
            assert c_missed.downloaded_count == 7

            # 3. Interrupted queue recovery after crash: simulate cycle on Nov 19 with Slot 1 completed and Slot 2 pending
            bs_state["free_remaining"] = 2
            # Force reset of remaining catalog options or enqueue another uncollected item (1009, 1010)
            # First Nov 17 used 7 assets (e.g. 1001..1007); Nov 18 used remaining 5 or duplicates skipped.
            # Pick a fresh uncollected ID from default catalog or verify crash queue mechanics on brand new IDs:
            already_done_id = ref_db.list_reference_assets()[0].blendswap_id
            pending_id = 1011
            if ref_db.has_downloaded_asset(pending_id):
                pending_id = next(i for i in range(1001, 1013) if not ref_db.has_downloaded_asset(i))

            crashed = CollectionCycleRecord(
                cycle_id="cyc_restart_crash",
                cycle_date="2026-11-20",
                status="RUNNING",
                target_count=2,
                trigger_type="scheduled",
            )
            ref_db.create_cycle(crashed)
            ref_db.enqueue_download_item(
                DownloadQueueItem(
                    queue_id="q_done_crash",
                    cycle_id="cyc_restart_crash",
                    blendswap_id=already_done_id,
                    file_id=already_done_id * 10 + 1,
                    slot_number=1,
                    slot_reason="completed before crash",
                    selection_score=95.0,
                    status="COMPLETED",
                )
            )
            ref_db.enqueue_download_item(
                DownloadQueueItem(
                    queue_id="q_pending_crash",
                    cycle_id="cyc_restart_crash",
                    blendswap_id=pending_id,
                    file_id=pending_id * 10 + 1,
                    slot_number=2,
                    slot_reason="interrupted item",
                    selection_score=85.0,
                    status="PENDING",
                )
            )

            # New scheduler & collector instances simulating app/PC reboot
            dl_calls_before = len(bs_state["download_calls"])
            _, db2, ref_db2, client2, collector2, sched2 = _make_scheduler(tmp_path, bs_url)
            try:
                recovered = sched2.check_and_run_due(now=datetime(2026, 11, 20, 4, 0, tzinfo=timezone.utc))
                assert recovered is not None
                assert recovered.cycle_id == "cyc_restart_crash"
                assert recovered.status == "COMPLETED"
                assert recovered.downloaded_count == 2
                # Strictly 1 new download call (COMPLETED slot 1 was NOT duplicated!)
                assert len(bs_state["download_calls"]) == dl_calls_before + 1
                assert bs_state["download_calls"][-1]["asset_id"] == pending_id
                assert sched2.get_schedule_config()["last_cycle_date"] == "2026-11-20"
            finally:
                client2.close()
        finally:
            client.close()
    finally:
        bs_server.shutdown()


def test_europe_london_dst_transitions(tmp_path: Path) -> None:
    """Verify spring-forward (23h UTC) and fall-back (25h UTC) DST transitions at 03:00 Europe/London."""
    london_fallback = _LondonTzInfo()
    # PEP-495 checks on fallback tzinfo:
    # Spring forward: 2026-03-29 01:00 UTC -> 02:00 BST (+01:00)
    dt_spring = datetime(2026, 3, 29, 2, 0, tzinfo=timezone.utc).astimezone(london_fallback)
    assert dt_spring.hour == 3 and dt_spring.utcoffset().total_seconds() == 3600
    assert dt_spring.astimezone(timezone.utc) == datetime(2026, 3, 29, 2, 0, tzinfo=timezone.utc)

    # Fall back: 2026-10-25 00:30 UTC -> 01:30 BST (fold=0, +01:00); 01:30 UTC -> 01:30 GMT (fold=1, +00:00)
    dt_fold0 = datetime(2026, 10, 25, 0, 30, tzinfo=timezone.utc).astimezone(london_fallback)
    dt_fold1 = datetime(2026, 10, 25, 1, 30, tzinfo=timezone.utc).astimezone(london_fallback)
    assert dt_fold0.hour == 1 and dt_fold0.minute == 30 and dt_fold0.utcoffset().total_seconds() == 3600
    assert dt_fold1.hour == 1 and dt_fold1.minute == 30 and dt_fold1.utcoffset().total_seconds() == 0
    assert dt_fold0.astimezone(timezone.utc) == datetime(2026, 10, 25, 0, 30, tzinfo=timezone.utc)
    assert dt_fold1.astimezone(timezone.utc) == datetime(2026, 10, 25, 1, 30, tzinfo=timezone.utc)

    bs_server, bs_url, bs_state = build_mock_blendswap_server(
        free_downloads_remaining=7,
        credits=20,
        assets=_large_catalog(35),
    )
    try:
        cfg, db, ref_db, client, collector, sched = _make_scheduler(tmp_path, bs_url)
        try:
            # 1. Spring forward weekend: March 28 (GMT) -> March 29 (BST)
            bs_state["free_remaining"] = 7
            mar28 = sched.check_and_run_due(now=datetime(2026, 3, 28, 3, 0, tzinfo=timezone.utc))
            assert mar28 is not None and mar28.cycle_date == "2026-03-28"
            next_mar29 = sched.compute_next_run_at(now=datetime(2026, 3, 28, 3, 5, tzinfo=timezone.utc))
            # March 29 03:00 BST == 02:00 UTC! (23 hours after March 28 03:00 UTC)
            assert next_mar29.astimezone(timezone.utc) == datetime(2026, 3, 29, 2, 0, tzinfo=timezone.utc)

            bs_state["free_remaining"] = 7
            # 01:59 UTC is 02:59 BST -> too early
            assert sched.check_and_run_due(now=datetime(2026, 3, 29, 1, 59, tzinfo=timezone.utc)) is None
            # 02:00 UTC is 03:00 BST -> triggers!
            mar29 = sched.check_and_run_due(now=datetime(2026, 3, 29, 2, 0, tzinfo=timezone.utc))
            assert mar29 is not None and mar29.cycle_date == "2026-03-29"

            # 2. Fall back weekend: Oct 24 (BST, 02:00 UTC) -> Oct 25 (GMT, 03:00 UTC)
            bs_state["free_remaining"] = 7
            oct24 = sched.check_and_run_due(now=datetime(2026, 10, 24, 2, 0, tzinfo=timezone.utc))
            assert oct24 is not None and oct24.cycle_date == "2026-10-24"
            next_oct25 = sched.compute_next_run_at(now=datetime(2026, 10, 24, 2, 5, tzinfo=timezone.utc))
            # Oct 25 03:00 GMT == 03:00 UTC! (25 hours after Oct 24 02:00 UTC)
            assert next_oct25.astimezone(timezone.utc) == datetime(2026, 10, 25, 3, 0, tzinfo=timezone.utc)

            bs_state["free_remaining"] = 7
            # 02:30 UTC is only 02:30 GMT -> too early
            assert sched.check_and_run_due(now=datetime(2026, 10, 25, 2, 30, tzinfo=timezone.utc)) is None
            # 03:00 UTC is 03:00 GMT -> triggers!
            oct25 = sched.check_and_run_due(now=datetime(2026, 10, 25, 3, 0, tzinfo=timezone.utc))
            assert oct25 is not None and oct25.cycle_date == "2026-10-25"
        finally:
            client.close()
    finally:
        bs_server.shutdown()


def test_windows_task_scheduler_xml_and_mock_cli_actions(tmp_path: Path) -> None:
    xml = build_windows_task_xml(
        collection_time="03:00",
        timezone_name="Europe/London",
        python_executable=tmp_path / ".venv" / "Scripts" / "python.exe",
        working_dir=tmp_path,
        resume_missed_jobs=True,
        recheck_interval_minutes=15,
        now=datetime(2026, 11, 18, 0, 0, tzinfo=timezone.utc),
    )
    assert "<WakeToRun>false</WakeToRun>" in xml
    assert "<StartWhenAvailable>true</StartWhenAvailable>" in xml
    assert "<LogonTrigger>" in xml
    assert "<Interval>PT15M</Interval>" in xml
    assert "collect-daily --scheduled" in xml

    recorded_cmds: list[list[str]] = []

    def fake_runner(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        recorded_cmds.append(cmd)
        if "/Query" in cmd:
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=(
                    "TaskName:                             \\BlenderAssetVault-ReferenceDailyCollection\n"
                    "Next Run Time:                        19/11/2026 03:00:00\n"
                    "Status:                               Ready\n"
                    "Last Run Time:                        18/11/2026 03:00:01\n"
                    "Last Result:                          0\n"
                ),
                stderr="",
            )
        return subprocess.CompletedProcess(cmd, 0, stdout="SUCCESS\n", stderr="")

    cfg = Settings(vault_data_dir=tmp_path / "vault")
    cfg.ensure_directories()
    mgr = WindowsTaskSchedulerManager(
        cfg,
        working_dir=tmp_path,
        command_runner=fake_runner,
        platform_name="win32",
    )
    inst = mgr.install_task(collection_time="03:00", timezone_name="Europe/London", recheck_interval_minutes=15)
    assert inst["installed"] is True
    assert Path(inst["xml_path"]).is_file()
    assert recorded_cmds[-1][:3] == ["schtasks", "/Create", "/TN"]

    st = mgr.query_task_status()
    assert st["installed"] is True
    assert st["status"] == "Ready"
    assert st["next_run_time"] == "19/11/2026 03:00:00"
    assert st["task_name"] == DEFAULT_WINDOWS_TASK_NAME

    uninst = mgr.uninstall_task()
    assert uninst["uninstalled"] is True
    assert recorded_cmds[-1][:3] == ["schtasks", "/Delete", "/TN"]


def test_dashboard_pause_resume_and_manual_run_endpoints(tmp_path: Path) -> None:
    bs_server, bs_url, bs_state = build_mock_blendswap_server(
        free_downloads_remaining=3,
        credits=40,
        assets=_catalog_with_real_blend(),
    )
    try:
        cfg = Settings(
            vault_data_dir=tmp_path / "vault",
            blendswap_key="bsk_test_valid_key",
            blendswap_base_url=f"{bs_url}/api/v1",
            collection_scheduler_enabled=False,
            blender_executable=Settings().blender_executable,
        )
        cfg.ensure_directories()
        app = create_app(settings=cfg, start_scheduler=False)
        with TestClient(app) as web_client:
            paused = web_client.post("/api/reference/scheduler/pause").json()
            assert paused["enabled"] is False
            assert paused["scheduler_state"] == "PAUSED"

            resumed = web_client.post("/api/reference/scheduler/resume").json()
            assert resumed["enabled"] is True
            assert resumed["scheduler_state"] == "SCHEDULED"

            updated = web_client.post(
                "/api/reference/scheduler",
                json={"collection_time": "03:00", "timezone": "Europe/London", "target_downloads": 5},
            ).json()
            assert updated["collection_time"] == "03:00"
            assert updated["target_downloads"] == 5

            # Manual execution works while respecting 3 free remaining and spending 0 credits
            manual = web_client.post("/api/reference/cycles/run", json={"sync_to_drive": False}).json()
            assert manual["downloaded_count"] == 3
            assert manual["credits_spent"] == 0
            assert bs_state["credits"] == 40

            notifs = web_client.get("/api/reference/scheduler/notifications").json()
            assert len(notifs["notifications"]) >= 3  # pause, resume, completed cycle

            dash = web_client.get("/api/reference/dashboard?check_live=true").json()
            ov = dash["collection_overview"]
            assert ov["latest_notification"] is not None
            assert ov["windows_task_scheduler"] is not None
            assert ov["schedule"]["collection_time"] == "03:00"
    finally:
        bs_server.shutdown()
