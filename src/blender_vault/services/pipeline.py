"""End-to-end asset lifecycle orchestrator: generate/import -> technical QA -> visual QA -> human review -> Drive sync."""

from __future__ import annotations

import logging
import shutil
import uuid
from pathlib import Path
from typing import Any, Optional

from ..blender.export_manager import RobloxExportManager
from ..blender_worker.runner import BlenderWorkerError, BlenderWorkerRunner
from ..config import STANDARD_CATEGORIES, Settings
from ..database import ExportFormat, ProductionStatus, RobloxAssetRegistrar
from ..db import VaultDatabase
from ..hashing import compute_sha256, slugify_name
from ..models import (
    CATEGORY_TO_DEFAULT_PROFILE,
    AssetRecord,
    AssetStatus,
    AssetVersion,
    DriveSyncStatus,
    GenerateAssetRequest,
    GeometryProfile,
    HumanApprovalStatus,
    HumanReviewRecord,
    HumanReviewRequest,
    ProvenanceInfo,
    ProvenanceType,
    TechnicalQAStatus,
    VisualQAStatus,
    utc_now_iso,
)
from ..roblox_qa import RobloxQAEngine, RobloxWorkflowService
from ..storage.sync_manager import RobloxDriveSyncManager
from .gdrive_service import GoogleDriveSyncService
from .qa_rules import evaluate_technical_qa
from .storage import VaultStorage
from .vision_qa import (
    assemble_turntable_webp,
    build_contact_sheet,
    build_vision_provider,
    verify_render_sanity,
)

logger = logging.getLogger("blender_vault.pipeline")

SUPPORTED_IMPORT_EXTENSIONS = {".blend", ".obj", ".fbx", ".stl", ".glb", ".gltf"}


class UnsupportedModelFormatError(ValueError):
    """Raised when an imported file format is not supported."""


class DuplicateAssetError(ValueError):
    """Raised when an identical model already exists in the catalogue."""

    def __init__(self, message: str, existing_asset_id: str, existing_version: int) -> None:
        super().__init__(message)
        self.existing_asset_id = existing_asset_id
        self.existing_version = existing_version


def infer_category_from_prompt(prompt: str, template_id: Optional[str] = None) -> str:
    text = f"{prompt} {template_id or ''}".lower()
    if any(k in text for k in ("chair", "table", "desk", "sofa", "bench", "stool", "cabinet", "shelf")):
        return "Furniture"
    if any(k in text for k in ("gear", "cog", "bolt", "valve", "piston", "engine", "mechanical", "bracket")):
        return "Mechanical Objects"
    if any(k in text for k in ("vase", "cup", "mug", "lamp", "bowl", "bottle", "pot")):
        return "Household Objects"
    if any(k in text for k in ("leaf", "tree", "plant", "rock", "flower", "foliage", "branch")):
        return "Nature"
    if any(k in text for k in ("wall", "door", "window", "pillar", "arch", "building", "house", "stairs")):
        return "Architecture"
    if any(k in text for k in ("car", "truck", "bike", "ship", "plane", "vehicle", "wheel")):
        return "Vehicles"
    if any(k in text for k in ("character", "human", "person", "robot", "creature", "figure")):
        return "Characters"
    return "Miscellaneous"


