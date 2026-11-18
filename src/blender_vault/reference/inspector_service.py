"""Isolated headless Blender inspection service for reference assets and Branch 1 models."""

from __future__ import annotations

import json
import logging
import shutil
import tarfile
import uuid
import zipfile
from pathlib import Path
from typing import Optional
from urllib.parse import unquote

from ..blender_worker.runner import BlenderWorkerError, BlenderWorkerRunner
from ..config import Settings
from ..models import utc_now_iso
from .models import ReferenceInspectionReport

logger = logging.getLogger("blender_vault.reference.inspector")

SUPPORTED_3D_EXTENSIONS = (".blend", ".glb", ".gltf", ".fbx", ".obj", ".stl")
MAX_ARCHIVE_MEMBERS = 1000
MAX_ARCHIVE_EXTRACTED_BYTES = 512 * 1024 * 1024  # 512 MiB
_COPY_CHUNK_BYTES = 64 * 1024


def _copy_stream_with_budget(
    src,
    dst,
    *,
    remaining_bytes: int,
    archive_name: str,
    member_name: str,
) -> int:
    """Copy stream chunk-by-chunk, raising ValueError if copied bytes exceed remaining_bytes."""
    copied = 0
    while True:
        chunk = src.read(_COPY_CHUNK_BYTES)
        if not chunk:
            break
        copied += len(chunk)
        if copied > remaining_bytes:
            raise ValueError(
                f"Archive '{archive_name}' exceeded maximum extracted size budget while reading '{member_name}'."
            )
        dst.write(chunk)
    return copied


def _resolve_safe_member_target(
    dest_root: Path,
    member_name: str,
    *,
    member_count: int,
    max_members: int,
    archive_name: str,
    member_kind: str = "archive",
) -> Path:
    """Enforce member-count limit and path containment inside dest_root."""
    if member_count > max_members:
        raise ValueError(
            f"Archive '{archive_name}' exceeded maximum member limit ({max_members})."
        )
    target = (dest_root / member_name).resolve()
    if dest_root != target and dest_root not in target.parents:
        raise ValueError(f"Unsafe {member_kind} member path blocked: {member_name}")
    return target


def _safe_extract_zip(
    archive_path: Path,
    dest_dir: Path,
    *,
    max_members: int = MAX_ARCHIVE_MEMBERS,
    max_bytes: int = MAX_ARCHIVE_EXTRACTED_BYTES,
) -> None:
    dest_root = dest_dir.resolve()
    member_count = 0
    remaining_bytes = max_bytes
    with zipfile.ZipFile(archive_path, "r") as zf:
        for member in zf.infolist():
            member_count += 1
            target = _resolve_safe_member_target(
                dest_root,
                member.filename,
                member_count=member_count,
                max_members=max_members,
                archive_name=archive_path.name,
                member_kind="archive",
            )
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                if member.file_size > remaining_bytes:
                    raise ValueError(
                        f"Archive '{archive_path.name}' exceeded maximum extracted size budget ({max_bytes} bytes)."
                    )
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member, "r") as src, target.open("wb") as dst:
                    copied = _copy_stream_with_budget(
                        src,
                        dst,
                        remaining_bytes=remaining_bytes,
                        archive_name=archive_path.name,
                        member_name=member.filename,
                    )
                    remaining_bytes -= copied


def _safe_extract_tar(
    archive_path: Path,
    dest_dir: Path,
    *,
    max_members: int = MAX_ARCHIVE_MEMBERS,
    max_bytes: int = MAX_ARCHIVE_EXTRACTED_BYTES,
) -> None:
    dest_root = dest_dir.resolve()
    member_count = 0
    remaining_bytes = max_bytes
    with tarfile.open(archive_path, "r:*") as tf:
        for member in tf.getmembers():
            member_count += 1
            target = _resolve_safe_member_target(
                dest_root,
                member.name,
                member_count=member_count,
                max_members=max_members,
                archive_name=archive_path.name,
                member_kind="tar",
            )
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                if member.size > remaining_bytes:
                    raise ValueError(
                        f"Archive '{archive_path.name}' exceeded maximum extracted size budget ({max_bytes} bytes)."
                    )
                src = tf.extractfile(member)
                if src is not None:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with src, target.open("wb") as dst:
                        copied = _copy_stream_with_budget(
                            src,
                            dst,
                            remaining_bytes=remaining_bytes,
                            archive_name=archive_path.name,
                            member_name=member.name,
                        )
                        remaining_bytes -= copied


