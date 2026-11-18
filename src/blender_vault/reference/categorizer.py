"""Reference library categorization and extensible multi-tag classification engine."""

from __future__ import annotations

from typing import Optional

from .models import (
    PRIMARY_CATEGORIES,
    TECHNICAL_CATEGORIES,
    BlendSwapAssetSummary,
    ReferenceInspectionReport,
)
from .ref_db import ReferenceDatabase


_CATEGORY_SYNONYMS: dict[str, str] = {
    "architecture": "Architecture",
    "architectural": "Architecture",
    "building": "Architecture",
    "buildings": "Architecture",
    "interior": "Architecture",
    "animals": "Animals",
    "animal": "Animals",
    "creature": "Animals",
    "creatures": "Animals",
    "wildlife": "Animals",
    "characters": "Characters",
    "character": "Characters",
    "human": "Characters",
    "humans": "Characters",
    "people": "Characters",
    "vehicles": "Vehicles",
    "vehicle": "Vehicles",
    "car": "Vehicles",
    "cars": "Vehicles",
    "aircraft": "Vehicles",
    "spaceship": "Vehicles",
    "furniture": "Furniture",
    "chair": "Furniture",
    "table": "Furniture",
    "sofa": "Furniture",
    "electronics": "Electronics",
    "electronic": "Electronics",
    "gadget": "Electronics",
    "tech": "Electronics",
    "computer": "Electronics",
    "food": "Food",
    "drink": "Food",
    "fruit": "Food",
    "kitchenware": "Props and objects",
    "clothing": "Clothing",
    "clothes": "Clothing",
    "apparel": "Clothing",
    "garment": "Clothing",
    "plants": "Plants",
    "plant": "Plants",
    "nature": "Plants",
    "foliage": "Plants",
    "trees": "Plants",
    "tree": "Plants",
    "botanical": "Plants",
    "weapons": "Weapons",
    "weapon": "Weapons",
    "sword": "Weapons",
    "gun": "Weapons",
    "props": "Props and objects",
    "prop": "Props and objects",
    "props_and_objects": "Props and objects",
    "household": "Props and objects",
    "objects": "Props and objects",
    "environments": "Environments",
    "environment": "Environments",
    "landscape": "Environments",
    "terrain": "Environments",
    "scenery": "Environments",
    "mechanical": "Mechanical components",
    "mechanical_components": "Mechanical components",
    "mechanical_objects": "Mechanical components",
    "machinery": "Mechanical components",
    "hardware": "Mechanical components",
    "other": "Other",
    "miscellaneous": "Other",
}

_KEYWORD_TO_PRIMARY: list[tuple[tuple[str, ...], str]] = [
    (("chair", "stool", "sofa", "couch", "table", "desk", "bed", "cabinet", "bookshelf", "bench"), "Furniture"),
    (("character", "humanoid", "mannequin", "knight", "warrior", "avatar", "head", "bust"), "Characters"),
    (("animal", "dog", "cat", "fox", "bird", "horse", "dragon", "fish", "insect", "wolf"), "Animals"),
    (("car", "truck", "bike", "motorcycle", "plane", "helicopter", "ship", "boat", "train", "spaceship"), "Vehicles"),
    (("house", "building", "arch", "pillar", "wall", "door", "window", "stairs", "cathedral", "bridge"), "Architecture"),
    (("phone", "laptop", "monitor", "camera", "speaker", "keyboard", "console", "tv", "radio", "electronic"), "Electronics"),
    (("apple", "bread", "cake", "pizza", "burger", "fruit", "vegetable", "food", "cupcake", "cheese"), "Food"),
    (("shirt", "jacket", "coat", "dress", "hat", "helmet", "shoe", "boot", "cloth", "pants"), "Clothing"),
    (("tree", "plant", "flower", "leaf", "bush", "grass", "fern", "cactus", "foliage"), "Plants"),
    (("sword", "axe", "knife", "gun", "rifle", "pistol", "bow", "shield", "weapon", "dagger"), "Weapons"),
    (("gear", "cog", "piston", "engine", "bolt", "nut", "valve", "robotic arm", "hydraulic", "turbine"), "Mechanical components"),
    (("terrain", "landscape", "forest", "island", "cave", "valley", "planet", "environment", "mountain"), "Environments"),
    (("barrel", "crate", "lamp", "vase", "bottle", "mug", "chest", "prop", "lantern", "book"), "Props and objects"),
]


