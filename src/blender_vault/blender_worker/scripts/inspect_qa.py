"""Blender worker script: deep geometry, structure, dependency, and GLB round-trip inspection."""

from __future__ import annotations

import math
import sys
import traceback
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import bmesh  # noqa: E402
import bpy  # noqa: E402
from mathutils import Vector, kdtree  # noqa: E402

from _common import (  # noqa: E402
    compute_combined_bounds,
    export_scene_glb,
    get_mesh_objects,
    parse_cli_args,
    write_output,
)


def _count_duplicate_verts(bm: bmesh.types.BMesh, tolerance: float = 1e-5) -> int:
    n = len(bm.verts)
    if n == 0:
        return 0
    kd = kdtree.KDTree(n)
    for i, v in enumerate(bm.verts):
        kd.insert(v.co, i)
    kd.balance()
    dup_count = 0
    seen_groups: set[int] = set()
    for i, v in enumerate(bm.verts):
        if i in seen_groups:
            continue
        matches = kd.find_range(v.co, tolerance)
        if len(matches) > 1:
            for _, idx, _ in matches:
                if idx != i and idx not in seen_groups:
                    seen_groups.add(idx)
                    dup_count += 1
            seen_groups.add(i)
    return dup_count


def _count_inverted_normals(bm: bmesh.types.BMesh) -> int:
    """Compare face normals on a scratch copy after recalc_face_normals."""
    if len(bm.faces) == 0:
        return 0
    bm_copy = bm.copy()
    orig_normals = [f.normal.copy() for f in bm_copy.faces]
    bmesh.ops.recalc_face_normals(bm_copy, faces=bm_copy.faces)
    flipped = 0
    for f, orig_n in zip(bm_copy.faces, orig_normals):
        if orig_n.length > 1e-6 and f.normal.length > 1e-6:
            if orig_n.dot(f.normal) < -0.5:
                flipped += 1
    bm_copy.free()
    return flipped


def _inspect_mesh_object(obj: bpy.types.Object) -> dict[str, Any]:
    mesh: bpy.types.Mesh = obj.data
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    bm.normal_update()

    non_manifold_edges = 0
    boundary_edges = 0
    multi_face_edges = 0
    loose_edges = 0
    for edge in bm.edges:
        lf = len(edge.link_faces)
        if lf == 0:
            loose_edges += 1
        elif lf == 1:
            boundary_edges += 1
        elif lf > 2:
            multi_face_edges += 1
        if not edge.is_manifold:
            non_manifold_edges += 1

    loose_verts = sum(1 for v in bm.verts if len(v.link_edges) == 0)

    degenerate_faces = 0
    zero_area_faces = 0
    surface_area = 0.0
    for face in bm.faces:
        area = face.calc_area()
        surface_area += area
        if area <= 1e-10:
            zero_area_faces += 1
            degenerate_faces += 1
        elif area <= 1e-8 or any(e.calc_length() <= 1e-7 for e in face.edges):
            degenerate_faces += 1

    duplicate_verts = _count_duplicate_verts(bm)
    inverted_normals = _count_inverted_normals(bm)

    # Signed volume for closed-mesh inside-out check
    bm_tri = bm.copy()
    bmesh.ops.triangulate(bm_tri, faces=bm_tri.faces)
    signed_volume = float(bm_tri.calc_volume(signed=True)) if len(bm_tri.faces) > 0 else 0.0
    tri_count = len(bm_tri.faces)
    bm_tri.free()
    bm.free()

    # World bbox for this object
    depsgraph = bpy.context.evaluated_depsgraph_get()
    eval_obj = obj.evaluated_get(depsgraph)
    corners = [eval_obj.matrix_world @ Vector(c) for c in eval_obj.bound_box]
    xs = [c.x for c in corners]
    ys = [c.y for c in corners]
    zs = [c.z for c in corners]
    bbox_min = [min(xs), min(ys), min(zs)] if corners else [0.0, 0.0, 0.0]
    bbox_max = [max(xs), max(ys), max(zs)] if corners else [0.0, 0.0, 0.0]
    dims = [bbox_max[i] - bbox_min[i] for i in range(3)]

    mat_names = [slot.material.name for slot in obj.material_slots if slot.material is not None]
    valid_slot_indices = {idx for idx, slot in enumerate(obj.material_slots) if slot.material is not None}
    if not valid_slot_indices:
        unassigned_faces = len(mesh.polygons)
    else:
        unassigned_faces = sum(1 for p in mesh.polygons if p.material_index not in valid_slot_indices)

    col_name = obj.users_collection[0].name if obj.users_collection else "Scene Collection"
    modifiers = [
        {
            "name": mod.name,
            "type": mod.type,
            "show_viewport": bool(mod.show_viewport),
            "show_render": bool(mod.show_render),
            "levels": getattr(mod, "levels", None),
            "render_levels": getattr(mod, "render_levels", None),
        }
        for mod in obj.modifiers
    ]

    return {
        "name": obj.name,
        "collection": col_name,
        "vertex_count": len(mesh.vertices),
        "edge_count": len(mesh.edges),
        "face_count": len(mesh.polygons),
        "tri_count": tri_count,
        "dimensions": [round(d, 5) for d in dims],
        "scale": [round(float(v), 5) for v in obj.scale],
        "rotation_euler": [round(float(v), 5) for v in obj.rotation_euler],
        "origin_location": [round(float(v), 5) for v in obj.location],
        "bbox_min": [round(v, 5) for v in bbox_min],
        "bbox_max": [round(v, 5) for v in bbox_max],
        "surface_area": round(float(surface_area), 6),
        "signed_volume": round(float(signed_volume), 6),
        "non_manifold_edges": non_manifold_edges,
        "boundary_edges": boundary_edges,
        "multi_face_edges": multi_face_edges,
        "loose_verts": loose_verts,
        "loose_edges": loose_edges,
        "degenerate_faces": degenerate_faces,
        "zero_area_faces": zero_area_faces,
        "duplicate_verts": duplicate_verts,
        "inverted_normals_count": inverted_normals,
        "has_uv": len(mesh.uv_layers) > 0,
        "uv_layer_names": [uv.name for uv in mesh.uv_layers],
        "material_count": len(mat_names),
        "materials": mat_names,
        "unassigned_material_faces": unassigned_faces,
        "modifiers": modifiers,
    }


