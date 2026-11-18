"""Local Reference Library directory architecture, immutable file storage, and portable JSON persistence."""

from __future__ import annotations

import json
import logging
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

from ..hashing import compute_md5, compute_sha256, slugify_name
from ..models import utc_now_iso
from .models import (
    AttributionRecord,
    BenchmarkReport,
    CollectionCycleRecord,
    DownloadQueueItem,
    ReferenceAssetRecord,
    ReferenceInspectionReport,
)
from .ref_db import ReferenceDatabase

logger = logging.getLogger("blender_vault.reference.storage")

CORE_CATEGORY_FOLDERS: tuple[str, ...] = (
    "architecture",
    "characters",
    "furniture",
    "vehicles",
    "props",
    "animals",
    "environments",
    "other",
)


def category_folder_slug(primary_category: str) -> str:
    """Map primary library category to clean local/Drive folder name."""
    norm = primary_category.strip().lower()
    mapping = {
        "architecture": "architecture",
        "characters": "characters",
        "furniture": "furniture",
        "vehicles": "vehicles",
        "props and objects": "props",
        "props": "props",
        "animals": "animals",
        "environments": "environments",
        "mechanical components": "mechanical",
        "electronics": "electronics",
        "food": "food",
        "clothing": "clothing",
        "plants": "plants",
        "weapons": "weapons",
        "other": "other",
    }
    return mapping.get(norm, slugify_name(primary_category) or "other")