def classify_primary_category(
    summary: BlendSwapAssetSummary,
    ref_db: Optional[ReferenceDatabase] = None,
    *,
    allow_auto_extend: bool = True,
) -> str:
    """Resolve an asset into one of the 14 primary categories (or auto-register a new category)."""
    slug = (summary.category.slug or "").strip().lower().replace("-", "_")
    name = (summary.category.name or "").strip().lower().replace("-", "_").replace(" ", "_")

    for candidate in (slug, name):
        if candidate in _CATEGORY_SYNONYMS:
            return _CATEGORY_SYNONYMS[candidate]

    # Check text/tags heuristics before creating a new category
    haystack = f"{summary.title} {summary.description} {' '.join(summary.tags)}".lower()
    for keywords, target in _KEYWORD_TO_PRIMARY:
        if any(kw in haystack for kw in keywords):
            return target

    # Preserve unrecognized genuine BlendSwap subject category if non-empty & auto-extend allowed
    raw_name = (summary.category.name or "").strip()
    if allow_auto_extend and raw_name and raw_name.lower() not in {"other", "misc", "miscellaneous", "uncategorized"}:
        if raw_name in PRIMARY_CATEGORIES:
            return raw_name
        if ref_db is not None:
            ref_db.register_custom_category(raw_name, kind="primary")
        return raw_name

    return "Other"


def classify_technical_categories(
    summary: BlendSwapAssetSummary,
    inspection: Optional[ReferenceInspectionReport] = None,
    ref_db: Optional[ReferenceDatabase] = None,
) -> list[str]:
    """Infer multi-label technical categories from BlendSwap metadata and real Blender inspection."""
    tags: set[str] = set()
    text = f"{summary.title} {summary.description} {' '.join(summary.tags)}".lower()
    norm_tags = {t.strip().lower().replace("_", "-") for t in summary.tags}

    # Explicit metadata hints
    for tech in TECHNICAL_CATEGORIES:
        if tech in norm_tags or tech.replace("-", " ") in text or tech in text:
            tags.add(tech)

    if any(k in text for k in ("rig", "armature", "skeleton", "skinned")):
        tags.add("rigged")
    if any(k in text for k in ("anim", "walkcycle", "walk cycle", "keyframes")):
        tags.add("animated")
    if any(k in text for k in ("sculpt", "zbrush", "dyntopo", "multires")):
        tags.add("sculpted")
    if any(k in text for k in ("procedural", "nodes", "geonodes", "geometry nodes")):
        tags.add("procedural")
    if any(k in text for k in ("modular", "kit", "kitbash", "tileable")):
        tags.add("modular")
    if any(k in text for k in ("game ready", "game-ready", "lowpoly", "low poly")):
        tags.add("game-ready")
    if any(k in text for k in ("photoreal", "realistic", "archviz", "pbr")):
        tags.add("photorealistic")

    # Authoritative inspection refinements
    if inspection is not None and inspection.inspection_completed_ok:
        tris = inspection.triangle_count
        if tris > 0:
            # Remove stale poly buckets from text hints when real count disagrees
            tags.discard("low-poly")
            tags.discard("mid-poly")
            tags.discard("high-poly")
            if tris < 5_000:
                tags.add("low-poly")
            elif tris <= 60_000:
                tags.add("mid-poly")
            else:
                tags.add("high-poly")

        if inspection.has_rig:
            tags.add("rigged")
        if inspection.has_animations:
            tags.add("animated")
        if inspection.procedural_shader_count > 0 and inspection.texture_reference_count == 0:
            tags.add("procedural")
        if inspection.triangle_count > 100_000 and inspection.quad_ratio < 0.55 and not inspection.subdivision_ready:
            tags.add("sculpted")

    # Modeling style inference (hard-surface vs organic) if neither explicitly set
    if "hard-surface" not in tags and "organic" not in tags:
        primary = classify_primary_category(summary, ref_db=None, allow_auto_extend=False)
        if primary in {"Animals", "Plants", "Food", "Characters"}:
            tags.add("organic")
        elif primary in {"Vehicles", "Weapons", "Electronics", "Mechanical components", "Architecture", "Furniture"}:
            tags.add("hard-surface")

    # Ensure custom technical tags found in database are recognized without failing
    ordered = [t for t in TECHNICAL_CATEGORIES if t in tags] + sorted(t for t in tags if t not in TECHNICAL_CATEGORIES)
    if ref_db is not None:
        for t in ordered:
            if t not in TECHNICAL_CATEGORIES:
                ref_db.register_custom_category(t, kind="technical")
    return ordered


def classify_quality_category(grade_letter: Optional[str]) -> str:
    if not grade_letter:
        return "Ungraded"
    return f"{grade_letter} reference"
