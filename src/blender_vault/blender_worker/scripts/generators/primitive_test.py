"""Clean procedural templates: calibration primitives, table, mechanical gear, vase, and botanical leaf."""

from __future__ import annotations

import math
from typing import Any

import bmesh
import bpy
from mathutils import Vector

from _common import (
    assign_box_uv,
    create_principled_material,
    create_procedural_wood_material,
    ensure_asset_collection,
    finalize_mesh_object,
    link_object_exclusively,
)


def generate_test_cube(asset_name: str, params: dict[str, Any]) -> dict[str, Any]:
    size = float(params.get("size", 1.0))
    col = ensure_asset_collection(f"Asset_{asset_name.replace(' ', '_')}")
    mat = create_principled_material("Calibrated_Clay_Matte", base_color=(0.56, 0.60, 0.65, 1.0), roughness=0.45)

    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=size)
    bmesh.ops.translate(bm, verts=bm.verts, vec=Vector((0.0, 0.0, size * 0.5)))
    bmesh.ops.bevel(bm, geom=list(bm.edges), offset=size * 0.03, segments=2, affect="EDGES", clamp_overlap=True)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)

    mesh = bpy.data.meshes.new("Test_Cube_Mesh")
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new("Calibrated_Test_Cube", mesh)
    link_object_exclusively(obj, col)
    obj.data.materials.append(mat)
    assign_box_uv(obj)
    finalize_mesh_object(obj, smooth_angle_deg=35.0)
    return {"template_id": "test_cube", "collection": col.name, "object_names": [obj.name], "materials": [mat.name]}


def generate_test_uv_sphere(asset_name: str, params: dict[str, Any]) -> dict[str, Any]:
    radius = float(params.get("radius", 0.5))
    col = ensure_asset_collection(f"Asset_{asset_name.replace(' ', '_')}")
    mat = create_principled_material("Calibrated_Ceramic_Blue", base_color=(0.20, 0.44, 0.72, 1.0), roughness=0.22)

    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=32, v_segments=16, radius=radius)
    bmesh.ops.translate(bm, verts=bm.verts, vec=Vector((0.0, 0.0, radius)))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)

    mesh = bpy.data.meshes.new("Test_UVSphere_Mesh")
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new("Calibrated_UV_Sphere", mesh)
    link_object_exclusively(obj, col)
    obj.data.materials.append(mat)
    assign_box_uv(obj)
    finalize_mesh_object(obj, smooth_angle_deg=45.0)
    return {"template_id": "test_uv_sphere", "collection": col.name, "object_names": [obj.name], "materials": [mat.name]}


def generate_mechanical_gear(asset_name: str, params: dict[str, Any]) -> dict[str, Any]:
    """Generate a watertight precision spur gear with teeth, recessed web, and central bore."""
    teeth = max(8, min(36, int(params.get("teeth", 16))))
    outer_r = float(params.get("outer_radius", 0.25))
    thickness = float(params.get("thickness", 0.06))
    bore_r = outer_r * 0.24
    root_r = outer_r * 0.80

    col = ensure_asset_collection(f"Asset_{asset_name.replace(' ', '_')}")
    mat = create_principled_material(
        "Brushed_Steel",
        base_color=(0.64, 0.67, 0.70, 1.0),
        roughness=0.26,
        metallic=0.92,
    )

    bm = bmesh.new()
    steps_per_tooth = 4
    total_steps = teeth * steps_per_tooth
    top_outer: list[bmesh.types.BMVert] = []
    bot_outer: list[bmesh.types.BMVert] = []
    top_bore: list[bmesh.types.BMVert] = []
    bot_bore: list[bmesh.types.BMVert] = []

    for i in range(total_steps):
        ang = 2.0 * math.pi * i / total_steps
        phase = i % steps_per_tooth
        r = outer_r if phase in (1, 2) else root_r
        # Slight involute-style tooth taper
        ang_adj = ang
        if phase == 1:
            ang_adj += 0.018
        elif phase == 2:
            ang_adj -= 0.018
        x, y = r * math.cos(ang_adj), r * math.sin(ang_adj)
        top_outer.append(bm.verts.new(Vector((x, y, thickness))))
        bot_outer.append(bm.verts.new(Vector((x, y, 0.0))))
        bx, by = bore_r * math.cos(ang), bore_r * math.sin(ang)
        top_bore.append(bm.verts.new(Vector((bx, by, thickness))))
        bot_bore.append(bm.verts.new(Vector((bx, by, 0.0))))

    for i in range(total_steps):
        ni = (i + 1) % total_steps
        # Outer rim
        bm.faces.new((bot_outer[i], bot_outer[ni], top_outer[ni], top_outer[i]))
        # Bore inner rim
        bm.faces.new((top_bore[i], top_bore[ni], bot_bore[ni], bot_bore[i]))
        # Top cap ring
        bm.faces.new((top_bore[i], top_outer[i], top_outer[ni], top_bore[ni]))
        # Bottom cap ring
        bm.faces.new((bot_outer[i], bot_bore[i], bot_bore[ni], bot_outer[ni]))

    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bmesh.ops.bevel(bm, geom=list(bm.edges), offset=0.0025, segments=1, affect="EDGES", clamp_overlap=True)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)

    mesh = bpy.data.meshes.new("Spur_Gear_Mesh")
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new("Spur_Gear", mesh)
    link_object_exclusively(obj, col)
    obj.data.materials.append(mat)
    assign_box_uv(obj, scale=2.5)
    finalize_mesh_object(obj, smooth_angle_deg=25.0)
    return {"template_id": "mechanical_gear", "collection": col.name, "object_names": [obj.name], "materials": [mat.name]}


