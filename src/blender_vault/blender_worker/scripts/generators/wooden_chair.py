"""Procedural detailed wooden chair generator using bmesh and procedural wood shaders."""

from __future__ import annotations

import math
from typing import Any

import bmesh
import bpy
from mathutils import Matrix, Vector

from _common import (
    assign_box_uv,
    create_procedural_wood_material,
    ensure_asset_collection,
    finalize_mesh_object,
    link_object_exclusively,
)


WOOD_PALETTES: dict[str, tuple[tuple[float, float, float, float], tuple[float, float, float, float]]] = {
    "oak": ((0.50, 0.30, 0.14, 1.0), (0.26, 0.14, 0.06, 1.0)),
    "walnut": ((0.24, 0.13, 0.07, 1.0), (0.10, 0.05, 0.025, 1.0)),
    "cherry": ((0.46, 0.18, 0.09, 1.0), (0.22, 0.07, 0.03, 1.0)),
    "pine": ((0.68, 0.50, 0.27, 1.0), (0.45, 0.29, 0.13, 1.0)),
    "ebony": ((0.11, 0.09, 0.08, 1.0), (0.04, 0.035, 0.03, 1.0)),
}


def _make_lofted_tube(
    bm: bmesh.types.BMesh,
    rings: list[tuple[Vector, float, float]],
    *,
    sides: int = 12,
) -> None:
    """Create a closed watertight lofted column along center points with (rx, ry) radii."""
    ring_verts: list[list[bmesh.types.BMVert]] = []
    for idx, (center, rx, ry) in enumerate(rings):
        if idx < len(rings) - 1:
            tangent = (rings[idx + 1][0] - center).normalized()
        else:
            tangent = (center - rings[idx - 1][0]).normalized()
        up_ref = Vector((1.0, 0.0, 0.0)) if abs(tangent.x) < 0.8 else Vector((0.0, 1.0, 0.0))
        side_x = up_ref.cross(tangent).normalized()
        side_y = tangent.cross(side_x).normalized()

        verts = []
        for s in range(sides):
            ang = 2.0 * math.pi * s / sides
            pt = center + side_x * (math.cos(ang) * rx) + side_y * (math.sin(ang) * ry)
            verts.append(bm.verts.new(pt))
        ring_verts.append(verts)

    for r_idx in range(len(ring_verts) - 1):
        r0 = ring_verts[r_idx]
        r1 = ring_verts[r_idx + 1]
        for s in range(sides):
            ns = (s + 1) % sides
            bm.faces.new((r0[s], r0[ns], r1[ns], r1[s]))

    bm.faces.new(list(reversed(ring_verts[0])))
    bm.faces.new(ring_verts[-1])


def _make_curved_plank(
    bm: bmesh.types.BMesh,
    *,
    x_half: float,
    z_bottom: float,
    z_top: float,
    y_center: float,
    y_curve_depth: float,
    thickness: float,
    segments_x: int = 14,
    segments_z: int = 3,
    recline_slope_y_per_z: float = 0.0,
    z_ref: float = 0.45,
) -> None:
    """Create a smooth curved watertight rectangular board (crest rail, lumbar rail, or slat)."""
    half_t = thickness * 0.5
    # Build front and back surface grids, then stitch perimeter
    front_grid: list[list[bmesh.types.BMVert]] = []
    back_grid: list[list[bmesh.types.BMVert]] = []

    for iz in range(segments_z + 1):
        fz = iz / segments_z
        z = z_bottom + fz * (z_top - z_bottom)
        recline_y = (z - z_ref) * recline_slope_y_per_z
        row_f: list[bmesh.types.BMVert] = []
        row_b: list[bmesh.types.BMVert] = []
        for ix in range(segments_x + 1):
            u = (ix / segments_x) * 2.0 - 1.0  # -1..+1
            x = u * x_half
            # Parabolic/circular curve bowing backward (+Y) in the center for ergonomic cradle
            curve_y = y_curve_depth * (1.0 - u * u)
            # Subtle crown arch along top edge if crest rail
            cy = y_center + recline_y + curve_y
            row_f.append(bm.verts.new(Vector((x, cy - half_t, z))))
            row_b.append(bm.verts.new(Vector((x, cy + half_t, z))))
        front_grid.append(row_f)
        back_grid.append(row_b)

    # Front & Back faces
    for iz in range(segments_z):
        for ix in range(segments_x):
            bm.faces.new((front_grid[iz][ix], front_grid[iz][ix + 1], front_grid[iz + 1][ix + 1], front_grid[iz + 1][ix]))
            bm.faces.new((back_grid[iz][ix + 1], back_grid[iz][ix], back_grid[iz + 1][ix], back_grid[iz + 1][ix + 1]))

    # Bottom & Top rims
    for ix in range(segments_x):
        bm.faces.new((front_grid[0][ix + 1], front_grid[0][ix], back_grid[0][ix], back_grid[0][ix + 1]))
        bm.faces.new((front_grid[segments_z][ix], front_grid[segments_z][ix + 1], back_grid[segments_z][ix + 1], back_grid[segments_z][ix]))

    # Left & Right rims
    for iz in range(segments_z):
        bm.faces.new((front_grid[iz][0], front_grid[iz + 1][0], back_grid[iz + 1][0], back_grid[iz][0]))
        bm.faces.new((front_grid[iz + 1][segments_x], front_grid[iz][segments_x], back_grid[iz][segments_x], back_grid[iz + 1][segments_x]))


