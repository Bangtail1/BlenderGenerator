"""Command-line entry point for running Blender Asset Vault web server, Branch 2 collector, or CLI checks."""

from __future__ import annotations

import argparse
import json
import time

import uvicorn

from .blender_worker.runner import BlenderWorkerRunner
from .config import Settings
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
from .services.gdrive_service import GoogleDriveSyncService
from .services.storage import VaultStorage


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


def main() -> None:
    parser = argparse.ArgumentParser(description="Blender Asset Vault CLI")
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


if __name__ == "__main__":
    main()
