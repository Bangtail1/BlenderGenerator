"""Command-line entry point for running Blender Asset Vault web server, Branch 2 collector, or Roblox QA CLI."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import uvicorn

from .blender_worker.runner import BlenderWorkerRunner
from .config import Settings
from .database import RobloxAssetRegistrar
from .db import VaultDatabase
from .logging_config import configure_logging
from .reference.benchmark_engine import BenchmarkEngine
from .reference.blendswap_client import BlendSwapClient
from .reference.collector import CollectionCycleService
from .reference.grading_engine import QualityGradingEngine
from .reference.inspector_service import ReferenceInspectorService
from .reference.ref_db import ReferenceDatabase
from .reference.reference_drive_sync import ReferenceDriveSyncService
from .reference.reference_storage import ReferenceStorage
from .reference.scheduler import DailyCollectionScheduler
from .reports.report_generator import QAReportGenerator
from .roblox_qa import RobloxQAEngine, RobloxWorkflowService
from .services.gdrive_service import GoogleDriveSyncService
from .services.storage import VaultStorage
from .storage.sync_manager import RobloxDriveSyncManager


def _build_branch2_services(settings: Settings):
    db = VaultDatabase(settings.db_path)
    storage = VaultStorage(settings.assets_dir, settings.imports_dir)
    runner = BlenderWorkerRunner(settings.blender_executable, settings.blender_timeout_seconds)
    gdrive = GoogleDriveSyncService(settings, db, storage)
    ref_db = ReferenceDatabase(db)
    ref_storage = ReferenceStorage(settings.resolved_reference_library_dir)
    client = BlendSwapClient(settings, ref_db)
    inspector = ReferenceInspectorService(settings, runner)
    grader = QualityGradingEngine()
    ref_drive = ReferenceDriveSyncService(gdrive, ref_db, ref_storage)
    collector = CollectionCycleService(settings, ref_db, ref_storage, client, inspector, grader, ref_drive)
    scheduler = DailyCollectionScheduler(settings, db, collector)
    benchmark = BenchmarkEngine(db, ref_db, ref_storage, inspector, grader, ref_drive)
    return db, storage, runner, gdrive, ref_db, ref_storage, client, collector, scheduler, benchmark


def _build_roblox_services(settings: Settings):
    db = VaultDatabase(settings.db_path)
    storage = VaultStorage(settings.assets_dir, settings.imports_dir)
    runner = BlenderWorkerRunner(settings.blender_executable, settings.blender_timeout_seconds)
    gdrive = GoogleDriveSyncService(settings, db, storage)
    registrar = RobloxAssetRegistrar(settings)
    sync_mgr = RobloxDriveSyncManager(gdrive, registrar.db, db)
    workflow = RobloxWorkflowService(
        settings,
        roblox_db=registrar.db,
        qa_engine=RobloxQAEngine(settings, runner=runner),
        sync_manager=sync_mgr,
    )
    return db, registrar, sync_mgr, workflow


def main() -> None:
    parser = argparse.ArgumentParser(description="Blender Asset Vault & Roblox QA CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    serve_p = sub.add_parser("serve", help="Start the Blender Asset Vault Web UI & API server")
    serve_p.add_argument("--host", default=None)
    serve_p.add_argument("--port", type=int, default=None)

    sub.add_parser("health", help="Check Blender installation, Google Drive auth, and BlendSwap API status")
    sub.add_parser("gdrive-auth", help="Run interactive Google Drive OAuth 2.0 desktop login")
    sub.add_parser("reindex", help="Rebuild SQLite catalogue from local manifest.json files")

    # Branch 2 Reference Engine CLI commands
    sub.add_parser("blendswap-health", help="Check BlendSwap API key connectivity, free allowance, and credits")
    collect_p = sub.add_parser("collect-daily", help="Run (or recover) a single BlendSwap reference collection cycle")
    collect_p.add_argument("--max-downloads", type=int, default=None)
    collect_p.add_argument("--no-drive-sync", action="store_true")
    sub.add_parser("scheduler", help="Run the persistent daily BlendSwap collection scheduler in foreground")
    sub.add_parser("ref-reindex", help="Rebuild Branch 2 SQLite tables from reference_library JSON files")

    bench_p = sub.add_parser("benchmark", help="Benchmark a Branch 1 local asset against Branch 2 references")
    bench_p.add_argument("asset_id", help="Branch 1 asset_id")
    bench_p.add_argument("--version", type=int, default=None)
    bench_p.add_argument("--usage", default=None)
    bench_p.add_argument("--style", default=None)

    # Pipeline 1 & 2 Roblox Asset Management & QA CLI commands
    r_init = sub.add_parser("roblox-init", help="Initialize Roblox SQLite DB, seed 25+15 departments, and sync existing Vault assets")
    r_init.add_argument("--no-sync-existing", action="store_true")

    r_qa = sub.add_parser("roblox-qa", help="Run automated 100-point Roblox QA and generate JSON/HTML reports")
    r_qa.add_argument("target", help="Roblox asset_id or path to a .blend file")
    r_qa.add_argument("--department", default="FUR", help="Department code when targeting a standalone .blend")
    r_qa.add_argument("--version", type=int, default=None)
    r_qa.add_argument("--format", default=None, choices=["FBX", "GLB", "OBJ"])
    r_qa.add_argument("--no-previews", action="store_true")

    r_app = sub.add_parser("roblox-approve", help="Human final approval gate and production Google Drive sync")
    r_app.add_argument("asset_id", help="Roblox asset_id")
    r_app.add_argument("--reviewer", required=True)
    r_app.add_argument("--notes", default="")
    r_app.add_argument("--version", type=int, default=None)
    r_app.add_argument("--no-drive-sync", action="store_true")

    sub.add_parser("roblox-retry-drive", help="Retry pending queued Google Drive uploads")
    sub.add_parser("roblox-status", help="Show Roblox DB integrity, department counts, and assets summary")

    args = parser.parse_args()
    settings = Settings()
    settings.ensure_directories()
    configure_logging(settings.logs_dir, settings.vault_log_level)

    if args.command == "serve":
        uvicorn.run(
            "blender_vault.web.app:create_app",
            factory=True,
            host=args.host or settings.vault_host,
            port=args.port or settings.vault_port,
            reload=False,
        )
    elif args.command == "health":
        runner = BlenderWorkerRunner(settings.blender_executable, settings.blender_timeout_seconds)
        db = VaultDatabase(settings.db_path)
        storage = VaultStorage(settings.assets_dir, settings.imports_dir)
        gdrive = GoogleDriveSyncService(settings, db, storage)
        ref_db = ReferenceDatabase(db)
        bs_client = BlendSwapClient(settings, ref_db)
        try:
            bs_status = bs_client.check_connectivity()
        finally:
            bs_client.close()
        print(
            json.dumps(
                {
                    "blender": runner.check_health(),
                    "gdrive": gdrive.get_auth_status(),
                    "blendswap": bs_status,
                    "data_dir": str(settings.vault_data_dir.resolve()),
                    "reference_library_dir": str(settings.resolved_reference_library_dir.resolve()),
                    "roblox_db_path": str(settings.resolved_roblox_db_path.resolve()),
                },
                indent=2,
            )
        )
    elif args.command == "gdrive-auth":
        db = VaultDatabase(settings.db_path)
        storage = VaultStorage(settings.assets_dir, settings.imports_dir)
        gdrive = GoogleDriveSyncService(settings, db, storage)
        status = gdrive.authenticate_interactive_local_server()
        print(json.dumps(status, indent=2))
    elif args.command == "reindex":
        db = VaultDatabase(settings.db_path)
        storage = VaultStorage(settings.assets_dir, settings.imports_dir)
        res = storage.rebuild_database_from_manifests(db)
        print(json.dumps(res, indent=2))
    elif args.command == "blendswap-health":
        _, _, _, _, _, _, client, _, scheduler, _ = _build_branch2_services(settings)
        try:
            print(
                json.dumps(
                    {
                        "connectivity": client.check_connectivity(),
                        "scheduler": scheduler.get_schedule_config(),
                    },
                    indent=2,
                )
            )
        finally:
            client.close()
    elif args.command == "collect-daily":
        _, _, _, _, _, _, client, _, scheduler, _ = _build_branch2_services(settings)
        try:
            if args.no_drive_sync:
                settings.reference_drive_sync_enabled = False
            cycle = scheduler.check_and_run_due(
                force=True,
                trigger_type="manual_cli",
                max_downloads=args.max_downloads,
            )
            print(json.dumps(cycle.model_dump() if cycle else {"status": "NO_ACTION"}, indent=2))
        finally:
            client.close()
    elif args.command == "scheduler":
        _, _, _, _, _, _, client, _, scheduler, _ = _build_branch2_services(settings)
        scheduler.start()
        try:
            while True:
                time.sleep(5)
        except KeyboardInterrupt:
            scheduler.stop()
            client.close()
    elif args.command == "ref-reindex":
        db = VaultDatabase(settings.db_path)
        ref_db = ReferenceDatabase(db)
        ref_storage = ReferenceStorage(settings.resolved_reference_library_dir)
        print(json.dumps(ref_storage.rebuild_database_from_disk(ref_db), indent=2))
    elif args.command == "benchmark":
        _, _, _, _, _, _, client, _, _, benchmark = _build_branch2_services(settings)
        try:
            report = benchmark.benchmark_branch1_asset(
                args.asset_id,
                version_num=args.version,
                intended_usage=args.usage,
                modeling_style=args.style,
            )
            print(report.model_dump_json(indent=2))
        finally:
            client.close()
    elif args.command == "roblox-init":
        db, registrar, _, _ = _build_roblox_services(settings)
        synced = [] if args.no_sync_existing else registrar.sync_all_from_vault_db(db)
        print(
            json.dumps(
                {
                    "db_path": str(registrar.db.db_path),
                    "integrity": registrar.db.verify_integrity(),
                    "universal_departments": len(registrar.categories.list_categories(library_type="UNIVERSAL")),
                    "genre_departments": len(registrar.categories.list_categories(library_type="GENRE")),
                    "synced_assets": [a.asset_id for a in synced],
                },
                indent=2,
            )
        )
    elif args.command == "roblox-qa":
        _, _, _, wf = _build_roblox_services(settings)
        p = Path(args.target)
        if p.is_file():
            qa_out = wf.qa_engine.evaluate_blend(
                blend_path=p,
                asset_id=p.stem,
                asset_name=p.stem,
                department_code=args.department,
                export_format=args.format,
                render_previews=not args.no_previews,
            )
            rpaths = QAReportGenerator(settings.resolved_roblox_reports_dir).generate_reports(qa_out)
            print(
                json.dumps(
                    {
                        "asset_id": qa_out.asset_id,
                        "overall_score": qa_out.overall_score,
                        "grade": qa_out.grade,
                        "final_status": qa_out.final_status,
                        "report_paths": {k: str(v) for k, v in rpaths.items()},
                    },
                    indent=2,
                )
            )
        else:
            res = wf.run_qa_for_asset(
                args.target,
                version_number=args.version,
                render_previews=not args.no_previews,
                export_format=args.format,
            )
            print(
                json.dumps(
                    {
                        "asset_id": res["asset"].asset_id,
                        "overall_score": res["qa_output"].overall_score,
                        "grade": res["qa_output"].grade,
                        "final_status": res["qa_output"].final_status,
                        "report_paths": {k: str(v) for k, v in res["report_paths"].items()},
                    },
                    indent=2,
                )
            )
    elif args.command == "roblox-approve":
        _, _, _, wf = _build_roblox_services(settings)
        res = wf.approve_asset(
            args.asset_id,
            version_number=args.version,
            reviewer=args.reviewer,
            notes=args.notes,
            sync_to_drive=not args.no_drive_sync,
        )
        print(
            json.dumps(
                {
                    "asset_id": res["asset"].asset_id,
                    "status": res["asset"].production_status.value,
                    "approved_by": res["asset"].approved_by,
                    "drive_sync": res["drive_sync"],
                },
                indent=2,
            )
        )
    elif args.command == "roblox-retry-drive":
        _, _, sync_mgr, _ = _build_roblox_services(settings)
        print(json.dumps(sync_mgr.retry_pending_uploads(), indent=2))
    elif args.command == "roblox-status":
        registrar = RobloxAssetRegistrar(settings)
        print(
            json.dumps(
                {
                    "integrity": registrar.db.verify_integrity(),
                    "assets": [
                        {
                            "asset_id": a.asset_id,
                            "asset_name": a.asset_name,
                            "status": a.production_status.value,
                            "qa_score": a.qa_score,
                            "qa_grade": a.qa_grade,
                            "human_approved": a.human_approved,
                            "google_drive_file_id": a.google_drive_file_id,
                        }
                        for a in registrar.assets.list_assets()
                    ],
                    "pending_drive_uploads": len(registrar.logs.list_pending_drive_uploads()),
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