def _assign_packed_smart_uv(obj: bpy.types.Object) -> None:
    """Create a non-overlapping [0, 1] Smart UV Project unwrap suitable for unique PBR baking."""
    mesh = obj.data
    if not mesh or not mesh.polygons:
        return
    if not mesh.uv_layers:
        mesh.uv_layers.new(name="UVMap")
    bpy.ops.object.select_all(action="DESELECT")
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    try:
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.uv.smart_project(angle_limit=math.radians(66.0), island_margin=0.025)
    finally:
        if bpy.context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        obj.select_set(False)


def _create_mesh_obj(
    name: str,
    bm: bmesh.types.BMesh,
    collection: bpy.types.Collection,
    material: bpy.types.Material,
    *,
    bevel_width: float = 0.0,
    bevel_segments: int = 2,
    bevel_sharp_only: bool = False,
    bevel_min_angle_deg: float = 28.0,
    clean_uv_pack: bool = False,
) -> bpy.types.Object:
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    if bevel_width > 0.0:
        if bevel_sharp_only:
            min_rad = math.radians(bevel_min_angle_deg)
            bevel_edges = [
                e for e in bm.edges
                if len(e.link_faces) == 2 and e.calc_face_angle(0.0) >= min_rad
            ]
        else:
            bevel_edges = list(bm.edges)
        if bevel_edges:
            bmesh.ops.bevel(
                bm,
                geom=bevel_edges,
                offset=bevel_width,
                segments=bevel_segments,
                profile=0.5,
                affect="EDGES",
                clamp_overlap=True,
            )
            bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=5e-5)
            bmesh.ops.recalc_face_normals(bm, faces=bm.faces)

    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()

    obj = bpy.data.objects.new(name, mesh)
    link_object_exclusively(obj, collection)
    obj.data.materials.append(material)
    if clean_uv_pack:
        _assign_packed_smart_uv(obj)
    else:
        assign_box_uv(obj, scale=2.0)
    finalize_mesh_object(obj, recalc_normals=True, smooth_angle_deg=35.0, box_uv=not clean_uv_pack)
    return obj


