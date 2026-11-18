"""Blender worker script: deep isolated technical inspection of BlendSwap or Branch 1 .blend / 3D assets."""

from __future__ import annotations

import math
import sys
import traceback
from collections import deque
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import bmesh  # noqa: E402
import bpy  # noqa: E402
from mathutils import Vector, kdtree  # noqa: E402

from _common import (  # noqa: E402
    clear_scene,
    compute_combined_bounds,
    get_mesh_objects,
    parse_cli_args,
    write_output,
)

GENERIC_NAME_PREFIXES = (
    "cube",
    "sphere",
    "uvsphere",
    "icosphere",
    "plane",
    "circle",
    "cylinder",
    "cone",
    "torus",
    "monkey",
    "suzanne",
    "object",
    "mesh",
)


def _is_generic_name(name: str) -> bool:
    base = name.strip().lower().split(".")[0]
    return base in GENERIC_NAME_PREFIXES


def _open_or_import_isolated(source_path: Path) -> tuple[bool, str, str]:
    """Load .blend or import exchange format (.obj/.fbx/.stl/.glb/.gltf) into a clean scene."""
    ext = source_path.suffix.lower()
    if not source_path.is_file():
        raise FileNotFoundError(f"Asset file not found: {source_path}")

    if ext == ".blend":
        bpy.ops.wm.open_mainfile(filepath=str(source_path))
        ver_tuple = getattr(bpy.data, "version", (0, 0, 0))
        header_ver = ".".join(str(x) for x in ver_tuple)
        return True, "blender", header_ver

    clear_scene()
    if ext == ".obj":
        bpy.ops.wm.obj_import(filepath=str(source_path))
    elif ext == ".stl":
        bpy.ops.wm.stl_import(filepath=str(source_path))
    elif ext in {".glb", ".gltf"}:
        bpy.ops.import_scene.gltf(filepath=str(source_path))
    elif ext == ".fbx":
        if hasattr(bpy.ops.import_scene, "fbx"):
            bpy.ops.import_scene.fbx(filepath=str(source_path))
        else:
            bpy.ops.wm.fbx_import(filepath=str(source_path))
    else:
        raise ValueError(f"Unsupported 3D asset extension for inspection: {ext}")

    return False, ext.lstrip("."), bpy.app.version_string


def _count_connected_components(bm: bmesh.types.BMesh) -> int:
    if len(bm.verts) == 0:
        return 0
    visited: set[int] = set()
    components = 0
    for v in bm.verts:
        if v.index in visited:
            continue
        components += 1
        q: deque[bmesh.types.BMVert] = deque([v])
        visited.add(v.index)
        while q:
            cur = q.popleft()
            for e in cur.link_edges:
                other = e.other_vert(cur)
                if other.index not in visited:
                    visited.add(other.index)
                    q.append(other)
    return components


def _count_duplicate_verts(bm: bmesh.types.BMesh, tolerance: float = 1e-5) -> int:
    n = len(bm.verts)
    if n == 0 or n > 250_000:
        return 0
    kd = kdtree.KDTree(n)
    for i, v in enumerate(bm.verts):
        kd.insert(v.co, i)
    kd.balance()
    dup_count = 0
    seen: set[int] = set()
    for i, v in enumerate(bm.verts):
        if i in seen:
            continue
        matches = kd.find_range(v.co, tolerance)
        if len(matches) > 1:
            for _, idx, _ in matches:
                if idx != i and idx not in seen:
                    seen.add(idx)
                    dup_count += 1
            seen.add(i)
    return dup_count


def _count_inverted_normals(bm: bmesh.types.BMesh) -> int:
    if len(bm.faces) == 0 or len(bm.faces) > 150_000:
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


