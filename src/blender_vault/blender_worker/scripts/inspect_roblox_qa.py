"""Blender worker script: comprehensive Roblox 3D asset QA inspection inside headless Blender."""

from __future__ import annotations

import math
import sys
import traceback
from collections import deque
from pathlib import Path
from typing import Any, Optional

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import bmesh  # noqa: E402
import bpy  # noqa: E402
from mathutils import Vector, kdtree  # noqa: E402

from _common import (  # noqa: E402
    compute_combined_bounds,
    get_mesh_objects,
    parse_cli_args,
    write_output,
)

GENERIC_PREFIXES = (
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

COLLISION_PREFIXES = ("ucx_", "ubx_", "usp_", "col_", "collision_", "collider_")


def _is_collision_object(name: str) -> bool:
    low = name.strip().lower()
    return low.startswith(COLLISION_PREFIXES) or low.endswith("_col") or low.endswith("_collider")


def _is_generic_name(name: str) -> bool:
    base = name.strip().lower().split(".")[0]
    return base in GENERIC_PREFIXES


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


def _count_duplicate_faces(bm: bmesh.types.BMesh) -> int:
    seen: set[tuple[int, ...]] = set()
    dups = 0
    for face in bm.faces:
        sig = tuple(sorted(v.index for v in face.verts))
        if sig in seen:
            dups += 1
        else:
            seen.add(sig)
    return dups


def _count_inverted_or_invalid_normals(bm: bmesh.types.BMesh) -> tuple[int, int]:
    if len(bm.faces) == 0:
        return 0, 0
    invalid = 0
    for f in bm.faces:
        if (
            math.isnan(f.normal.x)
            or math.isnan(f.normal.y)
            or math.isnan(f.normal.z)
            or f.normal.length <= 1e-8
        ):
            invalid += 1
    if len(bm.faces) > 150_000:
        return 0, invalid
    bm_copy = bm.copy()
    orig_normals = [f.normal.copy() for f in bm_copy.faces]
    bmesh.ops.recalc_face_normals(bm_copy, faces=bm_copy.faces)
    flipped = 0
    for f, orig_n in zip(bm_copy.faces, orig_normals):
        if orig_n.length > 1e-6 and f.normal.length > 1e-6:
            if orig_n.dot(f.normal) < -0.5:
                flipped += 1
    bm_copy.free()
    return flipped, invalid


def _component_face_counts(bm: bmesh.types.BMesh) -> list[int]:
    if len(bm.verts) == 0:
        return []
    visited_v: set[int] = set()
    comp_faces: list[int] = []
    for v in bm.verts:
        if v.index in visited_v:
            continue
        q: deque[bmesh.types.BMVert] = deque([v])
        visited_v.add(v.index)
        faces_in_comp: set[int] = set()
        while q:
            cur = q.popleft()
            for f in cur.link_faces:
                faces_in_comp.add(f.index)
            for e in cur.link_edges:
                other = e.other_vert(cur)
                if other.index not in visited_v:
                    visited_v.add(other.index)
                    q.append(other)
        comp_faces.append(len(faces_in_comp))
    return sorted(comp_faces, reverse=True)


def _point_in_tri_strict_2d(
    p: tuple[float, float],
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
) -> bool:
    """Return True if 2D point p lies strictly inside triangle (a, b, c) via barycentric coordinates."""
    v0x, v0y = c[0] - a[0], c[1] - a[1]
    v1x, v1y = b[0] - a[0], b[1] - a[1]
    v2x, v2y = p[0] - a[0], p[1] - a[1]
    denom = v0x * v1y - v1x * v0y
    if abs(denom) <= 1e-12:
        return False
    u = (v2x * v1y - v1x * v2y) / denom
    v = (v0x * v2y - v2x * v0y) / denom
    return u > 0.02 and v > 0.02 and (u + v) < 0.98


def _rasterize_tri_cells(
    p0: tuple[float, float],
    p1: tuple[float, float],
    p2: tuple[float, float],
    grid_size: int,
) -> set[int]:
    """Rasterize a 2D UV triangle onto a grid_size x grid_size unit-[0,1] cell set."""
    mapped: list[tuple[float, float]] = []
    for u, v in (p0, p1, p2):
        wu = u % 1.0 if (u > 1.0 or u < 0.0) else u
        wv = v % 1.0 if (v > 1.0 or v < 0.0) else v
        mapped.append((max(0.0, min(0.9999, wu)), max(0.0, min(0.9999, wv))))

    a, b, c = mapped
    cells: set[int] = set()
    cx_cent = int(((a[0] + b[0] + c[0]) / 3.0) * grid_size)
    cy_cent = int(((a[1] + b[1] + c[1]) / 3.0) * grid_size)
    cells.add(cy_cent * grid_size + cx_cent)
    for px, py in mapped:
        cells.add(int(py * grid_size) * grid_size + int(px * grid_size))

    min_x = int(min(a[0], b[0], c[0]) * grid_size)
    max_x = int(max(a[0], b[0], c[0]) * grid_size)
    min_y = int(min(a[1], b[1], c[1]) * grid_size)
    max_y = int(max(a[1], b[1], c[1]) * grid_size)
    if (max_x - min_x) <= 1 and (max_y - min_y) <= 1:
        return cells

    def _sign(px: float, py: float, p1x: float, p1y: float, p2x: float, p2y: float) -> float:
        return (px - p2x) * (p1y - p2y) - (p1x - p2x) * (py - p2y)

    inv_g = 1.0 / grid_size
    for cy in range(min_y, max_y + 1):
        py = (cy + 0.5) * inv_g
        for cx in range(min_x, max_x + 1):
            px = (cx + 0.5) * inv_g
            d1 = _sign(px, py, a[0], a[1], b[0], b[1])
            d2 = _sign(px, py, b[0], b[1], c[0], c[1])
            d3 = _sign(px, py, c[0], c[1], a[0], a[1])
            has_neg = (d1 < -1e-7) or (d2 < -1e-7) or (d3 < -1e-7)
            has_pos = (d1 > 1e-7) or (d2 > 1e-7) or (d3 > 1e-7)
            if not (has_neg and has_pos):
                cells.add(cy * grid_size + cx)
    return cells


def _analyze_uvs(bm: bmesh.types.BMesh, grid_size: int = 64) -> dict[str, Any]:
    uv_layer = bm.loops.layers.uv.active
    face_count = len(bm.faces)
    if uv_layer is None or face_count == 0:
        return {
            "has_uv": uv_layer is not None,
            "mapped_faces": 0,
            "unmapped_faces": face_count,
            "invalid_coords": 0,
            "out_of_unit_range_loops": 0,
            "extreme_uv_loops": 0,
            "overlap_cell_ratio": 0.0,
            "self_overlap_cell_ratio": 0.0,
            "coverage_ratio": 0.0,
            "uv_island_count": 0,
            "flipped_uv_faces": 0,
            "stacked_island_pairs": 0,
            "uv_layout_mode": "MISSING",
        }

    mapped_faces = 0
    unmapped_faces = 0
    invalid_coords = 0
    out_of_unit = 0
    extreme_uv = 0
    flipped_uv_faces = 0

    for face in bm.faces:
        pts = [lp[uv_layer].uv for lp in face.loops]
        bad_face = False
        area2 = 0.0
        for i, uv in enumerate(pts):
            if math.isnan(uv.x) or math.isnan(uv.y) or math.isinf(uv.x) or math.isinf(uv.y):
                invalid_coords += 1
                bad_face = True
            if uv.x < -1e-4 or uv.x > 1.0001 or uv.y < -1e-4 or uv.y > 1.0001:
                out_of_unit += 1
            if abs(uv.x) > 4.0 or abs(uv.y) > 4.0:
                extreme_uv += 1
            nxt = pts[(i + 1) % len(pts)]
            area2 += uv.x * nxt.y - nxt.x * uv.y
        signed_area = area2 * 0.5
        if bad_face or abs(signed_area) <= 1e-8:
            unmapped_faces += 1
        else:
            mapped_faces += 1
            if signed_area < -1e-8:
                flipped_uv_faces += 1

    # Overlap, centroid-based self-overlap & island classification
    bm.faces.ensure_lookup_table()
    stride = 1 if face_count <= 25_000 else max(1, face_count // 25_000)
    face_to_island: dict[int, int] = {}
    island_faces: list[list[bmesh.types.BMFace]] = []

    if stride == 1:
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
                cur_uv = {lp.vert.index: lp[uv_layer].uv for lp in cur.loops}
                for edge in cur.edges:
                    for nf in edge.link_faces:
                        if nf.index in face_to_island:
                            continue
                        nf_uv = {lp.vert.index: lp[uv_layer].uv for lp in nf.loops}
                        v0, v1 = edge.verts[0].index, edge.verts[1].index
                        if (
                            v0 in cur_uv
                            and v0 in nf_uv
                            and v1 in cur_uv
                            and v1 in nf_uv
                            and (cur_uv[v0] - nf_uv[v0]).length < 1e-4
                            and (cur_uv[v1] - nf_uv[v1]).length < 1e-4
                        ):
                            face_to_island[nf.index] = iid
                            q.append(nf)
            island_faces.append(group)
    else:
        island_faces = [[bm.faces[i] for i in range(0, face_count, stride)]]

    union_cells: set[int] = set()
    overlap_cells: set[int] = set()
    total_checked_faces = 0
    self_overlapping_faces = 0
    island_unit_sets: list[set[int]] = []

    for group in island_faces:
        island_cells: set[int] = set()
        # Spatial hash of face triangles within this single island using strict face centroids (zero edge-sharing false positives)
        tri_records: list[tuple[int, tuple[float, float], tuple[float, float], tuple[float, float], tuple[float, float, float, float]]] = []
        centroids: list[tuple[int, float, float]] = []

        for f in group:
            uvs = [(float(lp[uv_layer].uv.x), float(lp[uv_layer].uv.y)) for lp in f.loops]
            if len(uvs) < 3:
                continue
            cx_f = sum(p[0] for p in uvs) / len(uvs)
            cy_f = sum(p[1] for p in uvs) / len(uvs)
            centroids.append((f.index, cx_f, cy_f))
            for k in range(1, len(uvs) - 1):
                a, b, c = uvs[0], uvs[k], uvs[k + 1]
                island_cells.update(_rasterize_tri_cells(a, b, c, grid_size))
                tri_records.append(
                    (
                        f.index,
                        a,
                        b,
                        c,
                        (min(a[0], b[0], c[0]), max(a[0], b[0], c[0]), min(a[1], b[1], c[1]), max(a[1], b[1], c[1])),
                    )
                )

        if 2 <= len(centroids) <= 4000:
            total_checked_faces += len(centroids)
            # Hash triangles into 16x16 world-UV spatial buckets inside the island bbox for O(1) self-overlap test
            buckets: dict[tuple[int, int], list[int]] = {}
            xs = [c[1] for c in centroids]
            ys = [c[2] for c in centroids]
            min_u, max_u = min(xs), max(xs)
            min_v, max_v = min(ys), max(ys)
            span_u = max(max_u - min_u, 1e-4)
            span_v = max(max_v - min_v, 1e-4)
            for t_idx, (_, _, _, _, bb) in enumerate(tri_records):
                bx0 = max(0, min(15, int(((bb[0] - min_u) / span_u) * 16)))
                bx1 = max(0, min(15, int(((bb[1] - min_u) / span_u) * 16)))
                by0 = max(0, min(15, int(((bb[2] - min_v) / span_v) * 16)))
                by1 = max(0, min(15, int(((bb[3] - min_v) / span_v) * 16)))
                for by in range(by0, by1 + 1):
                    for bx in range(bx0, bx1 + 1):
                        buckets.setdefault((bx, by), []).append(t_idx)

            for f_idx, px, py in centroids:
                bx = max(0, min(15, int(((px - min_u) / span_u) * 16)))
                by = max(0, min(15, int(((py - min_v) / span_v) * 16)))
                for t_idx in buckets.get((bx, by), ()):
                    tf_idx, a, b, c, bb = tri_records[t_idx]
                    if tf_idx == f_idx:
                        continue
                    if bb[0] <= px <= bb[1] and bb[2] <= py <= bb[3] and _point_in_tri_strict_2d((px, py), a, b, c):
                        self_overlapping_faces += 1
                        break

        overlap_cells.update(union_cells.intersection(island_cells))
        union_cells.update(island_cells)
        island_unit_sets.append(island_cells)

    # Count island pairs that stack cleanly onto the same UV footprint (mirrored/box-projected reuse)
    stacked_pairs = 0
    n_isl = len(island_unit_sets)
    if 2 <= n_isl <= 64:
        for i in range(n_isl):
            si = island_unit_sets[i]
            if not si:
                continue
            for j in range(i + 1, n_isl):
                sj = island_unit_sets[j]
                if not sj:
                    continue
                inter = len(si.intersection(sj))
                if inter > 0 and (inter / float(min(len(si), len(sj)))) >= 0.70:
                    stacked_pairs += 1

    overlap_ratio = round(len(overlap_cells) / max(len(union_cells), 1), 4)
    self_overlap_ratio = round(self_overlapping_faces / max(total_checked_faces, 1), 4) if total_checked_faces else 0.0

    if overlap_ratio <= 0.10 and self_overlap_ratio <= 0.05 and out_of_unit == 0:
        uv_layout_mode = "UNIQUE_PACKED"
    elif overlap_ratio <= 0.15 and self_overlap_ratio <= 0.05:
        uv_layout_mode = "UNIQUE_UNWRAPPED"
    elif out_of_unit > 0 and self_overlap_ratio <= 0.10:
        uv_layout_mode = "INTENTIONAL_BOX_OR_TILED"
    elif self_overlap_ratio > 0.12:
        uv_layout_mode = "TANGLED_OR_FOLDED"
    elif stacked_pairs > 0 or flipped_uv_faces > 0:
        uv_layout_mode = "MIRRORED_OR_STACKED"
    else:
        uv_layout_mode = "UNPACKED_OVERLAP"

    return {
        "has_uv": True,
        "mapped_faces": mapped_faces,
        "unmapped_faces": unmapped_faces,
        "invalid_coords": invalid_coords,
        "out_of_unit_range_loops": out_of_unit,
        "extreme_uv_loops": extreme_uv,
        "overlap_cell_ratio": overlap_ratio,
        "self_overlap_cell_ratio": self_overlap_ratio,
        "coverage_ratio": round(len(union_cells) / float(grid_size * grid_size), 4),
        "uv_island_count": len(island_faces),
        "flipped_uv_faces": flipped_uv_faces,
        "stacked_island_pairs": stacked_pairs,
        "uv_layout_mode": uv_layout_mode,
    }


def _inspect_materials() -> dict[str, Any]:
    materials_info: list[dict[str, Any]] = []
    missing_textures: list[str] = []
    missing_libraries: list[str] = []
    image_dims: list[tuple[int, int]] = []
    has_base_color_map = False
    has_normal_map = False
    has_roughness_map = False
    has_metallic_map = False
    procedural_count = 0
    unsupported_configs: list[str] = []

    for lib in bpy.data.libraries:
        if lib.filepath:
            resolved = Path(bpy.path.abspath(lib.filepath))
            if not resolved.is_file():
                missing_libraries.append(f"Missing linked library '{lib.name}': {lib.filepath}")

    for mat in bpy.data.materials:
        if getattr(mat, "is_grease_pencil", False):
            continue
        nt = getattr(mat, "node_tree", None)
        if nt is None:
            materials_info.append({"name": mat.name, "type": "legacy_no_nodes", "valid": True})
            continue

        bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
        if bsdf is None:
            unsupported_configs.append(f"Material '{mat.name}' lacks Principled BSDF shader node.")
        has_proc = any(n.type.startswith("TEX_") and n.type != "TEX_IMAGE" for n in nt.nodes)
        if has_proc:
            procedural_count += 1

        tex_nodes = [n for n in nt.nodes if n.type == "TEX_IMAGE"]
        mat_textures: list[dict[str, Any]] = []
        for tnode in tex_nodes:
            img = tnode.image
            if img is None:
                missing_textures.append(f"Material '{mat.name}' has Image Texture node '{tnode.name}' with no image assigned.")
                continue
            exists = True
            if img.source == "FILE" and img.packed_file is None:
                if not img.filepath:
                    exists = False
                else:
                    resolved = Path(bpy.path.abspath(img.filepath))
                    if not resolved.is_file():
                        exists = False
                if not exists:
                    missing_textures.append(f"Missing texture '{img.name}' in material '{mat.name}': {img.filepath}")

            w, h = int(img.size[0]), int(img.size[1])
            if w > 0 and h > 0:
                image_dims.append((w, h))

            # Inspect downstream socket names to identify PBR texture map slots
            connected_roles: list[str] = []
            for link in nt.links:
                if link.from_node == tnode:
                    tgt_name = link.to_socket.name.lower()
                    connected_roles.append(link.to_socket.name)
                    if "base color" in tgt_name or "color" in tgt_name:
                        has_base_color_map = True
                    elif "normal" in tgt_name or "height" in tgt_name:
                        has_normal_map = True
                    elif "roughness" in tgt_name:
                        has_roughness_map = True
                    elif "metallic" in tgt_name:
                        has_metallic_map = True
                elif link.to_node == bsdf and "normal" in link.to_socket.name.lower():
                    # Check 2-hop NormalMap node
                    if link.from_node.type == "NORMAL_MAP":
                        for l2 in nt.links:
                            if l2.to_node == link.from_node and l2.from_node == tnode:
                                has_normal_map = True

            mat_textures.append({
                "image_name": img.name,
                "filepath": img.filepath,
                "packed": img.packed_file is not None,
                "exists": exists,
                "width": w,
                "height": h,
                "roles": connected_roles,
            })

        materials_info.append({
            "name": mat.name,
            "has_principled_bsdf": bsdf is not None,
            "is_procedural": has_proc,
            "image_textures": mat_textures,
        })

    return {
        "materials": materials_info,
        "material_count": len(materials_info),
        "procedural_material_count": procedural_count,
        "image_texture_count": sum(len(m.get("image_textures", [])) for m in materials_info),
        "image_dimensions": image_dims,
        "missing_textures": missing_textures,
        "missing_libraries": missing_libraries,
        "has_base_color_map": has_base_color_map,
        "has_normal_map": has_normal_map,
        "has_roughness_map": has_roughness_map,
        "has_metallic_map": has_metallic_map,
        "unsupported_configs": unsupported_configs,
    }


def _check_missing_object_references() -> list[str]:
    issues: list[str] = []
    for obj in bpy.data.objects:
        if obj.type == "MESH" and obj.data is None:
            issues.append(f"Object '{obj.name}' has missing Mesh data reference.")
        for mod in obj.modifiers:
            for attr in ("object", "target", "mirror_object", "start_cap", "end_cap"):
                if hasattr(mod, attr):
                    val = getattr(mod, attr)
                    if val is not None and val.name not in bpy.data.objects:
                        issues.append(f"Modifier '{mod.name}' on '{obj.name}' references missing object.")
    return issues


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
    quads = 0
    tris_poly = 0
    ngons = 0
    surface_area = 0.0
    for face in bm.faces:
        vc = len(face.verts)
        if vc == 4:
            quads += 1
        elif vc == 3:
            tris_poly += 1
        elif vc > 4:
            ngons += 1
        area = face.calc_area()
        surface_area += area
        if area <= 1e-10:
            zero_area_faces += 1
            degenerate_faces += 1
        elif area <= 1e-8 or any(e.calc_length() <= 1e-7 for e in face.edges):
            degenerate_faces += 1

    dup_verts = _count_duplicate_verts(bm)
    dup_faces = _count_duplicate_faces(bm)
    inv_normals, invalid_normals = _count_inverted_or_invalid_normals(bm)
    comp_face_counts = _component_face_counts(bm)
    uv_stats = _analyze_uvs(bm)

    bm_tri = bm.copy()
    bmesh.ops.triangulate(bm_tri, faces=bm_tri.faces)
    tri_count = len(bm_tri.faces)
    signed_volume = float(bm_tri.calc_volume(signed=True)) if tri_count > 0 else 0.0
    bm_tri.free()
    bm.free()

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
    valid_slots = {idx for idx, slot in enumerate(obj.material_slots) if slot.material is not None}
    unassigned_faces = (
        len(mesh.polygons)
        if not valid_slots
        else sum(1 for p in mesh.polygons if p.material_index not in valid_slots)
    )
    col_name = obj.users_collection[0].name if obj.users_collection else "Scene Collection"

    return {
        "name": obj.name,
        "is_collision_mesh": _is_collision_object(obj.name),
        "generic_name": _is_generic_name(obj.name),
        "collection": col_name,
        "parent": obj.parent.name if obj.parent else None,
        "vertex_count": len(mesh.vertices),
        "edge_count": len(mesh.edges),
        "face_count": len(mesh.polygons),
        "tri_count": tri_count,
        "quad_count": quads,
        "tri_face_count": tris_poly,
        "ngon_count": ngons,
        "dimensions": [round(d, 5) for d in dims],
        "scale": [round(float(v), 5) for v in obj.scale],
        "rotation_euler": [round(float(v), 5) for v in obj.rotation_euler],
        "origin_location": [round(float(v), 5) for v in obj.location],
        "bbox_min": [round(v, 5) for v in bbox_min],
        "bbox_max": [round(v, 5) for v in bbox_max],
        "surface_area": round(float(surface_area), 6),
        "signed_volume": round(float(signed_volume), 6),
        "duplicate_verts": dup_verts,
        "duplicate_faces": dup_faces,
        "non_manifold_edges": non_manifold_edges,
        "boundary_edges": boundary_edges,
        "multi_face_edges": multi_face_edges,
        "degenerate_faces": degenerate_faces,
        "zero_area_faces": zero_area_faces,
        "inverted_normals_count": inv_normals,
        "invalid_normals_count": invalid_normals,
        "loose_verts": loose_verts,
        "loose_edges": loose_edges,
        "connected_components": len(comp_face_counts),
        "component_face_counts": comp_face_counts,
        "has_uv": uv_stats["has_uv"],
        "uv_layer_names": [uv.name for uv in mesh.uv_layers],
        "uv_stats": uv_stats,
        "material_count": len(mat_names),
        "materials": mat_names,
        "unassigned_material_faces": unassigned_faces,
    }


def main() -> None:
    payload, out_json = parse_cli_args()
    try:
        blend_path = Path(payload["blend_path"]).resolve()
        if not blend_path.is_file():
            raise FileNotFoundError(f"Blend file not found: {blend_path}")

        bpy.ops.wm.open_mainfile(filepath=str(blend_path))
        mesh_objs = get_mesh_objects()
        visible_objs = [o for o in mesh_objs if not _is_collision_object(o.name)]
        collision_objs = [o for o in mesh_objs if _is_collision_object(o.name)]

        bounds = compute_combined_bounds(visible_objs or mesh_objs)
        obj_metrics = [_inspect_mesh_object(obj) for obj in mesh_objs]
        mat_info = _inspect_materials()
        missing_obj_refs = _check_missing_object_references()
        collections = sorted({c.name for c in bpy.data.collections})

        visible_metrics = [o for o in obj_metrics if not o["is_collision_mesh"]]
        collision_metrics = [o for o in obj_metrics if o["is_collision_mesh"]]

        write_output(
            out_json,
            {
                "ok": True,
                "blender_version": bpy.app.version_string,
                "file_opened_ok": True,
                "unit_system": bpy.context.scene.unit_settings.system,
                "unit_scale_length": float(bpy.context.scene.unit_settings.scale_length),
                "object_count": len(bpy.data.objects),
                "mesh_object_count": len(visible_objs),
                "collision_object_count": len(collision_objs),
                "total_vertices": sum(o["vertex_count"] for o in visible_metrics),
                "total_edges": sum(o["edge_count"] for o in visible_metrics),
                "total_faces": sum(o["face_count"] for o in visible_metrics),
                "total_tris": sum(o["tri_count"] for o in visible_metrics),
                "dimensions": [round(d, 5) for d in bounds["dimensions"]],
                "bbox_min": [round(d, 5) for d in bounds["min"]],
                "bbox_max": [round(d, 5) for d in bounds["max"]],
                "collections": collections,
                "missing_object_references": missing_obj_refs,
                "objects": visible_metrics,
                "collision_objects": collision_metrics,
                "material_inspection": mat_info,
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