def generate_wooden_table(asset_name: str, params: dict[str, Any]) -> dict[str, Any]:
    width = float(params.get("width", 1.20))
    depth = float(params.get("depth", 0.75))
    height = float(params.get("height", 0.74))
    col = ensure_asset_collection(f"Asset_{asset_name.replace(' ', '_')}")
    mat = create_procedural_wood_material("Wood_Walnut_Table", light_color=(0.35, 0.19, 0.09, 1.0), dark_color=(0.16, 0.08, 0.035, 1.0))

    objs: list[bpy.types.Object] = []
    # Tabletop
    bm_top = bmesh.new()
    bmesh.ops.create_cube(bm_top, size=1.0)
    bmesh.ops.scale(bm_top, vec=Vector((width, depth, 0.04)), verts=bm_top.verts)
    bmesh.ops.translate(bm_top, vec=Vector((0.0, 0.0, height - 0.02)), verts=bm_top.verts)
    bmesh.ops.bevel(bm_top, geom=list(bm_top.edges), offset=0.006, segments=2, affect="EDGES", clamp_overlap=True)
    bmesh.ops.recalc_face_normals(bm_top, faces=bm_top.faces)
    m_top = bpy.data.meshes.new("Table_Top_Mesh")
    bm_top.to_mesh(m_top)
    bm_top.free()
    o_top = bpy.data.objects.new("Table_Top", m_top)
    link_object_exclusively(o_top, col)
    o_top.data.materials.append(mat)
    assign_box_uv(o_top)
    finalize_mesh_object(o_top)
    objs.append(o_top)

    # Four legs
    leg_h = height - 0.04
    lx = width * 0.5 - 0.07
    ly = depth * 0.5 - 0.07
    for label, sx, sy in (("FL", -1, -1), ("FR", 1, -1), ("RL", -1, 1), ("RR", 1, 1)):
        bm_leg = bmesh.new()
        bmesh.ops.create_cone(
            bm_leg,
            cap_ends=True,
            cap_tris=False,
            segments=12,
            radius1=0.022,
            radius2=0.032,
            depth=leg_h,
        )
        bmesh.ops.translate(bm_leg, vec=Vector((sx * lx, sy * ly, leg_h * 0.5)), verts=bm_leg.verts)
        bmesh.ops.recalc_face_normals(bm_leg, faces=bm_leg.faces)
        m_leg = bpy.data.meshes.new(f"Table_Leg_{label}_Mesh")
        bm_leg.to_mesh(m_leg)
        bm_leg.free()
        o_leg = bpy.data.objects.new(f"Table_Leg_{label}", m_leg)
        link_object_exclusively(o_leg, col)
        o_leg.data.materials.append(mat)
        assign_box_uv(o_leg)
        finalize_mesh_object(o_leg)
        objs.append(o_leg)

    return {"template_id": "wooden_table", "collection": col.name, "object_names": [o.name for o in objs], "materials": [mat.name]}