def _analyze_uv_metrics(bm: bmesh.types.BMesh, grid_size: int = 64) -> dict[str, Any]:
    uv_layer = bm.loops.layers.uv.active
    if uv_layer is None or len(bm.faces) == 0:
        return {
            "mapped_faces": 0,
            "total_faces": len(bm.faces),
            "coverage_cells": set(),
            "overlap_cells": set(),
        }

    # Build UV islands using BFS over shared edges whose UV coords match
    bm.faces.ensure_lookup_table()
    face_count = len(bm.faces)
    stride = 1 if face_count <= 40_000 else max(1, face_count // 40_000)

    def face_uv_area(f: bmesh.types.BMFace) -> float:
        pts = [loop[uv_layer].uv for loop in f.loops]
        if len(pts) < 3:
            return 0.0
        area = 0.0
        for i in range(len(pts)):
            x1, y1 = pts[i].x, pts[i].y
            x2, y2 = pts[(i + 1) % len(pts)].x, pts[(i + 1) % len(pts)].y
            area += x1 * y2 - x2 * y1
        return abs(area) * 0.5

    mapped_faces = 0
    face_to_island: dict[int, int] = {}
    island_faces: list[list[bmesh.types.BMFace]] = []

    if stride == 1:
        # Exact UV island connectivity
        for f in bm.faces:
            if f.index in face_to_island:
                continue
            iid = len(island_faces)
            group: list[bmesh.types.BMFace] = []
            q: deque[bmesh.types.BMFace] = deque([f])
            face_to_island[f.index] = iid
            while q:
                cur = q.popleft()
                group.append(cur)
                if face_uv_area(cur) > 1e-8:
                    mapped_faces += 1
                cur_uv_by_vert = {lp.vert.index: lp[uv_layer].uv for lp in cur.loops}
                for edge in cur.edges:
                    for nf in edge.link_faces:
                        if nf.index in face_to_island:
                            continue
                        nf_uv_by_vert = {lp.vert.index: lp[uv_layer].uv for lp in nf.loops}
                        v0, v1 = edge.verts[0].index, edge.verts[1].index
                        if (
                            v0 in cur_uv_by_vert
                            and v0 in nf_uv_by_vert
                            and v1 in cur_uv_by_vert
                            and v1 in nf_uv_by_vert
                            and (cur_uv_by_vert[v0] - nf_uv_by_vert[v0]).length < 1e-4
                            and (cur_uv_by_vert[v1] - nf_uv_by_vert[v1]).length < 1e-4
                        ):
                            face_to_island[nf.index] = iid
                            q.append(nf)
            island_faces.append(group)
    else:
        # High-poly fast path: treat sampled faces cleanly without deep island BFS
        sampled = [bm.faces[i] for i in range(0, face_count, stride)]
        mapped_faces = sum(1 for f in sampled if face_uv_area(f) > 1e-8)
        mapped_faces = int(round(mapped_faces * stride))
        island_faces = [sampled]

    union_cells: set[int] = set()
    overlap_cells: set[int] = set()
    for group in island_faces:
        island_cells: set[int] = set()
        for f in group:
            uvs = [lp[uv_layer].uv for lp in f.loops]
            if len(uvs) < 3:
                continue
            us = [max(0.0, min(0.9999, u.x)) for u in uvs]
            vs = [max(0.0, min(0.9999, u.y)) for u in uvs]
            cx0 = int(min(us) * grid_size)
            cx1 = int(max(us) * grid_size)
            cy0 = int(min(vs) * grid_size)
            cy1 = int(max(vs) * grid_size)
            # Mark centroid and bounded face cells
            for cy in range(cy0, cy1 + 1):
                for cx in range(cx0, cx1 + 1):
                    island_cells.add(cy * grid_size + cx)
        overlap_cells.update(union_cells.intersection(island_cells))
        union_cells.update(island_cells)

    return {
        "mapped_faces": min(mapped_faces, face_count),
        "total_faces": face_count,
        "coverage_cells": union_cells,
        "overlap_cells": overlap_cells,
    }


def _inspect_mesh_object(obj: bpy.types.Object, armature_names: set[str]) -> dict[str, Any]:
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
    interior_verts = [v for v in bm.verts if len(v.link_edges) > 0 and not v.is_boundary]
    pole_verts = sum(1 for v in interior_verts if len(v.link_edges) not in (4,))

    quads = 0
    tris = 0
    ngons = 0
    degenerate_faces = 0
    zero_area_faces = 0
    surface_area = 0.0
    tri_count_from_faces = 0

    for face in bm.faces:
        vcount = len(face.verts)
        if vcount == 4:
            quads += 1
            tri_count_from_faces += 2
        elif vcount == 3:
            tris += 1
            tri_count_from_faces += 1
        elif vcount > 4:
            ngons += 1
            tri_count_from_faces += (vcount - 2)

        area = face.calc_area()
        if not math.isfinite(area) or area <= 1e-10:
            zero_area_faces += 1
            degenerate_faces += 1
        elif area <= 1e-8 or any(e.calc_length() <= 1e-7 for e in face.edges):
            surface_area += area
            degenerate_faces += 1
        else:
            surface_area += area

    components = _count_connected_components(bm)
    dup_verts = _count_duplicate_verts(bm)
    inv_normals = _count_inverted_normals(bm)
    uv_info = _analyze_uv_metrics(bm)
    bm.free()

    depsgraph = bpy.context.evaluated_depsgraph_get()
    eval_obj = obj.evaluated_get(depsgraph)
    corners = [eval_obj.matrix_world @ Vector(c) for c in eval_obj.bound_box]
    xs = [c.x for c in corners]
    ys = [c.y for c in corners]
    zs = [c.z for c in corners]
    dims = [max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs)] if corners else [0.0, 0.0, 0.0]

    mat_names = [slot.material.name for slot in obj.material_slots if slot.material is not None]
    valid_slots = {idx for idx, slot in enumerate(obj.material_slots) if slot.material is not None}
    if not valid_slots:
        unassigned_faces = len(mesh.polygons)
    else:
        unassigned_faces = sum(1 for p in mesh.polygons if p.material_index not in valid_slots)

    has_armature_mod = any(m.type == "ARMATURE" for m in obj.modifiers)
    has_weights = len(obj.vertex_groups) > 0 or has_armature_mod or (obj.parent and obj.parent.name in armature_names)

    return {
        "name": obj.name,
        "collection": obj.users_collection[0].name if obj.users_collection else "Scene Collection",
        "generic_name": _is_generic_name(obj.name),
        "vertex_count": len(mesh.vertices),
        "edge_count": len(mesh.edges),
        "face_count": len(mesh.polygons),
        "tri_count": tri_count_from_faces,
        "quad_count": quads,
        "tri_face_count": tris,
        "ngon_count": ngons,
        "interior_vert_count": len(interior_verts),
        "pole_vert_count": pole_verts,
        "connected_components": components,
        "dimensions": [round(float(d), 5) if math.isfinite(d) else 0.0 for d in dims],
        "scale": [round(float(v), 5) if math.isfinite(v) else 1.0 for v in obj.scale],
        "surface_area": round(float(surface_area), 6) if math.isfinite(surface_area) else 0.0,
        "non_manifold_edges": non_manifold_edges,
        "boundary_edges": boundary_edges,
        "multi_face_edges": multi_face_edges,
        "loose_verts": loose_verts,
        "loose_edges": loose_edges,
        "degenerate_faces": degenerate_faces,
        "zero_area_faces": zero_area_faces,
        "duplicate_verts": dup_verts,
        "inverted_normals_count": inv_normals,
        "has_uv": len(mesh.uv_layers) > 0,
        "uv_layer_count": len(mesh.uv_layers),
        "uv_mapped_faces": uv_info["mapped_faces"],
        "uv_coverage_cells": sorted(uv_info["coverage_cells"]),
        "uv_overlap_cells": sorted(uv_info["overlap_cells"]),
        "material_count": len(mat_names),
        "materials": mat_names,
        "unassigned_material_faces": unassigned_faces,
        "has_weights": bool(has_weights),
        "modifiers": [{"name": m.name, "type": m.type} for m in obj.modifiers],
    }