class ReferenceStorage:
    """Manages vault_storage/reference_library/ layout and portable JSON exports."""

    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir.resolve()
        self.database_dir = self.root_dir / "database"
        self.assets_dir = self.root_dir / "assets"
        self.previews_dir = self.root_dir / "previews"
        self.metadata_dir = self.root_dir / "metadata"
        self.inspections_dir = self.root_dir / "inspections"
        self.benchmark_reports_dir = self.root_dir / "benchmark_reports"
        self.attribution_dir = self.root_dir / "attribution"
        self.collection_logs_dir = self.root_dir / "collection_logs"
        self.ensure_layout()

    def ensure_layout(self) -> None:
        for d in (
            self.root_dir,
            self.database_dir,
            self.assets_dir,
            self.previews_dir,
            self.metadata_dir,
            self.inspections_dir,
            self.benchmark_reports_dir,
            self.attribution_dir,
            self.collection_logs_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)
        for cat_slug in CORE_CATEGORY_FOLDERS:
            (self.assets_dir / cat_slug).mkdir(parents=True, exist_ok=True)

    def asset_dir(self, primary_category: str, blendswap_id: int) -> Path:
        folder = self.assets_dir / category_folder_slug(primary_category) / str(blendswap_id)
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def asset_previews_dir(self, blendswap_id: int) -> Path:
        folder = self.previews_dir / str(blendswap_id)
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def preserve_downloaded_file(
        self,
        *,
        temp_path: Path,
        primary_category: str,
        blendswap_id: int,
        original_filename: str,
    ) -> tuple[Path, int, str, str]:
        """Move or copy downloaded binary into immutable reference storage without altering contents."""
        dest_dir = self.asset_dir(primary_category, blendswap_id)
        safe_name = Path(original_filename).name or f"blendswap_{blendswap_id}.blend"
        dest = dest_dir / safe_name
        shutil.copy2(temp_path, dest)
        return dest, dest.stat().st_size, compute_sha256(dest), compute_md5(dest)

    def write_metadata(self, record: ReferenceAssetRecord) -> Path:
        path = self.metadata_dir / f"{record.blendswap_id}.json"
        self._atomic_write_text(path, record.model_dump_json(indent=2))
        return path

    def write_inspection(self, report: ReferenceInspectionReport) -> Path:
        path = self.inspections_dir / f"{report.blendswap_id}.json"
        self._atomic_write_text(path, report.model_dump_json(indent=2))
        return path

    def write_attribution(self, attr: AttributionRecord) -> Path:
        path = self.attribution_dir / f"{attr.blendswap_id}_attribution.json"
        self._atomic_write_text(path, attr.model_dump_json(indent=2))
        return path

    def export_attribution_registry(self, ref_db: ReferenceDatabase) -> Path:
        assets = ref_db.list_reference_assets(include_details=False)
        items = [
            {
                "blendswap_id": a.blendswap_id,
                "title": a.title,
                "url": a.url,
                "author_username": a.author.username,
                "author_profile_url": a.author.url,
                "license_key": a.license_assessment.license_key,
                "license_name": a.license_assessment.license_name,
                "license_url": a.license_assessment.license_url,
                "license_source": a.license_assessment.license_source,
                "legally_verified": a.license_assessment.legally_verified,
                "license_classes": [c.value for c in a.license_assessment.applicable_classes],
                "download_date": a.downloaded_at,
                "original_tags": a.original_tags,
            }
            for a in assets
        ]
        out = self.attribution_dir / "attribution_registry.json"
        self._atomic_write_text(
            out,
            json.dumps({"schema_version": "2.0", "generated_at": utc_now_iso(), "count": len(items), "attributions": items}, indent=2),
        )
        return out

    def write_benchmark_report(self, report: BenchmarkReport) -> Path:
        path = self.benchmark_reports_dir / f"{report.benchmark_id}.json"
        report.report_path = str(path)
        self._atomic_write_text(path, report.model_dump_json(indent=2))
        return path

    def write_collection_log(
        self,
        cycle: CollectionCycleRecord,
        queue_items: list[DownloadQueueItem],
        extra: Optional[dict[str, Any]] = None,
    ) -> Path:
        path = self.collection_logs_dir / f"cycle_{cycle.cycle_date}_{cycle.cycle_id}.json"
        cycle.log_path = str(path)
        payload = {
            "cycle": cycle.model_dump(),
            "queue": [q.model_dump() for q in queue_items],
            "details": extra or {},
        }
        self._atomic_write_text(path, json.dumps(payload, indent=2))
        return path

    def export_reference_index(self, ref_db: ReferenceDatabase) -> Path:
        assets = ref_db.list_reference_assets(include_details=False)
        payload = {
            "schema_version": "2.0",
            "generated_at": utc_now_iso(),
            "total_reference_assets": len(assets),
            "assets": [
                {
                    "blendswap_id": a.blendswap_id,
                    "title": a.title,
                    "primary_category": a.primary_category,
                    "technical_categories": a.technical_categories,
                    "license_key": a.license_assessment.license_key,
                    "grade_letter": a.grade_letter,
                    "technical_quality_score": a.technical_quality_score,
                    "popularity_score": a.popularity_score,
                    "status": a.status.value,
                    "sha256": a.sha256,
                }
                for a in assets
            ],
        }
        out = self.database_dir / "reference_index.json"
        self._atomic_write_text(out, json.dumps(payload, indent=2))
        return out

    _STORAGE_SUBDIRS = frozenset(
        {
            "assets",
            "previews",
            "metadata",
            "inspections",
            "attribution",
            "benchmark_reports",
            "collection_logs",
            "database",
        }
    )

    def _rebase_path(self, raw_path: Optional[str], *, fallback_rel: Optional[Path] = None) -> Optional[str]:
        """Rebase a stored artifact path to self.root_dir using its storage-relative subpath."""
        if raw_path and raw_path.strip():
            parts = [p for p in re.split(r"[\\/]+", raw_path.strip()) if p and p != "."]
            candidates = [
                (self.root_dir / Path(*parts[idx:])).resolve()
                for idx in range(len(parts) - 1, -1, -1)
                if parts[idx] in self._STORAGE_SUBDIRS
            ]
            for candidate in candidates:
                if candidate.exists():
                    return str(candidate)
            if candidates:
                return str(candidates[0])
            raw_obj = Path(raw_path.strip())
            if not raw_obj.is_absolute():
                return str((self.root_dir / raw_obj).resolve())
            if fallback_rel is not None:
                return str((self.root_dir / fallback_rel).resolve())
            return raw_path
        if fallback_rel is not None:
            candidate = (self.root_dir / fallback_rel).resolve()
            if candidate.exists():
                return str(candidate)
        return raw_path

    def _rebase_record_paths(self, rec: ReferenceAssetRecord, meta_path: Path) -> None:
        """Normalize all artifact paths on a restored ReferenceAssetRecord to self.root_dir."""
        asset_rel_dir = Path("assets") / category_folder_slug(rec.primary_category) / str(rec.blendswap_id)
        rec.local_dir = self._rebase_path(rec.local_dir, fallback_rel=asset_rel_dir)

        if rec.primary_file_path:
            fname = re.split(r"[\\/]+", rec.primary_file_path.strip())[-1]
            rec.primary_file_path = self._rebase_path(
                rec.primary_file_path,
                fallback_rel=asset_rel_dir / fname if fname else None,
            )
        if rec.blend_file_path:
            bname = re.split(r"[\\/]+", rec.blend_file_path.strip())[-1]
            rec.blend_file_path = self._rebase_path(
                rec.blend_file_path,
                fallback_rel=asset_rel_dir / bname if bname else None,
            )

        rebased_previews: list[str] = []
        for p in rec.preview_paths:
            pname = re.split(r"[\\/]+", p.strip())[-1] if p.strip() else ""
            rebased = self._rebase_path(
                p,
                fallback_rel=Path("previews") / str(rec.blendswap_id) / pname if pname else None,
            )
            if rebased:
                rebased_previews.append(rebased)
        rec.preview_paths = rebased_previews

        rec.metadata_path = str(meta_path.resolve())
        attr_rel = Path("attribution") / f"{rec.blendswap_id}_attribution.json"
        rec.attribution_path = self._rebase_path(rec.attribution_path, fallback_rel=attr_rel)

    def rebuild_database_from_disk(self, ref_db: ReferenceDatabase) -> dict[str, Any]:
        """Reconstruct Branch 2 SQLite catalogue from portable JSON files under reference_library/."""
        discovered = 0
        restored = 0
        errors: list[str] = []

        for meta_path in sorted(self.metadata_dir.glob("*.json")):
            discovered += 1
            try:
                rec = ReferenceAssetRecord.model_validate_json(meta_path.read_text(encoding="utf-8"))
                self._rebase_record_paths(rec, meta_path)
                ref_db.upsert_reference_asset(rec)
                insp_path = self.inspections_dir / f"{rec.blendswap_id}.json"
                if insp_path.is_file():
                    insp = ReferenceInspectionReport.model_validate_json(insp_path.read_text(encoding="utf-8"))
                    ref_db.save_inspection(insp)
                if rec.grade is not None:
                    ref_db.save_quality_grade(
                        rec.grade,
                        quality_category=rec.quality_category,
                        technical_categories=rec.technical_categories,
                    )
                restored += 1
            except Exception as exc:
                errors.append(f"{meta_path.name}: {exc}")

        for b_path in sorted(self.benchmark_reports_dir.glob("*.json")):
            try:
                report = BenchmarkReport.model_validate_json(b_path.read_text(encoding="utf-8"))
                report.report_path = str(b_path.resolve())
                ref_db.save_benchmark_report(report)
            except Exception as exc:
                errors.append(f"{b_path.name}: {exc}")

        return {"discovered": discovered, "restored": restored, "errors": errors}

    @staticmethod
    def _atomic_write_text(target: Path, content: str) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp_path: Optional[Path] = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=target.parent,
                prefix=f".{target.name}.",
                suffix=".tmp",
                delete=False,
            ) as fh:
                tmp_path = Path(fh.name)
                fh.write(content)
            for attempt in range(1, 9):
                try:
                    tmp_path.replace(target)
                    return
                except PermissionError:
                    if attempt < 8:
                        time.sleep(0.15 * attempt)
                        continue
                    target.write_text(content, encoding="utf-8")
                    tmp_path.unlink(missing_ok=True)
                    return
        except Exception:
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)
            raise