class AssetPipelineService:
    """Coordinates generation, import, technical QA, visual rendering/QA, human approval, and Drive sync."""

    def __init__(
        self,
        settings: Settings,
        db: VaultDatabase,
        storage: VaultStorage,
        runner: BlenderWorkerRunner,
        gdrive: GoogleDriveSyncService,
        *,
        benchmark_hook: Optional[Any] = None,
        roblox_registrar: Optional[RobloxAssetRegistrar] = None,
        roblox_sync_manager: Optional[RobloxDriveSyncManager] = None,
        roblox_qa_hook: Optional[Any] = None,
    ) -> None:
        self.settings = settings
        self.db = db
        self.storage = storage
        self.runner = runner
        self.gdrive = gdrive
        self.benchmark_hook = benchmark_hook
        self.export_manager = RobloxExportManager(runner)
        self.roblox_qa_hook = roblox_qa_hook
        if roblox_registrar is not None:
            self.roblox_registrar = roblox_registrar
        elif settings.roblox_auto_register:
            self.roblox_registrar = RobloxAssetRegistrar(settings)
        else:
            self.roblox_registrar = None
        if roblox_sync_manager is not None:
            self.roblox_sync = roblox_sync_manager
        elif self.roblox_registrar is not None:
            self.roblox_sync = RobloxDriveSyncManager(gdrive, self.roblox_registrar.db, db)
        else:
            self.roblox_sync = None

        if self.roblox_registrar is not None:
            self.roblox_workflow: Optional[RobloxWorkflowService] = RobloxWorkflowService(
                settings,
                roblox_db=self.roblox_registrar.db,
                qa_engine=RobloxQAEngine(settings, runner=runner),
                sync_manager=self.roblox_sync,
            )
            if self.roblox_qa_hook is None and settings.roblox_auto_qa:
                self.roblox_qa_hook = self._default_roblox_qa_hook
        else:
            self.roblox_workflow = None

    # ------------------------------------------------------------------
    # Procedural Asset Generation
    # ------------------------------------------------------------------
    def generate_procedural_asset(self, req: GenerateAssetRequest) -> AssetRecord:
        category = req.category if req.category in STANDARD_CATEGORIES else infer_category_from_prompt(req.prompt, req.template_id)
        profile = req.geometry_profile or CATEGORY_TO_DEFAULT_PROFILE.get(category, GeometryProfile.GENERAL)

        raw_name = (req.name or "").strip()
        if not raw_name:
            # Derive clean short title from prompt
            words = [w for w in req.prompt.strip().split() if w.isalnum()]
            raw_name = " ".join(words[:5]).title() if words else "Procedural Asset"

        asset_id = uuid.uuid4().hex[:12]
        slug = slugify_name(raw_name)
        now = utc_now_iso()
        tags = sorted({t.strip().lower() for t in req.tags if t.strip()} | {category.lower(), "procedural"})

        provenance = ProvenanceInfo.for_generated_original(req.template_id or "prompt_resolver")
        version = AssetVersion(
            asset_id=asset_id,
            version=1,
            status=AssetStatus.PROCESSING,
            created_at=now,
            updated_at=now,
        )
        asset = AssetRecord(
            asset_id=asset_id,
            slug=slug,
            name=raw_name,
            description=req.prompt.strip(),
            category=category,
            geometry_profile=profile,
            tags=tags,
            provenance=provenance,
            current_version=1,
            created_at=now,
            updated_at=now,
        )
        self.db.create_asset_with_version(asset, version)

        job_id = uuid.uuid4().hex[:10]
        work_dir = (self.settings.tmp_dir / f"job_{job_id}").resolve()
        work_dir.mkdir(parents=True, exist_ok=True)
        self.db.record_job(
            job_id=job_id,
            asset_id=asset_id,
            version=1,
            job_type="generate_procedural",
            status="RUNNING",
            stage="generation",
        )

        vdir = self.storage.version_dir(asset_id, 1).resolve()
        target_blend = vdir / f"{slug}.blend"
        target_glb = vdir / f"{slug}.glb"

        roblox_rec = None
        desired_export_fmt = (req.export_format or self.settings.roblox_default_export_format or "FBX").upper()
        if desired_export_fmt not in {"FBX", "GLB", "OBJ"}:
            desired_export_fmt = "GLB"

        if self.roblox_registrar is not None:
            try:
                roblox_rec = self.roblox_registrar.register_vault_asset(
                    asset,
                    variant_name=req.variant_name,
                    visual_style=req.visual_style,
                    department_code=req.department_code,
                    genre_codes=req.genre_codes,
                    production_status=ProductionStatus.MODELLING,
                )
            except Exception as exc:
                logger.warning("Roblox pre-generation registration failed for %s: %s", asset_id, exc)

        try:
            gen_res = self.runner.run_script(
                "generate.py",
                {
                    "asset_name": raw_name,
                    "prompt": req.prompt,
                    "template_id": req.template_id,
                    "params": req.params,
                    "output_blend": str(target_blend),
                    "output_glb": str(target_glb),
                },
                work_dir / "generate",
            )
            gen_meta = gen_res.get("generation", {})
            if gen_meta.get("template_id"):
                asset.provenance.notes = f"Template: {gen_meta['template_id']}"
                self.db.update_asset_metadata(asset_id, provenance=asset.provenance)

            version.blender_version = str(gen_res.get("blender_version", ""))
            version.blend_path = str(target_blend)
            version.glb_path = str(target_glb) if target_glb.is_file() else None
            version.file_formats = ["blend"] + (["glb"] if version.glb_path else [])
            if target_blend.is_file():
                fsize, sha, md5 = self.storage.compute_file_hashes(target_blend)
                version.file_size_bytes = fsize
                version.sha256 = sha
                version.md5 = md5

            roblox_export_path: Optional[Path] = target_glb if desired_export_fmt == "GLB" else None
            if (
                desired_export_fmt in {"FBX", "OBJ"}
                and target_blend.is_file()
                and int(gen_res.get("mesh_object_count", 0)) > 0
            ):
                candidate_export = vdir / f"{slug}.{desired_export_fmt.lower()}"
                try:
                    self.export_manager.export_and_validate(
                        blend_path=target_blend,
                        output_path=candidate_export,
                        export_format=desired_export_fmt,
                        work_dir=work_dir / "roblox_export",
                        verify_roundtrip=False,
                    )
                    if candidate_export.is_file():
                        roblox_export_path = candidate_export
                        if desired_export_fmt.lower() not in version.file_formats:
                            version.file_formats.append(desired_export_fmt.lower())
                except Exception as exp_exc:
                    logger.warning("Optional Roblox %s export failed for %s: %s", desired_export_fmt, asset_id, exp_exc)

            self.db.update_version(version)

            if roblox_rec is not None and self.roblox_registrar is not None:
                self._update_roblox_generated_files(
                    roblox_rec.asset_id,
                    version_num=1,
                    blend_path=target_blend if target_blend.is_file() else None,
                    export_path=roblox_export_path if (roblox_export_path and roblox_export_path.is_file()) else (target_glb if target_glb.is_file() else None),
                    export_format=desired_export_fmt if (roblox_export_path and roblox_export_path.is_file()) else ("GLB" if target_glb.is_file() else None),
                    sha256=version.sha256,
                )

            self._run_qa_and_visual_stages(asset, version, work_dir, job_id)
            self.db.record_job(job_id=job_id, asset_id=asset_id, version=1, job_type="generate_procedural", status="COMPLETED", stage=version.status.value)

            if roblox_rec is not None and self.roblox_registrar is not None:
                self._sync_roblox_after_qa(roblox_rec.asset_id, asset, version)
                if req.sync_to_drive_pending and version.status != AssetStatus.FAILED and self.roblox_sync is not None:
                    self.roblox_sync.sync_asset_version(
                        roblox_rec.asset_id,
                        1,
                        require_approved_for_production=False,
                        queue_on_failure=True,
                    )
        except Exception as exc:
            logger.exception("Procedural generation pipeline failed for %s", asset_id)
            version.status = AssetStatus.FAILED
            version.failed_stage = version.failed_stage or "generation"
            version.error_message = str(exc)
            self.db.update_version(version)
            self.storage.write_version_artifacts(asset, version)
            self.db.record_job(
                job_id=job_id,
                asset_id=asset_id,
                version=1,
                job_type="generate_procedural",
                status="FAILED",
                stage=version.failed_stage,
                error_message=str(exc),
            )
            if roblox_rec is not None and self.roblox_registrar is not None:
                try:
                    self.roblox_registrar.assets.transition_status(
                        roblox_rec.asset_id,
                        ProductionStatus.QA_FAILED,
                        process_name="generation",
                        message=f"Generation failed: {exc}",
                        error_details=str(exc),
                    )
                except Exception:
                    pass
        finally:
            self._cleanup_work_dir(work_dir)

        final_asset = self.db.get_asset(asset_id) or asset
        if roblox_rec is not None:
            final_asset.roblox_asset_id = roblox_rec.asset_id
        return final_asset

    # ------------------------------------------------------------------
    # Import Existing Model (.blend/.obj/.fbx/.stl/.glb/.gltf)
    # ------------------------------------------------------------------
    def import_model_file(
        self,
        *,
        temp_upload_path: Path,
        original_filename: str,
        name: Optional[str] = None,
        description: str = "",
        category: str = "Miscellaneous",
        geometry_profile: Optional[GeometryProfile] = None,
        tags: Optional[list[str]] = None,
        provenance_type: ProvenanceType = ProvenanceType.USER_UPLOADED,
        author: Optional[str] = None,
        source_url: Optional[str] = None,
        license_name: Optional[str] = None,
        usage_restrictions: Optional[str] = None,
        existing_asset_id: Optional[str] = None,
        allow_duplicate: bool = False,
    ) -> AssetRecord:
        ext = Path(original_filename).suffix.lower()
        if ext not in SUPPORTED_IMPORT_EXTENSIONS:
            raise UnsupportedModelFormatError(
                f"Unsupported file format '{ext or '(none)'}' for '{original_filename}'. "
                f"Supported formats: {', '.join(sorted(SUPPORTED_IMPORT_EXTENSIONS))}."
            )
        if not temp_upload_path.is_file():
            raise FileNotFoundError(f"Uploaded file not found: {temp_upload_path}")

        src_sha256 = compute_sha256(temp_upload_path)
        if not allow_duplicate and not existing_asset_id:
            dup = self.db.find_version_by_sha256(src_sha256)
            if dup:
                ex_asset, ex_ver = dup
                raise DuplicateAssetError(
                    f"Duplicate model detected (matches existing asset '{ex_asset.name}' [{ex_asset.asset_id}] v{ex_ver.version}).",
                    existing_asset_id=ex_asset.asset_id,
                    existing_version=ex_ver.version,
                )

        cat = category if category in STANDARD_CATEGORIES else "Miscellaneous"
        profile = geometry_profile or CATEGORY_TO_DEFAULT_PROFILE.get(cat, GeometryProfile.GENERAL)
        now = utc_now_iso()

        if existing_asset_id:
            asset = self.db.get_asset(existing_asset_id)
            if not asset:
                raise KeyError(f"Asset not found for versioning: {existing_asset_id}")
            new_ver_num = asset.current_version + 1
            version = AssetVersion(
                asset_id=asset.asset_id,
                version=new_ver_num,
                status=AssetStatus.PROCESSING,
                created_at=now,
                updated_at=now,
            )
            self.db.add_version(version)
            asset = self.db.get_asset(existing_asset_id) or asset
        else:
            asset_id = uuid.uuid4().hex[:12]
            clean_name = (name or "").strip() or Path(original_filename).stem.replace("_", " ").replace("-", " ").title()
            slug = slugify_name(clean_name)
            prov = ProvenanceInfo.for_import(
                provenance_type=provenance_type,
                author=author,
                source_url=source_url,
                license_name=license_name,
                usage_restrictions=usage_restrictions,
                notes=f"Original filename: {Path(original_filename).name}; source SHA-256: {src_sha256}",
            )
            clean_tags = sorted({t.strip().lower() for t in (tags or []) if t.strip()} | {cat.lower(), ext.lstrip(".")})
            version = AssetVersion(
                asset_id=asset_id,
                version=1,
                status=AssetStatus.PROCESSING,
                created_at=now,
                updated_at=now,
            )
            asset = AssetRecord(
                asset_id=asset_id,
                slug=slug,
                name=clean_name,
                description=description.strip() or f"Imported 3D model ({ext})",
                category=cat,
                geometry_profile=profile,
                tags=clean_tags,
                provenance=prov,
                current_version=1,
                created_at=now,
                updated_at=now,
            )
            self.db.create_asset_with_version(asset, version)

        # Preserve original untouched import file
        preserved_original = self.storage.preserve_original_import(
            asset.asset_id,
            version.version,
            temp_upload_path,
            original_filename,
        ).resolve()
        version.source_original_path = str(preserved_original)
        self.db.update_version(version)

        job_id = uuid.uuid4().hex[:10]
        work_dir = (self.settings.tmp_dir / f"job_{job_id}").resolve()
        work_dir.mkdir(parents=True, exist_ok=True)
        self.db.record_job(
            job_id=job_id,
            asset_id=asset.asset_id,
            version=version.version,
            job_type="import_model",
            status="RUNNING",
            stage="import",
        )

        vdir = self.storage.version_dir(asset.asset_id, version.version).resolve()
        target_blend = vdir / f"{asset.slug}.blend"
        target_glb = vdir / f"{asset.slug}.glb"

        try:
            imp_res = self.runner.run_script(
                "import_convert.py",
                {
                    "source_path": str(preserved_original),
                    "output_blend": str(target_blend),
                    "output_glb": str(target_glb),
                },
                work_dir / "import",
            )
            version.blender_version = str(imp_res.get("blender_version", ""))
            version.blend_path = str(target_blend)
            version.glb_path = str(target_glb) if target_glb.is_file() else None
            formats = sorted({ext.lstrip("."), "blend"} | ({"glb"} if version.glb_path else set()))
            version.file_formats = formats
            if target_blend.is_file():
                fsize, sha, md5 = self.storage.compute_file_hashes(target_blend)
                version.file_size_bytes = fsize
                version.sha256 = sha if ext == ".blend" else src_sha256
                version.md5 = md5
            self.db.update_version(version)

            self._run_qa_and_visual_stages(asset, version, work_dir, job_id)
            self.db.record_job(
                job_id=job_id,
                asset_id=asset.asset_id,
                version=version.version,
                job_type="import_model",
                status="COMPLETED" if version.status != AssetStatus.FAILED else "FAILED",
                stage=version.status.value,
                error_message=version.error_message,
            )
            if self.roblox_registrar is not None:
                try:
                    existing_rbx = self.roblox_registrar.assets.get_by_legacy_vault_id(asset.asset_id)
                    if existing_rbx and version.version > existing_rbx.current_version:
                        self.roblox_registrar.versions.create_new_version(
                            existing_rbx.asset_id,
                            blender_source_path=version.blend_path,
                            export_format=ExportFormat.GLB if version.glb_path else None,
                            export_path=version.glb_path,
                            change_description=f"Imported version v{version.version} ({original_filename})",
                            sha256=version.sha256,
                            reset_status=ProductionStatus.QA_PENDING,
                        )
                        rbx_rec = self.roblox_registrar.assets.get_asset(existing_rbx.asset_id)
                    else:
                        rbx_rec = self.roblox_registrar.register_vault_asset(asset)
                    if rbx_rec:
                        self._sync_roblox_after_qa(rbx_rec.asset_id, asset, version)
                except Exception as rbx_exc:
                    logger.warning("Roblox import registration failed for %s: %s", asset.asset_id, rbx_exc)
        except Exception as exc:
            logger.exception("Import pipeline failed for %s", asset.asset_id)
            version.status = AssetStatus.FAILED
            version.failed_stage = version.failed_stage or "import"
            version.error_message = str(exc)
            self.db.update_version(version)
            self.storage.write_version_artifacts(asset, version)
            self.db.record_job(
                job_id=job_id,
                asset_id=asset.asset_id,
                version=version.version,
                job_type="import_model",
                status="FAILED",
                stage=version.failed_stage,
                error_message=str(exc),
            )
        finally:
            self._cleanup_work_dir(work_dir)

        final_asset = self.db.get_asset(asset.asset_id) or asset
        if self.roblox_registrar is not None:
            rbx = self.roblox_registrar.assets.get_by_legacy_vault_id(asset.asset_id)
            if rbx:
                final_asset.roblox_asset_id = rbx.asset_id
        return final_asset

    # ------------------------------------------------------------------
    # Technical QA + Multi-Angle Rendering + Stage 1 Visual QA
    # ------------------------------------------------------------------
    def _run_qa_and_visual_stages(self, asset: AssetRecord, version: AssetVersion, work_dir: Path, job_id: str) -> None:
        if not version.blend_path or not Path(version.blend_path).is_file():
            raise RuntimeError("Missing .blend file before QA stage.")

        blend_path = Path(version.blend_path).resolve()

        # Stage 1A: Technical QA Inspection
        version.status = AssetStatus.TECHNICAL_TESTING
        version.failed_stage = None
        version.error_message = None
        self.db.update_version(version)
        self.db.record_job(job_id=job_id, asset_id=asset.asset_id, version=version.version, job_type="qa_pipeline", status="RUNNING", stage="technical_qa")

        roundtrip_glb = (work_dir / "roundtrip_test.glb").resolve()
        try:
            raw_qa = self.runner.run_script(
                "inspect_qa.py",
                {"blend_path": str(blend_path), "roundtrip_glb": str(roundtrip_glb)},
                work_dir / "inspect_qa",
            )
        except BlenderWorkerError as exc:
            version.status = AssetStatus.FAILED
            version.failed_stage = "technical_qa"
            version.error_message = str(exc)
            self.db.update_version(version)
            self.storage.write_version_artifacts(asset, version)
            return

        qa_report = evaluate_technical_qa(raw_qa, asset.geometry_profile)
        version.technical_qa = qa_report
        version.poly_count = qa_report.total_tris
        version.vertex_count = qa_report.total_vertices
        version.dimensions = qa_report.dimensions
        version.materials = qa_report.materials
        if qa_report.blender_version:
            version.blender_version = qa_report.blender_version
        self.db.update_version(version)

        # Even if technical QA failed, render visual evidence if mesh geometry exists so humans can inspect failure!
        if qa_report.mesh_object_count > 0:
            self._run_visual_stage(asset, version, blend_path, work_dir, job_id, tech_failed=(qa_report.status == TechnicalQAStatus.FAIL))

        if qa_report.status == TechnicalQAStatus.FAIL:
            version.status = AssetStatus.FAILED
            version.failed_stage = "technical_qa"
            version.error_message = qa_report.summary
            self.db.update_version(version)
            self.storage.write_version_artifacts(asset, version)
            return

        # Technical QA passed (or passed with warnings) -> awaits Stage 2 Human Visual Approval
        version.status = AssetStatus.AWAITING_HUMAN_APPROVAL
        self.db.update_version(version)
        self.storage.write_version_artifacts(asset, version)

        if self.benchmark_hook is not None:
            try:
                self.benchmark_hook(asset.asset_id, version.version)
            except Exception as exc:
                logger.warning("Optional Branch 2 benchmark hook failed for %s v%s: %s", asset.asset_id, version.version, exc)

    def _run_visual_stage(
        self,
        asset: AssetRecord,
        version: AssetVersion,
        blend_path: Path,
        work_dir: Path,
        job_id: str,
        *,
        tech_failed: bool = False,
    ) -> None:
        if not tech_failed:
            version.status = AssetStatus.VISUAL_TESTING
            self.db.update_version(version)
        self.db.record_job(job_id=job_id, asset_id=asset.asset_id, version=version.version, job_type="qa_pipeline", status="RUNNING", stage="visual_qa")

        previews_dir = self.storage.previews_dir(asset.asset_id, version.version).resolve()
        ren_res = self.runner.run_script(
            "render_views.py",
            {
                "blend_path": str(blend_path),
                "output_dir": str(previews_dir),
                "render_engine": self.settings.blender_render_engine,
                "resolution": self.settings.blender_render_resolution,
                "samples": self.settings.blender_render_samples,
                "turntable_frames": 12,
                "turntable_resolution": 384,
            },
            work_dir / "render_views",
        )

        render_paths: dict[str, str] = ren_res.get("render_paths", {})
        tt_frames: list[str] = ren_res.get("turntable_frame_paths", [])

        sheet_path = build_contact_sheet(render_paths, previews_dir / "contact_sheet.png")
        turntable_path = assemble_turntable_webp(tt_frames, previews_dir / "turntable.webp", duration_ms=130)
        # Clean temporary raw turntable frames after stitching WebP to keep vault tidy
        tt_frames_dir = previews_dir / "turntable_frames"
        if tt_frames_dir.is_dir():
            shutil.rmtree(tt_frames_dir, ignore_errors=True)

        sanity_ok, sanity_details = verify_render_sanity(render_paths)
        provider = build_vision_provider(self.settings)

        try:
            vqa_report = provider.evaluate(
                asset_name=asset.name,
                description=asset.description,
                category=asset.category,
                render_paths=render_paths,
                turntable_path=str(turntable_path) if turntable_path else None,
                contact_sheet_path=str(sheet_path) if sheet_path else None,
                render_sanity_ok=sanity_ok,
                render_sanity_details=sanity_details,
            )
        except Exception as exc:
            logger.exception("Vision QA provider '%s' raised error; flagging for human review.", provider.provider_name)
            from ..models import VisualQAReport

            vqa_report = VisualQAReport(
                provider=provider.provider_name,
                model="error",
                status=VisualQAStatus.ERROR,
                confidence=0.0,
                recommendation="NEEDS_HUMAN_REVIEW",
                summary=f"Automated vision QA error ({exc}); human visual inspection required.",
                issues_found=[str(exc)],
                render_paths=render_paths,
                turntable_path=str(turntable_path) if turntable_path else None,
                contact_sheet_path=str(sheet_path) if sheet_path else None,
                render_sanity_ok=sanity_ok,
                render_sanity_details=sanity_details,
            )

        version.visual_qa = vqa_report
        self.db.update_version(version)

    # ------------------------------------------------------------------
    # Stage 2: Human Visual Review & Approval
    # ------------------------------------------------------------------
    def submit_human_review(self, asset_id: str, version_num: int, req: HumanReviewRequest) -> AssetRecord:
        asset = self.db.get_asset(asset_id)
        if not asset:
            raise KeyError(f"Asset '{asset_id}' not found.")
        version = self.db.get_version(asset_id, version_num)
        if not version:
            raise KeyError(f"Asset '{asset_id}' version v{version_num} not found.")

        if req.decision == HumanApprovalStatus.APPROVED:
            if not version.technical_qa or version.technical_qa.status == TechnicalQAStatus.FAIL:
                raise ValueError(
                    "Cannot approve an asset version whose Technical QA failed or has not completed."
                )
            if version.status == AssetStatus.FAILED:
                raise ValueError("Cannot approve a FAILED asset version.")

        if req.category and req.category in STANDARD_CATEGORIES and req.category != asset.category:
            self.db.update_asset_metadata(asset_id, category=req.category)
            asset.category = req.category

        version.human_review = HumanReviewRecord(
            status=req.decision,
            reviewer=req.reviewer.strip() or "Local Reviewer",
            notes=req.notes.strip(),
            reviewed_at=utc_now_iso(),
        )
        if req.decision == HumanApprovalStatus.APPROVED:
            version.status = AssetStatus.APPROVED
        elif req.decision == HumanApprovalStatus.REJECTED:
            version.status = AssetStatus.REJECTED
        else:
            version.status = AssetStatus.AWAITING_HUMAN_APPROVAL

        self.db.update_version(version)
        updated_asset = self.db.get_asset(asset_id) or asset
        self.storage.write_version_artifacts(updated_asset, version)
        if self.roblox_registrar is not None:
            try:
                rbx = self.roblox_registrar.assets.get_by_legacy_vault_id(asset_id)
                if rbx:
                    updated_asset.roblox_asset_id = rbx.asset_id
                    if req.decision == HumanApprovalStatus.APPROVED:
                        if self.roblox_workflow is not None and self.roblox_workflow.qa_reports.get_latest_report_for_asset(rbx.asset_id):
                            self.roblox_workflow.approve_asset(
                                rbx.asset_id,
                                version_number=version_num,
                                reviewer=version.human_review.reviewer or "Local Reviewer",
                                notes=version.human_review.notes,
                                sync_to_drive=False,
                            )
                        else:
                            self.roblox_registrar.versions.mark_version_approved(
                                rbx.asset_id,
                                version_num,
                                approved_by=version.human_review.reviewer or "Local Reviewer",
                                approved_at=version.human_review.reviewed_at,
                            )
                    elif req.decision == HumanApprovalStatus.REJECTED:
                        if self.roblox_workflow is not None:
                            self.roblox_workflow.reject_asset(
                                rbx.asset_id,
                                version_number=version_num,
                                reviewer=version.human_review.reviewer or "Local Reviewer",
                                reason=version.human_review.notes,
                            )
                        else:
                            self.roblox_registrar.assets.transition_status(
                                rbx.asset_id,
                                ProductionStatus.REJECTED,
                                process_name="human_review",
                                message=f"Rejected by {version.human_review.reviewer}: {version.human_review.notes}",
                            )
            except Exception as exc:
                logger.warning("Failed to mirror human review to Roblox DB for %s: %s", asset_id, exc)
        return updated_asset

    # ------------------------------------------------------------------
    # Retry / Re-inspect / Google Drive Upload
    # ------------------------------------------------------------------
    def retry_asset_version(self, asset_id: str, version_num: int) -> AssetRecord:
        asset = self.db.get_asset(asset_id)
        if not asset:
            raise KeyError(f"Asset '{asset_id}' not found.")
        version = self.db.get_version(asset_id, version_num)
        if not version:
            raise KeyError(f"Asset '{asset_id}' version v{version_num} not found.")

        if version.failed_stage == "google_drive_upload" and version.technical_qa and version.technical_qa.status != TechnicalQAStatus.FAIL:
            self.upload_to_drive(asset_id, version_num)
            return self.db.get_asset(asset_id) or asset

        # If import failed before creating .blend, retry from preserved original
        if (not version.blend_path or not Path(version.blend_path).is_file()) and version.source_original_path:
            src = Path(version.source_original_path)
            if src.is_file():
                job_id = uuid.uuid4().hex[:10]
                work_dir = (self.settings.tmp_dir / f"retry_{job_id}").resolve()
                work_dir.mkdir(parents=True, exist_ok=True)
                try:
                    vdir = self.storage.version_dir(asset_id, version_num).resolve()
                    target_blend = vdir / f"{asset.slug}.blend"
                    target_glb = vdir / f"{asset.slug}.glb"
                    imp_res = self.runner.run_script(
                        "import_convert.py",
                        {"source_path": str(src.resolve()), "output_blend": str(target_blend), "output_glb": str(target_glb)},
                        work_dir / "import",
                    )
                    version.blend_path = str(target_blend)
                    version.glb_path = str(target_glb) if target_glb.is_file() else None
                    version.blender_version = str(imp_res.get("blender_version", ""))
                    self.db.update_version(version)
                    self._run_qa_and_visual_stages(asset, version, work_dir, job_id)
                finally:
                    self._cleanup_work_dir(work_dir)
                return self.db.get_asset(asset_id) or asset

        job_id = uuid.uuid4().hex[:10]
        work_dir = (self.settings.tmp_dir / f"retry_{job_id}").resolve()
        work_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._run_qa_and_visual_stages(asset, version, work_dir, job_id)
        finally:
            self._cleanup_work_dir(work_dir)
        return self.db.get_asset(asset_id) or asset

    def upload_to_drive(self, asset_id: str, version_num: int) -> AssetVersion:
        asset = self.db.get_asset(asset_id)
        if not asset:
            raise KeyError(f"Asset '{asset_id}' not found.")
        version = self.db.get_version(asset_id, version_num)
        if not version:
            raise KeyError(f"Asset '{asset_id}' version v{version_num} not found.")
        if version.status == AssetStatus.FAILED:
            raise ValueError("Cannot upload a FAILED asset version.")
        updated_ver = self.gdrive.upload_asset_version(asset, version)
        if self.roblox_registrar is not None:
            try:
                rbx = self.roblox_registrar.assets.get_by_legacy_vault_id(asset_id)
                if rbx:
                    refreshed = self.db.get_asset(asset_id)
                    if refreshed:
                        self.roblox_registrar.sync_vault_asset_metadata(rbx.asset_id, refreshed)
            except Exception as exc:
                logger.warning("Failed to mirror Drive upload IDs to Roblox DB for %s: %s", asset_id, exc)
        return updated_ver

    def verify_drive_asset(self, asset_id: str, version_num: int) -> dict[str, Any]:
        version = self.db.get_version(asset_id, version_num)
        if not version:
            raise KeyError(f"Asset '{asset_id}' version v{version_num} not found.")
        work_dir = (self.settings.tmp_dir / f"verify_{uuid.uuid4().hex[:8]}").resolve()
        work_dir.mkdir(parents=True, exist_ok=True)
        try:
            return self.gdrive.verify_remote_version_integrity(version, work_dir, self.runner)
        finally:
            self._cleanup_work_dir(work_dir)

    def _update_roblox_generated_files(
        self,
        roblox_asset_id: str,
        *,
        version_num: int,
        blend_path: Optional[Path],
        export_path: Optional[Path],
        export_format: Optional[str],
        sha256: Optional[str],
    ) -> None:
        if self.roblox_registrar is None:
            return
        asset_updates: dict[str, Any] = {}
        ver_updates: dict[str, Any] = {}
        if blend_path is not None:
            asset_updates["source_blend_path"] = str(blend_path)
            ver_updates["blender_source_path"] = str(blend_path)
        if export_path is not None:
            asset_updates["export_path"] = str(export_path)
            ver_updates["export_path"] = str(export_path)
        if export_format is not None:
            asset_updates["export_format"] = export_format
            ver_updates["export_format"] = export_format
        if sha256 is not None:
            ver_updates["sha256"] = sha256
        if asset_updates:
            self.roblox_registrar.assets.update_asset(roblox_asset_id, **asset_updates)
        ver_rec = self.roblox_registrar.versions.get_asset_version(roblox_asset_id, version_num)
        if ver_rec and ver_updates and not ver_rec.is_approved:
            self.roblox_registrar.versions.update_version(ver_rec.version_id, **ver_updates)
        self.roblox_registrar.assets.transition_status(
            roblox_asset_id,
            ProductionStatus.QA_PENDING,
            process_name="generation",
            message=f"Blender generation finished for v{version_num:03d}; queued for QA.",
        )

    def _default_roblox_qa_hook(
        self,
        roblox_asset_id: str,
        version_num: int,
        vault_asset: AssetRecord,
        vault_version: AssetVersion,
    ) -> None:
        if self.roblox_workflow is None:
            return
        if not vault_version.blend_path or not Path(vault_version.blend_path).is_file():
            return
        self.roblox_workflow.run_qa_for_asset(
            roblox_asset_id,
            version_number=version_num,
            render_previews=True,
        )

    def _sync_roblox_after_qa(
        self,
        roblox_asset_id: str,
        vault_asset: AssetRecord,
        vault_version: AssetVersion,
    ) -> None:
        if self.roblox_registrar is None:
            return
        refreshed = self.db.get_asset(vault_asset.asset_id) or vault_asset
        self.roblox_registrar.sync_vault_asset_metadata(roblox_asset_id, refreshed)

        if self.roblox_qa_hook is not None:
            try:
                self.roblox_qa_hook(roblox_asset_id, vault_version.version, refreshed, vault_version)
                return
            except Exception as exc:
                logger.warning("Roblox QA hook failed for %s: %s", roblox_asset_id, exc)

        if vault_version.status == AssetStatus.FAILED:
            try:
                self.roblox_registrar.assets.transition_status(
                    roblox_asset_id,
                    ProductionStatus.QA_FAILED,
                    process_name="technical_qa",
                    message=vault_version.error_message or "Technical QA failed.",
                    error_details=vault_version.error_message,
                )
            except Exception:
                pass
        elif vault_version.status == AssetStatus.AWAITING_HUMAN_APPROVAL:
            try:
                cur = self.roblox_registrar.assets.get_asset(roblox_asset_id)
                if cur and cur.production_status == ProductionStatus.QA_PENDING:
                    self.roblox_registrar.assets.transition_status(
                        roblox_asset_id,
                        ProductionStatus.QA_RUNNING,
                        process_name="technical_qa",
                        message="Branch 1 Technical QA running.",
                    )
                    self.roblox_registrar.assets.transition_status(
                        roblox_asset_id,
                        ProductionStatus.QA_PASSED,
                        process_name="technical_qa",
                        message="Branch 1 Technical QA passed.",
                    )
                    self.roblox_registrar.assets.transition_status(
                        roblox_asset_id,
                        ProductionStatus.AWAITING_HUMAN_APPROVAL,
                        process_name="technical_qa",
                        message="Awaiting human approval.",
                    )
                    ver_rec = self.roblox_registrar.versions.get_asset_version(roblox_asset_id, vault_version.version)
                    if ver_rec and not ver_rec.is_approved:
                        self.roblox_registrar.versions.update_version(
                            ver_rec.version_id,
                            qa_status="AWAITING_HUMAN_APPROVAL",
                        )
            except Exception as exc:
                logger.warning("Roblox status transition error for %s: %s", roblox_asset_id, exc)

    @staticmethod
    def _cleanup_work_dir(work_dir: Path) -> None:
        if work_dir.is_dir():
            shutil.rmtree(work_dir, ignore_errors=True)
