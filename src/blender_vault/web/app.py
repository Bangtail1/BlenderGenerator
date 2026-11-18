"""FastAPI web application and REST API for Blender Asset Vault."""

from __future__ import annotations

import logging
import shutil
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..blender_worker.runner import BlenderWorkerRunner
from ..config import STANDARD_CATEGORIES, Settings
from ..db import VaultDatabase
from ..logging_config import configure_logging
from ..models import (
    AssetRecord,
    AssetStatus,
    GenerateAssetRequest,
    GeometryProfile,
    HumanReviewRequest,
    ProvenanceType,
)
from ..reference.benchmark_engine import BenchmarkEngine
from ..reference.blendswap_client import BlendSwapClient
from ..reference.collector import CollectionCycleService
from ..reference.dashboard_service import ReferenceDashboardService
from ..reference.grading_engine import QualityGradingEngine
from ..reference.inspector_service import ReferenceInspectorService
from ..reference.models import ReferenceAssetStatus
from ..reference.ref_db import ReferenceDatabase
from ..reference.reference_drive_sync import ReferenceDriveSyncService
from ..reference.reference_storage import ReferenceStorage
from ..reference.router import build_reference_router
from ..reference.scheduler import DailyCollectionScheduler
from ..services.desktop import launch_in_blender_gui
from ..services.gdrive_service import (
    DriveIntegrityError,
    DriveNotAuthenticatedError,
    DriveNotConfiguredError,
    GoogleDriveSyncService,
)
from ..services.pipeline import (
    SUPPORTED_IMPORT_EXTENSIONS,
    AssetPipelineService,
    DuplicateAssetError,
    UnsupportedModelFormatError,
)
from ..services.storage import VaultStorage

logger = logging.getLogger("blender_vault.web")

STATIC_DIR = Path(__file__).resolve().parent / "static"


class SelectFolderBody(BaseModel):
    folder_id: str
    folder_name: Optional[str] = None


