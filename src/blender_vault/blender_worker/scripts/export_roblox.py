"""Blender worker script: export a .blend to FBX/GLB/OBJ for Roblox and verify re-import integrity."""

from __future__ import annotations

import sys
import traceback
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import bmesh  # noqa: E402
import bpy  # noqa: E402

from _common import (  # noqa: E402
    compute_combined_bounds,
    export_scene_glb,
    get_mesh_objects,
    parse_cli_args,
    write_output,
)


def _export_asset(fmt: str, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "GLB":
        export_scene_glb(out_path)
        return
    if fmt == "FBX":
        bpy.ops.export_scene.fbx(
            filepath=str(out_path),
            use_selection=False,
            global_scale=1.0,
            apply_unit_scale=True,
            apply_scale_options="FBX_SCALE_ALL",
            use_space_transform=True,
            bake_space_transform=False,
            object_types={"MESH", "ARMATURE", "EMPTY"},
            use_mesh_modifiers=True,
            mesh_smooth_type="FACE",
            use_tspace=True,
            path_mode="COPY",
            embed_textures=True,
            axis_forward="-Z",
            axis_up="Y",
        )
        return
    if fmt == "OBJ":
        bpy.ops.wm.obj_export(
            filepath=str(out_path),
            forward_axis="NEGATIVE_Z",
            up_axis="Y",
            global_scale=1.0,
            apply_modifiers=True,
            export_uv=True,
            export_normals=True,
            export_materials=True,
            path_mode="COPY",
        )
        return
    raise ValueError(f"Unsupported Roblox export format: {fmt}")


def _reimport_asset(fmt: str, file_path: Path) -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    if fmt == "GLB":
        bpy.ops.import_scene.gltf(filepath=str(file_path))
        return
    if fmt == "FBX":
        if hasattr(bpy.ops.import_scene, "fbx"):
            bpy.ops.import_scene.fbx(filepath=str(file_path))
        else:
            bpy.ops.wm.fbx_import(filepath=str(file_path))
        return
    if fmt == "OBJ":
        bpy.ops.wm.obj_import(filepath=str(file_path))
        return
    raise ValueError(f"Unsupported Roblox import format: {fmt}")


def _summarize_scene_meshes() -> dict[str, Any]:
    meshes = get_mesh_objects()
    bounds = compute_combined_bounds(meshes)
    mats: set[str] = set()
    total_tris = 0
    total_verts = 0
    total_faces = 0
    has_uv_all = len(meshes) > 0
    names: list[str] = []

    for obj in meshes:
        names.append(obj.name)
        mesh = obj.data
        total_verts += len(mesh.vertices)
        total_faces += len(mesh.polygons)
        if len(mesh.uv_layers) == 0:
            has_uv_all = False
        for slot in obj.material_slots:
            if slot.material is not None:
                mats.add(slot.material.name)
        bm = bmesh.new()
        bm.from_mesh(mesh)
        bmesh.ops.triangulate(bm, faces=bm.faces)
        total_tris += len(bm.faces)
        bm.free()

    return {
        "mesh_count": len(meshes),
        "object_names": names,
        "vertex_count": total_verts,
        "face_count": total_faces,
        "tri_count": total_tris,
        "dimensions": [round(float(x), 5) for x in bounds["dimensions"]],
        "material_count": len(mats),
        "materials": sorted(mats),
        "has_uv_all": has_uv_all,
    }


def main() -> None:
    payload, out_json = parse_cli_args()
    try:
        blend_path = Path(payload["blend_path"]).resolve()
        export_path = Path(payload["export_path"]).resolve()
        fmt = str(payload.get("export_format", "FBX")).upper()
        verify_roundtrip = bool(payload.get("verify_roundtrip", True))

        if not blend_path.is_file():
            raise FileNotFoundError(f"Blend file not found: {blend_path}")

        bpy.ops.wm.open_mainfile(filepath=str(blend_path))
        before = _summarize_scene_meshes()
        if before["mesh_count"] == 0:
            raise RuntimeError("Cannot export for Roblox: .blend scene contains zero mesh objects.")

        _export_asset(fmt, export_path)
        if not export_path.is_file() or export_path.stat().st_size == 0:
            raise RuntimeError(f"Exported {fmt} file is missing or empty: {export_path}")

        roundtrip: dict[str, Any] = {"attempted": False, "success": False}
        if verify_roundtrip:
            try:
                _reimport_asset(fmt, export_path)
                after = _summarize_scene_meshes()
                orig_dims = before["dimensions"]
                re_dims = after["dimensions"]
                dim_delta = max(abs(orig_dims[i] - re_dims[i]) for i in range(3))
                # GLB/FBX may split vertices across UV seams, so compare faces/tris & dimensions
                tri_delta = abs(before["tri_count"] - after["tri_count"])
                roundtrip = {
                    "attempted": True,
                    "success": after["mesh_count"] > 0,
                    "before": before,
                    "after": after,
                    "dimension_delta_max": round(float(dim_delta), 5),
                    "tri_delta": int(tri_delta),
                    "materials_preserved": not (before["material_count"] > 0 and after["material_count"] == 0),
                    "uvs_preserved": (not before["has_uv_all"]) or after["has_uv_all"],
                }
            except Exception as exc:
                roundtrip = {
                    "attempted": True,
                    "success": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }

        write_output(
            out_json,
            {
                "ok": True,
                "blender_version": bpy.app.version_string,
                "export_format": fmt,
                "export_path": str(export_path),
                "file_size_bytes": export_path.stat().st_size,
                "before": before,
                "roundtrip": roundtrip,
            },
        )
    except Exception as exc:
        write_output(
            out_json,
            {
                "ok": False,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "traceback": traceback.format_exc(),
            },
        )


if __name__ == "__main__":
    main()
