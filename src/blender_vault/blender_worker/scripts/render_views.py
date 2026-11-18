"""Blender worker script: render multi-angle inspection views, wireframe, material beauty, and turntable frames."""

from __future__ import annotations

import math
import sys
import traceback
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import bpy  # noqa: E402
from mathutils import Vector  # noqa: E402

from _common import (  # noqa: E402
    compute_combined_bounds,
    create_principled_material,
    get_mesh_objects,
    parse_cli_args,
    write_output,
)


def _setup_world(bg_rgb: tuple[float, float, float], strength: float = 0.75) -> None:
    scene = bpy.context.scene
    if scene.world is None:
        scene.world = bpy.data.worlds.new("QA_Studio_World")
    world = scene.world
    if hasattr(world, "use_nodes") and not world.use_nodes:
        world.use_nodes = True
    nt = world.node_tree
    bg = next((n for n in nt.nodes if n.type == "BACKGROUND"), None)
    if bg is None:
        nt.nodes.clear()
        bg = nt.nodes.new("ShaderNodeBackground")
        out = nt.nodes.new("ShaderNodeOutputWorld")
        nt.links.new(bg.outputs["Background"], out.inputs["Surface"])
    bg.inputs["Color"].default_value = (*bg_rgb, 1.0)
    bg.inputs["Strength"].default_value = strength


def _aim_object_at(obj: bpy.types.Object, target: Vector) -> None:
    direction = target - obj.location
    rot_quat = direction.to_track_quat("-Z", "Y")
    obj.rotation_euler = rot_quat.to_euler()


def _create_studio_lights(center: Vector, radius: float) -> list[bpy.types.Object]:
    lights: list[bpy.types.Object] = []
    d = max(radius * 2.8, 1.2)
    specs = [
        ("QA_Key_Light", Vector((center.x - d * 0.9, center.y - d * 1.1, center.z + d * 1.0)), 120.0 * d * d, (1.0, 0.97, 0.92), d * 0.9),
        ("QA_Fill_Light", Vector((center.x + d * 1.2, center.y - d * 0.6, center.z + d * 0.4)), 65.0 * d * d, (0.85, 0.92, 1.0), d * 1.2),
        ("QA_Rim_Light", Vector((center.x + d * 0.4, center.y + d * 1.3, center.z + d * 0.9)), 95.0 * d * d, (1.0, 0.96, 0.90), d * 0.8),
        ("QA_Bottom_Bounce", Vector((center.x, center.y - d * 0.2, center.z - d * 1.2)), 30.0 * d * d, (0.90, 0.92, 0.96), d * 1.5),
    ]
    for name, loc, energy, color, size in specs:
        ldata = bpy.data.lights.new(name=name, type="AREA")
        ldata.energy = energy
        ldata.color = color
        ldata.size = size
        lobj = bpy.data.objects.new(name, ldata)
        bpy.context.scene.collection.objects.link(lobj)
        lobj.location = loc
        _aim_object_at(lobj, center)
        lights.append(lobj)
    return lights


def _create_ground_disc(center: Vector, min_z: float, radius: float) -> bpy.types.Object:
    bpy.ops.mesh.primitive_circle_add(
        vertices=64,
        radius=max(radius * 12.0, 6.0),
        fill_type="NGON",
        location=(center.x, center.y, min_z - 0.002),
    )
    disc = bpy.context.active_object
    disc.name = "QA_Studio_Ground"
    mat = create_principled_material("QA_Ground_Mat", base_color=(0.11, 0.12, 0.14, 1.0), roughness=0.92)
    disc.data.materials.append(mat)
    return disc


def _configure_render(engine: str, res: int, samples: int) -> None:
    scene = bpy.context.scene
    scene.render.engine = engine
    scene.render.resolution_x = res
    scene.render.resolution_y = res
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    if engine == "BLENDER_EEVEE":
        if hasattr(scene.eevee, "taa_render_samples"):
            scene.eevee.taa_render_samples = samples
    elif engine == "CYCLES":
        scene.cycles.samples = samples
        scene.cycles.use_denoising = True