def _copy_gltf_external_resources(source_gltf: Path, isolated_dir: Path) -> None:
    """Copy external .bin buffers and image URIs referenced by a standalone .gltf into isolated_dir."""
    try:
        payload = json.loads(source_gltf.read_text(encoding="utf-8"))
    except Exception:
        return
    if not isinstance(payload, dict):
        return

    source_root = source_gltf.parent.resolve()
    isolated_root = isolated_dir.resolve()

    entries: list[dict] = []
    for section in ("buffers", "images"):
        section_items = payload.get(section)
        if isinstance(section_items, list):
            entries.extend(item for item in section_items if isinstance(item, dict))

    for item in entries:
        uri = item.get("uri")
        if not isinstance(uri, str) or not uri.strip():
            continue
        raw_uri = uri.strip()
        if raw_uri.startswith("data:") or "://" in raw_uri:
            continue
        rel_path = Path(unquote(raw_uri))
        if rel_path.is_absolute():
            continue
        src_ref = (source_root / rel_path).resolve()
        dst_ref = (isolated_root / rel_path).resolve()
        if src_ref == source_root or source_root not in src_ref.parents:
            continue
        if dst_ref == isolated_root or isolated_root not in dst_ref.parents:
            continue
        if src_ref.is_file():
            dst_ref.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_ref, dst_ref)


def _locate_primary_3d_file(search_dir: Path) -> Optional[Path]:
    candidates = [p for p in search_dir.rglob("*") if p.is_file() and not p.name.startswith(".")]
    for ext in SUPPORTED_3D_EXTENSIONS:
        matches = sorted(p for p in candidates if p.suffix.lower() == ext)
        if matches:
            return matches[0]
    return None


