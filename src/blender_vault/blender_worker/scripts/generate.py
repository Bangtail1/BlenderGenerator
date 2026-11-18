"""Blender worker script: procedurally generate an asset, save .blend, and export .glb."""

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
from generators.registry import UnsupportedGenerationRequestError, run_procedural_generator  # noqa: E402


def main() -> None:
    payload, out_json = parse_cli_args()
    try:
        clear_scene()
        asset_name = str(payload.get("asset_name", "Procedural_Asset"))
        prompt = str(payload.get("prompt", ""))
        template_id = payload.get("template_id")
        params = payload.get("params") or {}
        output_blend = Path(payload["output_blend"]).resolve()
        output_glb = Path(payload["output_glb"]).resolve() if payload.get("output_glb") else None

        gen_meta = run_procedural_generator(
            asset_name=asset_name,
            prompt=prompt,
            template_id=template_id,
            params=params,
        )

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
                "blend_path": str(output_blend),
                "glb_path": str(output_glb) if glb_exported else None,
                "mesh_object_count": len(mesh_objs),
                "bounds": bounds,
                "generation": gen_meta,
            },
        )
    except UnsupportedGenerationRequestError as exc:
        write_output(out_json, {"ok": False, "error_type": "UNSUPPORTED_PROMPT", "error": str(exc)})
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
