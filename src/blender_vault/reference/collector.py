"""Daily BlendSwap collection cycle orchestrator with persistent queue, retry, and crash recovery."""

from __future__ import annotations

import logging
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ..config import Settings
from ..models import DriveSyncStatus, utc_now_iso
from .blendswap_client import (
    BlendSwapAPIError,
    BlendSwapAllowanceExhaustedError,
    BlendSwapClient,
)
from .categorizer import (
    classify_primary_category,
    classify_quality_category,
    classify_technical_categories,
)
from .grading_engine import QualityGradingEngine
from .inspector_service import ReferenceInspectorService
from .license_engine import assess_license, build_attribution_record
from .models import (
    CollectionCycleRecord,
    DownloadQueueItem,
    ReferenceAssetRecord,
    ReferenceAssetStatus,
    SelectionCandidate,
)
from .ref_db import ReferenceDatabase
from .reference_drive_sync import ReferenceDriveSyncService
from .reference_storage import ReferenceStorage
from .selection_engine import SelectionEngine, compute_popularity_score

logger = logging.getLogger("blender_vault.reference.collector")


class CollectionCycleService:
    """Executes and recovers daily free BlendSwap collection cycles safely."""

    def __init__(
        self,
        settings: Settings,
        ref_db: ReferenceDatabase,
        ref_storage: ReferenceStorage,
        client: BlendSwapClient,
        inspector: ReferenceInspectorService,
        grader: QualityGradingEngine,
        drive_sync: Optional[ReferenceDriveSyncService] = None,
    ) -> None:
        self.settings = settings
        self.ref_db = ref_db
        self.ref_storage = ref_storage
        self.client = client
        self.inspector = inspector
        self.grader = grader
        self.drive_sync = drive_sync
        self.selector = SelectionEngine(settings, ref_db, client)

    def run_daily_cycle(
        self,
        *,
        cycle_date: str,
        trigger_type: str = "scheduled",
        max_downloads: Optional[int] = None,
        sync_to_drive: bool = True,
    ) -> CollectionCycleRecord:
        """Execute a daily collection cycle (first recovering any incomplete interrupted cycles)."""
        # First recover any interrupted cycles cleanly before starting a brand new one
        self.recover_incomplete_cycles(sync_to_drive=sync_to_drive)

        cycle_id = f"cyc_{uuid.uuid4().hex[:10]}"
        requested_target = max_downloads if max_downloads is not None else self.settings.collection_daily_target

        # Always check real remaining allowance from authenticated GET /me
        try:
            acct = self.client.get_me()
        except Exception as exc:
            cycle = CollectionCycleRecord(
                cycle_id=cycle_id,
                cycle_date=cycle_date,
                timezone=self.settings.collection_timezone,
                trigger_type=trigger_type,
                status="FAILED",
                target_count=requested_target,
                error_message=f"Failed to query BlendSwap /me before collection: {exc}",
            )
            self.ref_db.create_cycle(cycle)
            self.ref_storage.write_collection_log(cycle, [])
            return cycle

        # Never exceed free allowance; never spend credits
        allowed_slots = min(max(0, requested_target), acct.free_downloads_remaining)
        if allowed_slots <= 0:
            cycle = CollectionCycleRecord(
                cycle_id=cycle_id,
                cycle_date=cycle_date,
                timezone=self.settings.collection_timezone,
                trigger_type=trigger_type,
                status="SKIPPED_NO_ALLOWANCE",
                target_count=requested_target,
                free_allowance_start=acct.free_downloads_remaining,
                free_allowance_end=acct.free_downloads_remaining,
                credits_start=acct.credits,
                credits_end=acct.credits,
                shortage_count=requested_target,
                shortage_reason="Daily free download allowance is 0 (possibly shared with website downloads); credits preserved.",
                completed_at=utc_now_iso(),
            )
            self.ref_db.create_cycle(cycle)
            self.ref_storage.write_collection_log(cycle, [])
            return cycle

        cycle = CollectionCycleRecord(
            cycle_id=cycle_id,
            cycle_date=cycle_date,
            timezone=self.settings.collection_timezone,
            trigger_type=trigger_type,
            status="RUNNING",
            target_count=allowed_slots,
            free_allowance_start=acct.free_downloads_remaining,
            credits_start=acct.credits,
        )
        self.ref_db.create_cycle(cycle)

        try:
            sel_result = self.selector.discover_and_select(target_count=allowed_slots)
            cycle.shortage_count = sel_result.shortage_count
            cycle.shortage_reason = sel_result.shortage_reason
            self.ref_db.update_cycle(cycle)

            queue_items: list[DownloadQueueItem] = []
            for cand in sel_result.selected:
                rec = self._upsert_discovered_candidate(cand)
                q_item = DownloadQueueItem(
                    queue_id=f"q_{uuid.uuid4().hex[:10]}",
                    cycle_id=cycle_id,
                    blendswap_id=cand.asset.id,
                    file_id=cand.selected_file.id or None,
                    slot_number=cand.slot_number,
                    slot_reason=cand.slot_reason,
                    selection_score=cand.selection_score,
                    status="PENDING",
                )
                self.ref_db.enqueue_download_item(q_item)
                queue_items.append(q_item)

            return self._process_cycle_queue(cycle, queue_items, sync_to_drive=sync_to_drive)
        except Exception as exc:
            logger.exception("Collection cycle %s failed", cycle_id)
            cycle.status = "FAILED"
            cycle.error_message = str(exc)
            cycle.completed_at = utc_now_iso()
            self.ref_db.update_cycle(cycle)
            self.ref_storage.write_collection_log(cycle, self.ref_db.get_queue_for_cycle(cycle_id))
            return cycle

    def recover_incomplete_cycles(self, *, sync_to_drive: bool = True) -> list[CollectionCycleRecord]:
        """Resume any PENDING/RUNNING cycles after crash, shutdown, or internet disconnect without duplicating downloads."""
        recovered: list[CollectionCycleRecord] = []
        for cycle in self.ref_db.get_incomplete_cycles():
            logger.info("Recovering incomplete collection cycle %s (%s)", cycle.cycle_id, cycle.cycle_date)
            cycle.trigger_type = "resume"
            cycle.status = "RUNNING"
            self.ref_db.update_cycle(cycle)
            queue = self.ref_db.get_queue_for_cycle(cycle.cycle_id)
            if not queue:
                cycle.status = "FAILED"
                cycle.error_message = "Interrupted before candidate queue was persisted."
                cycle.completed_at = utc_now_iso()
                self.ref_db.update_cycle(cycle)
                recovered.append(cycle)
                continue
            recovered.append(self._process_cycle_queue(cycle, queue, sync_to_drive=sync_to_drive))
        return recovered

    def _process_cycle_queue(
        self,
        cycle: CollectionCycleRecord,
        queue: list[DownloadQueueItem],
        *,
        sync_to_drive: bool = True,
    ) -> CollectionCycleRecord:
        downloaded_now = 0
        failed_now = 0
        retry_pending = 0
        unexpected_charge_detected = False

        for item in queue:
            if item.status == "COMPLETED":
                downloaded_now += 1
                continue
            if item.status == "SKIPPED":
                continue
            if item.status == "FAILED":
                failed_now += 1
                continue
            if unexpected_charge_detected:
                item.status = "SKIPPED"
                item.last_error = "Halted remaining downloads after unexpected credit charge."
                self.ref_db.update_queue_item(item)
                continue

            rec = self.ref_db.get_reference_asset(item.blendswap_id)
            if rec is None:
                try:
                    summary = self.client.get_asset(item.blendswap_id)
                    lic = assess_license(summary.license, license_source_override=summary.license_source)
                    rec = ReferenceAssetRecord(
                        blendswap_id=summary.id,
                        title=summary.title,
                        url=summary.url,
                        description=summary.description,
                        asset_type=summary.asset_type,
                        asset_type_label=summary.asset_type_label,
                        author=summary.author,
                        license_assessment=lic,
                        blendswap_category=summary.category,
                        primary_category=classify_primary_category(summary, ref_db=self.ref_db),
                        technical_categories=classify_technical_categories(summary, ref_db=self.ref_db),
                        tags=list(summary.tags),
                        original_tags=list(summary.tags),
                        blender_versions=list(summary.blender_versions),
                        counts=summary.counts,
                        explicit=summary.explicit,
                        files=list(summary.files),
                        popularity_score=compute_popularity_score(summary),
                        selection_score=item.selection_score,
                        status=ReferenceAssetStatus.QUEUED,
                    )
                    self.ref_db.upsert_reference_asset(rec, selected_file_id=item.file_id)
                except Exception as exc:
                    item.status = "FAILED"
                    item.last_error = f"Could not restore asset metadata: {exc}"
                    self.ref_db.update_queue_item(item)
                    failed_now += 1
                    continue

            # If already downloaded locally (crash happened during inspection/grading!), resume post-download stages
            already_local = bool(rec.primary_file_path and Path(rec.primary_file_path).is_file())
            if not already_local:
                credits_before = cycle.credits_spent
                success = self._download_queue_item(cycle, item, rec)
                if cycle.credits_spent > credits_before and not self.settings.allow_credit_spend:
                    unexpected_charge_detected = True
                    cycle.error_message = (
                        "Halted remaining queue items because BlendSwap charged credits while allow_credit_spend=False."
                    )
                if not success:
                    if item.status == "RETRY_WAIT":
                        retry_pending += 1
                    else:
                        failed_now += 1
                    continue
                rec = self.ref_db.get_reference_asset(item.blendswap_id) or rec

            # Inspection + Grading + Portable Storage + Attribution + Optional Google Drive Sync
            item.status = "INSPECTING"
            self.ref_db.update_queue_item(item)
            self._inspect_grade_and_persist(rec, sync_to_drive=sync_to_drive)
            item.status = "COMPLETED"
            item.last_error = None
            self.ref_db.update_queue_item(item)
            downloaded_now += 1

        # Refresh final account state (non-fatal if offline)
        try:
            acct_end = self.client.get_me()
            cycle.free_allowance_end = acct_end.free_downloads_remaining
            cycle.credits_end = acct_end.credits
        except Exception:
            pass

        cycle.downloaded_count = downloaded_now
        cycle.failed_count = failed_now
        if retry_pending > 0:
            cycle.status = "RETRY_WAIT"
            cycle.completed_at = None
        elif downloaded_now == 0 and failed_now > 0:
            cycle.status = "FAILED"
            cycle.completed_at = utc_now_iso()
        elif failed_now > 0 or cycle.shortage_count > 0 or unexpected_charge_detected:
            cycle.status = "PARTIAL"
            cycle.completed_at = utc_now_iso()
        else:
            cycle.status = "COMPLETED"
            cycle.completed_at = utc_now_iso()
        self.ref_db.update_cycle(cycle)

        log_path = self.ref_storage.write_collection_log(cycle, self.ref_db.get_queue_for_cycle(cycle.cycle_id))
        self.ref_storage.export_reference_index(self.ref_db)
        self.ref_storage.export_attribution_registry(self.ref_db)

        if sync_to_drive and self.drive_sync is not None and self.settings.reference_drive_sync_enabled:
            try:
                if self.drive_sync.gdrive.get_auth_status().get("authenticated"):
                    self.drive_sync.sync_collection_log(log_path)
            except Exception as exc:
                logger.warning("Could not sync collection log to Drive: %s", exc)

        return cycle

    def _download_queue_item(
        self,
        cycle: CollectionCycleRecord,
        item: DownloadQueueItem,
        rec: ReferenceAssetRecord,
    ) -> bool:
        item.attempts += 1
        item.status = "DOWNLOADING"
        self.ref_db.update_queue_item(item)

        tmp_dl_dir = (self.settings.tmp_dir / f"bs_dl_{cycle.cycle_id}_{rec.blendswap_id}").resolve()
        tmp_dl_dir.mkdir(parents=True, exist_ok=True)
        selected_file = next((f for f in rec.files if f.id == item.file_id), rec.files[0] if rec.files else None)
        filename = selected_file.filename if selected_file else f"blendswap_{rec.blendswap_id}.blend"
        expected_size = selected_file.size_bytes if selected_file and selected_file.size_bytes > 0 else None
        tmp_file = tmp_dl_dir / Path(filename).name

        try:
            ticket, size_b, sha, md5 = self.client.download_with_refresh(
                asset_id=rec.blendswap_id,
                file_id=item.file_id,
                dest_path=tmp_file,
                expected_size=expected_size,
            )
            if ticket.charged and not self.settings.allow_credit_spend:
                cycle.credits_spent += ticket.credits_spent

            item.download_url = ticket.download_url
            item.download_url_expires_at = ticket.expires_at
            self.ref_db.update_queue_item(item)

            saved_path, saved_size, saved_sha, saved_md5 = self.ref_storage.preserve_downloaded_file(
                temp_path=tmp_file,
                primary_category=rec.primary_category,
                blendswap_id=rec.blendswap_id,
                original_filename=filename,
            )
            previews_dir = self.ref_storage.asset_previews_dir(rec.blendswap_id)
            saved_previews = self.client.download_previews(ticket.preview_urls, previews_dir)

            # Duplicate SHA-256 note (keep record cleanly)
            dup_id = self.ref_db.find_reference_by_sha256(saved_sha)
            if dup_id and dup_id != rec.blendswap_id:
                logger.info("Downloaded file for #%s matches SHA-256 of existing #%s", rec.blendswap_id, dup_id)

            now = utc_now_iso()
            rec.local_dir = str(saved_path.parent)
            rec.primary_file_path = str(saved_path)
            rec.blend_file_path = str(saved_path) if saved_path.suffix.lower() == ".blend" else None
            rec.file_size_bytes = saved_size
            rec.sha256 = saved_sha
            rec.md5 = saved_md5
            rec.preview_paths = [str(p) for p in saved_previews]
            rec.status = ReferenceAssetStatus.DOWNLOADED
            rec.downloaded_at = now
            rec.error_message = None

            # Persist attribution & metadata JSON immediately
            summary_for_attr = self._record_to_summary(rec)
            attr = build_attribution_record(summary_for_attr, rec.license_assessment, download_date=now)
            rec.attribution_path = str(self.ref_storage.write_attribution(attr))
            rec.metadata_path = str(self.ref_storage.write_metadata(rec))
            self.ref_db.upsert_reference_asset(rec, selected_file_id=item.file_id)

            self.ref_db.record_download_history(
                history_id=f"dlh_{uuid.uuid4().hex[:12]}",
                cycle_id=cycle.cycle_id,
                blendswap_id=rec.blendswap_id,
                file_id=item.file_id,
                reason=ticket.reason,
                charged=ticket.charged,
                credits_spent=ticket.credits_spent,
                credits_remaining=ticket.credits_remaining,
                download_url_expires_at=ticket.expires_at,
                local_path=str(saved_path),
                sha256=saved_sha,
                file_size_bytes=saved_size,
                success=True,
            )
            return True
        except Exception as exc:
            logger.warning("Download attempt %d failed for BlendSwap #%s: %s", item.attempts, rec.blendswap_id, exc)
            item.last_error = str(exc)
            exhausted = item.attempts >= item.max_attempts
            item.status = "FAILED" if exhausted else "RETRY_WAIT"
            self.ref_db.update_queue_item(item)

            rec.status = ReferenceAssetStatus.FAILED if exhausted else ReferenceAssetStatus.QUEUED
            rec.error_message = str(exc)
            self.ref_db.upsert_reference_asset(rec, selected_file_id=item.file_id)
            self.ref_db.record_download_history(
                history_id=f"dlh_{uuid.uuid4().hex[:12]}",
                cycle_id=cycle.cycle_id,
                blendswap_id=rec.blendswap_id,
                file_id=item.file_id,
                reason="failed",
                charged=False,
                credits_spent=0,
                credits_remaining=None,
                download_url_expires_at=None,
                local_path=None,
                sha256=None,
                file_size_bytes=0,
                success=False,
                error_message=str(exc),
            )
            return False
        finally:
            if tmp_dl_dir.is_dir():
                shutil.rmtree(tmp_dl_dir, ignore_errors=True)

    def _inspect_grade_and_persist(self, rec: ReferenceAssetRecord, *, sync_to_drive: bool = True) -> ReferenceAssetRecord:
        if not rec.primary_file_path:
            return rec

        inspection = self.inspector.inspect_asset_file(
            source_file=Path(rec.primary_file_path),
            blendswap_id=rec.blendswap_id,
            preview_image_count=len(rec.preview_paths),
        )
        self.ref_storage.write_inspection(inspection)
        self.ref_db.save_inspection(inspection)

        summary = self._record_to_summary(rec)
        refined_tech = classify_technical_categories(summary, inspection=inspection, ref_db=self.ref_db)
        grade = self.grader.grade(
            inspection,
            primary_category=rec.primary_category,
            technical_categories=refined_tech,
            asset_type=rec.asset_type,
            popularity_score=rec.popularity_score,
            blendswap_id=rec.blendswap_id,
        )
        qual_cat = classify_quality_category(grade.grade_letter)
        self.ref_db.save_quality_grade(
            grade,
            quality_category=qual_cat,
            technical_categories=refined_tech,
        )

        updated = self.ref_db.get_reference_asset(rec.blendswap_id) or rec
        updated.metadata_path = str(self.ref_storage.write_metadata(updated))
        self.ref_db.upsert_reference_asset(updated)

        if sync_to_drive and self.drive_sync is not None and self.settings.reference_drive_sync_enabled:
            try:
                if self.drive_sync.gdrive.get_auth_status().get("authenticated"):
                    updated = self.drive_sync.sync_reference_asset(updated.blendswap_id)
            except Exception as exc:
                logger.warning("Drive sync deferred for BlendSwap #%s: %s", updated.blendswap_id, exc)
        return updated

    def _upsert_discovered_candidate(self, cand: SelectionCandidate) -> ReferenceAssetRecord:
        summary = cand.asset
        lic = assess_license(summary.license, license_source_override=summary.license_source)
        primary_cat = classify_primary_category(summary, ref_db=self.ref_db)
        tech_cats = classify_technical_categories(summary, ref_db=self.ref_db)
        is_native = cand.selected_file.format.lower() in {"blender", "blend"} or cand.selected_file.filename.lower().endswith(".blend")

        rec = ReferenceAssetRecord(
            blendswap_id=summary.id,
            title=summary.title,
            url=summary.url,
            description=summary.description,
            asset_type=summary.asset_type,
            asset_type_label=summary.asset_type_label,
            author=summary.author,
            license_assessment=lic,
            blendswap_category=summary.category,
            primary_category=primary_cat,
            technical_categories=tech_cats,
            tags=sorted({t.strip().lower() for t in summary.tags if t.strip()}),
            original_tags=list(summary.tags),
            blender_versions=list(summary.blender_versions),
            counts=summary.counts,
            explicit=summary.explicit,
            primary_format=cand.selected_file.format,
            is_native_blender=is_native,
            file_size_bytes=cand.selected_file.size_bytes or summary.size_bytes,
            files=list(summary.files) or [cand.selected_file],
            status=ReferenceAssetStatus.QUEUED,
            popularity_score=cand.popularity_score,
            selection_score=cand.selection_score,
            blendswap_created_at=summary.created_at,
        )
        self.ref_db.upsert_reference_asset(rec, selected_file_id=cand.selected_file.id or None)
        return rec

    @staticmethod
    def _record_to_summary(rec: ReferenceAssetRecord):
        from .models import BlendSwapAssetSummary, BlendSwapLicenseInfo

        return BlendSwapAssetSummary(
            id=rec.blendswap_id,
            title=rec.title,
            url=rec.url,
            description=rec.description,
            asset_type=rec.asset_type,
            asset_type_label=rec.asset_type_label,
            license=BlendSwapLicenseInfo(
                key=rec.license_assessment.license_key,
                name=rec.license_assessment.license_name,
                url=rec.license_assessment.license_url,
                license_source=rec.license_assessment.license_source,
            ),
            license_source=rec.license_assessment.license_source,
            tags=rec.original_tags or rec.tags,
            category=rec.blendswap_category,
            author=rec.author,
            counts=rec.counts,
            explicit=rec.explicit,
            blender_versions=rec.blender_versions,
            size_bytes=rec.file_size_bytes,
            created_at=rec.blendswap_created_at,
            files=rec.files,
        )