def _inspect_materials_and_textures(search_roots: list[Path]) -> dict[str, Any]:
    missing_deps: list[str] = []
    missing_textures: list[str] = []
    available_textures = 0
    texture_refs = 0

    for img in bpy.data.images:
        if img.source == "FILE":
            texture_refs += 1
            if img.packed_file is not None:
                available_textures += 1
                continue
            found = False
            if img.filepath:
                resolved = Path(bpy.path.abspath(img.filepath))
                if resolved.is_file():
                    found = True
                else:
                    # Also search relative basename under unpacked archive roots
                    fname = Path(img.filepath.replace("\\", "/")).name
                    for root in search_roots:
                        if root.is_dir() and any(root.rglob(fname)):
                            found = True
                            break
            if found:
                available_textures += 1
            else:
                msg = f"Missing texture '{img.name}': {img.filepath}"
                missing_textures.append(msg)
                missing_deps.append(msg)

    for lib in bpy.data.libraries:
        if lib.filepath:
            resolved = Path(bpy.path.abspath(lib.filepath))
            if not resolved.is_file():
                missing_deps.append(f"Missing linked library '{lib.name}': {lib.filepath}")

    proc_shaders = 0
    principled_count = 0
    node_counts: list[int] = []
    for mat in bpy.data.materials:
        if mat.is_grease_pencil:
            continue
        nt = getattr(mat, "node_tree", None)
        if nt is not None:
            nodes = list(nt.nodes)
            node_counts.append(len(nodes))
            if any(n.type == "BSDF_PRINCIPLED" for n in nodes):
                principled_count += 1
            has_proc = any(n.type.startswith("TEX_") and n.type != "TEX_IMAGE" for n in nodes)
            if has_proc:
                proc_shaders += 1

    return {
        "missing_dependencies": missing_deps,
        "missing_textures": missing_textures,
        "texture_reference_count": texture_refs,
        "available_image_textures": available_textures,
        "missing_image_textures": len(missing_textures),
        "procedural_shader_count": proc_shaders,
        "principled_bsdf_count": principled_count,
        "material_node_count_avg": round(sum(node_counts) / len(node_counts), 2) if node_counts else 0.0,
    }


