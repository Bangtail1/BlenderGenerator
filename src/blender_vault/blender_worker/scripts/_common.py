"""Shared Blender Python (bpy/bmesh) utilities executed inside Blender."""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any

import bmesh
import bpy
from mathutils import Vector


def parse_cli_args() -> tuple[dict[str, Any], Path]:
    """Read input JSON path and return (payload, output_json_path)."""
    if "--" not in sys.argv:
        raise RuntimeError("Missing '-- <input.json> <output.json>' CLI arguments.")
    idx = sys.argv.index("--")
    args = sys.argv[idx + 1 :]
    if len(args) < 2:
        raise RuntimeError("Expected <input.json> <output.json> after '--'.")
    in_path = Path(args[0])
    out_path = Path(args[1])
    payload = json.loads(in_path.read_text(encoding="utf-8"))
    return payload, out_path


def write_output(out_path: Path, data: dict[str, Any]) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def clear_scene() -> None:
    """Reset the active Blender scene to completely empty data blocks."""
    bpy.ops.wm.read_factory_settings(use_empty=True)


def ensure_asset_collection(name: str) -> bpy.types.Collection:
    """Create or return a top-level collection for the asset."""
    col = bpy.data.collections.get(name)
    if col is None:
        col = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(col)
    return col


def link_object_exclusively(obj: bpy.types.Object, collection: bpy.types.Collection) -> None:
    """Link object exclusively into target collection (removing from root/other collections)."""
    for existing in list(obj.users_collection):
        existing.objects.unlink(obj)
    collection.objects.link(obj)


def get_mesh_objects() -> list[bpy.types.Object]:
    return [obj for obj in bpy.context.scene.objects if obj.type == "MESH" and obj.data is not None]


def compute_combined_bounds(objects: list[bpy.types.Object]) -> dict[str, list[float]]:
    """Compute world-space axis-aligned bounding box across mesh objects."""
    if not objects:
        return {
            "min": [0.0, 0.0, 0.0],
            "max": [0.0, 0.0, 0.0],
            "dimensions": [0.0, 0.0, 0.0],
            "center": [0.0, 0.0, 0.0],
        }
    xs: list[float] = []
    ys: list[float] = []
    zs: list[float] = []
    depsgraph = bpy.context.evaluated_depsgraph_get()
    for obj in objects:
        eval_obj = obj.evaluated_get(depsgraph)
        for corner in eval_obj.bound_box:
            world_pt = eval_obj.matrix_world @ Vector(corner)
            xs.append(world_pt.x)
            ys.append(world_pt.y)
            zs.append(world_pt.z)
    min_pt = [min(xs), min(ys), min(zs)]
    max_pt = [max(xs), max(ys), max(zs)]
    dims = [max_pt[0] - min_pt[0], max_pt[1] - min_pt[1], max_pt[2] - min_pt[2]]
    center = [(min_pt[0] + max_pt[0]) * 0.5, (min_pt[1] + max_pt[1]) * 0.5, (min_pt[2] + max_pt[2]) * 0.5]
    return {"min": min_pt, "max": max_pt, "dimensions": dims, "center": center}


def set_material_nodes_enabled(mat: bpy.types.Material) -> bpy.types.NodeTree:
    """Obtain material node tree across Blender 4.x/5.x safely."""
    if hasattr(mat, "use_nodes") and not mat.use_nodes:
        mat.use_nodes = True
    return mat.node_tree