def _find_missing_dependencies() -> list[str]:
    missing: list[str] = []
    for img in bpy.data.images:
        if img.source == "FILE" and img.packed_file is None and img.filepath:
            resolved = Path(bpy.path.abspath(img.filepath))
            if not resolved.is_file():
                missing.append(f"Missing image file '{img.name}': {img.filepath}")
    for lib in bpy.data.libraries:
        if lib.filepath:
            resolved = Path(bpy.path.abspath(lib.filepath))
            if not resolved.is_file():
                missing.append(f"Missing linked library '{lib.name}': {lib.filepath}")
    return missing


def _run_glb_round_trip(roundtrip_glb: Path, orig_bounds: dict[str, list[float]], orig_mesh_count: int, orig_mat_count: int, orig_tris: int) -> dict[str, Any]:
    if orig_mesh_count == 0:
        return {
            "attempted": False,
            "success": False,
            "error": "Skipped GLB round-trip because scene contains zero mesh objects.",
        }
    try:
        export_scene_glb(roundtrip_glb)
        if not roundtrip_glb.is_file() or roundtrip_glb.stat().st_size == 0:
            return {"attempted": True, "success": False, "error": "Exported GLB is missing or empty."}

        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.ops.import_scene.gltf(filepath=str(roundtrip_glb))
        re_meshes = get_mesh_objects()
        re_bounds = compute_combined_bounds(re_meshes)
        re_dims = re_bounds["dimensions"]
        orig_dims = orig_bounds["dimensions"]
        delta_max = max(abs(re_dims[i] - orig_dims[i]) for i in range(3))

        re_mats: set[str] = set()
        re_tris = 0
        for obj in re_meshes:
            for slot in obj.material_slots:
                if slot.material:
                    re_mats.add(slot.material.name)
            bm = bmesh.new()
            bm.from_mesh(obj.data)
            bmesh.ops.triangulate(bm, faces=bm.faces)
            re_tris += len(bm.faces)
            bm.free()

        return {
            "attempted": True,
            "success": len(re_meshes) > 0,
            "glb_path": str(roundtrip_glb),
            "original_mesh_count": orig_mesh_count,
            "reimported_mesh_count": len(re_meshes),
            "original_dimensions": [round(x, 5) for x in orig_dims],
            "reimported_dimensions": [round(x, 5) for x in re_dims],
            "dimension_delta_max": round(float(delta_max), 5),
            "material_count_before": orig_mat_count,
            "material_count_after": len(re_mats),
            "tri_count_before": orig_tris,
            "tri_count_after": re_tris,
            "error": None if re_meshes else "Reimported GLB contained zero meshes.",
        }
    except Exception as exc:
        return {
            "attempted": True,
            "success": False,
            "glb_path": str(roundtrip_glb),
            "error": f"{type(exc).__name__}: {exc}",
        }


def main() -> None:
    payload, out_json = parse_cli_args()
    try:
        blend_path = Path(payload["blend_path"]).resolve()
        roundtrip_glb = Path(payload["roundtrip_glb"]).resolve()
        if not blend_path.is_file():
            raise FileNotFoundError(f"Blend file not found: {blend_path}")

        bpy.ops.wm.open_mainfile(filepath=str(blend_path))
        mesh_objs = get_mesh_objects()
        bounds = compute_combined_bounds(mesh_objs)

        obj_metrics = [_inspect_mesh_object(obj) for obj in mesh_objs]
        all_materials = sorted({m for om in obj_metrics for m in om["materials"]})
        collections = sorted({c.name for c in bpy.data.collections})
        missing_deps = _find_missing_dependencies()

        total_verts = sum(om["vertex_count"] for om in obj_metrics)
        total_edges = sum(om["edge_count"] for om in obj_metrics)
        total_faces = sum(om["face_count"] for om in obj_metrics)
        total_tris = sum(om["tri_count"] for om in obj_metrics)

        round_trip = _run_glb_round_trip(
            roundtrip_glb=roundtrip_glb,
            orig_bounds=bounds,
            orig_mesh_count=len(mesh_objs),
            orig_mat_count=len(all_materials),
            orig_tris=total_tris,
        )

        write_output(
            out_json,
            {
                "ok": True,
                "blender_version": bpy.app.version_string,
                "file_opened_ok": True,
                "object_count": len(bpy.data.objects),
                "mesh_object_count": len(mesh_objs),
                "total_vertices": total_verts,
                "total_edges": total_edges,
                "total_faces": total_faces,
                "total_tris": total_tris,
                "dimensions": [round(d, 5) for d in bounds["dimensions"]],
                "bbox_min": [round(d, 5) for d in bounds["min"]],
                "bbox_max": [round(d, 5) for d in bounds["max"]],
                "materials": all_materials,
                "collections": collections,
                "missing_dependencies": missing_deps,
                "objects": obj_metrics,
                "round_trip": round_trip,
            },
        )
    except Exception as exc:
        write_output(
            out_json,
            {
                "ok": False,
                "file_opened_ok": False,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "traceback": traceback.format_exc(),
            },
        )


if __name__ == "__main__":
    main()