def generate_ceramic_vase(asset_name: str, params: dict[str, Any]) -> dict[str, Any]:
    """Generate a revolved watertight ceramic vase with wall thickness."""
    height = float(params.get("height", 0.34))
    col = ensure_asset_collection(f"Asset_{asset_name.replace(' ', '_')}")
    mat = create_principled_material("Ceramic_Glaze_Sage", base_color=(0.30, 0.48, 0.42, 1.0), roughness=0.14)

    profile = [
        (0.0, 0.0),
        (0.055, 0.0),
        (0.085, height * 0.25),
        (0.092, height * 0.50),
        (0.052, height * 0.78),
        (0.062, height),
        (0.052, height),
        (0.042, height * 0.78),
        (0.080, height * 0.50),
        (0.072, height * 0.25),
        (0.044, 0.015),
        (0.0, 0.015),
    ]
    segs = 32
    bm = bmesh.new()
    rings: list[list[bmesh.types.BMVert]] = []
    center_bot = bm.verts.new(Vector((0.0, 0.0, 0.0)))
    center_in = bm.verts.new(Vector((0.0, 0.0, 0.015)))

    inner_profiles = profile[1:-1]
    for r, z in inner_profiles:
        ring = []
        for s in range(segs):
            ang = 2.0 * math.pi * s / segs
            ring.append(bm.verts.new(Vector((r * math.cos(ang), r * math.sin(ang), z))))
        rings.append(ring)

    for i in range(len(rings) - 1):
        for s in range(segs):
            ns = (s + 1) % segs
            bm.faces.new((rings[i][s], rings[i][ns], rings[i + 1][ns], rings[i + 1][s]))
    for s in range(segs):
        ns = (s + 1) % segs
        bm.faces.new((center_bot, rings[0][ns], rings[0][s]))
        bm.faces.new((center_in, rings[-1][s], rings[-1][ns]))

    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    mesh = bpy.data.meshes.new("Ceramic_Vase_Mesh")
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new("Ceramic_Vase", mesh)
    link_object_exclusively(obj, col)
    obj.data.materials.append(mat)
    assign_box_uv(obj)
    finalize_mesh_object(obj, smooth_angle_deg=45.0)
    return {"template_id": "ceramic_vase", "collection": col.name, "object_names": [obj.name], "materials": [mat.name]}


def generate_stylized_leaf(asset_name: str, params: dict[str, Any]) -> dict[str, Any]:
    """Generate an organic open-sheet botanical leaf (non-watertight open boundary by design)."""
    length = float(params.get("length", 0.35))
    col = ensure_asset_collection(f"Asset_{asset_name.replace(' ', '_')}")
    mat = create_principled_material("Botanical_Leaf_Green", base_color=(0.14, 0.42, 0.13, 1.0), roughness=0.42)

    bm = bmesh.new()
    nu, nv = 12, 8
    grid: list[list[bmesh.types.BMVert]] = []
    for iu in range(nu + 1):
        t = iu / nu  # 0 base .. 1 tip
        y = t * length
        half_w = (length * 0.28) * math.sin(math.pi * (t ** 0.72)) + 0.004
        arch_z = 0.03 * math.sin(math.pi * t)
        row: list[bmesh.types.BMVert] = []
        for iv in range(nv + 1):
            u = (iv / nv) * 2.0 - 1.0
            x = u * half_w
            v_crease = abs(u) * 0.014 * math.sin(math.pi * t)
            row.append(bm.verts.new(Vector((x, y, arch_z + v_crease + 0.02))))
        grid.append(row)

    for iu in range(nu):
        for iv in range(nv):
            bm.faces.new((grid[iu][iv], grid[iu][iv + 1], grid[iu + 1][iv + 1], grid[iu + 1][iv]))

    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    mesh = bpy.data.meshes.new("Botanical_Leaf_Mesh")
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new("Botanical_Leaf_Blade", mesh)
    link_object_exclusively(obj, col)
    obj.data.materials.append(mat)
    assign_box_uv(obj)
    finalize_mesh_object(obj, recalc_normals=False, smooth_angle_deg=45.0)
    return {"template_id": "stylized_leaf", "collection": col.name, "object_names": [obj.name], "materials": [mat.name]}
