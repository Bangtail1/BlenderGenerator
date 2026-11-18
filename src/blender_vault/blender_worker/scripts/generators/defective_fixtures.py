"""Intentionally defective mesh generators for automated Technical QA verification tests."""

from __future__ import annotations

from typing import Any

import bmesh
import bpy
from mathutils import Vector

from _common import create_principled_material, ensure_asset_collection, link_object_exclusively


def generate_defect_empty_scene(asset_name: str, params: dict[str, Any]) -> dict[str, Any]:
    """Produces a valid .blend file with zero mesh objects."""
    col = ensure_asset_collection(f"Asset_{asset_name.replace(' ', '_')}")
    return {"template_id": "defect_empty_scene", "collection": col.name, "object_names": [], "materials": []}


def generate_defect_zero_area_and_loose(asset_name: str, params: dict[str, Any]) -> dict[str, Any]:
    """Produces a mesh with zero-area degenerate faces, loose verts, loose edge, and duplicate verts."""
    col = ensure_asset_collection(f"Asset_{asset_name.replace(' ', '_')}")
    mat = create_principled_material("Defect_Mat", base_color=(0.8, 0.2, 0.2, 1.0))

    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    # Zero-area degenerate triangle (collinear points)
    v1 = bm.verts.new(Vector((0.0, 0.0, 1.5)))
    v2 = bm.verts.new(Vector((0.5, 0.0, 1.5)))
    v3 = bm.verts.new(Vector((1.0, 0.0, 1.5)))
    bm.faces.new((v1, v2, v3))
    # Loose vertices
    bm.verts.new(Vector((2.0, 0.0, 0.0)))
    bm.verts.new(Vector((2.2, 0.0, 0.0)))
    # Loose edge
    le1 = bm.verts.new(Vector((-2.0, 0.0, 0.0)))
    le2 = bm.verts.new(Vector((-2.0, 1.0, 0.0)))
    bm.edges.new((le1, le2))
    # Exact overlapping duplicate vertices
    bm.verts.new(Vector((0.5, 0.5, 0.5)))
    bm.verts.new(Vector((0.5, 0.5, 0.5)))

    mesh = bpy.data.meshes.new("Defective_Geometry_Mesh")
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new("Defective_Geometry_Object", mesh)
    link_object_exclusively(obj, col)
    obj.data.materials.append(mat)
    return {"template_id": "defect_zero_area_and_loose", "collection": col.name, "object_names": [obj.name], "materials": [mat.name]}


def generate_defect_inverted_normals(asset_name: str, params: dict[str, Any]) -> dict[str, Any]:
    """Produces a closed cube with inverted inward normals and a non-manifold internal fin."""
    col = ensure_asset_collection(f"Asset_{asset_name.replace(' ', '_')}")
    mat = create_principled_material("Inverted_Mat")

    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    for face in bm.faces:
        face.normal_flip()
    # Add an extra face sharing an existing edge to create a 3-face non-manifold edge
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    e = bm.edges[0]
    v_extra = bm.verts.new(Vector((0.0, 0.0, 0.8)))
    bm.faces.new((e.verts[0], e.verts[1], v_extra))

    mesh = bpy.data.meshes.new("Inverted_Normals_Mesh")
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new("Inverted_Normals_Cube", mesh)
    link_object_exclusively(obj, col)
    obj.data.materials.append(mat)
    return {"template_id": "defect_inverted_normals", "collection": col.name, "object_names": [obj.name], "materials": [mat.name]}


def generate_defect_unapplied_scale_and_no_material(asset_name: str, params: dict[str, Any]) -> dict[str, Any]:
    """Produces an object with default 'Cube' name in Scene Collection, unapplied extreme scale, no UV, no material."""
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    mesh = bpy.data.meshes.new("Cube")
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new("Cube", mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.scale = (14.0, 0.05, 5.5)
    return {"template_id": "defect_unapplied_scale_and_no_material", "collection": "Scene Collection", "object_names": [obj.name], "materials": []}


def generate_defect_missing_texture(asset_name: str, params: dict[str, Any]) -> dict[str, Any]:
    """Produces a cube referencing an external image texture that does not exist on disk."""
    col = ensure_asset_collection(f"Asset_{asset_name.replace(' ', '_')}")
    mat = bpy.data.materials.new("Missing_Texture_Mat")
    if hasattr(mat, "use_nodes") and not mat.use_nodes:
        mat.use_nodes = True
    nt = mat.node_tree
    bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
    tex = nt.nodes.new("ShaderNodeTexImage")
    img = bpy.data.images.new("MissingAlbedo", width=64, height=64)
    img.source = "FILE"
    img.filepath = "/nonexistent/textures/missing_wood_albedo.png"
    tex.image = img
    nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])

    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    mesh = bpy.data.meshes.new("Missing_Tex_Mesh")
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new("Asset_Missing_Texture", mesh)
    link_object_exclusively(obj, col)
    obj.data.materials.append(mat)
    return {"template_id": "defect_missing_texture", "collection": col.name, "object_names": [obj.name], "materials": [mat.name]}