def generate_wooden_chair(asset_name: str, params: dict[str, Any]) -> dict[str, Any]:
    """Build a detailed four-legged wooden chair with curved backrest & wood shader."""
    wood_tone = str(params.get("wood_tone", "oak")).lower()
    light_col, dark_col = WOOD_PALETTES.get(wood_tone, WOOD_PALETTES["oak"])
    slat_count = max(1, min(5, int(params.get("slat_count", 3))))
    seat_width = float(params.get("seat_width", 0.45))
    seat_depth = float(params.get("seat_depth", 0.43))
    seat_height = float(params.get("seat_height", 0.45))
    back_height = float(params.get("back_height", 0.89))
    optimize_topology = bool(params.get("optimize_topology", False))
    complexity = str(params.get("complexity", "medium")).lower()
    clean_uv_pack = bool(params.get("clean_uv_pack", optimize_topology))

    col = ensure_asset_collection(f"Asset_{asset_name.replace(' ', '_')}")
    wood_mat = create_procedural_wood_material(
        name=f"Wood_{wood_tone.title()}",
        light_color=light_col,
        dark_color=dark_col,
        roughness_base=0.34,
        grain_scale=7.5,
    )

    created_objs: list[bpy.types.Object] = []
    hw = seat_width * 0.5
    hd = seat_depth * 0.5

    # 1. Contoured beveled seat panel
    bm_seat = bmesh.new()
    nx, ny = (8, 8) if optimize_topology else ((16, 16) if complexity == "high" else (12, 12))
    seat_thick = 0.032
    top_grid: list[list[bmesh.types.BMVert]] = []
    bot_grid: list[list[bmesh.types.BMVert]] = []
    for iy in range(ny + 1):
        vy = (iy / ny) * 2.0 - 1.0  # -1 front .. +1 back
        # Taper slightly narrower toward rear
        row_taper = 1.0 - 0.08 * ((vy + 1.0) * 0.5)
        y = vy * hd
        row_top: list[bmesh.types.BMVert] = []
        row_bot: list[bmesh.types.BMVert] = []
        for ix in range(nx + 1):
            vx = (ix / nx) * 2.0 - 1.0
            x = vx * hw * row_taper
            # Round outer corners softly
            corner_r = max(0.0, (vx * vx + vy * vy) - 1.55)
            y_adj = y - math.copysign(corner_r * 0.015, y)
            x_adj = x - math.copysign(corner_r * 0.015, x)
            # Subtle ergonomic saddle scoop in center top
            scoop = -0.008 * max(0.0, 1.0 - (vx * vx * 1.15 + (vy - 0.08) ** 2 * 1.25))
            # Waterfall front edge bevel drop
            waterfall = -0.006 * max(0.0, (-vy - 0.75) / 0.25) ** 2
            row_top.append(bm_seat.verts.new(Vector((x_adj, y_adj, seat_height + scoop + waterfall))))
            row_bot.append(bm_seat.verts.new(Vector((x_adj, y_adj, seat_height - seat_thick))))
        top_grid.append(row_top)
        bot_grid.append(row_bot)

    for iy in range(ny):
        for ix in range(nx):
            bm_seat.faces.new((top_grid[iy][ix], top_grid[iy][ix + 1], top_grid[iy + 1][ix + 1], top_grid[iy + 1][ix]))
            bm_seat.faces.new((bot_grid[iy][ix + 1], bot_grid[iy][ix], bot_grid[iy + 1][ix], bot_grid[iy + 1][ix + 1]))
    for ix in range(nx):
        bm_seat.faces.new((bot_grid[0][ix], bot_grid[0][ix + 1], top_grid[0][ix + 1], top_grid[0][ix]))
        bm_seat.faces.new((bot_grid[ny][ix + 1], bot_grid[ny][ix], top_grid[ny][ix], top_grid[ny][ix + 1]))
    for iy in range(ny):
        bm_seat.faces.new((bot_grid[iy + 1][0], bot_grid[iy][0], top_grid[iy][0], top_grid[iy + 1][0]))
        bm_seat.faces.new((bot_grid[iy][nx], bot_grid[iy + 1][nx], top_grid[iy + 1][nx], top_grid[iy][nx]))

    created_objs.append(
        _create_mesh_obj(
            "Chair_Seat",
            bm_seat,
            col,
            wood_mat,
            bevel_width=0.004,
            bevel_segments=1 if optimize_topology else 2,
            bevel_sharp_only=optimize_topology,
            clean_uv_pack=clean_uv_pack,
        )
    )

    # 2. Front tapered splayed legs
    leg_sides = 12 if optimize_topology else 14
    leg_top_z = seat_height - seat_thick * 0.4
    for side_label, sign in (("Left", -1.0), ("Right", 1.0)):
        bm_fleg = bmesh.new()
        _make_lofted_tube(
            bm_fleg,
            [
                (Vector((sign * (hw - 0.032), -hd + 0.035, 0.0)), 0.013, 0.013),
                (Vector((sign * (hw - 0.035), -hd + 0.042, 0.16)), 0.016, 0.016),
                (Vector((sign * (hw - 0.040), -hd + 0.050, leg_top_z)), 0.020, 0.020),
            ],
            sides=leg_sides,
        )
        created_objs.append(
            _create_mesh_obj(
                f"Chair_Leg_Front_{side_label}",
                bm_fleg,
                col,
                wood_mat,
                clean_uv_pack=clean_uv_pack,
            )
        )

    # 3. Continuous Rear Legs + Reclined Backrest Stiles
    stile_recline = 0.21  # backward +Y slope per meter above seat
    for side_label, sign in (("Left", -1.0), ("Right", 1.0)):
        bm_rleg = bmesh.new()
        x_foot = sign * (hw - 0.042)
        x_seat = sign * (hw - 0.052)
        x_top = sign * (hw - 0.054)
        y_seat = hd - 0.038
        rings = [
            (Vector((x_foot, y_seat + 0.030, 0.0)), 0.014, 0.015),
            (Vector((x_seat, y_seat + 0.012, 0.18)), 0.017, 0.018),
            (Vector((x_seat, y_seat, seat_height - 0.01)), 0.019, 0.021),
            (Vector((x_top, y_seat + 0.18 * stile_recline, seat_height + 0.18)), 0.017, 0.020),
            (Vector((x_top, y_seat + 0.34 * stile_recline, seat_height + 0.34)), 0.016, 0.019),
            (Vector((x_top, y_seat + (back_height - seat_height) * stile_recline, back_height)), 0.015, 0.018),
        ]
        _make_lofted_tube(bm_rleg, rings, sides=leg_sides)
        created_objs.append(
            _create_mesh_obj(
                f"Chair_Stile_Rear_{side_label}",
                bm_rleg,
                col,
                wood_mat,
                clean_uv_pack=clean_uv_pack,
            )
        )

    # 4. Under-seat Aprons & H-Stretchers
    bm_frame = bmesh.new()
    apron_z = seat_height - seat_thick - 0.020
    # Front, rear, left, right aprons
    apron_specs = [
        (Vector((-hw + 0.045, -hd + 0.052, apron_z)), Vector((hw - 0.045, -hd + 0.052, apron_z)), 0.010, 0.018),
        (Vector((-hw + 0.055, hd - 0.036, apron_z)), Vector((hw - 0.055, hd - 0.036, apron_z)), 0.010, 0.018),
        (Vector((-hw + 0.048, -hd + 0.058, apron_z)), Vector((-hw + 0.055, hd - 0.044, apron_z)), 0.010, 0.018),
        (Vector((hw - 0.048, -hd + 0.058, apron_z)), Vector((hw - 0.055, hd - 0.044, apron_z)), 0.010, 0.018),
        # Side H-stretchers at Z=0.15
        (Vector((-hw + 0.036, -hd + 0.042, 0.15)), Vector((-hw + 0.050, hd - 0.024, 0.15)), 0.009, 0.011),
        (Vector((hw - 0.036, -hd + 0.042, 0.15)), Vector((hw - 0.050, hd - 0.024, 0.15)), 0.009, 0.011),
        # Center cross-stretcher
        (Vector((-hw + 0.044, 0.01, 0.15)), Vector((hw - 0.044, 0.01, 0.15)), 0.009, 0.011),
    ]
    for p0, p1, rx, ry in apron_specs:
        _make_lofted_tube(bm_frame, [(p0, rx, ry), (p1, rx, ry)], sides=8)
    created_objs.append(
        _create_mesh_obj(
            "Chair_Aprons_And_Stretchers",
            bm_frame,
            col,
            wood_mat,
            clean_uv_pack=clean_uv_pack,
        )
    )

    # 5. Curved Backrest Crest Rail, Lower Lumbar Rail, and Curved Slats
    y_stile_seat = hd - 0.038
    bm_crest = bmesh.new()
    _make_curved_plank(
        bm_crest,
        x_half=hw - 0.050,
        z_bottom=back_height - 0.085,
        z_top=back_height - 0.012,
        y_center=y_stile_seat,
        y_curve_depth=0.042,
        thickness=0.024,
        segments_x=12 if optimize_topology else 16,
        segments_z=2 if optimize_topology else 4,
        recline_slope_y_per_z=stile_recline,
        z_ref=seat_height,
    )
    created_objs.append(
        _create_mesh_obj(
            "Chair_Backrest_Crest_Rail",
            bm_crest,
            col,
            wood_mat,
            bevel_width=0.003,
            bevel_segments=1 if optimize_topology else 2,
            bevel_sharp_only=optimize_topology,
            clean_uv_pack=clean_uv_pack,
        )
    )

    bm_lumbar = bmesh.new()
    _make_curved_plank(
        bm_lumbar,
        x_half=hw - 0.052,
        z_bottom=seat_height + 0.095,
        z_top=seat_height + 0.135,
        y_center=y_stile_seat,
        y_curve_depth=0.038,
        thickness=0.020,
        segments_x=10 if optimize_topology else 14,
        segments_z=2,
        recline_slope_y_per_z=stile_recline,
        z_ref=seat_height,
    )
    created_objs.append(
        _create_mesh_obj(
            "Chair_Backrest_Lumbar_Rail",
            bm_lumbar,
            col,
            wood_mat,
            bevel_width=0.002,
            bevel_segments=1 if optimize_topology else 2,
            bevel_sharp_only=optimize_topology,
            clean_uv_pack=clean_uv_pack,
        )
    )

    # Vertical curved slats spanning from lumbar rail up into crest rail
    bm_slats = bmesh.new()
    span_x = (hw - 0.085)
    for idx in range(slat_count):
        if slat_count == 1:
            cx = 0.0
            slat_half_w = 0.042
        else:
            t = (idx / (slat_count - 1)) * 2.0 - 1.0
            cx = t * (span_x * 0.72)
            slat_half_w = 0.026
        # Match the parabolic curve Y offset at cx so slats embed cleanly inside rails!
        u_rail = cx / (hw - 0.050)
        y_offset = 0.040 * (1.0 - u_rail * u_rail)
        # Build angled slat centered at cx
        s_bm = bmesh.new()
        _make_curved_plank(
            s_bm,
            x_half=slat_half_w,
            z_bottom=seat_height + 0.125,
            z_top=back_height - 0.070,
            y_center=y_stile_seat + y_offset,
            y_curve_depth=0.005,
            thickness=0.013,
            segments_x=2 if optimize_topology else 4,
            segments_z=5 if optimize_topology else 8,
            recline_slope_y_per_z=stile_recline,
            z_ref=seat_height,
        )
        # Translate slat to cx and merge into bm_slats
        bmesh.ops.translate(s_bm, verts=s_bm.verts, vec=Vector((cx, 0.0, 0.0)))
        temp_mesh = bpy.data.meshes.new("_tmp_slat")
        s_bm.to_mesh(temp_mesh)
        s_bm.free()
        bm_slats.from_mesh(temp_mesh)
        bpy.data.meshes.remove(temp_mesh)

    created_objs.append(
        _create_mesh_obj(
            "Chair_Backrest_Slats",
            bm_slats,
            col,
            wood_mat,
            bevel_width=0.002,
            bevel_segments=1 if optimize_topology else 2,
            bevel_sharp_only=optimize_topology,
            clean_uv_pack=clean_uv_pack,
        )
    )

    return {
        "template_id": "wooden_chair",
        "collection": col.name,
        "object_names": [o.name for o in created_objs],
        "materials": [wood_mat.name],
        "parameters_used": {
            "wood_tone": wood_tone,
            "slat_count": slat_count,
            "seat_width": seat_width,
            "seat_depth": seat_depth,
            "seat_height": seat_height,
            "back_height": back_height,
            "optimize_topology": optimize_topology,
            "clean_uv_pack": clean_uv_pack,
        },
    }