class ReferenceInspectorService:
    """Runs isolated Blender technical inspections without ever altering original files."""

    METHODOLOGY_VERSION = "2.0.0"

    def __init__(self, settings: Settings, runner: BlenderWorkerRunner) -> None:
        self.settings = settings
        self.runner = runner

    def inspect_asset_file(
        self,
        *,
        source_file: Path,
        blendswap_id: int = 0,
        preview_image_count: int = 0,
    ) -> ReferenceInspectionReport:
        """Copy/unpack source_file into an isolated workspace and run headless Blender inspection."""
        inspection_id = f"insp_{uuid.uuid4().hex[:12]}"
        work_dir = (self.settings.tmp_dir / f"ref_inspect_{inspection_id}").resolve()
        isolated_dir = work_dir / "isolated"
        isolated_dir.mkdir(parents=True, exist_ok=True)

        logs_copy_dir = self.settings.logs_dir / "inspections"
        logs_copy_dir.mkdir(parents=True, exist_ok=True)
        persisted_log_path = logs_copy_dir / f"{blendswap_id}_{inspection_id}.log"

        try:
            if not source_file.is_file():
                return ReferenceInspectionReport(
                    inspection_id=inspection_id,
                    blendswap_id=blendswap_id,
                    methodology_version=self.METHODOLOGY_VERSION,
                    preview_image_count=preview_image_count,
                    error_message=f"Source asset file missing: {source_file}",
                )

            ext = source_file.suffix.lower()
            search_roots: list[str] = [str(source_file.parent.resolve())]
            target_to_open: Optional[Path] = None

            if ext == ".zip":
                _safe_extract_zip(source_file, isolated_dir)
                target_to_open = _locate_primary_3d_file(isolated_dir)
                search_roots.insert(0, str(isolated_dir))
            elif ext in {".tar", ".gz", ".tgz"} or source_file.name.lower().endswith(".tar.gz"):
                _safe_extract_tar(source_file, isolated_dir)
                target_to_open = _locate_primary_3d_file(isolated_dir)
                search_roots.insert(0, str(isolated_dir))
            elif ext in SUPPORTED_3D_EXTENSIONS:
                copied = isolated_dir / source_file.name
                shutil.copy2(source_file, copied)
                if ext == ".gltf":
                    _copy_gltf_external_resources(source_file, isolated_dir)
                target_to_open = copied
            else:
                return ReferenceInspectionReport(
                    inspection_id=inspection_id,
                    blendswap_id=blendswap_id,
                    methodology_version=self.METHODOLOGY_VERSION,
                    source_format=ext.lstrip("."),
                    is_native_blender=False,
                    preview_image_count=preview_image_count,
                    error_message=f"Unsupported format '{ext}' for headless Blender inspection.",
                )

            if target_to_open is None or not target_to_open.is_file():
                return ReferenceInspectionReport(
                    inspection_id=inspection_id,
                    blendswap_id=blendswap_id,
                    methodology_version=self.METHODOLOGY_VERSION,
                    preview_image_count=preview_image_count,
                    error_message=f"Archive '{source_file.name}' did not contain a supported 3D file (.blend/.glb/.fbx/.obj/.stl).",
                )

            raw = self.runner.run_script(
                "inspect_reference.py",
                {
                    "source_path": str(target_to_open.resolve()),
                    "search_roots": search_roots,
                },
                work_dir / "blender_run",
            )
            run_log = Path(raw.get("_log_path", ""))
            if run_log.is_file():
                shutil.copy2(run_log, persisted_log_path)

            return ReferenceInspectionReport(
                inspection_id=inspection_id,
                blendswap_id=blendswap_id,
                methodology_version=self.METHODOLOGY_VERSION,
                source_format=str(raw.get("source_format", target_to_open.suffix.lstrip("."))),
                is_native_blender=bool(raw.get("is_native_blender", target_to_open.suffix.lower() == ".blend")),
                blend_header_version=str(raw.get("blend_header_version", "")),
                blender_runtime_version=str(raw.get("blender_runtime_version", "")),
                file_opened_ok=bool(raw.get("file_opened_ok", False)),
                inspection_completed_ok=bool(raw.get("inspection_completed_ok", False)),
                missing_dependencies=list(raw.get("missing_dependencies", [])),
                missing_textures=list(raw.get("missing_textures", [])),
                object_count=int(raw.get("object_count", 0)),
                mesh_object_count=int(raw.get("mesh_object_count", 0)),
                non_mesh_object_count=int(raw.get("non_mesh_object_count", 0)),
                vertex_count=int(raw.get("vertex_count", 0)),
                edge_count=int(raw.get("edge_count", 0)),
                face_count=int(raw.get("face_count", 0)),
                polygon_count=int(raw.get("polygon_count", 0)),
                triangle_count=int(raw.get("triangle_count", 0)),
                connected_components=int(raw.get("connected_components", 0)),
                is_manifold=bool(raw.get("is_manifold", False)),
                non_manifold_edges=int(raw.get("non_manifold_edges", 0)),
                boundary_edges=int(raw.get("boundary_edges", 0)),
                multi_face_edges=int(raw.get("multi_face_edges", 0)),
                loose_verts=int(raw.get("loose_verts", 0)),
                loose_edges=int(raw.get("loose_edges", 0)),
                degenerate_faces=int(raw.get("degenerate_faces", 0)),
                zero_area_faces=int(raw.get("zero_area_faces", 0)),
                duplicate_verts=int(raw.get("duplicate_verts", 0)),
                inverted_normals_count=int(raw.get("inverted_normals_count", 0)),
                mesh_density_tris_per_m3=float(raw.get("mesh_density_tris_per_m3", 0.0)),
                surface_area=float(raw.get("surface_area", 0.0)),
                dimensions=[float(x) for x in raw.get("dimensions", [0.0, 0.0, 0.0])],
                quad_count=int(raw.get("quad_count", 0)),
                tri_face_count=int(raw.get("tri_face_count", 0)),
                ngon_count=int(raw.get("ngon_count", 0)),
                quad_ratio=float(raw.get("quad_ratio", 0.0)),
                triangle_ratio=float(raw.get("triangle_ratio", 0.0)),
                quad_to_tri_ratio=float(raw.get("quad_to_tri_ratio", 0.0)),
                ngon_ratio=float(raw.get("ngon_ratio", 0.0)),
                pole_vertex_ratio=float(raw.get("pole_vertex_ratio", 0.0)),
                edge_flow_score=float(raw.get("edge_flow_score", 0.0)),
                subdivision_ready=bool(raw.get("subdivision_ready", False)),
                subdivision_readiness_notes=str(raw.get("subdivision_readiness_notes", "")),
                mesh_cleanliness_score=float(raw.get("mesh_cleanliness_score", 0.0)),
                has_uv_map=bool(raw.get("has_uv_map", False)),
                uv_layer_count=int(raw.get("uv_layer_count", 0)),
                uv_mapped_mesh_ratio=float(raw.get("uv_mapped_mesh_ratio", 0.0)),
                uv_coverage_ratio=float(raw.get("uv_coverage_ratio", 0.0)),
                uv_overlapping_islands_detected=bool(raw.get("uv_overlapping_islands_detected", False)),
                uv_overlap_cell_ratio=float(raw.get("uv_overlap_cell_ratio", 0.0)),
                uv_completeness_ratio=float(raw.get("uv_completeness_ratio", 0.0)),
                material_count=int(raw.get("material_count", 0)),
                material_names=list(raw.get("material_names", [])),
                texture_reference_count=int(raw.get("texture_reference_count", 0)),
                available_image_textures=int(raw.get("available_image_textures", 0)),
                missing_image_textures=int(raw.get("missing_image_textures", 0)),
                procedural_shader_count=int(raw.get("procedural_shader_count", 0)),
                principled_bsdf_count=int(raw.get("principled_bsdf_count", 0)),
                material_node_count_avg=float(raw.get("material_node_count_avg", 0.0)),
                unassigned_material_faces=int(raw.get("unassigned_material_faces", 0)),
                named_objects=list(raw.get("named_objects", [])),
                generic_named_objects=list(raw.get("generic_named_objects", [])),
                naming_consistency_ratio=float(raw.get("naming_consistency_ratio", 0.0)),
                collection_count=int(raw.get("collection_count", 0)),
                collections=list(raw.get("collections", [])),
                object_separation_score=float(raw.get("object_separation_score", 0.0)),
                scene_organization_score=float(raw.get("scene_organization_score", 0.0)),
                unapplied_scale_objects=list(raw.get("unapplied_scale_objects", [])),
                armature_count=int(raw.get("armature_count", 0)),
                bone_count=int(raw.get("bone_count", 0)),
                has_rig=bool(raw.get("has_rig", False)),
                has_animations=bool(raw.get("has_animations", False)),
                action_count=int(raw.get("action_count", 0)),
                weighted_mesh_count=int(raw.get("weighted_mesh_count", 0)),
                camera_count=int(raw.get("camera_count", 0)),
                light_count=int(raw.get("light_count", 0)),
                has_camera=bool(raw.get("has_camera", False)),
                has_lighting=bool(raw.get("has_lighting", False)),
                lighting_quality_score=float(raw.get("lighting_quality_score", 0.0)),
                renderable=bool(raw.get("renderable", False)),
                preview_image_count=preview_image_count,
                objects=list(raw.get("objects", [])),
                log_path=str(persisted_log_path) if persisted_log_path.is_file() else raw.get("_log_path"),
                inspected_at=utc_now_iso(),
            )
        except BlenderWorkerError as exc:
            if exc.log_path and exc.log_path.is_file():
                shutil.copy2(exc.log_path, persisted_log_path)
            return ReferenceInspectionReport(
                inspection_id=inspection_id,
                blendswap_id=blendswap_id,
                methodology_version=self.METHODOLOGY_VERSION,
                preview_image_count=preview_image_count,
                error_message=str(exc),
                log_path=str(persisted_log_path) if persisted_log_path.is_file() else (str(exc.log_path) if exc.log_path else None),
            )
        except Exception as exc:
            logger.exception("Reference inspection failed for %s", source_file)
            return ReferenceInspectionReport(
                inspection_id=inspection_id,
                blendswap_id=blendswap_id,
                methodology_version=self.METHODOLOGY_VERSION,
                preview_image_count=preview_image_count,
                error_message=f"{type(exc).__name__}: {exc}",
            )
        finally:
            if work_dir.is_dir():
                shutil.rmtree(work_dir, ignore_errors=True)