def _render_view(cam_obj: bpy.types.Object, location: Vector, target: Vector, lens: float, out_path: Path, *, ortho_scale: float | None = None) -> None:
    cam_data: bpy.types.Camera = cam_obj.data
    cam_obj.location = location
    _aim_object_at(cam_obj, target)
    if ortho_scale is not None:
        cam_data.type = "ORTHO"
        cam_data.ortho_scale = ortho_scale
    else:
        cam_data.type = "PERSP"
        cam_data.lens = lens
    cam_data.clip_start = 0.005
    cam_data.clip_end = max((location - target).length * 20.0, 200.0)
    bpy.context.scene.camera = cam_obj
    bpy.context.scene.render.filepath = str(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.render.render(write_still=True)


def _render_wireframe_view(
    mesh_objs: list[bpy.types.Object],
    cam_obj: bpy.types.Object,
    location: Vector,
    target: Vector,
    lens: float,
    radius: float,
    out_path: Path,
) -> None:
    """Create temporary solid-clay + black-wireframe clones so headless EEVEE renders crisp topology."""
    clay_mat = create_principled_material("QA_Wire_Clay", base_color=(0.76, 0.78, 0.82, 1.0), roughness=0.75)
    wire_mat = create_principled_material("QA_Wire_Lines", base_color=(0.04, 0.05, 0.07, 1.0), roughness=0.90)

    clones: list[bpy.types.Object] = []
    hidden_originals: list[tuple[bpy.types.Object, bool]] = []
    wire_thickness = max(0.0012, min(0.008, radius * 0.004))

    for orig in mesh_objs:
        hidden_originals.append((orig, orig.hide_render))
        clone = orig.copy()
        clone.data = orig.data.copy()
        clone.hide_render = False
        orig.hide_render = True
        clone.name = f"_QA_WireClone_{orig.name}"
        bpy.context.scene.collection.objects.link(clone)
        clone.data.materials.clear()
        clone.data.materials.append(clay_mat)
        clone.data.materials.append(wire_mat)
        for poly in clone.data.polygons:
            poly.material_index = 0
        mod = clone.modifiers.new(name="QA_Wireframe", type="WIREFRAME")
        mod.thickness = wire_thickness
        mod.use_replace = False
        mod.material_offset = 1
        mod.use_even_offset = True
        clones.append(clone)

    try:
        _setup_world((0.24, 0.25, 0.28), strength=0.95)
        _render_view(cam_obj, location, target, lens, out_path)
    finally:
        for clone in clones:
            mesh_data = clone.data
            bpy.data.objects.remove(clone, do_unlink=True)
            if mesh_data and mesh_data.users == 0:
                bpy.data.meshes.remove(mesh_data)
        for orig, prev_hide in hidden_originals:
            orig.hide_render = prev_hide


def main() -> None:
    payload, out_json = parse_cli_args()
    try:
        blend_path = Path(payload["blend_path"]).resolve()
        output_dir = Path(payload["output_dir"]).resolve()
        engine = str(payload.get("render_engine", "BLENDER_EEVEE"))
        res = int(payload.get("resolution", 768))
        samples = int(payload.get("samples", 16))
        turntable_frames = int(payload.get("turntable_frames", 16))
        turntable_res = int(payload.get("turntable_resolution", 512))

        if not blend_path.is_file():
            raise FileNotFoundError(f"Blend file not found: {blend_path}")

        bpy.ops.wm.open_mainfile(filepath=str(blend_path))
        mesh_objs = get_mesh_objects()
        if not mesh_objs:
            raise RuntimeError("Cannot render inspection views: .blend contains zero mesh objects.")

        # Ensure any existing camera/lights inside imported scenes do not interfere cleanly
        for obj in list(bpy.context.scene.objects):
            if obj.type in {"CAMERA", "LIGHT"}:
                obj.hide_render = True

        bounds = compute_combined_bounds(mesh_objs)
        bmin = Vector(bounds["min"])
        bmax = Vector(bounds["max"])
        dims = Vector(bounds["dimensions"])
        center = Vector(bounds["center"])
        radius = max(dims.length * 0.5, 0.25)

        _setup_world((0.16, 0.18, 0.21), strength=0.75)
        _create_studio_lights(center, radius)
        ground_disc = _create_ground_disc(center, bmin.z, radius)
        ground_disc.hide_render = True  # Enable only on hero/material shots

        cam_data = bpy.data.cameras.new("QA_Inspection_Camera")
        cam_obj = bpy.data.objects.new("QA_Inspection_Camera", cam_data)
        bpy.context.scene.collection.objects.link(cam_obj)

        _configure_render(engine, res, samples)

        # Framing distances: fit bounding sphere cleanly inside 50mm lens (~35mm sensor)
        dist = radius * 2.85
        ortho_span = max(dims.x, dims.y, dims.z, 0.25) * 1.42
        render_paths: dict[str, str] = {}

        # Standard orthographic directional inspection angles (no ground occlusion)
        ortho_views = [
            ("front", Vector((center.x, center.y - dist, center.z))),
            ("back", Vector((center.x, center.y + dist, center.z))),
            ("left", Vector((center.x - dist, center.y, center.z))),
            ("right", Vector((center.x + dist, center.y, center.z))),
            ("top", Vector((center.x, center.y - 0.001, center.z + dist))),
        ]
        for view_name, loc in ortho_views:
            out_file = output_dir / f"{view_name}.png"
            _render_view(cam_obj, loc, center, 50.0, out_file, ortho_scale=ortho_span)
            render_paths[view_name] = str(out_file)

        # Hero 3/4 perspective view with subtle ground disc
        ground_disc.hide_render = False
        hero_dir = Vector((-0.65, -0.78, 0.48)).normalized()
        hero_loc = center + hero_dir * (dist * 1.05)
        hero_file = output_dir / "three_quarter.png"
        _render_view(cam_obj, hero_loc, center, 48.0, hero_file)
        render_paths["three_quarter"] = str(hero_file)

        # Material & surface beauty angle (opposite 3/4 grazing light angle)
        beauty_dir = Vector((0.68, -0.72, 0.36)).normalized()
        beauty_loc = center + beauty_dir * (dist * 0.95)
        beauty_file = output_dir / "material_beauty.png"
        _render_view(cam_obj, beauty_loc, center, 52.0, beauty_file)
        render_paths["material_beauty"] = str(beauty_file)

        # Close-up detail view targeting upper-center feature area
        closeup_target = Vector((center.x, center.y, bmin.z + dims.z * 0.62))
        closeup_dir = Vector((-0.55, -0.78, 0.28)).normalized()
        closeup_loc = closeup_target + closeup_dir * (dist * 0.56)
        closeup_file = output_dir / "closeup.png"
        _render_view(cam_obj, closeup_loc, closeup_target, 65.0, closeup_file)
        render_paths["closeup"] = str(closeup_file)

        # Wireframe topology inspection render (hide ground disc)
        ground_disc.hide_render = True
        wire_file = output_dir / "wireframe.png"
        _render_wireframe_view(mesh_objs, cam_obj, hero_loc, center, 48.0, radius, wire_file)
        render_paths["wireframe"] = str(wire_file)

        # Turntable 360-degree sequence
        turntable_dir = output_dir / "turntable_frames"
        turntable_dir.mkdir(parents=True, exist_ok=True)
        ground_disc.hide_render = False
        _setup_world((0.16, 0.18, 0.21), strength=0.75)
        _configure_render(engine, turntable_res, max(8, samples // 2))
        turntable_frame_paths: list[str] = []
        tt_dist = dist * 1.08
        for i in range(turntable_frames):
            angle = (2.0 * math.pi * i) / turntable_frames - math.pi * 0.5
            vx = math.cos(angle) * 0.92
            vy = math.sin(angle) * 0.92
            vz = 0.42
            t_dir = Vector((vx, vy, vz)).normalized()
            frame_file = turntable_dir / f"frame_{i:02d}.png"
            _render_view(cam_obj, center + t_dir * tt_dist, center, 48.0, frame_file)
            turntable_frame_paths.append(str(frame_file))

        write_output(
            out_json,
            {
                "ok": True,
                "blender_version": bpy.app.version_string,
                "render_engine": engine,
                "render_paths": render_paths,
                "turntable_frame_paths": turntable_frame_paths,
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
