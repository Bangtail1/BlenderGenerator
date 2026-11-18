"""FastAPI router exposing Pipeline 1 (Roblox SQLite DB) and Pipeline 2 (Automated QA & Reports)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..database import ProductionStatus


class ApproveBody(BaseModel):
    reviewer: str
    notes: str = ""
    version_number: Optional[int] = None
    sync_to_drive: bool = True


class RejectBody(BaseModel):
    reviewer: str
    reason: str = ""
    version_number: Optional[int] = None


class RunQABody(BaseModel):
    version_number: Optional[int] = None
    export_format: Optional[str] = None
    render_previews: bool = True


def build_roblox_router(app_state: Any) -> APIRouter:
    """Create FastAPI router for Roblox asset database, QA scoring, reports, and approval."""
    router = APIRouter(prefix="/api/roblox", tags=["Roblox Asset Pipeline"])

    @router.get("/health")
    def roblox_health() -> dict[str, Any]:
        wf = app_state.roblox_workflow
        integrity = wf.db.verify_integrity()
        pending_uploads = wf.logs.list_pending_drive_uploads()
        return {
            "status": "ok" if integrity["ok"] else "error",
            "db_path": str(wf.db.db_path),
            "integrity": integrity,
            "universal_departments": len(wf.categories.list_categories(library_type="UNIVERSAL")),
            "genre_departments": len(wf.categories.list_categories(library_type="GENRE")),
            "pending_drive_uploads": len(pending_uploads),
        }

    @router.get("/categories")
    def list_categories(library_type: Optional[str] = Query(default=None)) -> dict[str, Any]:
        wf = app_state.roblox_workflow
        cats = wf.categories.list_categories(library_type=library_type)
        return {"categories": [c.model_dump() for c in cats], "total": len(cats)}

    @router.get("/assets")
    def list_assets(
        category_id: Optional[int] = Query(default=None),
        production_status: Optional[str] = Query(default=None),
        qa_status: Optional[str] = Query(default=None),
        visual_style: Optional[str] = Query(default=None),
        genre_category_id: Optional[int] = Query(default=None),
        tag: Optional[str] = Query(default=None),
        search: Optional[str] = Query(default=None),
    ) -> dict[str, Any]:
        wf = app_state.roblox_workflow
        items = wf.assets.list_assets(
            category_id=category_id,
            production_status=production_status,
            qa_status=qa_status,
            visual_style=visual_style,
            genre_category_id=genre_category_id,
            tag=tag,
            search=search,
        )
        return {"assets": [a.model_dump() for a in items], "total": len(items)}

    @router.get("/assets/{asset_id}")
    def get_asset(asset_id: str) -> dict[str, Any]:
        wf = app_state.roblox_workflow
        asset = wf.assets.get_asset(asset_id)
        if not asset:
            raise HTTPException(status_code=404, detail=f"Asset '{asset_id}' not found.")
        versions = wf.versions.list_versions(asset_id)
        reports = wf.qa_reports.list_reports_for_asset(asset_id)
        logs = wf.logs.list_logs(asset_id=asset_id, limit=50)
        return {
            "asset": asset.model_dump(),
            "versions": [v.model_dump() for v in versions],
            "qa_reports": [r.model_dump() for r in reports],
            "production_logs": [l.model_dump() for l in logs],
        }

    @router.post("/assets/{asset_id}/qa")
    def run_asset_qa(asset_id: str, body: RunQABody = RunQABody()) -> dict[str, Any]:
        wf = app_state.roblox_workflow
        try:
            res = wf.run_qa_for_asset(
                asset_id,
                version_number=body.version_number,
                render_previews=body.render_previews,
                export_format=body.export_format,
            )
            return {
                "asset": res["asset"].model_dump(),
                "version": res["version"].model_dump(),
                "qa_report": res["qa_report"].model_dump(),
                "report_paths": {k: str(v) for k, v in res["report_paths"].items()},
            }
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/assets/{asset_id}/approve")
    def approve_asset(asset_id: str, body: ApproveBody) -> dict[str, Any]:
        wf = app_state.roblox_workflow
        try:
            res = wf.approve_asset(
                asset_id,
                version_number=body.version_number,
                reviewer=body.reviewer,
                notes=body.notes,
                sync_to_drive=body.sync_to_drive,
            )
            return {
                "asset": res["asset"].model_dump(),
                "version": res["version"].model_dump(),
                "report_paths": {k: str(v) for k, v in res["report_paths"].items()},
                "drive_sync": res["drive_sync"],
            }
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/assets/{asset_id}/reject")
    def reject_asset(asset_id: str, body: RejectBody) -> dict[str, Any]:
        wf = app_state.roblox_workflow
        try:
            updated = wf.reject_asset(
                asset_id,
                version_number=body.version_number,
                reviewer=body.reviewer,
                reason=body.reason,
            )
            return {"asset": updated.model_dump()}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/assets/{asset_id}/report/{kind}")
    def download_qa_report(asset_id: str, kind: str) -> FileResponse:
        wf = app_state.roblox_workflow
        report = wf.qa_reports.get_latest_report_for_asset(asset_id)
        if not report:
            raise HTTPException(status_code=404, detail=f"No QA report found for '{asset_id}'.")
        if kind == "json":
            target = Path(report.report_json_path) if report.report_json_path else None
        elif kind == "html":
            target = Path(report.report_html_path) if report.report_html_path else None
        else:
            raise HTTPException(status_code=400, detail="Report kind must be 'json' or 'html'.")
        if not target or not target.is_file():
            raise HTTPException(status_code=404, detail=f"{kind.upper()} report file missing on disk.")
        return FileResponse(path=str(target), filename=target.name)

    @router.post("/drive/retry-pending")
    def retry_pending_drive_uploads(include_failed: bool = Query(default=False)) -> dict[str, Any]:
        sync_mgr = app_state.roblox_sync
        return sync_mgr.retry_pending_uploads(include_failed=include_failed)

    return router