def create_app(
    *,
    settings: Optional[Settings] = None,
    gdrive_service_factory: Any = None,
    start_scheduler: bool = True,
) -> FastAPI:
    cfg = settings or Settings()
    cfg.ensure_directories()
    configure_logging(cfg.logs_dir, cfg.vault_log_level)

    db = VaultDatabase(cfg.db_path)
    storage = VaultStorage(cfg.assets_dir, cfg.imports_dir)
    runner = BlenderWorkerRunner(cfg.blender_executable, cfg.blender_timeout_seconds)
    gdrive = GoogleDriveSyncService(cfg, db, storage, service_factory=gdrive_service_factory)

    # Branch 2: Reference Intelligence Engine services (shared DB & Drive auth, strictly separate tables & storage)
    ref_db = ReferenceDatabase(db)
    ref_storage = ReferenceStorage(cfg.resolved_reference_library_dir)
    bs_client = BlendSwapClient(cfg, ref_db)
    ref_inspector = ReferenceInspectorService(cfg, runner)
    ref_grader = QualityGradingEngine()
    ref_drive = ReferenceDriveSyncService(gdrive, ref_db, ref_storage)
    ref_collector = CollectionCycleService(cfg, ref_db, ref_storage, bs_client, ref_inspector, ref_grader, ref_drive)
    ref_scheduler = DailyCollectionScheduler(cfg, db, ref_collector)
    ref_dashboard = ReferenceDashboardService(cfg, ref_db, bs_client, ref_scheduler)
    benchmark_engine = BenchmarkEngine(db, ref_db, ref_storage, ref_inspector, ref_grader, ref_drive)

    def _auto_benchmark_if_ready(asset_id: str, version_num: int) -> None:
        if ref_db.list_reference_assets(status=ReferenceAssetStatus.GRADED.value, limit=1, include_details=False):
            benchmark_engine.benchmark_branch1_asset(asset_id, version_num=version_num, sync_to_drive=False)

    pipeline = AssetPipelineService(cfg, db, storage, runner, gdrive, benchmark_hook=_auto_benchmark_if_ready)
    executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="vault-worker")

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if start_scheduler and cfg.collection_scheduler_enabled:
            ref_scheduler.start()
        try:
            yield
        finally:
            ref_scheduler.stop()
            bs_client.close()
            executor.shutdown(wait=False)

    app = FastAPI(title="Blender Asset Vault", version="0.2.0", lifespan=lifespan)
    app.state.settings = cfg
    app.state.db = db
    app.state.storage = storage
    app.state.runner = runner
    app.state.gdrive = gdrive
    app.state.pipeline = pipeline
    app.state.ref_db = ref_db
    app.state.ref_storage = ref_storage
    app.state.bs_client = bs_client
    app.state.ref_inspector = ref_inspector
    app.state.ref_grader = ref_grader
    app.state.ref_drive = ref_drive
    app.state.ref_collector = ref_collector
    app.state.ref_scheduler = ref_scheduler
    app.state.ref_dashboard = ref_dashboard
    app.state.benchmark_engine = benchmark_engine

    app.include_router(build_reference_router(app.state))

    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index_page() -> HTMLResponse:
        idx = STATIC_DIR / "index.html"
        if not idx.is_file():
            return HTMLResponse("<h1>Blender Asset Vault UI missing</h1>", status_code=500)
        return HTMLResponse(idx.read_text(encoding="utf-8"))

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        assets = db.list_assets()
        status_counts: dict[str, int] = {}
        for a in assets:
            st = a.latest.status.value if a.latest else "UNKNOWN"
            status_counts[st] = status_counts.get(st, 0) + 1

        return {
            "status": "ok",
            "blender": runner.check_health(),
            "gdrive": gdrive.get_auth_status(),
            "vision_provider": cfg.vision_qa_provider,
            "vision_configured": (
                (cfg.vision_qa_provider == "gemini" and bool(cfg.gemini_api_key.strip()))
                or (cfg.vision_qa_provider == "openai" and bool(cfg.openai_api_key.strip()))
                or (cfg.vision_qa_provider == "ollama")
            ),
            "categories": list(STANDARD_CATEGORIES),
            "geometry_profiles": [p.value for p in GeometryProfile],
            "supported_import_formats": sorted(SUPPORTED_IMPORT_EXTENSIONS),
            "procedural_templates": [
                {"id": "wooden_chair", "label": "Wooden Chair (4 legs, curved backrest, wood shader)", "category": "Furniture"},
                {"id": "wooden_table", "label": "Wooden Table (beveled top, 4 legs)", "category": "Furniture"},
                {"id": "mechanical_gear", "label": "Mechanical Spur Gear (watertight solid)", "category": "Mechanical Objects"},
                {"id": "ceramic_vase", "label": "Ceramic Vase (glazed revolved vessel)", "category": "Household Objects"},
                {"id": "stylized_leaf", "label": "Botanical Leaf (open-surface organic mesh)", "category": "Nature"},
                {"id": "test_cube", "label": "Calibrated Test Cube", "category": "Miscellaneous"},
                {"id": "test_uv_sphere", "label": "Calibrated UV Sphere", "category": "Miscellaneous"},
            ],
            "counts": {"total_assets": len(assets), "by_status": status_counts},
            "reference_engine": {
                "blendswap_configured": bool((cfg.blendswap_key or "").strip()),
                "reference_count": len(ref_db.list_reference_assets(include_details=False)),
                "scheduler": ref_scheduler.get_schedule_config(),
            },
        }

    # ------------------------------------------------------------------
    # Assets Catalogue & Details
    # ------------------------------------------------------------------
    @app.get("/api/assets")
    def list_assets(
        search: Optional[str] = Query(default=None),
        category: Optional[str] = Query(default=None),
        status: Optional[str] = Query(default=None),
    ) -> dict[str, Any]:
        items = db.list_assets(search=search, category=category, status=status)
        return {"assets": [a.model_dump() for a in items], "total": len(items)}

    @app.get("/api/assets/{asset_id}")
    def get_asset(asset_id: str) -> dict[str, Any]:
        asset = db.get_asset(asset_id)
        if not asset:
            raise HTTPException(status_code=404, detail=f"Asset '{asset_id}' not found.")
        return asset.model_dump()

    # ------------------------------------------------------------------
    # Generate & Import
    # ------------------------------------------------------------------
    @app.post("/api/assets/generate")
    def generate_asset(req: GenerateAssetRequest) -> dict[str, Any]:
        try:
            asset = pipeline.generate_procedural_asset(req)
            return asset.model_dump()
        except Exception as exc:
            logger.exception("Generate endpoint error")
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/assets/import")
    async def import_asset(
        file: UploadFile = File(...),
        name: Optional[str] = Form(default=None),
        description: str = Form(default=""),
        category: str = Form(default="Miscellaneous"),
        geometry_profile: Optional[str] = Form(default=None),
        tags: str = Form(default=""),
        provenance_type: str = Form(default=ProvenanceType.USER_UPLOADED.value),
        author: Optional[str] = Form(default=None),
        source_url: Optional[str] = Form(default=None),
        license_name: Optional[str] = Form(default=None),
        usage_restrictions: Optional[str] = Form(default=None),
        existing_asset_id: Optional[str] = Form(default=None),
        allow_duplicate: bool = Form(default=False),
    ) -> dict[str, Any]:
        filename = file.filename or "upload.bin"
        ext = Path(filename).suffix.lower()
        if ext not in SUPPORTED_IMPORT_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported 3D format '{ext or '(none)'}'. Supported: {', '.join(sorted(SUPPORTED_IMPORT_EXTENSIONS))}.",
            )

        tmp_upload = (cfg.tmp_dir / f"upload_{uuid.uuid4().hex}_{Path(filename).name}").resolve()
        tmp_upload.parent.mkdir(parents=True, exist_ok=True)
        try:
            with tmp_upload.open("wb") as out:
                shutil.copyfileobj(file.file, out)

            parsed_profile = GeometryProfile(geometry_profile) if geometry_profile else None
            parsed_prov = ProvenanceType(provenance_type)
            parsed_tags = [t.strip() for t in tags.split(",") if t.strip()]

            asset = pipeline.import_model_file(
                temp_upload_path=tmp_upload,
                original_filename=filename,
                name=name,
                description=description,
                category=category,
                geometry_profile=parsed_profile,
                tags=parsed_tags,
                provenance_type=parsed_prov,
                author=author,
                source_url=source_url,
                license_name=license_name,
                usage_restrictions=usage_restrictions,
                existing_asset_id=existing_asset_id or None,
                allow_duplicate=allow_duplicate,
            )
            return asset.model_dump()
        except DuplicateAssetError as exc:
            raise HTTPException(
                status_code=409,
                detail={"message": str(exc), "existing_asset_id": exc.existing_asset_id, "existing_version": exc.existing_version},
            ) from exc
        except UnsupportedModelFormatError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("Import endpoint error")
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        finally:
            tmp_upload.unlink(missing_ok=True)

    # ------------------------------------------------------------------
    # Stage 2 Human Review, Retry, Open in Blender, Downloads
    # ------------------------------------------------------------------
    @app.post("/api/assets/{asset_id}/versions/{version}/review")
    def submit_review(asset_id: str, version: int, req: HumanReviewRequest) -> dict[str, Any]:
        try:
            asset = pipeline.submit_human_review(asset_id, version, req)
            return asset.model_dump()
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/assets/{asset_id}/versions/{version}/retry")
    def retry_version(asset_id: str, version: int) -> dict[str, Any]:
        try:
            asset = pipeline.retry_asset_version(asset_id, version)
            return asset.model_dump()
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/api/assets/{asset_id}/versions/{version}/open-blender")
    def open_in_blender(asset_id: str, version: int) -> dict[str, Any]:
        v = db.get_version(asset_id, version)
        if not v or not v.blend_path:
            raise HTTPException(status_code=404, detail="Blend file not found for asset version.")
        try:
            pid = launch_in_blender_gui(Path(v.blend_path), cfg.blender_executable)
            return {"ok": True, "pid": pid, "blend_path": v.blend_path}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.get("/api/assets/{asset_id}/versions/{version}/preview/{filename}")
    def get_preview_image(asset_id: str, version: int, filename: str) -> FileResponse:
        safe_name = Path(filename).name
        previews_dir = storage.previews_dir(asset_id, version).resolve()
        target = (previews_dir / safe_name).resolve()
        if not target.is_file() or target.parent != previews_dir:
            raise HTTPException(status_code=404, detail=f"Preview file '{safe_name}' not found.")
        return FileResponse(path=str(target))

    @app.get("/api/assets/{asset_id}/versions/{version}/download/{kind}")
    def download_asset_file(asset_id: str, version: int, kind: str) -> FileResponse:
        asset = db.get_asset(asset_id)
        v = db.get_version(asset_id, version)
        if not asset or not v:
            raise HTTPException(status_code=404, detail="Asset version not found.")

        vdir = storage.version_dir(asset_id, version)
        mapping = {
            "blend": Path(v.blend_path) if v.blend_path else vdir / f"{asset.slug}.blend",
            "glb": Path(v.glb_path) if v.glb_path else vdir / f"{asset.slug}.glb",
            "original": Path(v.source_original_path) if v.source_original_path else None,
            "manifest": vdir / "manifest.json",
            "qa_report": vdir / "qa_report.json",
            "visual_qa": vdir / "visual_qa.json",
        }
        if kind not in mapping:
            raise HTTPException(status_code=400, detail=f"Invalid download kind '{kind}'.")
        target = mapping[kind]
        if target is None:
            raise HTTPException(status_code=404, detail=f"File '{kind}' not available for this asset version.")

        # If local file missing but uploaded on Drive, retrieve from Drive automatically!
        if not target.is_file():
            df = next((f for f in v.drive_files if f.role == kind and f.remote_file_id), None)
            if df and df.remote_file_id:
                gdrive.download_file(
                    df.remote_file_id,
                    target,
                    expected_sha256=df.local_sha256,
                    expected_md5=df.local_md5,
                )

        if not target.is_file():
            raise HTTPException(status_code=404, detail=f"File '{kind}' not available locally or on Drive.")
        return FileResponse(path=str(target), filename=target.name)

    # ------------------------------------------------------------------
    # Google Drive OAuth, Folder Picker, Upload, Verify, Reindex
    # ------------------------------------------------------------------
    @app.get("/api/gdrive/status")
    def gdrive_status() -> dict[str, Any]:
        return gdrive.get_auth_status()

    @app.get("/api/gdrive/auth-url")
    def gdrive_auth_url(request: Request) -> dict[str, str]:
        redirect_uri = str(request.url_for("gdrive_oauth_callback"))
        try:
            url = gdrive.build_web_auth_url(redirect_uri)
            return {"auth_url": url, "redirect_uri": redirect_uri}
        except DriveNotConfiguredError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/gdrive/oauth-callback", name="gdrive_oauth_callback")
    def gdrive_oauth_callback(request: Request, state: str = "", code: str = "", error: str = "") -> RedirectResponse:
        if error:
            return RedirectResponse(url=f"/?gdrive_error={error}")
        if not state or not code:
            return RedirectResponse(url="/?gdrive_error=missing_code_or_state")
        try:
            # Allow http://127.0.0.1 loopback callback for InstalledAppFlow
            import os
            os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
            gdrive.complete_web_auth_callback(state, str(request.url))
            return RedirectResponse(url="/?gdrive_connected=1")
        except Exception as exc:
            return RedirectResponse(url=f"/?gdrive_error={exc}")

    @app.post("/api/gdrive/disconnect")
    def gdrive_disconnect() -> dict[str, bool]:
        gdrive.disconnect()
        return {"disconnected": True}

    @app.get("/api/gdrive/folders")
    def gdrive_list_folders(parent_id: str = Query(default="root")) -> dict[str, Any]:
        try:
            return {"folders": gdrive.list_accessible_folders(parent_id=parent_id)}
        except (DriveNotConfiguredError, DriveNotAuthenticatedError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/gdrive/select-folder")
    def gdrive_select_folder(body: SelectFolderBody) -> dict[str, Any]:
        try:
            selected = gdrive.select_destination_folder(body.folder_id, body.folder_name)
            tree = gdrive.ensure_vault_folder_tree(body.folder_id)
            return {"selected": selected, "vault_tree": tree}
        except (DriveNotConfiguredError, DriveNotAuthenticatedError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/assets/{asset_id}/versions/{version}/upload-drive")
    def upload_drive(asset_id: str, version: int) -> dict[str, Any]:
        try:
            updated_ver = pipeline.upload_to_drive(asset_id, version)
            return {"version": updated_ver.model_dump(), "asset": (db.get_asset(asset_id)).model_dump()}
        except (DriveNotConfiguredError, DriveNotAuthenticatedError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("Drive upload error")
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/api/assets/{asset_id}/versions/{version}/verify-drive")
    def verify_drive(asset_id: str, version: int) -> dict[str, Any]:
        try:
            return pipeline.verify_drive_asset(asset_id, version)
        except (DriveNotConfiguredError, DriveNotAuthenticatedError, DriveIntegrityError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/catalogue/reindex")
    def reindex_catalogue(include_drive: bool = Query(default=False)) -> dict[str, Any]:
        local_res = storage.rebuild_database_from_manifests(db)
        drive_res = None
        if include_drive:
            tmp = (cfg.tmp_dir / f"reindex_{uuid.uuid4().hex[:8]}").resolve()
            tmp.mkdir(parents=True, exist_ok=True)
            try:
                drive_res = gdrive.rebuild_catalogue_from_drive(tmp)
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
        return {"local": local_res, "drive": drive_res}

    @app.get("/api/jobs")
    def list_jobs(asset_id: Optional[str] = Query(default=None), limit: int = Query(default=50)) -> dict[str, Any]:
        return {"jobs": db.list_jobs(asset_id=asset_id, limit=limit)}

    return app
