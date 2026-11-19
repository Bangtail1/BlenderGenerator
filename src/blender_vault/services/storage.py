"""Local immutable versioned asset storage, manifest persistence, and catalogue re-indexing."""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any

from ..db import VaultDatabase
from ..hashing import compute_md5, compute_sha256
from ..models import AssetManifest, AssetRecord, AssetVersion, utc_now_iso

logger = logging.getLogger("blender_vault.storage")


class VaultStorage:
    """Manages per-asset, per-version local directories and portable JSON manifests."""

    def __init__(self, assets_dir: Path, imports_dir: Path) -> None:
        self.assets_dir = assets_dir
        self.imports_dir = imports_dir
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        self.imports_dir.mkdir(parents=True, exist_ok=True)

    def version_dir(self, asset_id: str, version: int) -> Path:
        path = self.assets_dir / asset_id / f"v{version}"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def previews_dir(self, asset_id: str, version: int) -> Path:
        path = self.version_dir(asset_id, version) / "previews"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def preserve_original_import(self, asset_id: str, version: int, uploaded_temp_path: Path, original_filename: str) -> Path:
        """Copy an imported source model into immutable imports_original storage."""
        dest_dir = self.imports_dir / asset_id / f"v{version}"
        dest_dir.mkdir(parents=True, exist_ok=True)
        safe_name = Path(original_filename).name
        dest = dest_dir / safe_name
        shutil.copy2(uploaded_temp_path, dest)
        return dest

    def compute_file_hashes(self, path: Path) -> tuple[int, str, str]:
        return path.stat().st_size, compute_sha256(path), compute_md5(path)

    def write_version_artifacts(self, asset: AssetRecord, version: AssetVersion) -> dict[str, Path]:
        """Write manifest.json, qa_report.json, visual_qa.json, and provenance.json atomically."""
        vdir = self.version_dir(asset.asset_id, version.version)
        written: dict[str, Path] = {}

        manifest = AssetManifest(
            asset_id=asset.asset_id,
            slug=asset.slug,
            name=asset.name,
            description=asset.description,
            category=asset.category,
            geometry_profile=asset.geometry_profile,
            tags=asset.tags,
            provenance=asset.provenance,
            version=version,
            generated_at=utc_now_iso(),
        )
        manifest_path = vdir / "manifest.json"
        self._atomic_write_text(manifest_path, manifest.model_dump_json(indent=2))
        written["manifest"] = manifest_path

        prov_path = vdir / "provenance.json"
        self._atomic_write_text(prov_path, asset.provenance.model_dump_json(indent=2))
        written["provenance"] = prov_path

        if version.technical_qa is not None:
            qa_path = vdir / "qa_report.json"
            self._atomic_write_text(qa_path, version.technical_qa.model_dump_json(indent=2))
            written["qa_report"] = qa_path

        if version.visual_qa is not None:
            vqa_path = vdir / "visual_qa.json"
            self._atomic_write_text(vqa_path, version.visual_qa.model_dump_json(indent=2))
            written["visual_qa"] = vqa_path

        return written

    def export_library_index(self, db: VaultDatabase, out_path: Path) -> Path:
        """Write a consolidated library index JSON from all assets in the catalogue."""
        assets = db.list_assets()
        payload: dict[str, Any] = {
            "schema_version": "1.0",
            "generated_at": utc_now_iso(),
            "asset_count": len(assets),
            "assets": [
                {
                    "asset_id": a.asset_id,
                    "slug": a.slug,
                    "name": a.name,
                    "category": a.category,
                    "current_version": a.current_version,
                    "status": a.latest.status.value if a.latest else "UNKNOWN",
                    "tags": a.tags,
                    "sha256": a.latest.sha256 if a.latest else None,
                    "drive_folder_id": a.latest.drive_folder_id if a.latest else None,
                    "updated_at": a.updated_at,
                }
                for a in assets
            ],
        }
        self._atomic_write_text(out_path, json.dumps(payload, indent=2))
        return out_path

    def rebuild_database_from_manifests(self, db: VaultDatabase) -> dict[str, Any]:
        """Scan all local manifest.json files and reconstruct/update the SQLite catalogue."""
        discovered = 0
        reindexed = 0
        errors: list[str] = []

        for manifest_path in sorted(self.assets_dir.glob("*/*/manifest.json")):
            discovered += 1
            try:
                manifest = AssetManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
                db.upsert_from_manifest(manifest)
                reindexed += 1
            except Exception as exc:
                msg = f"{manifest_path}: {exc}"
                logger.error("Failed to re-index manifest %s", msg)
                errors.append(msg)

        return {"discovered": discovered, "reindexed": reindexed, "errors": errors}

    def delete_asset_files(self, asset_id: str) -> dict[str, bool]:
        """Safely remove local asset and original import directories for a deleted asset."""
        cleaned_id = str(asset_id or "").strip()
        if not cleaned_id or Path(cleaned_id).name != cleaned_id or cleaned_id in (".", ".."):
            raise ValueError(f"Invalid asset_id '{asset_id}' for deletion.")

        removed = {"assets_dir_removed": False, "imports_dir_removed": False}
        for key, base_dir in (("assets_dir_removed", self.assets_dir), ("imports_dir_removed", self.imports_dir)):
            base_resolved = base_dir.resolve()
            target = (base_dir / cleaned_id).resolve()
            if not target.is_relative_to(base_resolved) or target == base_resolved:
                raise ValueError(f"Refusing to delete outside vault directory: {target}")
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
                removed[key] = not target.exists()
        return removed

    @staticmethod
    def _atomic_write_text(target: Path, content: str) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(target)