def main() -> None:
    payload, out_json = parse_cli_args()
    try:
        source_path = Path(payload["source_path"]).resolve()
        search_roots = [Path(p).resolve() for p in payload.get("search_roots", []) if p]
        search_roots.append(source_path.parent)

        is_native, source_format, blend_header_ver = _open_or_import_isolated(source_path)

        mesh_objs = get_mesh_objects()
        armature_objs = [o for o in bpy.context.scene.objects if o.type == "ARMATURE"]
        armature_names = {o.name for o in armature_objs}
        bone_count = sum(len(o.data.bones) for o in armature_objs if o.data is not None)
        camera_objs = [o for o in bpy.context.scene.objects if o.type == "CAMERA"]
        light_objs = [o for o in bpy.context.scene.objects if o.type == "LIGHT"]

        bounds = compute_combined_bounds(mesh_objs)
        dims = bounds["dimensions"]
        obj_metrics = [_inspect_mesh_object(obj, armature_names) for obj in mesh_objs]
        mat_info = _inspect_materials_and_textures(search_roots)

        # Aggregate geometry & topology
        total_verts = sum(o["vertex_count"] for o in obj_metrics)
        total_edges = sum(o["edge_count"] for o in obj_metrics)
        total_faces = sum(o["face_count"] for o in obj_metrics)
        total_tris = sum(o["tri_count"] for o in obj_metrics)
        total_quads = sum(o["quad_count"] for o in obj_metrics)
        total_tri_faces = sum(o["tri_face_count"] for o in obj_metrics)
        total_ngons = sum(o["ngon_count"] for o in obj_metrics)
        total_components = sum(o["connected_components"] for o in obj_metrics)
        total_non_manifold = sum(o["non_manifold_edges"] for o in obj_metrics)
        total_boundary = sum(o["boundary_edges"] for o in obj_metrics)
        total_multi_face = sum(o["multi_face_edges"] for o in obj_metrics)
        total_loose_v = sum(o["loose_verts"] for o in obj_metrics)
        total_loose_e = sum(o["loose_edges"] for o in obj_metrics)
        total_degen = sum(o["degenerate_faces"] for o in obj_metrics)
        total_zero_area = sum(o["zero_area_faces"] for o in obj_metrics)
        total_dups = sum(o["duplicate_verts"] for o in obj_metrics)
        total_inv = sum(o["inverted_normals_count"] for o in obj_metrics)
        total_surface_area = sum(
            (o["surface_area"] if isinstance(o["surface_area"], (int, float)) and math.isfinite(o["surface_area"]) else 0.0)
            for o in obj_metrics
        )
        total_interior = sum(o["interior_vert_count"] for o in obj_metrics)
        total_poles = sum(o["pole_vert_count"] for o in obj_metrics)

        vol = max(dims[0], 0.01) * max(dims[1], 0.01) * max(dims[2], 0.01)
        density = round(total_tris / vol, 2) if total_tris > 0 else 0.0

        quad_ratio = round(total_quads / max(total_faces, 1), 4)
        tri_ratio = round(total_tri_faces / max(total_faces, 1), 4)
        ngon_ratio = round(total_ngons / max(total_faces, 1), 4)
        quad_to_tri = round(total_quads / max(total_tri_faces, 1), 4) if total_quads > 0 else 0.0
        pole_ratio = round(total_poles / max(total_interior, 1), 4) if total_interior > 0 else 0.0

        # Edge flow & subdivision readiness heuristics
        edge_flow_score = round(
            max(
                0.0,
                min(
                    100.0,
                    quad_ratio * 65.0 + max(0.0, 1.0 - ngon_ratio * 4.0) * 20.0 + max(0.0, 1.0 - pole_ratio * 2.0) * 15.0,
                ),
            ),
            2,
        )
        subdiv_ready = (
            total_faces > 0
            and total_multi_face == 0
            and total_zero_area == 0
            and total_loose_v == 0
            and quad_ratio >= 0.70
            and ngon_ratio <= 0.05
        )
        subdiv_notes = (
            "Quad-dominant clean manifold/flow suitable for subdivision."
            if subdiv_ready
            else f"Subdivision cautions: quad_ratio={quad_ratio:.2f}, ngon_ratio={ngon_ratio:.2f}, multi_face={total_multi_face}, zero_area={total_zero_area}."
        )

        # Cleanliness score (0..100)
        defect_penalty = (
            min(30.0, total_zero_area * 5.0)
            + min(20.0, (total_degen - total_zero_area) * 2.0)
            + min(20.0, (total_loose_v + total_loose_e) * 2.5)
            + min(15.0, (total_dups / max(total_verts, 1)) * 150.0)
            + min(15.0, (total_inv / max(total_faces, 1)) * 100.0)
        )
        mesh_cleanliness = round(max(0.0, 100.0 - defect_penalty), 2)

        # UV aggregation
        uv_layer_count = sum(o["uv_layer_count"] for o in obj_metrics)
        has_uv_map = any(o["has_uv"] for o in obj_metrics)
        uv_mapped_mesh_ratio = round(
            sum(1 for o in obj_metrics if o["has_uv"]) / max(len(obj_metrics), 1), 4
        )
        union_cells: set[int] = set()
        overlap_cells: set[int] = set()
        for o in obj_metrics:
            cset = set(o["uv_coverage_cells"])
            overlap_cells.update(union_cells.intersection(cset))
            overlap_cells.update(o["uv_overlap_cells"])
            union_cells.update(cset)
        uv_coverage = round(len(union_cells) / 4096.0, 4)
        uv_overlap_ratio = round(len(overlap_cells) / max(len(union_cells), 1), 4)
        uv_completeness = round(sum(o["uv_mapped_faces"] for o in obj_metrics) / max(total_faces, 1), 4)

        # Materials & Organization
        all_materials = sorted({m for o in obj_metrics for m in o["materials"]})
        unassigned_faces = sum(o["unassigned_material_faces"] for o in obj_metrics)
        named_objs = [o["name"] for o in obj_metrics if not o["generic_name"]]
        generic_objs = [o["name"] for o in obj_metrics if o["generic_name"]]
        naming_ratio = round(len(named_objs) / max(len(obj_metrics), 1), 4)
        collections = sorted({c.name for c in bpy.data.collections})
        unapplied_scales = [
            o["name"] for o in obj_metrics if any(abs(s - 1.0) > 0.02 for s in o["scale"])
        ]

        # Object separation & scene organization
        if len(obj_metrics) == 0:
            sep_score = 0.0
        elif len(obj_metrics) == 1 and total_faces > 50_000:
            sep_score = 55.0
        elif len(obj_metrics) > 200:
            sep_score = 65.0
        else:
            sep_score = 92.0 if len(obj_metrics) > 1 else 82.0

        org_score = round(
            naming_ratio * 45.0
            + (25.0 if len(collections) > 0 else 10.0)
            + (20.0 if not unapplied_scales else max(0.0, 20.0 - len(unapplied_scales) * 4.0))
            + (sep_score * 0.10),
            2,
        )

        # Animations & Rigging
        has_actions = len(bpy.data.actions) > 0
        weighted_meshes = sum(1 for o in obj_metrics if o["has_weights"])

        # Presentation (strictly separate from technical quality)
        positive_lights = [l for l in light_objs if getattr(l.data, "energy", 0.0) > 0.0]
        lighting_score = 0.0
        if positive_lights:
            lighting_score = min(100.0, 45.0 + len(positive_lights) * 20.0)
        elif bpy.context.scene.world is not None:
            lighting_score = 30.0
        renderable = len(mesh_objs) > 0 and bool(bpy.context.scene.render.engine)

        # Strip large cell lists from per-object summaries to keep JSON compact
        compact_objects = [
            {k: v for k, v in o.items() if k not in {"uv_coverage_cells", "uv_overlap_cells"}}
            for o in obj_metrics
        ]

        write_output(
            out_json,
            {
                "ok": True,
                "is_native_blender": is_native,
                "source_format": source_format,
                "blend_header_version": blend_header_ver,
                "blender_runtime_version": bpy.app.version_string,
                "file_opened_ok": True,
                "inspection_completed_ok": True,
                "object_count": len(bpy.data.objects),
                "mesh_object_count": len(mesh_objs),
                "non_mesh_object_count": len(bpy.data.objects) - len(mesh_objs),
                "vertex_count": total_verts,
                "edge_count": total_edges,
                "face_count": total_faces,
                "polygon_count": total_faces,
                "triangle_count": total_tris,
                "connected_components": total_components,
                "is_manifold": (total_multi_face == 0 and total_boundary == 0 and total_faces > 0),
                "non_manifold_edges": total_non_manifold,
                "boundary_edges": total_boundary,
                "multi_face_edges": total_multi_face,
                "loose_verts": total_loose_v,
                "loose_edges": total_loose_e,
                "degenerate_faces": total_degen,
                "zero_area_faces": total_zero_area,
                "duplicate_verts": total_dups,
                "inverted_normals_count": total_inv,
                "mesh_density_tris_per_m3": density if math.isfinite(density) else 0.0,
                "surface_area": round(total_surface_area, 4) if math.isfinite(total_surface_area) else 0.0,
                "dimensions": [round(d, 5) if math.isfinite(d) else 0.0 for d in dims],
                "quad_count": total_quads,
                "tri_face_count": total_tri_faces,
                "ngon_count": total_ngons,
                "quad_ratio": quad_ratio,
                "triangle_ratio": tri_ratio,
                "quad_to_tri_ratio": quad_to_tri,
                "ngon_ratio": ngon_ratio,
                "pole_vertex_ratio": pole_ratio,
                "edge_flow_score": edge_flow_score,
                "subdivision_ready": subdiv_ready,
                "subdivision_readiness_notes": subdiv_notes,
                "mesh_cleanliness_score": mesh_cleanliness,
                "has_uv_map": has_uv_map,
                "uv_layer_count": uv_layer_count,
                "uv_mapped_mesh_ratio": uv_mapped_mesh_ratio,
                "uv_coverage_ratio": uv_coverage,
                "uv_overlapping_islands_detected": uv_overlap_ratio > 0.05,
                "uv_overlap_cell_ratio": uv_overlap_ratio,
                "uv_completeness_ratio": uv_completeness,
                "material_count": len(all_materials),
                "material_names": all_materials,
                "unassigned_material_faces": unassigned_faces,
                "named_objects": named_objs,
                "generic_named_objects": generic_objs,
                "naming_consistency_ratio": naming_ratio,
                "collection_count": len(collections),
                "collections": collections,
                "object_separation_score": sep_score,
                "scene_organization_score": round(org_score, 2),
                "unapplied_scale_objects": unapplied_scales,
                "armature_count": len(armature_objs),
                "bone_count": bone_count,
                "has_rig": len(armature_objs) > 0 and bone_count > 0,
                "has_animations": has_actions,
                "action_count": len(bpy.data.actions),
                "weighted_mesh_count": weighted_meshes,
                "camera_count": len(camera_objs),
                "light_count": len(light_objs),
                "has_camera": len(camera_objs) > 0,
                "has_lighting": len(positive_lights) > 0,
                "lighting_quality_score": round(lighting_score, 2),
                "renderable": renderable,
                "objects": compact_objects,
                **mat_info,
            },
        )
    except Exception as exc:
        write_output(
            out_json,
            {
                "ok": False,
                "file_opened_ok": False,
                "inspection_completed_ok": False,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "traceback": traceback.format_exc(),
            },
        )


if __name__ == "__main__":
    main()
