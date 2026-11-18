"""Blender worker script: safely import .blend/.obj/.fbx/.stl/.glb/.gltf into an editable .blend without modifying source."""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import bpy  # noqa: E402
from _common import (  # noqa: E402
    clear_scene,
    compute_combined_bounds,
    export_scene_glb,
    get_mesh_objects,
    parse_cli_args,
    save_blend_file,
    write_output,
)

SUPPORTED_EXTENSIONS = {".blend", ".obj", ".fbx", ".stl", ".glb", ".gltf"}


def import_source_file(source_path: Path) -> str:
    ext = source_path.suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(
            f"Unsupported 3D model format '{ext}'. Supported formats: {', '.join(sorted(SUPPORTED_EXTENSIONS))}."
        )
    if not source_path.is_file():
        raise FileNotFoundError(f"Source file not found: {source_path}")

    if ext == ".blend":
        bpy.ops.wm.open_mainfile(filepath=str(source_path))
        return "wm.open_mainfile"

    clear_scene()
    if ext == ".obj":
        bpy.ops.wm.obj_import(filepath=str(source_path))
        return "wm.obj_import"
    if ext == ".stl":
        bpy.ops.wm.stl_import(filepath=str(source_path))
        return "wm.stl_import"
    if ext in {".glb", ".gltf"}:
        bpy.ops.import_scene.gltf(filepath=str(source_path))
        return "import_scene.gltf"
    if ext == ".fbx":
        if hasattr(bpy.ops.import_scene, "fbx"):
            bpy.ops.import_scene.fbx(filepath=str(source_path))
            return "import_scene.fbx"
        bpy.ops.wm.fbx_import(filepath=str(source_path))
        return "wm.fbx_import"

    raise ValueError(f"Unhandled extension: {ext}")


def main() -> None:
    payload, out_json = parse_cli_args()
    try:
        source_path = Path(payload["source_path"]).resolve()
        output_blend = Path(payload["output_blend"]).resolve()
        output_glb = Path(payload["output_glb"]).resolve() if payload.get("output_glb") else None

        importer_used = import_source_file(source_path)

        # Pack external textures if possible? Wait — do NOT hide missing external textures before QA!
        # Save editable working .blend copy (original source_path remains completely untouched).
        save_blend_file(output_blend)

        mesh_objs = get_mesh_objects()
        bounds = compute_combined_bounds(mesh_objs)

        glb_exported = False
        if output_glb is not None and mesh_objs:
            export_scene_glb(output_glb)
            glb_exported = output_glb.is_file()

        write_output(
            out_json,
            {
                "ok": True,
                "blender_version": bpy.app.version_string,
                "importer_used": importer_used,
                "source_extension": source_path.suffix.lower(),
                "blend_path": str(output_blend),
                "glb_path": str(output_glb) if glb_exported else None,
                "mesh_object_count": len(mesh_objs),
                "bounds": bounds,
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
