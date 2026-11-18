"""Google Drive remote backup synchronization for Branch 2 Reference Library."""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ..models import DriveFileRecord, DriveSyncStatus
from ..services.gdrive_service import GoogleDriveSyncService
from .models import ReferenceAssetRecord
from .ref_db import ReferenceDatabase
from .reference_storage import ReferenceStorage, category_folder_slug

logger = logging.getLogger("blender_vault.reference.drive_sync")

_ACTIVE_UPLOADS_LOCK = threading.Lock()
_ACTIVE_UPLOADS: set[tuple[str, int]] = set()


class ReferenceDriveSyncService:
    """Reuses GoogleDriveSyncService OAuth/retry/resumable uploads under a dedicated Reference Library/ tree."""

    def __init__(
        self,
        gdrive: GoogleDriveSyncService,
        ref_db: ReferenceDatabase,
        ref_storage: ReferenceStorage,
        *,
        upload_lease_seconds: int = 900,
    ) -> None:
        self.gdrive = gdrive
        self.ref_db = ref_db
        self.ref_storage = ref_storage
        self.upload_lease_seconds = upload_lease_seconds

    def _db_key(self) -> str:
        return str(self.ref_db.db.db_path.resolve())

    def _is_lease_expired(self, updated_at: Optional[str]) -> bool:
        if not updated_at:
            return True
        try:
            cleaned = updated_at.strip()
            if cleaned.endswith("Z"):
                cleaned = cleaned[:-1] + "+00:00"
            dt = datetime.fromisoformat(cleaned)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - dt).total_seconds() >= self.upload_lease_seconds
        except ValueError:
            return True

    def recover_abandoned_uploads(self) -> int:
        """Reset abandoned UPLOADING records from prior crashed runs back to PENDING."""
        db_key = self._db_key()
        with _ACTIVE_UPLOADS_LOCK:
            active_ids = {aid for k, aid in _ACTIVE_UPLOADS if k == db_key}
        return self.ref_db.recover_abandoned_drive_uploads(active_ids=active_ids)

    def ensure_reference_drive_tree(self) -> dict[str, Any]:
        """Create/locate Blender Asset Vault/Reference Library/... folders without modifying Branch 1 buckets."""
        svc = self.gdrive.get_service()
        stored_root = (
            self.gdrive.db.get_setting("gdrive_root_folder_id")
            or self.gdrive.settings.gdrive_root_folder_id
        )
        if not stored_root:
            stored_root = self.gdrive._ensure_child_folder(
                svc, "root", self.gdrive.settings.gdrive_root_folder_name
            )
            self.gdrive.db.set_setting("gdrive_root_folder_id", stored_root)

        ref_root_id = self.gdrive._ensure_child_folder(svc, stored_root, "Reference Library")
        assets_id = self.gdrive._ensure_child_folder(svc, ref_root_id, "Assets")
        metadata_id = self.gdrive._ensure_child_folder(svc, ref_root_id, "Metadata")
        inspections_id = self.gdrive._ensure_child_folder(svc, ref_root_id, "Inspections")
        benchmarks_id = self.gdrive._ensure_child_folder(svc, ref_root_id, "Benchmark Reports")
        attribution_id = self.gdrive._ensure_child_folder(svc, ref_root_id, "Attribution")
        logs_id = self.gdrive._ensure_child_folder(svc, ref_root_id, "Collection Logs")
        index_id = self.gdrive._ensure_child_folder(svc, ref_root_id, "Library Index")

        return {
            "root_id": stored_root,
            "ref_root_id": ref_root_id,
            "assets_id": assets_id,
            "metadata_id": metadata_id,
            "inspections_id": inspections_id,
            "benchmarks_id": benchmarks_id,
            "attribution_id": attribution_id,
            "logs_id": logs_id,
            "index_id": index_id,
        }

    def sync_reference_asset(self, blendswap_id: int) -> ReferenceAssetRecord:
        """Upload original reference asset, previews, metadata, inspection, and attribution with MD5 verification."""
        rec = self.ref_db.get_reference_asset(blendswap_id)
        if not rec:
            raise KeyError(f"Reference asset {blendswap_id} not found.")

        if not rec.primary_file_path or not Path(rec.primary_file_path).is_file():
            self.ref_db.update_drive_sync_state(blendswap_id, status=DriveSyncStatus.FAILED)
            raise FileNotFoundError(
                f"Primary reference binary missing for BlendSwap #{blendswap_id}: {rec.primary_file_path}"
            )

        upload_key = (self._db_key(), blendswap_id)
        with _ACTIVE_UPLOADS_LOCK:
            _ACTIVE_UPLOADS.add(upload_key)

        asset_folder: Optional[str] = rec.drive_folder_id
        uploaded: list[DriveFileRecord] = []
        try:
            svc = self.gdrive.get_service()
            tree = self.ensure_reference_drive_tree()

            cat_folder = self.gdrive._ensure_child_folder(svc, tree["assets_id"], category_folder_slug(rec.primary_category))
            asset_folder = self.gdrive._ensure_child_folder(svc, cat_folder, str(rec.blendswap_id))

            self.ref_db.update_drive_sync_state(blendswap_id, status=DriveSyncStatus.UPLOADING, folder_id=asset_folder)

            # 1. Primary downloaded binary (original untouched)
            p = Path(rec.primary_file_path)
            uploaded.append(
                self.gdrive._upload_and_verify_file(
                    svc,
                    local_path=p,
                    remote_name=p.name,
                    parent_folder_id=asset_folder,
                    role="reference_asset",
                )
            )

            # 2. Preview images inside previews subfolder
            if rec.preview_paths:
                prev_folder = self.gdrive._ensure_child_folder(svc, asset_folder, "previews")
                for p_str in rec.preview_paths:
                    p = Path(p_str)
                    if p.is_file():
                        uploaded.append(
                            self.gdrive._upload_and_verify_file(
                                svc,
                                local_path=p,
                                remote_name=p.name,
                                parent_folder_id=prev_folder,
                                role="preview",
                            )
                        )

            # 3. Portable metadata, inspection, and attribution JSONs
            if rec.metadata_path and Path(rec.metadata_path).is_file():
                p = Path(rec.metadata_path)
                uploaded.append(
                    self.gdrive._upload_or_update_metadata_file(
                        svc,
                        local_path=p,
                        remote_name=p.name,
                        parent_folder_id=tree["metadata_id"],
                        role="metadata",
                    )
                )
            insp_path = self.ref_storage.inspections_dir / f"{rec.blendswap_id}.json"
            if insp_path.is_file():
                uploaded.append(
                    self.gdrive._upload_or_update_metadata_file(
                        svc,
                        local_path=insp_path,
                        remote_name=insp_path.name,
                        parent_folder_id=tree["inspections_id"],
                        role="inspection",
                    )
                )
            if rec.attribution_path and Path(rec.attribution_path).is_file():
                p = Path(rec.attribution_path)
                uploaded.append(
                    self.gdrive._upload_or_update_metadata_file(
                        svc,
                        local_path=p,
                        remote_name=p.name,
                        parent_folder_id=tree["attribution_id"],
                        role="attribution",
                    )
                )

            # 4. Update remote index
            idx_path = self.ref_storage.export_reference_index(self.ref_db)
            self.gdrive._upload_or_update_metadata_file(
                svc,
                local_path=idx_path,
                remote_name="reference_index.json",
                parent_folder_id=tree["index_id"],
                role="reference_index",
            )

            self.ref_db.update_drive_sync_state(
                blendswap_id,
                status=DriveSyncStatus.VERIFIED,
                folder_id=asset_folder,
                drive_files=uploaded,
            )
            updated = self.ref_db.get_reference_asset(blendswap_id)
            if updated:
                self.ref_storage.write_metadata(updated)
                return updated
            return rec
        except Exception as exc:
            logger.exception("Reference Drive sync failed for BlendSwap #%s", blendswap_id)
            self.ref_db.update_drive_sync_state(
                blendswap_id,
                status=DriveSyncStatus.FAILED,
                folder_id=asset_folder,
                drive_files=uploaded,
            )
            raise
        finally:
            with _ACTIVE_UPLOADS_LOCK:
                _ACTIVE_UPLOADS.discard(upload_key)

    def sync_benchmark_report(self, report_path: Path) -> Optional[DriveFileRecord]:
        if not report_path.is_file():
            return None
        svc = self.gdrive.get_service()
        tree = self.ensure_reference_drive_tree()
        return self.gdrive._upload_or_update_metadata_file(
            svc,
            local_path=report_path,
            remote_name=report_path.name,
            parent_folder_id=tree["benchmarks_id"],
            role="benchmark_report",
        )

    def sync_collection_log(self, log_path: Path) -> Optional[DriveFileRecord]:
        if not log_path.is_file():
            return None
        svc = self.gdrive.get_service()
        tree = self.ensure_reference_drive_tree()
        return self.gdrive._upload_or_update_metadata_file(
            svc,
            local_path=log_path,
            remote_name=log_path.name,
            parent_folder_id=tree["logs_id"],
            role="collection_log",
        )

    def sync_pending_assets(self, *, max_items: int = 20) -> dict[str, Any]:
        """Retry/sync any downloaded/graded reference assets not yet verified on Google Drive."""
        assets = self.ref_db.list_reference_assets(include_details=False)
        db_key = self._db_key()
        with _ACTIVE_UPLOADS_LOCK:
            active_ids = {aid for k, aid in _ACTIVE_UPLOADS if k == db_key}

        def _is_retryable(rec: ReferenceAssetRecord) -> bool:
            if not rec.primary_file_path:
                return False
            if rec.drive_sync_status in (DriveSyncStatus.NOT_SYNCED, DriveSyncStatus.PENDING, DriveSyncStatus.FAILED):
                return True
            if rec.drive_sync_status == DriveSyncStatus.UPLOADING:
                return rec.blendswap_id not in active_ids and self._is_lease_expired(rec.updated_at)
            return False

        pending = [a for a in assets if _is_retryable(a)][:max_items]
        synced = 0
        failed: list[dict[str, Any]] = []
        for item in pending:
            try:
                self.sync_reference_asset(item.blendswap_id)
                synced += 1
            except Exception as exc:
                failed.append({"blendswap_id": item.blendswap_id, "error": str(exc)})
        return {"attempted": len(pending), "synced": synced, "failed": failed}
