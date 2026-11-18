"""Aggregated analytics and Sections A-F payload builder for the Branch 2 Dashboard."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any, Optional

from ..config import Settings
from .blendswap_client import BlendSwapClient
from .models import ReferenceAssetRecord, ReferenceAssetStatus
from .ref_db import ReferenceDatabase
from .scheduler import DailyCollectionScheduler, resolve_timezone


class ReferenceDashboardService:
    """Computes Sections A-F metrics cleanly from SQLite and live API status."""

    def __init__(
        self,
        settings: Settings,
        ref_db: ReferenceDatabase,
        client: BlendSwapClient,
        scheduler: DailyCollectionScheduler,
    ) -> None:
        self.settings = settings
        self.ref_db = ref_db
        self.client = client
        self.scheduler = scheduler

    def build_dashboard_summary(self, *, check_live_api: bool = True) -> dict[str, Any]:
        assets = self.ref_db.list_reference_assets(include_details=True)
        cycles = self.ref_db.list_cycles(limit=30)
        benchmarks = self.ref_db.list_benchmark_reports(limit=50)
        dl_history = self.ref_db.list_download_history(limit=100)
        api_usage = self.ref_db.get_api_usage_summary()
        sched_cfg = self.scheduler.get_schedule_config()

        tz = resolve_timezone(sched_cfg["timezone"])
        today_str = datetime.now(timezone.utc).astimezone(tz).date().isoformat()
        today_cycles = [c for c in cycles if c.cycle_date == today_str]
        today_downloaded = sum(c.downloaded_count for c in today_cycles)
        today_failed = sum(c.failed_count for c in today_cycles)
        latest_cycle = cycles[0] if cycles else None

        api_status = (
            self.client.check_connectivity()
            if check_live_api
            else {"configured": bool((self.settings.blendswap_key or "").strip()), "connected": None, "account": None}
        )
        acct = api_status.get("account") or {}

        # Section A: Collection Overview
        section_a = {
            "today_date": today_str,
            "today_downloaded": today_downloaded,
            "today_failed": today_failed,
            "daily_target": sched_cfg["daily_target"],
            "free_downloads_remaining": acct.get("free_downloads_remaining"),
            "free_downloads_limit": acct.get("free_downloads_limit", 7),
            "credits": acct.get("credits"),
            "allow_credit_spend": self.settings.allow_credit_spend,
            "next_collection_cycle": sched_cfg["next_run_at"],
            "schedule": sched_cfg,
            "api_connectivity": api_status,
            "api_usage": api_usage,
            "latest_cycle": latest_cycle.model_dump() if latest_cycle else None,
            "recent_cycles": [c.model_dump() for c in cycles[:10]],
            "total_successful_downloads": sum(1 for h in dl_history if h.get("success")),
            "total_failed_downloads": sum(1 for h in dl_history if not h.get("success")),
        }

        # Section B: Reference Library Distributions
        collected = [
            a
            for a in assets
            if a.status in (ReferenceAssetStatus.DOWNLOADED, ReferenceAssetStatus.INSPECTED, ReferenceAssetStatus.GRADED)
        ]
        cat_dist = Counter(a.primary_category for a in collected)
        tech_dist = Counter(t for a in collected for t in a.technical_categories)
        lic_dist = Counter(a.license_assessment.license_category.value for a in collected)
        grade_dist = Counter((a.grade_letter or "Ungraded") for a in collected)
        blender_ver_dist = Counter(v for a in collected for v in (a.blender_versions or ["Unknown"]))

        section_b = {
            "total_assets": len(collected),
            "total_catalog_records": len(assets),
            "categories": dict(cat_dist.most_common()),
            "technical_categories": dict(tech_dist.most_common()),
            "license_distribution": dict(lic_dist.most_common()),
            "quality_distribution": {k: grade_dist.get(k, 0) for k in ("A+", "A", "B", "C", "D", "F", "Ungraded")},
            "blender_version_distribution": dict(blender_ver_dist.most_common()),
        }

        # Section C: Popularity Analysis
        by_downloads = sorted(collected, key=lambda a: a.counts.downloads, reverse=True)[:10]
        trends_by_date: dict[str, dict[str, int]] = {}
        for c in reversed(cycles):
            entry = trends_by_date.setdefault(c.cycle_date, {"downloaded": 0, "failed": 0, "shortage": 0})
            entry["downloaded"] += c.downloaded_count
            entry["failed"] += c.failed_count
            entry["shortage"] += c.shortage_count

        section_c = {
            "most_downloaded": [self._compact_ref_summary(a) for a in by_downloads],
            "most_represented_categories": [{"category": k, "count": v} for k, v in cat_dist.most_common()],
            "historical_trends": [{"date": d, **vals} for d, vals in trends_by_date.items()],
        }

        # Section D: Quality Intelligence
        graded = [a for a in collected if a.grade is not None and a.inspection is not None]
        highest_rated = sorted(graded, key=lambda a: a.grade.total_score, reverse=True)[:8]
        best_topology = sorted(
            graded,
            key=lambda a: (a.inspection.edge_flow_score * 0.6 + a.inspection.quad_ratio * 40.0),
            reverse=True,
        )[:6]
        best_uv = sorted(
            [a for a in graded if a.inspection.has_uv_map],
            key=lambda a: (a.inspection.uv_completeness_ratio * 60.0 + a.inspection.uv_coverage_ratio * 40.0),
            reverse=True,
        )[:6]
        best_materials = sorted(
            [a for a in graded if a.inspection.material_count > 0],
            key=lambda a: (
                next((d.score for d in a.grade.dimensions if d.dimension == "Material quality"), 0.0) * 10.0
                + a.inspection.material_node_count_avg
            ),
            reverse=True,
        )[:6]
        best_organized = sorted(
            graded,
            key=lambda a: a.inspection.scene_organization_score,
            reverse=True,
        )[:6]

        section_d = {
            "highest_rated": [self._compact_ref_summary(a) for a in highest_rated],
            "best_topology": [self._compact_ref_summary(a) for a in best_topology],
            "best_uv": [self._compact_ref_summary(a) for a in best_uv],
            "best_materials": [self._compact_ref_summary(a) for a in best_materials],
            "best_organized": [self._compact_ref_summary(a) for a in best_organized],
        }

        # Section E: Benchmark Results
        history_by_b1: dict[str, list[dict[str, Any]]] = {}
        for b in reversed(benchmarks):
            history_by_b1.setdefault(b.branch1_asset_id, []).append(
                {
                    "benchmark_id": b.benchmark_id,
                    "version": b.branch1_version,
                    "name": b.branch1_name,
                    "score": b.local_grade.total_score,
                    "grade": b.local_grade.grade_letter,
                    "created_at": b.created_at,
                }
            )

        section_e = {
            "total_benchmarks": len(benchmarks),
            "reports": [b.model_dump() for b in benchmarks],
            "improvement_history": history_by_b1,
        }

        # Section F: Attribution and Licensing
        section_f = {
            "total_records": len(collected),
            "items": [
                {
                    "blendswap_id": a.blendswap_id,
                    "title": a.title,
                    "asset_url": a.url,
                    "author_username": a.author.username,
                    "author_profile_url": a.author.url,
                    "license_key": a.license_assessment.license_key,
                    "license_name": a.license_assessment.license_name,
                    "license_url": a.license_assessment.license_url,
                    "license_source": a.license_assessment.license_source,
                    "legally_verified": a.license_assessment.legally_verified,
                    "license_classes": [c.value for c in a.license_assessment.applicable_classes],
                    "requires_attribution": a.license_assessment.requires_attribution,
                    "requires_additional_review": a.license_assessment.requires_additional_review,
                    "review_warnings": a.license_assessment.review_warnings,
                    "downloaded_at": a.downloaded_at,
                }
                for a in collected
            ],
        }

        return {
            "collection_overview": section_a,
            "reference_library": section_b,
            "popularity_analysis": section_c,
            "quality_intelligence": section_d,
            "benchmark_results": section_e,
            "attribution_and_licensing": section_f,
        }

    @staticmethod
    def _compact_ref_summary(a: ReferenceAssetRecord) -> dict[str, Any]:
        return {
            "blendswap_id": a.blendswap_id,
            "title": a.title,
            "url": a.url,
            "author": a.author.username,
            "primary_category": a.primary_category,
            "technical_categories": a.technical_categories,
            "license_key": a.license_assessment.license_key,
            "license_name": a.license_assessment.license_name,
            "downloads": a.counts.downloads,
            "likes": a.counts.likes,
            "popularity_score": a.popularity_score,
            "technical_quality_score": a.technical_quality_score,
            "reference_usefulness_score": a.reference_usefulness_score,
            "grade_letter": a.grade_letter,
            "triangles": a.inspection.triangle_count if a.inspection else 0,
            "quad_ratio": a.inspection.quad_ratio if a.inspection else 0.0,
            "uv_coverage_ratio": a.inspection.uv_coverage_ratio if a.inspection else 0.0,
            "scene_organization_score": a.inspection.scene_organization_score if a.inspection else 0.0,
            "has_preview": len(a.preview_paths) > 0,
            "drive_sync_status": a.drive_sync_status.value,
        }
