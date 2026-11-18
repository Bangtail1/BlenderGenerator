"""Unified Control Studio FastAPI routes for Generation Studio, Overview, Collection Control, and Quality Intelligence."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import APIRouter, Body, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from ..blender_worker.scripts.generators.registry import PUBLIC_TEMPLATE_METADATA
from ..config import STANDARD_CATEGORIES
from ..models import AssetStatus, GenerateAssetRequest
from ..reference.models import CollectionRulesConfig
from ..reference.rules_manager import CollectionRulesManager
from ..services.pipeline import DuplicateGenerationJobError


class CollectionRunRequest(BaseModel):
    max_downloads: Optional[int] = Field(default=None, ge=1, le=50)
    sync_to_drive: bool = True


class CollectionRetryFailedRequest(BaseModel):
    cycle_id: Optional[str] = None
    sync_to_drive: bool = True


class CollectionRulesUpdateRequest(BaseModel):
    scope: Literal["permanent", "next_run"] = "permanent"
    daily_target: int = Field(default=7, ge=1, le=50)
    preferred_categories: list[str] = Field(default_factory=list)
    excluded_categories: list[str] = Field(default_factory=list)
    preferred_licenses: list[str] = Field(default_factory=list)
    min_downloads: int = Field(default=0, ge=0)
    preferred_modeling_styles: list[str] = Field(default_factory=list)
    preferred_poly_complexity: str = "any"
    preferred_file_format: str = "blend"
    diversity_enabled: bool = True
    max_per_category_per_cycle: int = Field(default=2, ge=1, le=10)
    collection_time: str = "07:00"
    timezone: str = "Europe/London"
    excluded_candidate_ids: list[str] = Field(default_factory=list)


class RestoreDefaultsRequest(BaseModel):
    clear_next_run: bool = True


def _infer_poly_complexity(tris: int) -> str:
    if tris <= 0:
        return "unknown"
    if tris <= 10_000:
        return "low_poly"
    if tris <= 80_000:
        return "mid_poly"
    return "high_poly"


def _extract_preview_url(asset: Any) -> Optional[str]:
    latest = getattr(asset, "latest", None)
    if not latest:
        return None
    vnum = latest.version
    vqa = latest.visual_qa
    if not vqa:
        return None
    if vqa.contact_sheet_path:
        return f"/api/assets/{asset.asset_id}/versions/{vnum}/preview/{Path(vqa.contact_sheet_path).name}"
    rp = vqa.render_paths
    if isinstance(rp, dict):
        for val in rp.values():
            if val:
                return f"/api/assets/{asset.asset_id}/versions/{vnum}/preview/{Path(str(val)).name}"
    elif isinstance(rp, list) and rp:
        return f"/api/assets/{asset.asset_id}/versions/{vnum}/preview/{Path(str(rp[0])).name}"
    return None


def _extract_ref_modeling_style(ref: Any) -> list[str]:
    styles: list[str] = []
    for tc in (ref.technical_categories or []):
        low = str(tc).strip().lower()
        if low:
            styles.append(low)
    insp = ref.inspection
    if insp and getattr(insp, "subdivision_ready", False) and "sub-d" not in styles:
        styles.append("sub-d")
    if insp and getattr(insp, "has_rig", False) and "rigged" not in styles:
        styles.append("rigged")
    tris = int(getattr(insp, "triangle_count", 0) or 0) if insp else 0
    if 0 < tris <= 10_000 and "low-poly" not in styles:
        styles.append("low-poly")
    return styles


def _score_to_grade_letter(score: Optional[float]) -> str:
    if score is None:
        return "Unrated"
    val = float(score)
    if val >= 90.0:
        return "A"
    if val >= 78.0:
        return "B"
    if val >= 64.0:
        return "C"
    if val >= 50.0:
        return "D"
    return "F"


def _get_executor(app_state: Any) -> ThreadPoolExecutor:
    existing = getattr(app_state, "executor", None)
    if existing is not None:
        return existing
    ex = ThreadPoolExecutor(max_workers=2, thread_name_prefix="studio-worker")
    app_state.executor = ex
    return ex


def _get_rules_manager(app_state: Any) -> CollectionRulesManager:
    mgr = getattr(app_state, "rules_manager", None)
    if mgr is not None:
        return mgr
    mgr = getattr(app_state.ref_collector, "rules_manager", None)
    if mgr is None:
        mgr = CollectionRulesManager(app_state.db, app_state.settings)
        app_state.ref_collector.rules_manager = mgr
    app_state.rules_manager = mgr
    return mgr


def _control_payload(app_state: Any) -> dict[str, Any]:
    ctrl = dict(app_state.ref_collector.get_control_state())
    ctrl["is_running"] = bool(app_state.ref_collector.is_running())
    latest = app_state.ref_db.get_latest_cycle()
    ctrl["current_stage"] = latest.current_stage if latest else None
    ctrl["stages"] = list(latest.stages) if latest else []
    return ctrl


def _rules_bundle_payload(rules_mgr: CollectionRulesManager) -> dict[str, Any]:
    b = rules_mgr.get_rules_bundle()
    return {
        "active_rules": b["active"],
        "permanent_rules": b["permanent"],
        "default_rules": b["defaults"],
        "next_run_override": b["next_run_override"],
        "has_next_run_override": b["has_next_run_override"],
        **b,
    }


def _serialize_queue_items(app_state: Any, queue_items: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for q in queue_items:
        ref = app_state.ref_db.get_reference_asset(q.blendswap_id)
        d = q.model_dump()
        d["item_id"] = q.queue_id
        d["title"] = ref.title if ref else f"BlendSwap #{q.blendswap_id}"
        d["primary_category"] = ref.primary_category if ref else "Other"
        d["license_key"] = (
            ref.license_assessment.license_key
            if (ref and ref.license_assessment)
            else "unknown"
        )
        d["primary_format"] = ref.primary_format if ref else "blend"
        d["retry_count"] = q.attempts
        d["error_message"] = q.last_error
        out.append(d)
    return out


def _serialize_job(job: dict[str, Any], asset_map: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    out = dict(job)
    aid = out.get("asset_id")
    if asset_map is not None and aid and aid in asset_map:
        asset = asset_map[aid]
        latest = asset.latest
        human_rev = latest.human_review if latest else None
        human_status = (
            human_rev.status.value
            if (human_rev and hasattr(human_rev.status, "value"))
            else (str(human_rev.status) if human_rev else None)
        )
        asset_status = (
            latest.status.value
            if (latest and hasattr(latest.status, "value"))
            else (str(latest.status) if latest else "UNKNOWN")
        )
        out["asset_summary"] = {
            "asset_id": asset.asset_id,
            "name": asset.name,
            "category": asset.category,
            "roblox_asset_id": asset.roblox_asset_id,
            "current_version": asset.current_version,
            "status": asset_status,
            "qa_score": latest.visual_qa.score if (latest and latest.visual_qa) else None,
            "tech_qa_status": latest.technical_qa.status if (latest and latest.technical_qa) else None,
            "visual_qa_status": latest.visual_qa.status if (latest and latest.visual_qa) else None,
            "human_decision": human_status,
            "drive_sync_status": latest.drive_sync_status if latest else None,
            "preview_url": _extract_preview_url(asset),
            "glb_url": (
                f"/api/assets/{asset.asset_id}/versions/{latest.version}/download/glb"
                if (latest and latest.glb_path)
                else None
            ),
            "poly_count": latest.poly_count if latest else 0,
        }
    return out


def build_studio_router(app_state: Any) -> APIRouter:
    """Create unified Control Studio API router backed by existing Vault/Reference/Roblox services."""
    router = APIRouter(prefix="/api", tags=["studio"])

    # ------------------------------------------------------------------
    # System Diagnostics (NEVER expose secret tokens or API keys)
    # ------------------------------------------------------------------
    @router.get("/system/health")
    def get_system_health(check_live: bool = Query(default=False)) -> dict[str, Any]:
        settings = app_state.settings
        blender = dict(app_state.runner.check_health())
        if not blender.get("version") and blender.get("version_line"):
            blender["version"] = blender["version_line"]
        if "meets_minimum" not in blender:
            blender["meets_minimum"] = bool(blender.get("available"))
        gdrive = app_state.gdrive.get_auth_status()
        sched_cfg = app_state.ref_scheduler.get_schedule_config()
        ref_summary = app_state.ref_dashboard.build_dashboard_summary(check_live_api=check_live)
        ov = ref_summary.get("collection_overview", {})
        vault_assets = app_state.db.list_assets()
        vault_jobs = app_state.db.list_jobs(limit=200)
        ref_assets = app_state.ref_db.list_reference_assets(include_details=False)
        roblox_assets = app_state.roblox_workflow.assets.list_assets(limit=500)

        vision_provider = (settings.vision_qa_provider or "").strip().lower()
        if not vision_provider or vision_provider == "auto":
            vision_provider = (
                "gemini"
                if bool((settings.gemini_api_key or "").strip())
                else ("openai" if bool((settings.openai_api_key or "").strip()) else "none")
            )
        return {
            "backend": {
                "ok": True,
                "status": "ONLINE",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
            "blender": blender,
            "google_drive": gdrive,
            "blendswap": {
                "key_configured": bool((settings.blendswap_key or "").strip()),
                "allow_credit_spend": bool(settings.allow_credit_spend),
                "connectivity": ov.get("api_connectivity", {}),
                "api_usage": ov.get("api_usage", {}),
            },
            "scheduler": {
                **sched_cfg,
                "is_running": app_state.ref_collector.is_running(),
                "windows_task": ov.get("windows_task_scheduler", {}),
            },
            "vision_qa": {
                "configured": vision_provider != "none"
                and (
                    bool((settings.gemini_api_key or "").strip())
                    or bool((settings.openai_api_key or "").strip())
                    or vision_provider == "ollama"
                ),
                "provider": vision_provider,
                "gemini_configured": bool((settings.gemini_api_key or "").strip()),
                "openai_configured": bool((settings.openai_api_key or "").strip()),
                "gemini_model": settings.gemini_vision_model,
                "openai_model": settings.openai_vision_model,
                "ollama_model": settings.ollama_vision_model,
            },
            "databases": {
                "vault": {
                    "ok": True,
                    "path": str(settings.db_path),
                    "assets_count": len(vault_assets),
                    "jobs_count": len(vault_jobs),
                },
                "reference": {
                    "ok": True,
                    "path": str(settings.db_path),
                    "assets_count": len(ref_assets),
                },
                "roblox": {
                    "ok": True,
                    "path": str(settings.resolved_roblox_db_path),
                    "assets_count": len(roblox_assets),
                },
            },
            "storage_paths": {
                "vault_dir": str(settings.vault_data_dir),
                "reference_library_dir": str(settings.resolved_reference_library_dir),
                "roblox_reports_dir": str(settings.resolved_roblox_reports_dir),
            },
        }

    # ------------------------------------------------------------------
    # Overview Dashboard
    # ------------------------------------------------------------------
    @router.get("/dashboard/overview")
    def get_dashboard_overview(check_live: bool = Query(default=False)) -> dict[str, Any]:
        health = get_system_health(check_live=check_live)
        ref_summary = app_state.ref_dashboard.build_dashboard_summary(check_live_api=check_live)
        ov = ref_summary.get("collection_overview", {})
        ctrl = _control_payload(app_state)

        vault_assets = app_state.db.list_assets()
        jobs = app_state.db.list_jobs(limit=100)
        asset_map = {a.asset_id: a for a in vault_assets}

        status_counts: dict[str, int] = {}
        category_counts: dict[str, int] = {}
        visual_scores: list[float] = []
        awaiting_qa = 0
        awaiting_approval = 0
        approved_count = 0
        failed_count = 0

        for a in vault_assets:
            category_counts[a.category] = category_counts.get(a.category, 0) + 1
            st = a.latest.status if a.latest else "PROCESSING"
            status_counts[st] = status_counts.get(st, 0) + 1
            if st in {"PROCESSING", "TECHNICAL_TESTING", "VISUAL_TESTING"}:
                awaiting_qa += 1
            elif st == "AWAITING_HUMAN_APPROVAL":
                awaiting_approval += 1
            elif st in {"APPROVED", "UPLOADED"}:
                approved_count += 1
            elif st in {"FAILED", "REJECTED"}:
                failed_count += 1
            if a.latest and a.latest.visual_qa and a.latest.visual_qa.score is not None:
                visual_scores.append(float(a.latest.visual_qa.score))

        active_jobs = [j for j in jobs if j.get("status") in {"QUEUED", "RUNNING"}]

        # Quality & Roblox metrics
        ref_assets = app_state.ref_db.list_reference_assets(include_details=True)
        benchmarks = app_state.ref_db.list_benchmark_reports(limit=50)
        roblox_assets = app_state.roblox_workflow.assets.list_assets(limit=500)

        ref_scores = [
            float(r.technical_quality_score)
            for r in ref_assets
            if r.technical_quality_score is not None
        ]
        roblox_scores = [float(r.qa_score) for r in roblox_assets if r.qa_score is not None]
        benchmark_scores = [
            float(b.local_grade.total_score)
            for b in benchmarks
            if b.local_grade and b.local_grade.total_score is not None
        ]

        ref_grade_dist: dict[str, int] = {"A": 0, "B": 0, "C": 0, "D": 0, "F": 0}
        for r in ref_assets:
            gl = (r.grade_letter or "").upper()
            if gl in ref_grade_dist:
                ref_grade_dist[gl] += 1

        # Activity timeline combining jobs, scheduler events, and Roblox logs
        events: list[dict[str, Any]] = []
        for j in jobs[:15]:
            events.append(
                {
                    "timestamp": j.get("updated_at") or j.get("created_at") or "",
                    "source": "generation",
                    "type": j.get("job_type") or "job",
                    "status": j.get("status") or "UNKNOWN",
                    "title": f"Job {j.get('job_id')} ({j.get('stage') or j.get('status')})",
                    "message": j.get("message") or "",
                    "asset_id": j.get("asset_id"),
                }
            )
        for ev in app_state.ref_db.list_scheduler_events(limit=15):
            events.append(
                {
                    "timestamp": ev.created_at,
                    "source": "collection",
                    "type": ev.event_type,
                    "status": ev.status,
                    "title": f"Collection {ev.event_type}",
                    "message": ev.message,
                    "cycle_id": ev.cycle_id,
                }
            )
        for lg in app_state.roblox_workflow.logs.list_logs(limit=15):
            events.append(
                {
                    "timestamp": lg.timestamp,
                    "source": "roblox_qa",
                    "type": lg.process_name,
                    "status": lg.status,
                    "title": f"{lg.asset_id}: {lg.process_name}",
                    "message": lg.message,
                    "asset_id": lg.asset_id,
                }
            )
        events.sort(key=lambda item: str(item.get("timestamp") or ""), reverse=True)

        avg_gen_score = (
            round(sum(roblox_scores) / len(roblox_scores), 1)
            if roblox_scores
            else (round(sum(visual_scores) / len(visual_scores), 1) if visual_scores else None)
        )

        return {
            "system_status": health,
            "generation": {
                "total_generated": len(vault_assets),
                "awaiting_qa": awaiting_qa,
                "awaiting_human_approval": awaiting_approval,
                "approved": approved_count,
                "failed": failed_count,
                "active_jobs_count": len(active_jobs),
                "active_jobs": [_serialize_job(j, asset_map) for j in active_jobs],
                "recent_jobs": [_serialize_job(j, asset_map) for j in jobs[:10]],
                "status_breakdown": status_counts,
                "category_breakdown": category_counts,
            },
            "reference_collection": {
                **ov,
                "control": ctrl,
            },
            "quality": {
                "average_generation_qa_score": avg_gen_score,
                "average_reference_score": round(sum(ref_scores) / len(ref_scores), 1) if ref_scores else None,
                "average_benchmark_score": (
                    round(sum(benchmark_scores) / len(benchmark_scores), 1) if benchmark_scores else None
                ),
                "reference_library_size": len(ref_assets),
                "benchmark_count": len(benchmarks),
                "roblox_assets_count": len(roblox_assets),
                "reference_grade_distribution": ref_grade_dist,
                "recent_benchmarks": [b.model_dump() for b in benchmarks[:5]],
            },
            "recent_activity": events[:25],
        }

    # ------------------------------------------------------------------
    # Generation Studio
    # ------------------------------------------------------------------
    @router.get("/generation/templates")
    def get_generation_templates() -> dict[str, Any]:
        return {
            "templates": [
                {"template_id": k, **meta}
                for k, meta in PUBLIC_TEMPLATE_METADATA.items()
            ],
            "categories": STANDARD_CATEGORIES,
            "visual_styles": ["LP", "REALISTIC", "STYLIZED", "MINIMAL"],
            "complexities": ["low", "medium", "high"],
        }

    @router.get("/generation/jobs")
    def list_generation_jobs(
        limit: int = Query(default=50, ge=1, le=200),
        status: Optional[str] = Query(default=None),
    ) -> dict[str, Any]:
        assets = app_state.db.list_assets()
        asset_map = {a.asset_id: a for a in assets}
        jobs = app_state.db.list_jobs(limit=limit)
        if status and status.strip().upper() != "ALL":
            wanted = status.strip().upper()
            jobs = [j for j in jobs if str(j.get("status") or "").upper() == wanted]
        return {
            "count": len(jobs),
            "jobs": [_serialize_job(j, asset_map) for j in jobs],
        }

    @router.get("/generation/jobs/{job_id}")
    def get_generation_job(job_id: str) -> dict[str, Any]:
        job = app_state.db.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail=f"Generation job '{job_id}' not found.")
        asset = app_state.db.get_asset(job["asset_id"]) if job.get("asset_id") else None
        asset_map = {asset.asset_id: asset} if asset else {}
        benchmarks = (
            [b.model_dump() for b in app_state.ref_db.list_benchmark_reports(branch1_asset_id=asset.asset_id, limit=10)]
            if asset
            else []
        )
        return {
            "job": _serialize_job(job, asset_map),
            "asset": asset.model_dump() if asset else None,
            "benchmarks": benchmarks,
        }

    @router.post("/generation/create")
    def create_generation_job(req: GenerateAssetRequest) -> dict[str, Any]:
        try:
            asset, job = app_state.pipeline.enqueue_generation_job(req)
        except DuplicateGenerationJobError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        executor = _get_executor(app_state)
        executor.submit(app_state.pipeline.execute_generation_job, job["job_id"], asset.asset_id, req)
        return {
            "status": "QUEUED",
            "job": _serialize_job(job, {asset.asset_id: asset}),
            "asset": asset.model_dump(),
        }

    @router.post("/generation/jobs/{job_id}/cancel")
    def cancel_generation_job(job_id: str) -> dict[str, Any]:
        existing = app_state.db.get_job(job_id)
        if not existing:
            raise HTTPException(status_code=404, detail=f"Generation job '{job_id}' not found.")
        updated = app_state.db.request_job_cancel(job_id)
        if (
            updated
            and updated.get("status") == "CANCELLED"
            and updated.get("asset_id")
            and existing.get("status") == "QUEUED"
        ):
            ver = app_state.db.get_version(updated["asset_id"], 1)
            if ver:
                ver.status = AssetStatus.FAILED
                ver.error_message = "Generation cancelled before worker execution started."
                ver.failed_stage = "cancelled"
                app_state.db.update_version(ver)
        return {"job": updated}

    @router.post("/generation/jobs/{job_id}/retry")
    def retry_generation_job(job_id: str) -> dict[str, Any]:
        existing = app_state.db.get_job(job_id)
        if not existing:
            raise HTTPException(status_code=404, detail=f"Generation job '{job_id}' not found.")
        if existing.get("status") in {"QUEUED", "RUNNING"}:
            raise HTTPException(status_code=409, detail="Cannot retry a job that is still active.")
        params = dict(existing.get("params") or {})
        asset = app_state.db.get_asset(existing["asset_id"]) if existing.get("asset_id") else None
        if not params and not asset:
            raise HTTPException(status_code=400, detail="Job lacks stored parameters to retry.")

        executor = _get_executor(app_state)
        if asset and asset.latest and asset.latest.blend_path and Path(asset.latest.blend_path).is_file():
            # Blend already exists; re-run QA pipeline on existing version
            fut = executor.submit(app_state.pipeline.retry_asset_version, asset.asset_id, asset.latest.version)
            return {
                "status": "RETRY_QA_QUEUED",
                "asset_id": asset.asset_id,
                "version": asset.latest.version,
                "reused_blend": True,
                "submitted": not fut.done(),
            }

        req = GenerateAssetRequest(
            prompt=str(params.get("prompt") or (asset.description if asset else "Wooden Chair")),
            name=params.get("name") or (f"{asset.name} Retry" if asset else None),
            category=str(params.get("category") or (asset.category if asset else "Furniture")),
            tags=list(params.get("tags") or (asset.tags if asset else [])),
            template_id=params.get("template_id"),
            geometry_profile=params.get("geometry_profile") or "game_low_poly",
            params=dict(params.get("params") or {}),
            visual_style=params.get("visual_style") or "LP",
            variant_name=params.get("variant_name") or "Basic",
            complexity=params.get("complexity") or "medium",
            poly_budget=params.get("poly_budget"),
            additional_instructions=params.get("additional_instructions") or "",
        )
        try:
            new_asset, new_job = app_state.pipeline.enqueue_generation_job(req)
        except DuplicateGenerationJobError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        executor.submit(app_state.pipeline.execute_generation_job, new_job["job_id"], new_asset.asset_id, req)
        return {
            "status": "QUEUED",
            "reused_blend": False,
            "job": _serialize_job(new_job, {new_asset.asset_id: new_asset}),
            "asset": new_asset.model_dump(),
        }

    # ------------------------------------------------------------------
    # Extended Reference Library Facet Filter
    # ------------------------------------------------------------------
    @router.get("/reference/library")
    def list_reference_library_faceted(
        search: Optional[str] = Query(default=None),
        category: Optional[str] = Query(default=None),
        license_key: Optional[str] = Query(default=None),
        grade_letter: Optional[str] = Query(default=None),
        poly_complexity: Optional[str] = Query(default=None),
        modeling_style: Optional[str] = Query(default=None),
        date_collected: Optional[str] = Query(default=None),
        status: Optional[str] = Query(default=None),
        limit: int = Query(default=100, ge=1, le=300),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        all_refs = app_state.ref_db.list_reference_assets(include_details=True)

        categories = sorted({r.primary_category for r in all_refs if r.primary_category})
        licenses = sorted({r.license_assessment.license_key for r in all_refs if r.license_assessment})
        grades = sorted({r.grade_letter for r in all_refs if r.grade_letter})
        styles = sorted({s for r in all_refs for s in _extract_ref_modeling_style(r)})
        dates = sorted({(r.downloaded_at or r.created_at or "")[:10] for r in all_refs if (r.downloaded_at or r.created_at)}, reverse=True)

        filtered: list[dict[str, Any]] = []
        q = (search or "").strip().lower()
        for r in all_refs:
            tris = int(r.inspection.triangle_count) if r.inspection else 0
            comp = _infer_poly_complexity(tris)
            ref_styles = _extract_ref_modeling_style(r)
            collected_day = (r.downloaded_at or r.created_at or "")[:10]

            if q:
                hay = f"{r.blendswap_id} {r.title} {r.description} {r.author.username} {' '.join(r.tags)}".lower()
                if q not in hay:
                    continue
            if category and r.primary_category.lower() != category.strip().lower():
                continue
            if license_key and (not r.license_assessment or r.license_assessment.license_key != license_key.strip().lower()):
                continue
            if grade_letter and (r.grade_letter or "").upper() != grade_letter.strip().upper():
                continue
            if status and r.status.lower() != status.strip().lower():
                continue
            if poly_complexity and poly_complexity != "all" and comp != poly_complexity.strip().lower():
                continue
            if modeling_style and modeling_style != "all" and modeling_style.strip().lower() not in ref_styles:
                continue
            if date_collected and collected_day != date_collected.strip():
                continue

            d = r.model_dump()
            d["poly_complexity"] = comp
            d["modeling_styles"] = ref_styles
            d["date_collected"] = collected_day
            d["preview_urls"] = [
                f"/api/reference/assets/{r.blendswap_id}/preview/{Path(p).name}"
                for p in (r.preview_paths or [])
            ]
            filtered.append(d)

        paged = filtered[offset : offset + limit]
        return {
            "total": len(filtered),
            "count": len(paged),
            "items": paged,
            "facets": {
                "categories": categories,
                "licenses": licenses,
                "grades": grades,
                "modeling_styles": styles,
                "poly_complexities": ["low_poly", "mid_poly", "high_poly"],
                "dates_collected": dates,
            },
        }

    # ------------------------------------------------------------------
    # Collection Control Panel (Status, SSE, History, Rules, Preview, Actions)
    # ------------------------------------------------------------------
    @router.get("/collection/status")
    def get_collection_status(check_live: bool = Query(default=False)) -> dict[str, Any]:
        rules_mgr = _get_rules_manager(app_state)
        latest_cycle = app_state.ref_db.get_latest_cycle()
        queue_items = (
            app_state.ref_db.get_queue_for_cycle(latest_cycle.cycle_id)
            if latest_cycle
            else []
        )
        ctrl = _control_payload(app_state)
        sched = app_state.ref_scheduler.get_schedule_config()
        ref_summary = app_state.ref_dashboard.build_dashboard_summary(check_live_api=check_live)
        ov = ref_summary.get("collection_overview", {})

        return {
            "control": ctrl,
            "today_cycle": latest_cycle.model_dump() if latest_cycle else None,
            "queue": _serialize_queue_items(app_state, queue_items),
            "scheduler": sched,
            "overview": ov,
            "rules": _rules_bundle_payload(rules_mgr),
        }

    @router.get("/collection/events")
    async def stream_collection_events(
        request: Request,
        once: bool = Query(default=False),
    ) -> StreamingResponse:
        bus = app_state.ref_collector.event_bus
        q = bus.subscribe()

        async def event_generator():
            try:
                latest_cycle = app_state.ref_db.get_latest_cycle()
                snapshot = {
                    "type": "snapshot",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "control": _control_payload(app_state),
                    "today_cycle": latest_cycle.model_dump() if latest_cycle else None,
                }
                yield f"event: snapshot\ndata: {json.dumps(snapshot, default=str)}\n\n"
                if once:
                    return

                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        evt = await asyncio.to_thread(q.get, True, 5.0)
                        evt_name = str(evt.get("type") or "message")
                        yield f"event: {evt_name}\ndata: {json.dumps(evt, default=str)}\n\n"
                    except Exception:
                        yield ": keepalive\n\n"
            finally:
                bus.unsubscribe(q)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @router.get("/collection/history")
    def get_collection_history(limit: int = Query(default=30, ge=1, le=200)) -> dict[str, Any]:
        cycles = app_state.ref_db.list_cycles(limit=limit)
        downloads = app_state.ref_db.list_download_history(limit=150)
        return {
            "cycles": [c.model_dump() for c in cycles],
            "download_history": downloads,
        }

    @router.get("/collection/history/{cycle_id}")
    def get_collection_cycle_report(cycle_id: str) -> dict[str, Any]:
        cycle = app_state.ref_db.get_cycle(cycle_id)
        if not cycle:
            raise HTTPException(status_code=404, detail=f"Collection cycle '{cycle_id}' not found.")
        queue_items = app_state.ref_db.get_queue_for_cycle(cycle_id)
        return {
            "cycle": cycle.model_dump(),
            "queue": _serialize_queue_items(app_state, queue_items),
        }

    @router.get("/collection/rules")
    def get_collection_rules() -> dict[str, Any]:
        rules_mgr = _get_rules_manager(app_state)
        return {
            **_rules_bundle_payload(rules_mgr),
            "options": {
                "categories": [
                    "Architecture",
                    "Characters",
                    "Clothing & Accessories",
                    "Furniture",
                    "Landscapes",
                    "Machinery",
                    "Mechanical components",
                    "Plants",
                    "Props and objects",
                    "Vehicles",
                    "Weapons & Armor",
                    "Other",
                ],
                "licenses": ["cc0", "cc_by", "cc_by_sa", "cc_by_nc", "cc_by_nc_sa"],
                "modeling_styles": ["low-poly", "sub-d", "pbr", "stylized", "procedural", "rigged"],
                "poly_complexities": ["any", "low_poly", "mid_poly", "high_poly"],
                "file_formats": ["blend", "fbx", "obj", "glb", "any"],
                "timezones": ["Europe/London", "UTC", "America/New_York", "America/Los_Angeles", "Asia/Kuala_Lumpur", "Asia/Tokyo"],
            },
        }

    @router.put("/collection/rules")
    def update_collection_rules(body: CollectionRulesUpdateRequest) -> dict[str, Any]:
        rules_mgr = _get_rules_manager(app_state)
        try:
            rules = CollectionRulesConfig(
                daily_target=body.daily_target,
                preferred_categories=body.preferred_categories,
                excluded_categories=body.excluded_categories,
                preferred_licenses=body.preferred_licenses,
                min_downloads=body.min_downloads,
                preferred_modeling_styles=body.preferred_modeling_styles,
                preferred_poly_complexity=body.preferred_poly_complexity,
                preferred_file_format=body.preferred_file_format,
                diversity_enabled=body.diversity_enabled,
                max_per_category_per_cycle=body.max_per_category_per_cycle,
                collection_time=body.collection_time,
                timezone=body.timezone,
                excluded_candidate_ids=body.excluded_candidate_ids,
                scope=body.scope,
            )
            bundle = rules_mgr.save_rules(rules, scope=body.scope)
            if body.scope == "permanent":
                perm = bundle["permanent"]
                app_state.ref_scheduler.update_schedule_config(
                    collection_time=perm["collection_time"],
                    timezone_name=perm["timezone"],
                    target_downloads=int(perm["daily_target"]),
                    free_downloads_only=True,
                    automatic_credit_spending=False,
                )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        payload = _rules_bundle_payload(rules_mgr)
        return {
            "saved_scope": body.scope,
            "rules": payload["active_rules"],
            **payload,
        }

    @router.post("/collection/rules/restore-defaults")
    def restore_collection_default_rules(
        _body: RestoreDefaultsRequest = Body(default_factory=RestoreDefaultsRequest),
    ) -> dict[str, Any]:
        rules_mgr = _get_rules_manager(app_state)
        bundle = rules_mgr.restore_defaults()
        perm = bundle["permanent"]
        app_state.ref_scheduler.update_schedule_config(
            collection_time=perm["collection_time"],
            timezone_name=perm["timezone"],
            target_downloads=int(perm["daily_target"]),
            free_downloads_only=True,
            automatic_credit_spending=False,
        )
        payload = _rules_bundle_payload(rules_mgr)
        return {
            "restored": True,
            "rules": payload["active_rules"],
            **payload,
        }

    @router.post("/collection/preview")
    def preview_collection_candidates(
        rules: Optional[CollectionRulesConfig] = Body(default=None),
    ) -> dict[str, Any]:
        try:
            raw = app_state.ref_collector.preview_candidates(rules_override=rules)
            selected = [
                {
                    **c,
                    "blendswap_id": str(c["blendswap_id"]),
                    "title": c.get("name") or c.get("title") or "",
                    "primary_category": c.get("category") or c.get("primary_category") or "Other",
                    "primary_format": c.get("file_format") or c.get("primary_format") or "blend",
                    "poly_complexity": c.get("modeling_style") or "",
                    "reason": c.get("selection_reason") or "",
                }
                for c in raw.get("candidates", [])
            ]
            return {
                **raw,
                "total_candidates": raw.get("evaluated_count", len(selected)),
                "selected_count": len(selected),
                "selected": selected,
            }
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Candidate preview failed: {exc}") from exc

    @router.post("/collection/run")
    def trigger_manual_collection(
        body: CollectionRunRequest = Body(default_factory=CollectionRunRequest),
    ) -> dict[str, Any]:
        if app_state.ref_collector.is_running():
            raise HTTPException(status_code=409, detail="A collection cycle is already running.")

        rules_mgr = _get_rules_manager(app_state)
        active_rules, _ = rules_mgr.get_active_rules()
        effective_target = body.max_downloads if body.max_downloads is not None else active_rules.daily_target

        executor = _get_executor(app_state)
        executor.submit(
            app_state.ref_scheduler.check_and_run_due,
            force=True,
            trigger_type="manual",
            max_downloads=effective_target,
            sync_to_drive=body.sync_to_drive,
        )
        return {
            "status": "STARTED",
            "target_downloads": effective_target,
            "free_downloads_only": True,
            "allow_credit_spend": False,
            "control": _control_payload(app_state),
        }

    @router.post("/collection/pause")
    def pause_collection() -> dict[str, Any]:
        sched = app_state.ref_scheduler.pause()
        app_state.ref_collector.pause_active_cycle()
        return {
            "status": "PAUSED",
            "scheduler": sched,
            "control": _control_payload(app_state),
        }

    @router.post("/collection/resume")
    def resume_collection() -> dict[str, Any]:
        sched = app_state.ref_scheduler.resume()
        app_state.ref_collector.resume_active_cycle()
        return {
            "status": "RESUMED",
            "scheduler": sched,
            "control": _control_payload(app_state),
        }

    @router.post("/collection/cancel")
    def cancel_collection() -> dict[str, Any]:
        app_state.ref_collector.cancel_active_cycle()
        return {
            "status": "CANCEL_REQUESTED",
            "control": _control_payload(app_state),
        }

    @router.post("/collection/retry-failed")
    def retry_failed_collection(
        body: CollectionRetryFailedRequest = Body(default_factory=CollectionRetryFailedRequest),
    ) -> dict[str, Any]:
        if app_state.ref_collector.is_running():
            raise HTTPException(status_code=409, detail="Cannot retry failed downloads while another cycle is running.")
        executor = _get_executor(app_state)
        executor.submit(
            app_state.ref_collector.retry_failed_items,
            cycle_id=body.cycle_id,
            sync_to_drive=body.sync_to_drive,
        )
        return {
            "status": "RETRY_STARTED",
            "cycle_id": body.cycle_id,
            "control": _control_payload(app_state),
        }

    # ------------------------------------------------------------------
    # Quality Intelligence
    # ------------------------------------------------------------------
    @router.get("/quality/reports")
    def get_quality_intelligence() -> dict[str, Any]:
        vault_assets = app_state.db.list_assets()
        ref_assets = app_state.ref_db.list_reference_assets(include_details=True)
        benchmarks = app_state.ref_db.list_benchmark_reports(limit=100)
        roblox_assets = app_state.roblox_workflow.assets.list_assets(limit=500)

        bench_by_asset: dict[str, list[Any]] = {}
        for b in benchmarks:
            bench_by_asset.setdefault(b.branch1_asset_id, []).append(b)

        branch1_rows: list[dict[str, Any]] = []
        for a in vault_assets:
            latest = a.latest
            latest_bench = bench_by_asset.get(a.asset_id, [None])[0]
            tech = latest.technical_qa if latest else None
            vis = latest.visual_qa if latest else None
            score = (
                latest_bench.local_grade.total_score
                if (latest_bench and latest_bench.local_grade)
                else (vis.score if vis and vis.score is not None else None)
            )
            grade = (
                latest_bench.local_grade.grade_letter
                if (latest_bench and latest_bench.local_grade)
                else _score_to_grade_letter(score)
            )
            branch1_rows.append(
                {
                    "asset_id": a.asset_id,
                    "name": a.name,
                    "category": a.category,
                    "status": latest.status if latest else "PROCESSING",
                    "roblox_asset_id": a.roblox_asset_id,
                    "poly_count": latest.poly_count if latest else 0,
                    "tech_qa_status": tech.status if tech else "PENDING",
                    "visual_qa_status": vis.status if vis else "PENDING",
                    "visual_score": vis.score if vis else None,
                    "benchmark_score": latest_bench.local_grade.total_score if (latest_bench and latest_bench.local_grade) else None,
                    "grade": grade,
                    "preview_url": _extract_preview_url(a),
                    "updated_at": a.updated_at,
                }
            )

        roblox_rows: list[dict[str, Any]] = []
        for ra in roblox_assets:
            rep = app_state.roblox_workflow.qa_reports.get_latest_report_for_asset(ra.asset_id)
            roblox_rows.append(
                {
                    "asset_id": ra.asset_id,
                    "legacy_vault_id": ra.legacy_vault_id,
                    "asset_name": ra.asset_name,
                    "visual_style": ra.visual_style.value if hasattr(ra.visual_style, "value") else str(ra.visual_style),
                    "production_status": ra.production_status.value if hasattr(ra.production_status, "value") else str(ra.production_status),
                    "qa_score": ra.qa_score,
                    "qa_status": ra.qa_status,
                    "qa_grade": ra.qa_grade,
                    "triangle_count": ra.triangle_count,
                    "human_approved": ra.human_approved,
                    "report_id": rep.report_id if rep else None,
                    "has_html_report": bool(rep and rep.report_html_path and Path(rep.report_html_path).is_file()),
                    "has_json_report": bool(rep and rep.report_json_path and Path(rep.report_json_path).is_file()),
                    "dimension_scores": (
                        {
                            "mesh_integrity": rep.mesh_integrity_score,
                            "geometry": rep.geometry_score,
                            "polygon_efficiency": rep.polygon_efficiency_score,
                            "uv": rep.uv_score,
                            "material": rep.material_score,
                            "scale": rep.scale_score,
                            "roblox_compatibility": rep.roblox_compatibility_score,
                            "collision": rep.collision_score,
                            "visual": rep.visual_score,
                            "organisation": rep.organisation_score,
                        }
                        if rep
                        else None
                    ),
                    "updated_at": ra.updated_at,
                }
            )

        # Category comparisons: Generated vs Reference
        cat_stats: dict[str, dict[str, Any]] = {}

        def _ensure_cat(cat_name: str) -> dict[str, Any]:
            if cat_name not in cat_stats:
                cat_stats[cat_name] = {
                    "category": cat_name,
                    "generated_count": 0,
                    "generated_scores": [],
                    "generated_tris": [],
                    "reference_count": 0,
                    "reference_scores": [],
                    "reference_tris": [],
                }
            return cat_stats[cat_name]

        for row in branch1_rows:
            c = _ensure_cat(row["category"] or "Other")
            c["generated_count"] += 1
            if row["benchmark_score"] is not None:
                c["generated_scores"].append(float(row["benchmark_score"]))
            elif row["visual_score"] is not None:
                c["generated_scores"].append(float(row["visual_score"]))
            if row["poly_count"]:
                c["generated_tris"].append(int(row["poly_count"]))

        for r in ref_assets:
            c = _ensure_cat(r.primary_category or "Other")
            c["reference_count"] += 1
            if r.technical_quality_score is not None:
                c["reference_scores"].append(float(r.technical_quality_score))
            if r.inspection and r.inspection.triangle_count:
                c["reference_tris"].append(int(r.inspection.triangle_count))

        category_comparisons = []
        for cat_name, raw in sorted(cat_stats.items()):
            gs = raw["generated_scores"]
            rs = raw["reference_scores"]
            gt = raw["generated_tris"]
            rt = raw["reference_tris"]
            category_comparisons.append(
                {
                    "category": cat_name,
                    "generated_count": raw["generated_count"],
                    "reference_count": raw["reference_count"],
                    "generated_avg_score": round(sum(gs) / len(gs), 1) if gs else None,
                    "reference_avg_score": round(sum(rs) / len(rs), 1) if rs else None,
                    "generated_avg_tris": int(sum(gt) / len(gt)) if gt else None,
                    "reference_avg_tris": int(sum(rt) / len(rt)) if rt else None,
                }
            )

        gen_grades: dict[str, int] = {"A": 0, "B": 0, "C": 0, "D": 0, "F": 0}
        for row in branch1_rows:
            g = row["grade"]
            if g in gen_grades:
                gen_grades[g] += 1
        for rrow in roblox_rows:
            g = (rrow.get("qa_grade") or "").upper()
            if g in gen_grades:
                gen_grades[g] += 1

        ref_grades: dict[str, int] = {"A": 0, "B": 0, "C": 0, "D": 0, "F": 0}
        for r in ref_assets:
            g = (r.grade_letter or "").upper()
            if g in ref_grades:
                ref_grades[g] += 1

        return {
            "branch1_assets": branch1_rows,
            "roblox_reports": roblox_rows,
            "benchmarks": [b.model_dump() for b in benchmarks],
            "reference_assets": [
                {
                    "blendswap_id": r.blendswap_id,
                    "title": r.title,
                    "category": r.primary_category,
                    "author": r.author.username,
                    "grade_letter": r.grade_letter,
                    "technical_quality_score": r.technical_quality_score,
                    "usefulness_score": r.reference_usefulness_score,
                    "triangle_count": r.inspection.triangle_count if r.inspection else 0,
                    "quad_ratio": r.inspection.quad_ratio if r.inspection else 0.0,
                    "uv_coverage_ratio": r.inspection.uv_coverage_ratio if r.inspection else 0.0,
                }
                for r in ref_assets
            ],
            "category_comparisons": category_comparisons,
            "grade_distribution": {
                "generated": gen_grades,
                "reference": ref_grades,
            },
        }

    return router
