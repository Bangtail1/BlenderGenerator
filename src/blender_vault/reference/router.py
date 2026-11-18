"""FastAPI APIRouter for Branch 2 (BlendSwap Reference Intelligence Engine & Benchmarking)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..services.desktop import launch_in_blender_gui
from ..services.gdrive_service import DriveNotAuthenticatedError, DriveNotConfiguredError
from .blendswap_client import BlendSwapAPIError
from .models import ReferenceAssetStatus

logger = logging.getLogger("blender_vault.reference.router")


class RunCycleBody(BaseModel):
    max_downloads: Optional[int] = None
    sync_to_drive: bool = True


class UpdateScheduleBody(BaseModel):
    enabled: Optional[bool] = None
    schedule_time: Optional[str] = None
    collection_time: Optional[str] = None
    timezone: Optional[str] = None
    frequency: Optional[str] = None
    target_downloads: Optional[int] = None
    resume_missed_jobs: Optional[bool] = None


class RunBenchmarkBody(BaseModel):
    version_num: Optional[int] = None
    intended_usage: Optional[str] = None
    modeling_style: Optional[str] = None
    reference_limit: int = 3
    sync_to_drive: bool = True


def build_reference_router(app_state: Any) -> APIRouter:
    """Create FastAPI router exposing Branch 2 collection, library, analytics, and benchmarking endpoints."""
    router = APIRouter(prefix="/api/reference", tags=["Reference Engine"])

    @router.get("/status")
    def reference_status(check_live: bool = Query(default=True)) -> dict[str, Any]:
        client = app_state.bs_client
        scheduler = app_state.ref_scheduler
        ref_db = app_state.ref_db
        conn = client.check_connectivity() if check_live else {
            "configured": bool((app_state.settings.blendswap_key or "").strip()),
            "connected": None,
            "account": None,
        }
        return {
            "blendswap": conn,
            "scheduler": scheduler.get_schedule_config(),
            "api_usage": ref_db.get_api_usage_summary(),
            "library_counts": ref_db.get_category_distribution(),
        }

    @router.get("/dashboard")
    def reference_dashboard(check_live: bool = Query(default=True)) -> dict[str, Any]:
        return app_state.ref_dashboard.build_dashboard_summary(check_live_api=check_live)

    @router.get("/assets")
    def list_reference_assets(
        search: Optional[str] = Query(default=None),
        primary_category: Optional[str] = Query(default=None),
        technical_category: Optional[str] = Query(default=None),
        license_key: Optional[str] = Query(default=None),
        grade_letter: Optional[str] = Query(default=None),
        status: Optional[str] = Query(default=None),
        sort_by: str = Query(default="updated_at"),
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        all_items = app_state.ref_db.list_reference_assets(
            search=search,
            primary_category=primary_category,
            technical_category=technical_category,
            license_key=license_key,
            grade_letter=grade_letter,
            status=status,
            sort_by=sort_by,
            include_details=False,
        )
        sliced = all_items[offset : offset + limit]
        return {
            "assets": [a.model_dump() for a in sliced],
            "total": len(all_items),
            "limit": limit,
            "offset": offset,
        }

    @router.get("/assets/{blendswap_id}")
    def get_reference_asset(blendswap_id: int) -> dict[str, Any]:
        rec = app_state.ref_db.get_reference_asset(blendswap_id)
        if not rec:
            raise HTTPException(status_code=404, detail=f"Reference asset #{blendswap_id} not found.")
        return rec.model_dump()

    @router.get("/assets/{blendswap_id}/preview/{filename}")
    def get_reference_preview(blendswap_id: int, filename: str) -> FileResponse:
        safe = Path(filename).name
        pdir = app_state.ref_storage.asset_previews_dir(blendswap_id).resolve()
        target = (pdir / safe).resolve()
        if not target.is_file() or target.parent != pdir:
            raise HTTPException(status_code=404, detail=f"Preview '{safe}' not found.")
        return FileResponse(path=str(target))

    @router.post("/assets/{blendswap_id}/open-blender")
    def open_reference_in_blender(blendswap_id: int) -> dict[str, Any]:
        rec = app_state.ref_db.get_reference_asset(blendswap_id)
        if not rec:
            raise HTTPException(status_code=404, detail=f"Reference asset #{blendswap_id} not found.")

        blend_target: Optional[Path] = None
        if rec.blend_file_path and Path(rec.blend_file_path).is_file():
            blend_target = Path(rec.blend_file_path).resolve()
        elif rec.primary_file_path and Path(rec.primary_file_path).is_file():
            try:
                blend_target = app_state.ref_inspector.resolve_blend_for_gui(
                    source_file=Path(rec.primary_file_path),
                    blendswap_id=blendswap_id,
                )
                rec.blend_file_path = str(blend_target)
                app_state.ref_db.upsert_reference_asset(rec)
            except FileNotFoundError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

        if not blend_target or not blend_target.is_file():
            raise HTTPException(status_code=404, detail="No native .blend file available locally for this reference.")
        try:
            pid = launch_in_blender_gui(blend_target, app_state.settings.blender_executable)
            return {"ok": True, "pid": pid, "blend_path": str(blend_target)}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.post("/assets/{blendswap_id}/reinspect")
    def reinspect_reference_asset(blendswap_id: int) -> dict[str, Any]:
        rec = app_state.ref_db.get_reference_asset(blendswap_id)
        if not rec or not rec.primary_file_path:
            raise HTTPException(status_code=404, detail=f"Downloaded file missing for BlendSwap #{blendswap_id}.")
        updated = app_state.ref_collector._inspect_grade_and_persist(rec, sync_to_drive=False)
        return updated.model_dump()

    @router.post("/assets/{blendswap_id}/sync-drive")
    def sync_reference_to_drive(blendswap_id: int) -> dict[str, Any]:
        try:
            updated = app_state.ref_drive.sync_reference_asset(blendswap_id)
            return updated.model_dump()
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except (DriveNotConfiguredError, DriveNotAuthenticatedError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/drive/sync-pending")
    def sync_pending_reference_drive() -> dict[str, Any]:
        try:
            return app_state.ref_drive.sync_pending_assets()
        except (DriveNotConfiguredError, DriveNotAuthenticatedError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/reindex")
    def reindex_reference_library() -> dict[str, Any]:
        return app_state.ref_storage.rebuild_database_from_disk(app_state.ref_db)

    @router.get("/discover")
    def discover_blendswap_assets(
        q: Optional[str] = Query(default=None),
        category: Optional[str] = Query(default=None),
        license: Optional[str] = Query(default=None),
        sort: str = Query(default="downloads"),
        page: int = Query(default=1, ge=1),
        per_page: int = Query(default=25, ge=1, le=100),
    ) -> dict[str, Any]:
        try:
            items, pagination = app_state.bs_client.search_assets(
                q=q,
                category=category,
                license=license,
                sort=sort,
                page=page,
                per_page=per_page,
            )
            return {"data": [i.model_dump() for i in items], "pagination": pagination}
        except BlendSwapAPIError as exc:
            raise HTTPException(status_code=exc.status_code or 400, detail=str(exc)) from exc

    @router.post("/cycles/run")
    def run_collection_cycle(body: Optional[RunCycleBody] = None) -> dict[str, Any]:
        opts = body or RunCycleBody()
        cycle = app_state.ref_scheduler.check_and_run_due(
            force=True,
            trigger_type="manual",
            max_downloads=opts.max_downloads,
            sync_to_drive=opts.sync_to_drive,
        )
        return cycle.model_dump() if cycle else {"status": "NO_ACTION"}

    @router.get("/cycles")
    def list_collection_cycles(limit: int = Query(default=30, ge=1, le=100)) -> dict[str, Any]:
        cycles = app_state.ref_db.list_cycles(limit=limit)
        return {"cycles": [c.model_dump() for c in cycles]}

    @router.get("/cycles/{cycle_id}")
    def get_collection_cycle(cycle_id: str) -> dict[str, Any]:
        cycle = app_state.ref_db.get_cycle(cycle_id)
        if not cycle:
            raise HTTPException(status_code=404, detail=f"Collection cycle '{cycle_id}' not found.")
        queue = app_state.ref_db.get_queue_for_cycle(cycle_id)
        return {"cycle": cycle.model_dump(), "queue": [q.model_dump() for q in queue]}

    @router.get("/scheduler")
    def get_scheduler() -> dict[str, Any]:
        cfg = app_state.ref_scheduler.get_schedule_config()
        win_mgr = getattr(app_state, "win_task_scheduler", None)
        return {
            **cfg,
            "windows_task_scheduler": win_mgr.query_task_status() if win_mgr else None,
        }

    @router.post("/scheduler")
    def update_scheduler(body: UpdateScheduleBody) -> dict[str, Any]:
        return app_state.ref_scheduler.update_schedule_config(
            enabled=body.enabled,
            schedule_time=body.schedule_time,
            collection_time=body.collection_time,
            timezone_name=body.timezone,
            frequency=body.frequency,
            target_downloads=body.target_downloads,
            resume_missed_jobs=body.resume_missed_jobs,
        )

    @router.post("/scheduler/pause")
    def pause_scheduler() -> dict[str, Any]:
        return app_state.ref_scheduler.pause()

    @router.post("/scheduler/resume")
    def resume_scheduler() -> dict[str, Any]:
        return app_state.ref_scheduler.resume()

    @router.get("/scheduler/notifications")
    def list_scheduler_notifications(limit: int = Query(default=30, ge=1, le=100)) -> dict[str, Any]:
        events = app_state.ref_scheduler.get_notifications(limit=limit)
        return {"notifications": [e.model_dump() for e in events]}

    @router.get("/scheduler/windows-task")
    def get_windows_task_status() -> dict[str, Any]:
        win_mgr = getattr(app_state, "win_task_scheduler", None)
        if not win_mgr:
            raise HTTPException(status_code=404, detail="Windows Task Scheduler manager not initialized.")
        return win_mgr.query_task_status()

    @router.post("/scheduler/windows-task/install")
    def install_windows_task() -> dict[str, Any]:
        win_mgr = getattr(app_state, "win_task_scheduler", None)
        if not win_mgr:
            raise HTTPException(status_code=404, detail="Windows Task Scheduler manager not initialized.")
        cfg = app_state.ref_scheduler.get_schedule_config()
        return win_mgr.install_task(
            collection_time=cfg["collection_time"],
            timezone_name=cfg["timezone"],
            enabled=bool(cfg["enabled"]),
            resume_missed_jobs=bool(cfg.get("resume_missed_jobs", True)),
            recheck_interval_minutes=int(cfg.get("reset_recheck_minutes", 15)),
        )

    @router.post("/scheduler/windows-task/uninstall")
    def uninstall_windows_task() -> dict[str, Any]:
        win_mgr = getattr(app_state, "win_task_scheduler", None)
        if not win_mgr:
            raise HTTPException(status_code=404, detail="Windows Task Scheduler manager not initialized.")
        return win_mgr.uninstall_task()

    @router.get("/benchmarks")
    def list_benchmarks(asset_id: Optional[str] = Query(default=None), limit: int = Query(default=50)) -> dict[str, Any]:
        reports = app_state.ref_db.list_benchmark_reports(branch1_asset_id=asset_id, limit=limit)
        return {"benchmarks": [r.model_dump() for r in reports], "total": len(reports)}

    @router.get("/benchmarks/{benchmark_id}")
    def get_benchmark(benchmark_id: str) -> dict[str, Any]:
        report = app_state.ref_db.get_benchmark_report(benchmark_id)
        if not report:
            raise HTTPException(status_code=404, detail=f"Benchmark '{benchmark_id}' not found.")
        return report.model_dump()

    @router.get("/benchmarks/matches/{asset_id}")
    def preview_benchmark_matches(
        asset_id: str,
        intended_usage: Optional[str] = Query(default=None),
        modeling_style: Optional[str] = Query(default=None),
        limit: int = Query(default=3, ge=1, le=10),
    ) -> dict[str, Any]:
        asset = app_state.db.get_asset(asset_id)
        if not asset:
            raise HTTPException(status_code=404, detail=f"Branch 1 asset '{asset_id}' not found.")
        matches = app_state.benchmark_engine.find_matching_references(
            asset,
            intended_usage=intended_usage,
            modeling_style=modeling_style,
            limit=limit,
        )
        return {
            "asset_id": asset_id,
            "matches": [
                {
                    "blendswap_id": ref.blendswap_id,
                    "title": ref.title,
                    "primary_category": ref.primary_category,
                    "grade_letter": ref.grade_letter,
                    "technical_quality_score": ref.technical_quality_score,
                    "match_score": score,
                    "reason": reason,
                }
                for ref, score, reason in matches
            ],
        }

    @router.post("/benchmarks/run/{asset_id}")
    def run_benchmark(asset_id: str, body: Optional[RunBenchmarkBody] = None) -> dict[str, Any]:
        opts = body or RunBenchmarkBody()
        try:
            report = app_state.benchmark_engine.benchmark_branch1_asset(
                asset_id,
                version_num=opts.version_num,
                intended_usage=opts.intended_usage,
                modeling_style=opts.modeling_style,
                reference_limit=opts.reference_limit,
                sync_to_drive=opts.sync_to_drive,
            )
            return report.model_dump()
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return router
