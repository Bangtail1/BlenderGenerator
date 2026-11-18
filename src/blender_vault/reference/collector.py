"""Daily BlendSwap collection cycle orchestrator with persistent queue, retry, and crash recovery."""

from __future__ import annotations

import logging
import queue
import shutil
import threading
import time
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
    CollectionRulesConfig,
    DownloadQueueItem,
    ReferenceAssetRecord,
    ReferenceAssetStatus,
    SelectionCandidate,
    make_default_collection_stages,
)
from .ref_db import ReferenceDatabase
from .reference_drive_sync import ReferenceDriveSyncService
from .reference_storage import ReferenceStorage
from .rules_manager import CollectionRulesManager
from .selection_engine import SelectionEngine, compute_popularity_score

logger = logging.getLogger("blender_vault.reference.collector")


class CollectionEventBus:
    """Thread-safe pub/sub fan-out for live SSE collection timeline events."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: set[queue.Queue[dict[str, Any]]] = set()

    def subscribe(self) -> queue.Queue[dict[str, Any]]:
        q: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=256)
        with self._lock:
            self._subscribers.add(q)
        return q

    def unsubscribe(self, q: queue.Queue[dict[str, Any]]) -> None:
        with self._lock:
            self._subscribers.discard(q)

    def publish(self, event: dict[str, Any]) -> None:
        with self._lock:
            dead: list[queue.Queue[dict[str, Any]]] = []
            for q in self._subscribers:
                try:
                    q.put_nowait(event)
                except queue.Full:
                    dead.append(q)
            for q in dead:
                self._subscribers.discard(q)


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
        rules_manager: Optional[CollectionRulesManager] = None,
    ) -> None:
        self.settings = settings
        self.ref_db = ref_db
        self.ref_storage = ref_storage
        self.client = client
        self.inspector = inspector
        self.grader = grader
        self.drive_sync = drive_sync
        self.rules_manager = rules_manager or CollectionRulesManager(settings, ref_db.db)
        self.selector = SelectionEngine(settings, ref_db, client)
        self.event_bus = CollectionEventBus()
        self._ctrl_lock = threading.Lock()
        self._active_cycle_id: Optional[str] = None
        self._paused: bool = False
        self._cancel_requested: bool = False

    def is_running(self) -> bool:
        with self._ctrl_lock:
            return self._active_cycle_id is not None

    def get_control_state(self) -> dict[str, Any]:
        with self._ctrl_lock:
            return {
                "active_cycle_id": self._active_cycle_id,
                "paused": self._paused,
                "cancel_requested": self._cancel_requested,
            }

    def pause_active_cycle(self) -> dict[str, Any]:
        with self._ctrl_lock:
            self._paused = True
            cid = self._active_cycle_id
        if cid:
            cyc = self.ref_db.get_cycle(cid)
            if cyc and cyc.status == "RUNNING":
                cyc.paused = True
                cyc.status = "PAUSED"
                self.ref_db.update_cycle(cyc)
                self._broadcast_cycle(cyc)
        return self.get_control_state()

    def resume_active_cycle(self) -> dict[str, Any]:
        with self._ctrl_lock:
            self._paused = False
            cid = self._active_cycle_id
        if cid:
            cyc = self.ref_db.get_cycle(cid)
            if cyc and cyc.status == "PAUSED":
                cyc.paused = False
                cyc.status = "RUNNING"
                self.ref_db.update_cycle(cyc)
                self._broadcast_cycle(cyc)
        return self.get_control_state()

    def cancel_active_cycle(self) -> dict[str, Any]:
        with self._ctrl_lock:
            self._cancel_requested = True
            self._paused = False
            cid = self._active_cycle_id
        if cid:
            cyc = self.ref_db.get_cycle(cid)
            if cyc and cyc.status in ("PENDING", "RUNNING", "PAUSED"):
                cyc.cancel_requested = True
                cyc.paused = False
                self.ref_db.update_cycle(cyc)
                self._broadcast_cycle(cyc)
        return self.get_control_state()

    def _broadcast_cycle(self, cycle: CollectionCycleRecord) -> None:
        self.event_bus.publish(
            {
                "type": "cycle_update",
                "cycle": cycle.model_dump(),
                "queue": [q.model_dump() for q in self.ref_db.get_queue_for_cycle(cycle.cycle_id)],
                "control": self.get_control_state(),
                "timestamp": utc_now_iso(),
            }
        )

    def _set_cycle_stage(
        self,
        cycle: CollectionCycleRecord,
        stage_id: str,
        status: str,
        *,
        detail: Optional[str] = None,
    ) -> None:
        now = utc_now_iso()
        if not cycle.stages:
            cycle.stages = make_default_collection_stages()
        cycle.current_stage = stage_id
        for st in cycle.stages:
            if st["id"] == stage_id:
                st["status"] = status
                if status == "RUNNING" and not st.get("started_at"):
                    st["started_at"] = now
                if status in ("COMPLETED", "FAILED", "CANCELLED", "SKIPPED"):
                    st["completed_at"] = now
                if detail is not None:
                    st["detail"] = detail
                break
        self.ref_db.update_cycle(cycle)
        self._broadcast_cycle(cycle)

    def _wait_if_paused_or_cancelled(self, cycle: CollectionCycleRecord) -> bool:
        """Sleep cooperatively while paused; return True if cancellation was requested."""
        while True:
            with self._ctrl_lock:
                cancelled = self._cancel_requested
                paused = self._paused
            if cancelled:
                return True
            if not paused:
                return False
            time.sleep(0.25)

    def preview_candidates(self, *, rules_override: Optional[CollectionRulesConfig] = None) -> dict[str, Any]:
        """Preview candidates matching active (or provided) rules without spending allowance."""
        active_rules, has_override = self.rules_manager.get_active_rules()
        eff_rules = rules_override or active_rules
        result = self.selector.discover_and_select(target_count=eff_rules.daily_target, rules=eff_rules)
        excluded_set = set(eff_rules.excluded_candidate_ids or [])
        candidates_out: list[dict[str, Any]] = []
        for cand in result.selected:
            lic = assess_license(cand.asset.license, license_source_override=cand.asset.license_source)
            candidates_out.append(
                {
                    "blendswap_id": cand.asset.id,
                    "name": cand.asset.title,
                    "author": cand.asset.author.username,
                    "url": cand.asset.url,
                    "category": cand.mapped_primary_category,
                    "downloads": cand.asset.counts.downloads,
                    "likes": cand.asset.counts.likes,
                    "declared_license": lic.license_name,
                    "license_key": lic.license_key,
                    "file_format": cand.selected_file.format,
                    "filename": cand.selected_file.filename,
                    "file_size_bytes": cand.selected_file.size_bytes,
                    "modeling_style": cand.modeling_style_hint,
                    "selection_score": cand.selection_score,
                    "selection_reason": cand.slot_reason,
                    "slot_number": cand.slot_number,
                    "excluded": cand.asset.id in excluded_set,
                }
            )
        return {
            "rules": eff_rules.model_dump(),
            "has_next_run_override": has_override,
            "evaluated_count": result.evaluated_count,
            "filtered_reasons": result.filtered_reasons,
            "shortage_count": result.shortage_count,
            "shortage_reason": result.shortage_reason,
            "candidates": candidates_out,
        }

    def run_daily_cycle(
        self,
        *,
        cycle_date: str,
        trigger_type: str = "scheduled",
        max_downloads: Optional[int] = None,
        sync_to_drive: bool = True,
        rules_override: Optional[CollectionRulesConfig] = None,
    ) -> CollectionCycleRecord:
        """Execute a daily collection cycle (first recovering any incomplete interrupted cycles)."""
        # First recover any interrupted cycles cleanly before starting a brand new one
        self.recover_incomplete_cycles(sync_to_drive=sync_to_drive)

        active_rules, _ = self.rules_manager.get_active_rules()
        eff_rules = rules_override or active_rules
        if rules_override is None:
            self.rules_manager.consume_next_run_override()

        cycle_id = f"cyc_{uuid.uuid4().hex[:10]}"
        requested_target = max_downloads if max_downloads is not None else eff_rules.daily_target
        stages = make_default_collection_stages()

        with self._ctrl_lock:
            self._active_cycle_id = cycle_id
            self._paused = False
            self._cancel_requested = False

        cycle = CollectionCycleRecord(
            cycle_id=cycle_id,
            cycle_date=cycle_date,
            timezone=eff_rules.timezone or self.settings.collection_timezone,
            trigger_type=trigger_type,
            status="RUNNING",
            target_count=requested_target,
            current_stage="api_authentication",
            stages=stages,
            rules_snapshot=eff_rules.model_dump(),
        )
        self.ref_db.create_cycle(cycle)
        self._set_cycle_stage(cycle, "api_authentication", "RUNNING")

        try:
            # Always check real remaining allowance from authenticated GET /me
            try:
                acct = self.client.get_me()
                self._set_cycle_stage(cycle, "api_authentication", "COMPLETED", detail=f"Authenticated ({acct.username or 'OK'})")
            except Exception as exc:
                self._set_cycle_stage(cycle, "api_authentication", "FAILED", detail=str(exc))
                cycle.status = "FAILED"
                cycle.error_message = f"Failed to query BlendSwap /me before collection: {exc}"
                cycle.completed_at = utc_now_iso()
                self.ref_db.update_cycle(cycle)
                self._broadcast_cycle(cycle)
                self.ref_storage.write_collection_log(cycle, [])
                return cycle

            self._set_cycle_stage(cycle, "free_quota_verification", "RUNNING")
            cycle.free_allowance_start = acct.free_downloads_remaining
            cycle.credits_start = acct.credits

            # Never exceed free allowance; never spend credits
            allowed_slots = min(max(0, requested_target), acct.free_downloads_remaining)
            if allowed_slots <= 0:
                self._set_cycle_stage(cycle, "free_quota_verification", "FAILED", detail="0 free downloads remaining")
                cycle.status = "SKIPPED_NO_ALLOWANCE"
                cycle.free_allowance_end = acct.free_downloads_remaining
                cycle.credits_end = acct.credits
                cycle.shortage_count = requested_target
                cycle.shortage_reason = "Daily free download allowance is 0 (possibly shared with website downloads); credits preserved."
                cycle.completed_at = utc_now_iso()
                self.ref_db.update_cycle(cycle)
                self._broadcast_cycle(cycle)
                self.ref_storage.write_collection_log(cycle, [])
                return cycle

            cycle.target_count = allowed_slots
            self._set_cycle_stage(
                cycle,
                "free_quota_verification",
                "COMPLETED",
                detail=f"{acct.free_downloads_remaining} free remaining; target capped at {allowed_slots}",
            )

            if self._wait_if_paused_or_cancelled(cycle):
                return self._finalize_cancelled_cycle(cycle, [])

            self._set_cycle_stage(cycle, "popular_asset_discovery", "RUNNING")
            sel_result = self.selector.discover_and_select(target_count=allowed_slots, rules=eff_rules)
            self._set_cycle_stage(
                cycle,
                "popular_asset_discovery",
                "COMPLETED",
                detail=f"Evaluated {sel_result.evaluated_count} BlendSwap items",
            )

            self._set_cycle_stage(cycle, "candidate_selection", "RUNNING")
            cycle.shortage_count = sel_result.shortage_count
            cycle.shortage_reason = sel_result.shortage_reason
            self.ref_db.update_cycle(cycle)

            queue_items: list[DownloadQueueItem] = []
            for cand in sel_result.selected:
                self._upsert_discovered_candidate(cand)
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

            self._set_cycle_stage(
                cycle,
                "candidate_selection",
                "COMPLETED",
                detail=f"Selected {len(queue_items)} candidate(s)",
            )
            return self._process_cycle_queue(cycle, queue_items, sync_to_drive=sync_to_drive)
        except Exception as exc:
            logger.exception("Collection cycle %s failed", cycle_id)
            cycle.status = "FAILED"
            cycle.error_message = str(exc)
            cycle.completed_at = utc_now_iso()
            self.ref_db.update_cycle(cycle)
            self._broadcast_cycle(cycle)
            self.ref_storage.write_collection_log(cycle, self.ref_db.get_queue_for_cycle(cycle_id))
            return cycle
        finally:
            with self._ctrl_lock:
                if self._active_cycle_id == cycle_id:
                    self._active_cycle_id = None
                    self._paused = False
                    self._cancel_requested = False

    def _finalize_cancelled_cycle(self, cycle: CollectionCycleRecord, queue: list[DownloadQueueItem]) -> CollectionCycleRecord:
        for item in queue:
            if item.status in ("PENDING", "RETRY_WAIT"):
                item.status = "CANCELLED"
                item.last_error = "Cancelled by user."
                self.ref_db.update_queue_item(item)
        cycle.status = "CANCELLED"
        cycle.cancel_requested = True
        cycle.paused = False
        cycle.completed_at = utc_now_iso()
        if cycle.current_stage:
            self._set_cycle_stage(cycle, cycle.current_stage, "CANCELLED", detail="Cancelled by user")
        self.ref_db.update_cycle(cycle)
        self._broadcast_cycle(cycle)
        self.ref_storage.write_collection_log(cycle, self.ref_db.get_queue_for_cycle(cycle.cycle_id))
        return cycle

    def retry_failed_items(self, *, cycle_id: Optional[str] = None, sync_to_drive: bool = True) -> CollectionCycleRecord:
        """Retry FAILED or RETRY_WAIT items from a cycle without re-downloading completed assets."""
        cycle = self.ref_db.get_cycle(cycle_id) if cycle_id else self.ref_db.get_latest_cycle()
        if not cycle:
            raise KeyError("No collection cycle found to retry.")
        queue = self.ref_db.get_queue_for_cycle(cycle.cycle_id)
        retryable = [q for q in queue if q.status in ("FAILED", "RETRY_WAIT", "CANCELLED")]
        if not retryable:
            return cycle

        # Reset retryable items to PENDING
        for q in retryable:
            q.status = "PENDING"
            q.last_error = None
            q.attempts = 0
            self.ref_db.update_queue_item(q)

        with self._ctrl_lock:
            self._active_cycle_id = cycle.cycle_id
            self._paused = False
            self._cancel_requested = False
        try:
            cycle.status = "RUNNING"
            cycle.cancel_requested = False
            cycle.paused = False
            cycle.completed_at = None
            self.ref_db.update_cycle(cycle)
            self._broadcast_cycle(cycle)
            return self._process_cycle_queue(cycle, self.ref_db.get_queue_for_cycle(cycle.cycle_id), sync_to_drive=sync_to_drive)
        finally:
            with self._ctrl_lock:
                if self._active_cycle_id == cycle.cycle_id:
                    self._active_cycle_id = None
                    self._paused = False
                    self._cancel_requested = False

    def recover_incomplete_cycles(self, *, sync_to_drive: bool = True) -> list[CollectionCycleRecord]:
        """Resume any PENDING/RUNNING cycles after crash, shutdown, or internet disconnect without duplicating downloads."""
        recovered: list[CollectionCycleRecord] = []
        for cycle in self.ref_db.get_incomplete_cycles():
            logger.info("Recovering incomplete collection cycle %s (%s)", cycle.cycle_id, cycle.cycle_date)
            cycle.trigger_type = "resume"
            cycle.status = "RUNNING"
            cycle.paused = False
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

        if not cycle.stages:
            cycle.stages = make_default_collection_stages()

        for item in queue:
            if item.status == "COMPLETED":
                downloaded_now += 1
                continue
            if item.status in ("SKIPPED", "CANCELLED"):
                continue
            if item.status == "FAILED":
                failed_now += 1
                continue

            if self._wait_if_paused_or_cancelled(cycle):
                return self._finalize_cancelled_cycle(cycle, queue)

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
                self._set_cycle_stage(cycle, "asset_downloading", "RUNNING", detail=f"Downloading #{rec.blendswap_id} {rec.title}")
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
                    self._broadcast_cycle(cycle)
                    continue
                rec = self.ref_db.get_reference_asset(item.blendswap_id) or rec
                self._set_cycle_stage(cycle, "file_verification", "COMPLETED", detail=f"SHA-256 verified for #{rec.blendswap_id}")

            # Inspection + Grading + Categorization + DB Registration + Optional Google Drive Sync
            item.status = "INSPECTING"
            self.ref_db.update_queue_item(item)
            self._set_cycle_stage(cycle, "blender_inspection", "RUNNING", detail=f"Inspecting #{rec.blendswap_id} in Blender")
            self._inspect_grade_and_persist(rec, sync_to_drive=sync_to_drive, cycle=cycle)
            item.status = "COMPLETED"
            item.last_error = None
            self.ref_db.update_queue_item(item)
            downloaded_now += 1
            self._broadcast_cycle(cycle)

        fresh_queue = self.ref_db.get_queue_for_cycle(cycle.cycle_id)
        if fresh_queue:
            downloaded_now = sum(1 for q in fresh_queue if q.status == "COMPLETED")
            failed_now = sum(1 for q in fresh_queue if q.status == "FAILED")
            retry_pending = sum(1 for q in fresh_queue if q.status == "RETRY_WAIT")

        if downloaded_now > 0:
            self._set_cycle_stage(cycle, "asset_downloading", "COMPLETED", detail=f"{downloaded_now} downloaded")
            self._set_cycle_stage(cycle, "file_verification", "COMPLETED")
            self._set_cycle_stage(cycle, "blender_inspection", "COMPLETED")
            self._set_cycle_stage(cycle, "categorization", "COMPLETED")
            self._set_cycle_stage(cycle, "database_registration", "COMPLETED")
            self._set_cycle_stage(
                cycle,
                "google_drive_sync",
                "COMPLETED" if (sync_to_drive and self.drive_sync is not None) else "SKIPPED",
            )

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
        self._broadcast_cycle(cycle)

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

    def _inspect_grade_and_persist(
        self,
        rec: ReferenceAssetRecord,
        *,
        sync_to_drive: bool = True,
        cycle: Optional[CollectionCycleRecord] = None,
    ) -> ReferenceAssetRecord:
        if not rec.primary_file_path:
            return rec

        inspection = self.inspector.inspect_asset_file(
            source_file=Path(rec.primary_file_path),
            blendswap_id=rec.blendswap_id,
            preview_image_count=len(rec.preview_paths),
        )
        self.ref_storage.write_inspection(inspection)
        self.ref_db.save_inspection(inspection)
        if cycle is not None:
            self._set_cycle_stage(cycle, "blender_inspection", "COMPLETED", detail=f"Inspected #{rec.blendswap_id}")
            self._set_cycle_stage(cycle, "categorization", "RUNNING", detail=f"Categorizing & grading #{rec.blendswap_id}")

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
        if cycle is not None:
            self._set_cycle_stage(cycle, "categorization", "COMPLETED", detail=f"{rec.primary_category} · Grade {grade.grade_letter}")
            self._set_cycle_stage(cycle, "database_registration", "RUNNING", detail=f"Registering #{rec.blendswap_id}")

        self.ref_db.save_quality_grade(
            grade,
            quality_category=qual_cat,
            technical_categories=refined_tech,
        )

        updated = self.ref_db.get_reference_asset(rec.blendswap_id) or rec
        updated.metadata_path = str(self.ref_storage.write_metadata(updated))
        self.ref_db.upsert_reference_asset(updated)
        if cycle is not None:
            self._set_cycle_stage(cycle, "database_registration", "COMPLETED", detail=f"Saved #{rec.blendswap_id}")

        if sync_to_drive and self.drive_sync is not None and self.settings.reference_drive_sync_enabled:
            try:
                if self.drive_sync.gdrive.get_auth_status().get("authenticated"):
                    if cycle is not None:
                        self._set_cycle_stage(cycle, "google_drive_sync", "RUNNING", detail=f"Syncing #{updated.blendswap_id} to Drive")
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
