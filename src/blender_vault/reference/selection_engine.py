"""Intelligent discovery and diversity-driven 7-slot asset selection engine."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any, Optional

from ..config import Settings
from .blendswap_client import BlendSwapClient
from .categorizer import classify_primary_category, classify_technical_categories
from .license_engine import assess_license
from .models import (
    PRIMARY_CATEGORIES,
    BlendSwapAssetSummary,
    BlendSwapFileInfo,
    SelectionCandidate,
)
from .ref_db import ReferenceDatabase

logger = logging.getLogger("blender_vault.reference.selection")

SUPPORTED_REFERENCE_FORMATS = {"blender", "blend", "fbx", "gltf", "glb", "obj", "stl", "usd", "usdz"}
MAX_RECOMMENDED_FILE_BYTES = 200 * 1024 * 1024
MIN_VALID_FILE_BYTES = 128


@dataclass
class SelectionResult:
    selected: list[SelectionCandidate]
    evaluated_count: int
    filtered_reasons: dict[str, int]
    shortage_count: int
    shortage_reason: Optional[str]


def compute_popularity_score(summary: BlendSwapAssetSummary) -> float:
    """Compute a 0..100 popularity + engagement score strictly separate from technical quality."""
    dls = max(0, summary.counts.downloads)
    likes = max(0, summary.counts.likes)
    comments = max(0, summary.counts.comments)

    dl_component = min(70.0, math.log10(dls + 1) * 15.5)  # ~10k downloads -> ~62
    like_component = min(20.0, math.log10(likes + 1) * 7.5)
    comment_component = min(10.0, math.log10(comments + 1) * 5.0)
    return round(min(100.0, dl_component + like_component + comment_component), 2)


def _select_best_file(files: list[BlendSwapFileInfo], summary: BlendSwapAssetSummary) -> Optional[BlendSwapFileInfo]:
    if not files:
        if summary.size_bytes >= MIN_VALID_FILE_BYTES:
            return BlendSwapFileInfo(
                id=0,
                format="blender",
                format_label="Blender",
                size_bytes=summary.size_bytes,
                software_version=summary.blender_versions[0] if summary.blender_versions else None,
                filename=f"blendswap_{summary.id}.blend",
                position=0,
            )
        return None

    def file_rank(f: BlendSwapFileInfo) -> tuple[int, int, int]:
        fmt = f.format.lower()
        ext = f.filename.lower().rsplit(".", 1)[-1] if "." in f.filename else ""
        is_native = 2 if (fmt in {"blender", "blend"} or ext == "blend") else (1 if fmt in SUPPORTED_REFERENCE_FORMATS else 0)
        size_ok = 1 if MIN_VALID_FILE_BYTES <= f.size_bytes <= MAX_RECOMMENDED_FILE_BYTES else 0
        return (is_native, size_ok, -f.position)

    eligible = [
        f
        for f in files
        if (f.format.lower() in SUPPORTED_REFERENCE_FORMATS or f.filename.lower().endswith((".blend", ".zip", ".fbx", ".obj", ".glb", ".gltf", ".stl")))
        and (f.size_bytes == 0 or f.size_bytes >= MIN_VALID_FILE_BYTES)
    ]
    if not eligible:
        return None
    eligible.sort(key=file_rank, reverse=True)
    return eligible[0]


def _blender_version_score(versions: list[str]) -> float:
    if not versions:
        return 4.0
    best = 0.0
    for v in versions:
        try:
            major = int(str(v).strip().split(".")[0])
            if major >= 4:
                best = max(best, 10.0)
            elif major == 3:
                best = max(best, 8.5)
            elif major == 2:
                best = max(best, 6.5)
            else:
                best = max(best, 4.0)
        except ValueError:
            best = max(best, 5.0)
    return best


class SelectionEngine:
    """Evaluates popular BlendSwap candidates and fills daily reference slots with diversity."""

    def __init__(self, settings: Settings, ref_db: ReferenceDatabase, client: BlendSwapClient) -> None:
        self.settings = settings
        self.ref_db = ref_db
        self.client = client

    def discover_and_select(
        self,
        *,
        target_count: int = 7,
        max_pages: int = 4,
        per_page: int = 50,
    ) -> SelectionResult:
        if target_count <= 0:
            return SelectionResult([], 0, {}, 0, "Target count is 0.")

        # Refresh category cache opportunistically (non-fatal if endpoint hiccups)
        try:
            self.client.get_categories()
        except Exception as exc:
            logger.debug("Skipping category cache refresh: %s", exc)

        raw_candidates: list[BlendSwapAssetSummary] = []
        seen_ids: set[int] = set()
        eligible_uncollected_count = 0
        for page in range(1, max_pages + 1):
            items, pagination = self.client.search_assets(
                asset_type="model_3d",
                sort="downloads",
                page=page,
                per_page=per_page,
            )
            if not items:
                break
            for item in items:
                if item.id not in seen_ids:
                    seen_ids.add(item.id)
                    raw_candidates.append(item)
                    if self._is_discovery_eligible_uncollected(item):
                        eligible_uncollected_count += 1
            total_pages = int(pagination.get("pages") or 1)
            if page >= total_pages or eligible_uncollected_count >= target_count * 12:
                break

        cat_counts = self.ref_db.get_category_distribution()
        return self.select_from_candidates(raw_candidates, target_count=target_count, category_distribution=cat_counts)

    def _prefilter_candidate(self, summary: BlendSwapAssetSummary) -> tuple[Optional[str], Optional[Any]]:
        """Run fast local checks (zero API calls); returns (rejection_reason, license_assessment)."""
        if self.ref_db.has_downloaded_asset(summary.id):
            return "already_collected", None

        existing = self.ref_db.get_reference_asset(summary.id)
        if existing and existing.status.value == "FAILED":
            return "previously_failed", None

        if summary.asset_type != "model_3d":
            return "non_3d_model_type", None
        if not summary.title.strip() or (not summary.description.strip() and not summary.tags):
            return "insufficient_metadata", None

        lic = assess_license(summary.license, license_source_override=summary.license_source)
        if lic.license_key == "commercial" or not lic.suitable_for_benchmarking:
            return "commercial_or_unsuitable_license", lic
        if self.settings.reference_license_policy == "permissive_only" and lic.license_key not in {"cc0", "cc_by"}:
            return "license_policy_filtered", lic
        if self.settings.reference_license_policy == "cc_only" and not lic.license_key.startswith("cc"):
            return "license_policy_filtered", lic

        return None, lic

    def _is_discovery_eligible_uncollected(self, summary: BlendSwapAssetSummary) -> bool:
        """Fast local check (zero API calls) for whether a candidate counts toward discovery target."""
        rejection, _ = self._prefilter_candidate(summary)
        return rejection is None

    def select_from_candidates(
        self,
        candidates: list[BlendSwapAssetSummary],
        *,
        target_count: int = 7,
        category_distribution: Optional[dict[str, int]] = None,
    ) -> SelectionResult:
        cat_dist = dict(category_distribution if category_distribution is not None else self.ref_db.get_category_distribution())
        filtered_reasons: dict[str, int] = {}
        scored: list[SelectionCandidate] = []

        def reject(reason: str) -> None:
            filtered_reasons[reason] = filtered_reasons.get(reason, 0) + 1

        max_cat_count = max(cat_dist.values(), default=0)

        for summary in candidates:
            # 1-3. Duplicate, past-failure, asset-type, metadata, and license checks
            rejection, lic = self._prefilter_candidate(summary)
            if rejection is not None or lic is None:
                reject(rejection or "filtered")
                continue

            # 4. File availability & format validation
            files = list(summary.files)
            if not files:
                try:
                    files = self.client.get_asset_files(summary.id)
                except Exception:
                    files = []

            best_file = _select_best_file(files, summary)
            if best_file is None:
                reject("unusable_or_missing_files")
                continue

            if best_file.id and self.ref_db.has_free_repeat_window(summary.id, best_file.id):
                reject("within_repeat_window")
                continue

            # 5. Weighted scoring (0..100)
            pop_score = compute_popularity_score(summary)
            is_native = best_file.format.lower() in {"blender", "blend"} or best_file.filename.lower().endswith(".blend")
            fmt_score = 18.0 if is_native else 8.0
            ver_score = _blender_version_score(summary.blender_versions)

            # File size sweet spot: 50KB .. 60MB (large enough for real geometry, small enough for reliable headless inspect)
            sz = best_file.size_bytes or summary.size_bytes
            if 50_000 <= sz <= 60_000_000:
                size_score = 10.0
            elif 5_000 <= sz < 50_000 or 60_000_000 < sz <= 150_000_000:
                size_score = 6.5
            else:
                size_score = 3.0

            # Metadata richness
            meta_score = 0.0
            if len(summary.description.strip()) >= 25:
                meta_score += 4.0
            if len(summary.tags) >= 3:
                meta_score += 4.0
            if summary.category.name and summary.category.name.lower() != "other":
                meta_score += 2.0

            # License friendliness bonus (never overrides technical quality later)
            lic_bonus = 6.0 if lic.license_key == "cc0" else (5.0 if lic.license_key == "cc_by" else 3.5)

            # Underrepresented category boost
            primary_cat = classify_primary_category(summary, ref_db=None, allow_auto_extend=False)
            existing_in_cat = cat_dist.get(primary_cat, 0)
            if max_cat_count == 0:
                underrep_score = 10.0 if existing_in_cat == 0 else 5.0
            else:
                underrep_score = round(10.0 * (1.0 - (existing_in_cat / max(max_cat_count + 1, 1))), 2)

            # Technical evaluation potential
            tech_tags = classify_technical_categories(summary)
            eval_potential = 6.0
            text_blob = f"{summary.title} {summary.description} {' '.join(summary.tags)}".lower()
            if any(k in text_blob for k in ("topology", "quad", "uv", "pbr", "material", "rig", "subdiv", "clean")):
                eval_potential += 4.0
            if best_file.render_engine in {"cycles", "eevee"}:
                eval_potential += 2.0

            # Popularity normalized into 28 points weight
            pop_weighted = round((pop_score / 100.0) * 28.0, 2)
            total_score = round(
                pop_weighted + fmt_score + ver_score + size_score + meta_score + lic_bonus + underrep_score + eval_potential,
                2,
            )

            style_hint = "rigged" if "rigged" in tech_tags else (
                "sculpted" if "sculpted" in tech_tags else (
                    "organic" if "organic" in tech_tags else ("hard-surface" if "hard-surface" in tech_tags else "general")
                )
            )
            scored.append(
                SelectionCandidate(
                    asset=summary,
                    selected_file=best_file,
                    selection_score=total_score,
                    popularity_score=pop_score,
                    score_breakdown={
                        "popularity_weighted": pop_weighted,
                        "native_format": fmt_score,
                        "blender_compat": ver_score,
                        "file_size": size_score,
                        "metadata_richness": meta_score,
                        "license_bonus": lic_bonus,
                        "category_underrep": underrep_score,
                        "eval_potential": eval_potential,
                    },
                    mapped_primary_category=primary_cat,
                    modeling_style_hint=style_hint,
                )
            )

        if not scored:
            return SelectionResult(
                selected=[],
                evaluated_count=len(candidates),
                filtered_reasons=filtered_reasons,
                shortage_count=target_count,
                shortage_reason=f"No eligible candidates found among {len(candidates)} assets ({filtered_reasons}).",
            )

        selected = self._allocate_diverse_slots(scored, target_count=target_count, category_distribution=cat_dist)
        shortage = max(0, target_count - len(selected))
        shortage_reason = (
            f"Only {len(selected)}/{target_count} eligible diverse assets found after filtering ({filtered_reasons})."
            if shortage > 0
            else None
        )
        return SelectionResult(
            selected=selected,
            evaluated_count=len(candidates),
            filtered_reasons=filtered_reasons,
            shortage_count=shortage,
            shortage_reason=shortage_reason,
        )

    def _allocate_diverse_slots(
        self,
        pool: list[SelectionCandidate],
        *,
        target_count: int,
        category_distribution: dict[str, int],
    ) -> list[SelectionCandidate]:
        """Select up to target_count diverse candidates across strategic benchmark slots."""
        remaining = sorted(pool, key=lambda c: c.selection_score, reverse=True)
        chosen: list[SelectionCandidate] = []
        chosen_ids: set[int] = set()
        used_cats: set[str] = set()
        used_styles: set[str] = set()

        def pick(slot_num: int, reason: str, key_fn) -> bool:
            nonlocal remaining
            avail = [c for c in remaining if c.asset.id not in chosen_ids]
            if not avail:
                return False
            best = max(avail, key=key_fn)
            best.slot_number = slot_num
            best.slot_reason = reason
            chosen.append(best)
            chosen_ids.add(best.asset.id)
            used_cats.add(best.mapped_primary_category)
            used_styles.add(best.modeling_style_hint)
            return True

        slot_strategies: list[tuple[str, Any]] = [
            ("Highest-ranked eligible asset", lambda c: (c.selection_score,)),
            (
                "Category diversity (distinct primary category)",
                lambda c: (1 if c.mapped_primary_category not in used_cats else 0, c.selection_score),
            ),
            (
                "Modeling style diversity",
                lambda c: (
                    1 if c.modeling_style_hint not in used_styles else 0,
                    1 if c.mapped_primary_category not in used_cats else 0,
                    c.selection_score,
                ),
            ),
            (
                "Geometrically complex / multi-part asset",
                lambda c: (
                    1 if c.mapped_primary_category not in used_cats else 0,
                    min(c.selected_file.size_bytes, 40_000_000),
                    c.selection_score,
                ),
            ),
            (
                "Strong topology / clean mesh candidate",
                lambda c: (
                    1 if any(k in " ".join(c.asset.tags).lower() + " " + c.asset.description.lower() for k in ("quad", "topology", "subdiv", "game-ready", "low-poly", "mid-poly")) else 0,
                    1 if c.mapped_primary_category not in used_cats else 0,
                    c.selection_score,
                ),
            ),
            (
                "Strong material / UV / PBR candidate",
                lambda c: (
                    1 if any(k in " ".join(c.asset.tags).lower() + " " + c.asset.description.lower() for k in ("pbr", "material", "texture", "uv", "procedural", "photoreal")) else 0,
                    1 if c.mapped_primary_category not in used_cats else 0,
                    c.selection_score,
                ),
            ),
            (
                "Underrepresented library category",
                lambda c: (
                    -category_distribution.get(c.mapped_primary_category, 0),
                    1 if c.mapped_primary_category not in used_cats else 0,
                    c.selection_score,
                ),
            ),
        ]

        for slot_idx in range(1, target_count + 1):
            reason, key_fn = slot_strategies[(slot_idx - 1) % len(slot_strategies)]
            if not pick(slot_idx, reason, key_fn):
                break
        return chosen
