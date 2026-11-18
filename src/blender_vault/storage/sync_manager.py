"""Resilient Google Drive synchronization and retry queue manager for Roblox assets."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from googleapiclient.errors import HttpError

from ..database import (
    AssetRepository,
    CategoryRepository,
    LogRepository,
    ProductionStatus,
    RobloxDatabaseManager,
    VersionRepository,
)
from ..database.models import utc_now_iso
from ..db import VaultDatabase
from ..hashing import compute_md5, compute_sha256
from ..models import AssetStatus
from ..services.gdrive_service import (
    DriveIntegrityError,
    DriveNotAuthenticatedError,
    DriveNotConfiguredError,
    GoogleDriveSyncService,
)

logger = logging.getLogger("blender_vault.storage.sync_manager")


class RobloxDriveSyncManager:
    """Reuses GoogleDriveSyncService while preserving local assets and queueing retries on outage."""

    def __init__(
        self,
        gdrive: GoogleDriveSyncService,
        roblox_db: RobloxDatabaseManager,
        vault_db: Optional[VaultDatabase] = None,
    ) -> None:
        self.gdrive = gdrive
        self.db = roblox_db
        self.vault_db = vault_db
        self.assets = AssetRepository(roblox_db)
        self.categories = CategoryRepository(roblox_db)
        self.versions = VersionRepository(roblox_db)
        self.logs = LogRepository(roblox_db)

    def sync_asset_version(
        self,
        asset_id: str,
        version_number: Optional[int] = None,
        *,
        require_approved_for_production: bool = True,
        queue_on_failure: bool = True,
        extra_files: Optional[list[tuple[str, Path]]] = None,
    ) -> dict[str, Any]:
        """Upload an asset version to Google Drive via existing OAuth/resumable service.

        - Never uploads incomplete or QA_FAILED assets as approved production assets.
        - If Google Drive is temporarily unavailable, preserves local files and queues retry.
        """
        asset = self.assets.get_asset(asset_id)
        if not asset:
            raise KeyError(f"Asset '{asset_id}' not found.")

        ver_num = version_number if version_number is not None else asset.current_version
        version = self.versions.get_asset_version(asset_id, ver_num)
        if not version:
            raise KeyError(f"Asset '{asset_id}' version v{ver_num:03d} not found.")

        if asset.production_status == ProductionStatus.QA_FAILED or version.qa_status in ("QA_FAILED", "FAILED"):
            raise ValueError(f"Cannot upload failed asset '{asset_id}' v{ver_num:03d} to Google Drive.")

        blend_path = Path(version.blender_source_path) if version.blender_source_path else None
        export_path = Path(version.export_path) if version.export_path else None
        if (not blend_path or not blend_path.is_file()) and (not export_path or not export_path.is_file()):
            raise FileNotFoundError(
                f"Cannot sync incomplete asset '{asset_id}' v{ver_num:03d}: missing local .blend and export files."
            )

        is_approved = bool(
            version.is_approved
            and version.qa_status == "APPROVED"
            and asset.production_status in (ProductionStatus.APPROVED, ProductionStatus.PUBLISHED)
        )
        if require_approved_for_production and not is_approved:
            raise ValueError(
                f"Cannot sync unapproved asset '{asset_id}' v{ver_num:03d} "
                f"(status='{asset.production_status.value}', version_qa='{version.qa_status}') "
                "to production Google Drive."
            )
        if not require_approved_for_production and asset.production_status in (
            ProductionStatus.PLANNED,
            ProductionStatus.QUEUED,
            ProductionStatus.MODELLING,
            ProductionStatus.TEXTURING,
            ProductionStatus.EXPORTING,
        ):
            raise ValueError(
                f"Cannot sync incomplete asset '{asset_id}' in status '{asset.production_status.value}'."
            )

        try:
            # Reuse Branch 1 vault sync directly only when linked status aligns with approval state
            if asset.legacy_vault_id and self.vault_db is not None:
                va = self.vault_db.get_asset(asset.legacy_vault_id)
                vv = self.vault_db.get_version(asset.legacy_vault_id, ver_num) if va else None
                vault_already_approved = bool(vv and vv.status in (AssetStatus.APPROVED, AssetStatus.UPLOADED))
                if va and vv and vv.status != AssetStatus.FAILED and (is_approved or not vault_already_approved):
                    if is_approved and vv.status not in (AssetStatus.APPROVED, AssetStatus.UPLOADED):
                        vv.status = AssetStatus.APPROVED
                        self.vault_db.update_version(vv)
                    updated_vv = self.gdrive.upload_asset_version(va, vv)
                    blend_rec = next((d for d in updated_vv.drive_files if d.role == "blend" and d.remote_file_id), None)
                    primary_rec = blend_rec or (updated_vv.drive_files[0] if updated_vv.drive_files else None)
                    file_id = primary_rec.remote_file_id if primary_rec else None
                    folder_id = updated_vv.drive_folder_id
                    url = primary_rec.web_view_link if primary_rec else None
                    self._upload_extra_files(folder_id, extra_files)
                    self._record_drive_success(asset_id, version.version_id, file_id, folder_id, url, is_approved)
                    return {
                        "synced": True,
                        "queued": False,
                        "asset_id": asset_id,
                        "version_number": ver_num,
                        "google_drive_file_id": file_id,
                        "google_drive_folder_id": folder_id,
                        "google_drive_url": url,
                        "bucket": "Approved Assets" if is_approved else "Pending Review",
                    }

            # Direct upload path reusing GoogleDriveSyncService.get_service() and _upload_and_verify_file()
            svc = self.gdrive.get_service()
            tree = self.gdrive.ensure_vault_folder_tree()
            cat = self.categories.get_by_id(asset.category_id)
            legacy_cat = (cat.legacy_category if cat and cat.legacy_category else None) or "Miscellaneous"

            if is_approved:
                bucket = tree["categories"].get(legacy_cat) or tree["categories"]["Miscellaneous"]
                bucket_label = f"Approved Assets/{legacy_cat}"
            elif asset.production_status == ProductionStatus.REJECTED:
                bucket = tree["rejected_id"]
                bucket_label = "Rejected Assets"
            else:
                bucket = tree["pending_id"]
                bucket_label = "Pending Review"

            asset_folder_id = self.gdrive._ensure_child_folder(svc, bucket, asset_id)
            ver_folder_id = self.gdrive._ensure_child_folder(svc, asset_folder_id, f"v{ver_num:03d}")

            uploaded_records = []
            if blend_path and blend_path.is_file():
                uploaded_records.append(
                    self.gdrive._upload_and_verify_file(
                        svc,
                        local_path=blend_path,
                        remote_name=blend_path.name,
                        parent_folder_id=ver_folder_id,
                        role="blend",
                    )
                )
            if export_path and export_path.is_file():
                uploaded_records.append(
                    self.gdrive._upload_and_verify_file(
                        svc,
                        local_path=export_path,
                        remote_name=export_path.name,
                        parent_folder_id=ver_folder_id,
                        role=(version.export_format or "export").lower(),
                    )
                )
            for role, p in extra_files or []:
                if p and Path(p).is_file():
                    uploaded_records.append(
                        self.gdrive._upload_and_verify_file(
                            svc,
                            local_path=Path(p),
                            remote_name=Path(p).name,
                            parent_folder_id=ver_folder_id,
                            role=role,
                        )
                    )

            primary = next((r for r in uploaded_records if r.role == "blend"), uploaded_records[0])
            self._record_drive_success(
                asset_id,
                version.version_id,
                primary.remote_file_id,
                ver_folder_id,
                primary.web_view_link,
                is_approved,
            )
            return {
                "synced": True,
                "queued": False,
                "asset_id": asset_id,
                "version_number": ver_num,
                "google_drive_file_id": primary.remote_file_id,
                "google_drive_folder_id": ver_folder_id,
                "google_drive_url": primary.web_view_link,
                "bucket": bucket_label,
                "uploaded_count": len(uploaded_records),
            }

        except (
            DriveNotAuthenticatedError,
            DriveNotConfiguredError,
            DriveIntegrityError,
            HttpError,
            OSError,
            ConnectionError,
            TimeoutError,
        ) as exc:
            logger.warning("Google Drive temporarily unavailable for %s v%03d: %s", asset_id, ver_num, exc)
            if not queue_on_failure:
                raise
            payload = {
                "version_number": ver_num,
                "extra_files": [(role, str(Path(p))) for role, p in (extra_files or [])],
                "require_approved_for_production": require_approved_for_production,
            }
            q_item = self.logs.enqueue_drive_upload(
                asset_id=asset_id,
                version_id=version.version_id,
                payload=payload,
                last_error=str(exc),
            )
            self.logs.log_event(
                asset_id=asset_id,
                process_name="google_drive_sync",
                status="RETRY_QUEUED",
                message=f"Drive sync deferred for v{ver_num:03d}; local asset preserved and queued (queue_id={q_item.queue_id}).",
                error_details=str(exc),
            )
            return {
                "synced": False,
                "queued": True,
                "queue_id": q_item.queue_id,
                "asset_id": asset_id,
                "version_number": ver_num,
                "error": str(exc),
            }

    def _upload_extra_files(self, folder_id: Optional[str], extra_files: Optional[list[tuple[str, Path]]]) -> None:
        if not folder_id or not extra_files:
            return
        svc = self.gdrive.get_service()
        for role, path_obj in extra_files:
            p = Path(path_obj)
            if p.is_file():
                self.gdrive._upload_and_verify_file(
                    svc,
                    local_path=p,
                    remote_name=p.name,
                    parent_folder_id=folder_id,
                    role=role,
                )

    def _record_drive_success(
        self,
        asset_id: str,
        version_id: int,
        file_id: Optional[str],
        folder_id: Optional[str],
        url: Optional[str],
        is_approved: bool,
    ) -> None:
        now = utc_now_iso()
        with self.db.connect(immediate=True) as conn:
            conn.execute(
                """
                UPDATE assets SET
                    google_drive_file_id = COALESCE(?, google_drive_file_id),
                    google_drive_folder_id = COALESCE(?, google_drive_folder_id),
                    google_drive_url = COALESCE(?, google_drive_url),
                    updated_at = ?
                WHERE asset_id = ?
                """,
                (file_id, folder_id, url, now, asset_id),
            )
            # Attach Drive IDs without mutating immutable local source/export paths
            conn.execute(
                """
                UPDATE asset_versions SET
                    google_drive_file_id = COALESCE(?, google_drive_file_id),
                    google_drive_folder_id = COALESCE(?, google_drive_folder_id),
                    google_drive_url = COALESCE(?, google_drive_url)
                WHERE version_id = ?
                """,
                (file_id, folder_id, url, version_id),
            )
            self.logs.log_event(
                asset_id=asset_id,
                process_name="google_drive_sync",
                status="SYNCED",
                message=(
                    f"Uploaded version_id={version_id} to Google Drive "
                    f"({'Approved Assets' if is_approved else 'Pending Review'}); file_id={file_id}."
                ),
                conn=conn,
            )

    def retry_pending_uploads(self, *, include_failed: bool = False) -> dict[str, Any]:
        """Process queued Drive uploads when connectivity/auth returns."""
        pending = self.logs.list_pending_drive_uploads(include_failed=include_failed)
        attempted = 0
        succeeded = 0
        still_pending = 0
        errors: list[str] = []

        for item in pending:
            attempted += 1
            ver = self.versions.get_version(item.version_id)
            if not ver:
                self.logs.mark_drive_upload_attempt(item.queue_id, error="Version record no longer exists.")
                still_pending += 1
                continue
            extra = [(r, Path(p)) for r, p in item.payload.get("extra_files", [])]
            try:
                self.sync_asset_version(
                    item.asset_id,
                    ver.version_number,
                    require_approved_for_production=bool(item.payload.get("require_approved_for_production", True)),
                    queue_on_failure=False,
                    extra_files=extra,
                )
                self.logs.mark_drive_upload_completed(item.queue_id)
                succeeded += 1
            except Exception as exc:
                self.logs.mark_drive_upload_attempt(item.queue_id, error=str(exc))
                still_pending += 1
                errors.append(f"{item.asset_id}: {exc}")

        return {
            "attempted": attempted,
            "succeeded": succeeded,
            "remaining": still_pending,
            "errors": errors,
        }