def create_principled_material(
    name: str,
    *,
    base_color: tuple[float, float, float, float] = (0.7, 0.7, 0.7, 1.0),
    roughness: float = 0.45,
    metallic: float = 0.0,
) -> bpy.types.Material:
    mat = bpy.data.materials.new(name=name)
    nt = set_material_nodes_enabled(mat)
    nodes = nt.nodes
    bsdf = next((n for n in nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None:
        nodes.clear()
        bsdf = nodes.new("ShaderNodeBsdfPrincipled")
        out = nodes.new("ShaderNodeOutputMaterial")
        nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    bsdf.inputs["Base Color"].default_value = base_color
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat


def create_procedural_wood_material(
    name: str = "Wood_Natural_Oak",
    *,
    light_color: tuple[float, float, float, float] = (0.48, 0.27, 0.12, 1.0),
    dark_color: tuple[float, float, float, float] = (0.24, 0.12, 0.05, 1.0),
    roughness_base: float = 0.36,
    grain_scale: float = 6.0,
) -> bpy.types.Material:
    """Create a rich procedural wood grain shader with bump and roughness variation."""
    mat = bpy.data.materials.new(name=name)
    nt = set_material_nodes_enabled(mat)
    nodes = nt.nodes
    links = nt.links
    nodes.clear()

    out = nodes.new("ShaderNodeOutputMaterial")
    out.location = (650, 0)

    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (350, 0)
    if "Specular IOR Level" in bsdf.inputs:
        bsdf.inputs["Specular IOR Level"].default_value = 0.45
    if "Coat Weight" in bsdf.inputs:
        bsdf.inputs["Coat Weight"].default_value = 0.12
    if "Coat Roughness" in bsdf.inputs:
        bsdf.inputs["Coat Roughness"].default_value = 0.25

    tex_coord = nodes.new("ShaderNodeTexCoord")
    tex_coord.location = (-850, 0)

    mapping = nodes.new("ShaderNodeMapping")
    mapping.location = (-650, 0)
    mapping.inputs["Scale"].default_value = (grain_scale * 1.35, grain_scale * 0.22, grain_scale * 0.95)

    noise = nodes.new("ShaderNodeTexNoise")
    noise.location = (-430, -140)
    noise.inputs["Scale"].default_value = 8.5
    noise.inputs["Detail"].default_value = 8.0
    noise.inputs["Roughness"].default_value = 0.65

    wave = nodes.new("ShaderNodeTexWave")
    wave.location = (-430, 140)
    wave.wave_type = "RINGS"
    wave.rings_direction = "X"
    wave.wave_profile = "SAW"
    wave.inputs["Scale"].default_value = 2.6
    wave.inputs["Distortion"].default_value = 6.5
    wave.inputs["Detail"].default_value = 4.0
    wave.inputs["Detail Scale"].default_value = 1.8

    mix_factor = nodes.new("ShaderNodeMix")
    mix_factor.data_type = "FLOAT"
    mix_factor.location = (-200, 40)
    mix_factor.inputs["Factor"].default_value = 0.35

    ramp = nodes.new("ShaderNodeValToRGB")
    ramp.location = (20, 140)
    ramp.color_ramp.elements[0].position = 0.18
    ramp.color_ramp.elements[0].color = dark_color
    ramp.color_ramp.elements[1].position = 0.84
    ramp.color_ramp.elements[1].color = light_color

    rough_ramp = nodes.new("ShaderNodeValToRGB")
    rough_ramp.location = (20, -100)
    rough_ramp.color_ramp.elements[0].position = 0.2
    rough_ramp.color_ramp.elements[0].color = (roughness_base + 0.12, roughness_base + 0.12, roughness_base + 0.12, 1.0)
    rough_ramp.color_ramp.elements[1].position = 0.85
    rough_ramp.color_ramp.elements[1].color = (roughness_base, roughness_base, roughness_base, 1.0)

    bump = nodes.new("ShaderNodeBump")
    bump.location = (120, -280)
    bump.inputs["Strength"].default_value = 0.08
    bump.inputs["Distance"].default_value = 0.004

    links.new(tex_coord.outputs["Object"], mapping.inputs["Vector"])
    links.new(mapping.outputs["Vector"], wave.inputs["Vector"])
    links.new(mapping.outputs["Vector"], noise.inputs["Vector"])
    links.new(wave.outputs["Fac"], mix_factor.inputs["A"])
    links.new(noise.outputs["Fac"], mix_factor.inputs["B"])
    links.new(mix_factor.outputs["Result"], ramp.inputs["Fac"])
    links.new(mix_factor.outputs["Result"], rough_ramp.inputs["Fac"])
    links.new(mix_factor.outputs["Result"], bump.inputs["Height"])
    links.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])
    links.new(rough_ramp.outputs["Color"], bsdf.inputs["Roughness"])
    links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    return mat


def assign_box_uv(obj: bpy.types.Object, scale: float = 1.0) -> None:
    """Generate clean, non-empty box-projected UV coordinates directly via bmesh."""
    mesh = obj.data
    if not mesh or not mesh.polygons:
        return
    bm = bmesh.new()
    bm.from_mesh(mesh)
    uv_layer = bm.loops.layers.uv.verify()
    for face in bm.faces:
        n = face.normal
        ax, ay, az = abs(n.x), abs(n.y), abs(n.z)
        for loop in face.loops:
            co = loop.vert.co * scale
            if az >= ax and az >= ay:
                uv = (co.x + 0.5, co.y + 0.5)
            elif ax >= ay and ax >= az:
                uv = (co.y + 0.5, co.z + 0.5)
            else:
                uv = (co.x + 0.5, co.z + 0.5)
            loop[uv_layer].uv = uv
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()


def finalize_mesh_object(
    obj: bpy.types.Object,
    *,
    recalc_normals: bool = True,
    smooth_angle_deg: float = 38.0,
    box_uv: bool = True,
) -> None:
    """Ensure consistent normals, optional UV layer, and clean shading."""
    mesh = obj.data
    if not isinstance(mesh, bpy.types.Mesh):
        return
    if recalc_normals and len(mesh.polygons) > 0:
        bm = bmesh.new()
        bm.from_mesh(mesh)
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        bm.to_mesh(mesh)
        bm.free()
    if box_uv and len(mesh.uv_layers) == 0:
        assign_box_uv(obj)
    for poly in mesh.polygons:
        poly.use_smooth = smooth_angle_deg > 0.0
    mesh.update()


def set_origin_to_world_point(obj: bpy.types.Object, world_point: Vector) -> None:
    """Move object origin to world_point while keeping vertices stationary in world space."""
    inv = obj.matrix_world.inverted()
    local_delta = inv @ world_point
    mesh = obj.data
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bmesh.ops.translate(bm, verts=bm.verts, vec=-local_delta)
    bm.to_mesh(mesh)
    bm.free()
    obj.matrix_world.translation = world_point


def apply_object_transforms(obj: bpy.types.Object) -> None:
    """Bake object scale and rotation cleanly into mesh data."""
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
    obj.select_set(False)


def export_scene_glb(filepath: Path) -> None:
    """Export all scene mesh objects cleanly to GLB."""
    filepath.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.export_scene.gltf(
        filepath=str(filepath),
        export_format="GLB",
        use_visible=True,
        export_apply=True,
        export_materials="EXPORT",
    )


def save_blend_file(filepath: Path) -> None:
    filepath.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(filepath))
