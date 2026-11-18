"""Persistent timezone-aware daily collection scheduler with reset detection and missed-run recovery."""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..config import Settings
from ..db import VaultDatabase
from .collector import CollectionCycleService
from .models import CollectionCycleRecord, CollectionScheduleConfig, SchedulerNotificationEvent
from .ref_db import ReferenceDatabase

logger = logging.getLogger("blender_vault.reference.scheduler")


class _LondonTzInfo(tzinfo):
    """Exact UK GMT/BST fallback tzinfo when OS/tzdata ZoneInfo database is unavailable."""

    @staticmethod
    def _last_sunday(year: int, month: int) -> date:
        if month == 12:
            d = date(year, 12, 31)
        else:
            d = date(year, month + 1, 1) - timedelta(days=1)
        return d - timedelta(days=(d.weekday() + 1) % 7)

    @classmethod
    def _bst_bounds_utc(cls, year: int) -> tuple[datetime, datetime]:
        start_utc = datetime.combine(cls._last_sunday(year, 3), time(1, 0))
        end_utc = datetime.combine(cls._last_sunday(year, 10), time(1, 0))
        return start_utc, end_utc

    def _is_bst_wall(self, dt: datetime) -> bool:
        naive = dt.replace(tzinfo=None)
        start_utc, end_utc = self._bst_bounds_utc(naive.year)
        if naive < start_utc:
            return False
        if naive < end_utc:
            return True
        if naive < end_utc + timedelta(hours=1):
            return getattr(dt, "fold", 0) == 0
        return False

    def utcoffset(self, dt: Optional[datetime]) -> timedelta:
        if dt is None:
            return timedelta(0)
        return timedelta(hours=1) if self._is_bst_wall(dt) else timedelta(0)

    def dst(self, dt: Optional[datetime]) -> timedelta:
        return self.utcoffset(dt)

    def tzname(self, dt: Optional[datetime]) -> str:
        return "BST" if (dt is not None and self._is_bst_wall(dt)) else "GMT"

    def fromutc(self, dt: datetime) -> datetime:
        if dt.tzinfo is not self:
            raise ValueError("fromutc: dt.tzinfo must be self")
        utc_naive = dt.replace(tzinfo=None)
        start_utc, end_utc = self._bst_bounds_utc(utc_naive.year)
        if start_utc <= utc_naive < end_utc:
            return (dt + timedelta(hours=1)).replace(fold=0)
        if end_utc <= utc_naive < end_utc + timedelta(hours=1):
            return dt.replace(fold=1)
        return dt.replace(fold=0)


def resolve_timezone(tz_name: str) -> tzinfo:
    """Resolve IANA timezone with safe GMT/BST fallback for Europe/London on Windows without tzdata."""
    clean = (tz_name or "Europe/London").strip()
    try:
        return ZoneInfo(clean)
    except ZoneInfoNotFoundError:
        if clean.lower() in {"europe/london", "uk", "london"}:
            return _LondonTzInfo()
        if clean.upper() in {"UTC", "GMT"}:
            return timezone.utc
        logger.warning("Timezone '%s' not found in system tzdata; falling back to Europe/London.", clean)
        return _LondonTzInfo()


def parse_schedule_time(hhmm: str) -> tuple[int, int]:
    parts = (hhmm or "03:00").strip().split(":")
    try:
        hour = max(0, min(23, int(parts[0])))
        minute = max(0, min(59, int(parts[1]))) if len(parts) > 1 else 0
        return hour, minute
    except ValueError:
        return 3, 0


def _parse_iso_dt(raw: Optional[str], tz: tzinfo) -> Optional[datetime]:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(tz)
    except ValueError:
        return None


class DailyCollectionScheduler:
    """Persistent daily collection scheduler surviving restarts, shutdowns, DST transitions, and reset delays."""

    KEY_LAST_DATE = "ref_scheduler_last_cycle_date"
    KEY_ARMED_SINCE = "ref_scheduler_armed_since"
    KEY_FREQUENCY = "ref_scheduler_frequency"
    KEY_SCHEDULE_TIME = "ref_scheduler_schedule_time"
    KEY_TIMEZONE = "ref_scheduler_timezone"
    KEY_ENABLED = "ref_scheduler_enabled"
    KEY_TARGET_DOWNLOADS = "ref_scheduler_target_downloads"
    KEY_FREE_ONLY = "ref_scheduler_free_only"
    KEY_AUTO_CREDIT = "ref_scheduler_auto_credit_spend"
    KEY_RESUME_MISSED = "ref_scheduler_resume_missed"
    KEY_CONFIG_MTIME = "ref_scheduler_config_file_mtime_ns"

    KEY_STATE = "ref_scheduler_state"
    KEY_POSTPONED_DATE = "ref_scheduler_postponed_date"
    KEY_POSTPONED_REASON = "ref_scheduler_postponed_reason"
    KEY_NEXT_RECHECK_AT = "ref_scheduler_next_recheck_at"
    KEY_LAST_ALLOWANCE_CHECK_AT = "ref_scheduler_last_allowance_check_at"
    KEY_LAST_ALLOWANCE_SEEN = "ref_scheduler_last_allowance_seen"
    KEY_ALLOWANCE_RESET_DETECTED_AT = "ref_scheduler_allowance_reset_detected_at"
    KEY_LAST_CYCLE_ID = "ref_scheduler_last_cycle_id"
    KEY_LAST_CYCLE_STATUS = "ref_scheduler_last_cycle_status"
    KEY_LAST_ERROR = "ref_scheduler_last_error"

    def __init__(
        self,
        settings: Settings,
        db: VaultDatabase,
        collector: CollectionCycleService,
        *,
        ref_db: Optional[ReferenceDatabase] = None,
        poll_interval_seconds: float = 30.0,
        reset_recheck_minutes: Optional[int] = None,
    ) -> None:
        self.settings = settings
        self.db = db
        self.collector = collector
        self.ref_db = ref_db or getattr(collector, "ref_db", None) or ReferenceDatabase(db)
        self.poll_interval_seconds = poll_interval_seconds
        self.reset_recheck_minutes = max(
            1,
            int(
                reset_recheck_minutes
                if reset_recheck_minutes is not None
                else settings.collection_reset_recheck_minutes
            ),
        )
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._run_lock = threading.Lock()
        self._config_lock = threading.Lock()
        self._sync_config_storage()

    @property
    def config_path(self) -> Path:
        return self.settings.resolved_collection_schedule_config_path

    def _default_schedule_model(self) -> CollectionScheduleConfig:
        h, m = parse_schedule_time(self.settings.collection_schedule_time)
        return CollectionScheduleConfig(
            enabled=self.settings.collection_scheduler_enabled,
            frequency=(self.settings.collection_schedule_frequency or "daily").strip().lower(),
            collection_time=f"{h:02d}:{m:02d}",
            timezone=(self.settings.collection_timezone or "Europe/London").strip(),
            target_downloads=max(1, int(self.settings.collection_daily_target)),
            free_downloads_only=not self.settings.allow_credit_spend,
            automatic_credit_spending=bool(self.settings.allow_credit_spend),
            resume_missed_jobs=bool(self.settings.collection_resume_missed_jobs),
        )

    def _write_config_to_db(self, model: CollectionScheduleConfig) -> None:
        self.db.set_setting(self.KEY_ENABLED, "true" if model.enabled else "false")
        self.db.set_setting(self.KEY_FREQUENCY, model.frequency)
        self.db.set_setting(self.KEY_SCHEDULE_TIME, model.collection_time)
        self.db.set_setting(self.KEY_TIMEZONE, model.timezone)
        self.db.set_setting(self.KEY_TARGET_DOWNLOADS, str(model.target_downloads))
        self.db.set_setting(self.KEY_FREE_ONLY, "true" if model.free_downloads_only else "false")
        self.db.set_setting(self.KEY_AUTO_CREDIT, "true" if model.automatic_credit_spending else "false")
        self.db.set_setting(self.KEY_RESUME_MISSED, "true" if model.resume_missed_jobs else "false")

    def _read_config_from_db(self) -> Optional[CollectionScheduleConfig]:
        sched_time = self.db.get_setting(self.KEY_SCHEDULE_TIME)
        if sched_time is None:
            return None
        enabled_raw = self.db.get_setting(self.KEY_ENABLED)
        target_raw = self.db.get_setting(self.KEY_TARGET_DOWNLOADS)
        free_only_raw = self.db.get_setting(self.KEY_FREE_ONLY)
        auto_credit_raw = self.db.get_setting(self.KEY_AUTO_CREDIT)
        resume_raw = self.db.get_setting(self.KEY_RESUME_MISSED)
        h, m = parse_schedule_time(sched_time)
        return CollectionScheduleConfig(
            enabled=self.settings.collection_scheduler_enabled if enabled_raw is None else enabled_raw.lower() == "true",
            frequency=(self.db.get_setting(self.KEY_FREQUENCY) or self.settings.collection_schedule_frequency).strip().lower(),
            collection_time=f"{h:02d}:{m:02d}",
            timezone=(self.db.get_setting(self.KEY_TIMEZONE) or self.settings.collection_timezone).strip(),
            target_downloads=max(1, int(target_raw)) if target_raw else max(1, int(self.settings.collection_daily_target)),
            free_downloads_only=(not self.settings.allow_credit_spend) if free_only_raw is None else free_only_raw.lower() == "true",
            automatic_credit_spending=bool(self.settings.allow_credit_spend) if auto_credit_raw is None else auto_credit_raw.lower() == "true",
            resume_missed_jobs=bool(self.settings.collection_resume_missed_jobs) if resume_raw is None else resume_raw.lower() == "true",
        )

    def _write_config_json(self, model: CollectionScheduleConfig) -> None:
        path = self.config_path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(model.model_dump(), indent=2), encoding="utf-8")
        os.replace(tmp, path)
        self.db.set_setting(self.KEY_CONFIG_MTIME, str(path.stat().st_mtime_ns))

    def _sync_config_storage(self) -> CollectionScheduleConfig:
        with self._config_lock:
            path = self.config_path
            if path.is_file():
                try:
                    mtime_ns = str(path.stat().st_mtime_ns)
                    stored_mtime = self.db.get_setting(self.KEY_CONFIG_MTIME)
                    db_cfg = self._read_config_from_db()
                    if stored_mtime != mtime_ns or db_cfg is None:
                        raw = json.loads(path.read_text(encoding="utf-8"))
                        h, m = parse_schedule_time(str(raw.get("collection_time") or self.settings.collection_schedule_time))
                        raw["collection_time"] = f"{h:02d}:{m:02d}"
                        resolve_timezone(str(raw.get("timezone") or self.settings.collection_timezone))
                        model = CollectionScheduleConfig.model_validate(raw)
                        self._write_config_to_db(model)
                        self.db.set_setting(self.KEY_CONFIG_MTIME, mtime_ns)
                        return model
                    return db_cfg
                except Exception as exc:
                    logger.warning("Failed to load schedule config JSON (%s); using DB/defaults: %s", path, exc)

            db_cfg = self._read_config_from_db()
            if db_cfg is not None:
                self._write_config_json(db_cfg)
                return db_cfg

            default_cfg = self._default_schedule_model()
            self._write_config_to_db(default_cfg)
            self._write_config_json(default_cfg)
            return default_cfg

    def _ensure_armed_date(self, today: date) -> str:
        armed = self.db.get_setting(self.KEY_ARMED_SINCE)
        if armed:
            return armed
        iso = today.isoformat()
        self.db.set_setting(self.KEY_ARMED_SINCE, iso)
        return iso

    def get_schedule_config(self, *, now: Optional[datetime] = None) -> dict[str, Any]:
        model = self._sync_config_storage()
        next_daily = self.compute_next_daily_run_at(now=now, _cfg=model)
        tz = resolve_timezone(model.timezone)
        current = (now or datetime.now(timezone.utc)).astimezone(tz)
        today_str = current.date().isoformat()
        last_ran = self.db.get_setting(self.KEY_LAST_DATE)
        recheck_dt = _parse_iso_dt(self.db.get_setting(self.KEY_NEXT_RECHECK_AT), tz)
        if recheck_dt and recheck_dt < current:
            recheck_dt = current

        next_wakeup = next_daily
        if recheck_dt is not None and last_ran != today_str and self.db.get_setting(self.KEY_POSTPONED_DATE):
            next_wakeup = recheck_dt

        raw_state = self.db.get_setting(self.KEY_STATE)
        if not model.enabled:
            scheduler_state = "PAUSED"
        elif self.db.get_setting(self.KEY_POSTPONED_DATE) and last_ran != today_str:
            scheduler_state = raw_state or "POSTPONED_WAITING_RESET"
        else:
            scheduler_state = raw_state if raw_state == "RUNNING" else "SCHEDULED"

        seen_raw = self.db.get_setting(self.KEY_LAST_ALLOWANCE_SEEN)
        return {
            **model.model_dump(),
            # Backwards-compatible aliases
            "schedule_time": model.collection_time,
            "daily_target": model.target_downloads,
            "last_cycle_date": last_ran,
            "next_run_at": next_wakeup.isoformat(),
            "next_daily_run_at": next_daily.isoformat(),
            "next_recheck_at": recheck_dt.isoformat() if recheck_dt else None,
            "scheduler_state": scheduler_state,
            "paused": not model.enabled,
            "postponed_date": self.db.get_setting(self.KEY_POSTPONED_DATE) or None,
            "postponed_reason": self.db.get_setting(self.KEY_POSTPONED_REASON) or None,
            "last_allowance_check_at": self.db.get_setting(self.KEY_LAST_ALLOWANCE_CHECK_AT) or None,
            "last_allowance_seen": int(seen_raw) if (seen_raw is not None and seen_raw.isdigit()) else None,
            "allowance_reset_detected_at": self.db.get_setting(self.KEY_ALLOWANCE_RESET_DETECTED_AT) or None,
            "last_cycle_id": self.db.get_setting(self.KEY_LAST_CYCLE_ID) or None,
            "last_cycle_status": self.db.get_setting(self.KEY_LAST_CYCLE_STATUS) or None,
            "last_error": self.db.get_setting(self.KEY_LAST_ERROR) or None,
            "reset_recheck_minutes": self.reset_recheck_minutes,
            "config_path": str(self.config_path),
        }

    def update_schedule_config(
        self,
        *,
        enabled: Optional[bool] = None,
        schedule_time: Optional[str] = None,
        collection_time: Optional[str] = None,
        timezone_name: Optional[str] = None,
        frequency: Optional[str] = None,
        target_downloads: Optional[int] = None,
        resume_missed_jobs: Optional[bool] = None,
        free_downloads_only: Optional[bool] = None,
        automatic_credit_spending: Optional[bool] = None,
    ) -> dict[str, Any]:
        current_model = self._sync_config_storage()
        time_input = collection_time if collection_time is not None else schedule_time
        new_time = current_model.collection_time
        if time_input is not None:
            h, m = parse_schedule_time(time_input)
            new_time = f"{h:02d}:{m:02d}"

        new_tz = current_model.timezone
        if timezone_name is not None:
            resolve_timezone(timezone_name)
            new_tz = timezone_name.strip()

        new_auto_credit = (
            current_model.automatic_credit_spending
            if automatic_credit_spending is None
            else bool(automatic_credit_spending and self.settings.allow_credit_spend)
        )
        new_free_only = (
            not new_auto_credit
            if free_downloads_only is None
            else (True if not self.settings.allow_credit_spend else bool(free_downloads_only))
        )

        updated = CollectionScheduleConfig(
            enabled=current_model.enabled if enabled is None else bool(enabled),
            frequency=current_model.frequency if frequency is None else frequency.strip().lower(),
            collection_time=new_time,
            timezone=new_tz,
            target_downloads=(
                current_model.target_downloads
                if target_downloads is None
                else max(1, min(50, int(target_downloads)))
            ),
            free_downloads_only=new_free_only,
            automatic_credit_spending=new_auto_credit,
            resume_missed_jobs=(
                current_model.resume_missed_jobs if resume_missed_jobs is None else bool(resume_missed_jobs)
            ),
        )
        with self._config_lock:
            self._write_config_to_db(updated)
            self._write_config_json(updated)
            if updated.enabled:
                if not (self.db.get_setting(self.KEY_POSTPONED_DATE) or ""):
                    self.db.set_setting(self.KEY_STATE, "SCHEDULED")
            else:
                self.db.set_setting(self.KEY_STATE, "PAUSED")
        return self.get_schedule_config()

    def pause(self) -> dict[str, Any]:
        cfg = self.update_schedule_config(enabled=False)
        self._record_simple_event(
            event_type="SCHEDULER_PAUSED",
            status="PAUSED",
            message="Automatic daily collection paused by user.",
            next_scheduled_run=cfg.get("next_run_at"),
        )
        return cfg

    def resume(self) -> dict[str, Any]:
        cfg = self.update_schedule_config(enabled=True)
        self._record_simple_event(
            event_type="SCHEDULER_RESUMED",
            status="SCHEDULED",
            message=f"Automatic daily collection resumed ({cfg['collection_time']} {cfg['timezone']}).",
            next_scheduled_run=cfg.get("next_run_at"),
        )
        return cfg

    def compute_next_daily_run_at(
        self,
        now: Optional[datetime] = None,
        *,
        _cfg: Optional[CollectionScheduleConfig] = None,
    ) -> datetime:
        model = _cfg or self._sync_config_storage()
        tz = resolve_timezone(model.timezone)
        h, m = parse_schedule_time(model.collection_time)
        current = (now or datetime.now(timezone.utc)).astimezone(tz)
        today_str = current.date().isoformat()
        candidate = datetime.combine(current.date(), time(h, m), tzinfo=tz)
        last_ran = self.db.get_setting(self.KEY_LAST_DATE)
        if candidate <= current and last_ran == today_str:
            candidate = datetime.combine(current.date() + timedelta(days=1), time(h, m), tzinfo=tz)
        elif candidate <= current and last_ran != today_str:
            return current
        return candidate

    def compute_next_run_at(self, now: Optional[datetime] = None) -> datetime:
        model = self._sync_config_storage()
        tz = resolve_timezone(model.timezone)
        current = (now or datetime.now(timezone.utc)).astimezone(tz)
        last_ran = self.db.get_setting(self.KEY_LAST_DATE)
        recheck_dt = _parse_iso_dt(self.db.get_setting(self.KEY_NEXT_RECHECK_AT), tz)
        if recheck_dt is not None and last_ran != current.date().isoformat() and self.db.get_setting(self.KEY_POSTPONED_DATE):
            return max(current, recheck_dt)
        return self.compute_next_daily_run_at(now=now, _cfg=model)

    def _clear_postponement(self) -> None:
        self.db.set_setting(self.KEY_POSTPONED_DATE, "")
        self.db.set_setting(self.KEY_POSTPONED_REASON, "")
        self.db.set_setting(self.KEY_NEXT_RECHECK_AT, "")
        self.db.set_setting(self.KEY_LAST_ERROR, "")

    def _detect_target_cycle_date(
        self,
        current: datetime,
        model: CollectionScheduleConfig,
        tz: tzinfo,
    ) -> tuple[Optional[str], str]:
        """Return `(cycle_date_to_run, trigger_type)` or `(None, '')` if not due yet."""
        today = current.date()
        today_str = today.isoformat()
        last_ran = self.db.get_setting(self.KEY_LAST_DATE)
        armed_since = self._ensure_armed_date(today)

        h, m = parse_schedule_time(model.collection_time)
        due_dt = datetime.combine(today, time(h, m), tzinfo=tz)

        if last_ran != today_str:
            if current >= due_dt:
                if model.resume_missed_jobs or current <= due_dt + timedelta(hours=2):
                    if current > due_dt + timedelta(minutes=60):
                        return today_str, "missed_run_recovery"
                    return today_str, "scheduled"
                return None, ""

        # Before today's scheduled time: check if yesterday's run was missed while PC was off
        if model.resume_missed_jobs:
            yesterday_str = (today - timedelta(days=1)).isoformat()
            if yesterday_str >= armed_since and (last_ran is None or last_ran < yesterday_str):
                prev_cycle = self.ref_db.get_latest_cycle(cycle_date=yesterday_str)
                if prev_cycle is None or prev_cycle.status not in {"COMPLETED", "PARTIAL"}:
                    return yesterday_str, "missed_run_recovery"
        return None, ""

    def _record_simple_event(
        self,
        *,
        event_type: str,
        status: str,
        message: str,
        cycle_date: Optional[str] = None,
        trigger_type: str = "scheduled",
        scheduled_time: Optional[str] = None,
        started_at: Optional[datetime] = None,
        completed_at: Optional[datetime] = None,
        free_allowance_remaining: Optional[int] = None,
        errors: Optional[list[str]] = None,
        next_scheduled_run: Optional[str] = None,
    ) -> SchedulerNotificationEvent:
        start_iso = (started_at or datetime.now(timezone.utc)).isoformat()
        end_iso = completed_at.isoformat() if completed_at else start_iso
        evt = SchedulerNotificationEvent(
            event_id=f"evt_{uuid.uuid4().hex[:12]}",
            cycle_date=cycle_date,
            trigger_type=trigger_type,
            event_type=event_type,
            scheduled_time=scheduled_time,
            started_at=start_iso,
            completed_at=end_iso,
            status=status,
            free_allowance_remaining=free_allowance_remaining,
            errors=errors or [],
            next_scheduled_run=next_scheduled_run,
            message=message,
        )
        self.ref_db.record_scheduler_event(evt)
        return evt

    def _record_cycle_notification(
        self,
        cycle: CollectionCycleRecord,
        *,
        scheduled_time: Optional[str],
        next_scheduled_run: str,
    ) -> SchedulerNotificationEvent:
        queue_items = self.ref_db.get_queue_for_cycle(cycle.cycle_id)
        assets_selected: list[dict[str, Any]] = []
        errors: list[str] = []
        if cycle.error_message:
            errors.append(cycle.error_message)
        if cycle.shortage_reason:
            errors.append(cycle.shortage_reason)
        for q in queue_items:
            ref = self.ref_db.get_reference_asset(q.blendswap_id)
            assets_selected.append(
                {
                    "blendswap_id": q.blendswap_id,
                    "title": ref.title if ref else f"BlendSwap #{q.blendswap_id}",
                    "slot_number": q.slot_number,
                    "slot_reason": q.slot_reason,
                    "status": q.status,
                }
            )
            if q.last_error and q.status != "COMPLETED":
                errors.append(f"Asset {q.blendswap_id}: {q.last_error}")

        message = (
            f"Cycle {cycle.cycle_id} ({cycle.cycle_date}) finished with {cycle.status}: "
            f"{cycle.downloaded_count} downloaded, {cycle.failed_count} failed, "
            f"{cycle.free_allowance_end if cycle.free_allowance_end is not None else '?'} free remaining."
        )
        evt = SchedulerNotificationEvent(
            event_id=f"evt_{uuid.uuid4().hex[:12]}",
            cycle_id=cycle.cycle_id,
            cycle_date=cycle.cycle_date,
            trigger_type=cycle.trigger_type,
            event_type=f"CYCLE_{cycle.status}",
            scheduled_time=scheduled_time,
            started_at=cycle.started_at,
            completed_at=cycle.completed_at or datetime.now(timezone.utc).isoformat(),
            status=cycle.status,
            downloaded_count=cycle.downloaded_count,
            failed_count=cycle.failed_count,
            free_allowance_remaining=cycle.free_allowance_end,
            assets_selected=assets_selected,
            errors=errors,
            next_scheduled_run=next_scheduled_run,
            message=message,
        )
        self.ref_db.record_scheduler_event(evt)
        logger.info(
            "Collection notification: %s | next=%s | selected=%d",
            message,
            next_scheduled_run,
            len(assets_selected),
        )
        return evt

    def get_notifications(self, limit: int = 30) -> list[SchedulerNotificationEvent]:
        return self.ref_db.list_scheduler_events(limit=limit)

    def get_latest_notification(self) -> Optional[SchedulerNotificationEvent]:
        return self.ref_db.get_latest_scheduler_event()

    def check_and_run_due(
        self,
        *,
        now: Optional[datetime] = None,
        force: bool = False,
        trigger_type: str = "scheduled",
        max_downloads: Optional[int] = None,
        sync_to_drive: bool = True,
    ) -> Optional[CollectionCycleRecord]:
        """Verify allowance reset state, recover interrupted/missed cycles, and run daily collection if due."""
        with self._run_lock:
            model = self._sync_config_storage()
            tz = resolve_timezone(model.timezone)
            current = (now or datetime.now(timezone.utc)).astimezone(tz)
            today_str = current.date().isoformat()
            h, m = parse_schedule_time(model.collection_time)
            due_dt = datetime.combine(current.date(), time(h, m), tzinfo=tz)

            if not force:
                if not model.enabled:
                    return None
                if not (self.settings.blendswap_key or "").strip():
                    return None

                has_incomplete = bool(model.resume_missed_jobs and self.ref_db.get_incomplete_cycles())
                target_date, detected_trigger = self._detect_target_cycle_date(current, model, tz)
                if not has_incomplete and target_date is None:
                    return None
                effective_date = target_date or today_str
                effective_trigger = (
                    trigger_type
                    if trigger_type != "scheduled"
                    else (detected_trigger or "scheduled")
                )

                # Wait out postponement backoff window before polling BlendSwap /me again
                recheck_dt = _parse_iso_dt(self.db.get_setting(self.KEY_NEXT_RECHECK_AT), tz)
                postponed_date = self.db.get_setting(self.KEY_POSTPONED_DATE)
                if postponed_date == effective_date and recheck_dt is not None and current < recheck_dt:
                    return None

                # Query authenticated BlendSwap account for live daily allowance reset check
                conn_info = self.collector.client.check_connectivity()
                self.db.set_setting(self.KEY_LAST_ALLOWANCE_CHECK_AT, current.isoformat())
                if not conn_info.get("connected"):
                    err = str(conn_info.get("error") or "BlendSwap API unreachable")
                    next_recheck = current + timedelta(minutes=self.reset_recheck_minutes)
                    self.db.set_setting(self.KEY_STATE, "POSTPONED_API_UNAVAILABLE")
                    self.db.set_setting(self.KEY_POSTPONED_DATE, effective_date)
                    self.db.set_setting(self.KEY_POSTPONED_REASON, err)
                    self.db.set_setting(self.KEY_LAST_ERROR, err)
                    self.db.set_setting(self.KEY_NEXT_RECHECK_AT, next_recheck.isoformat())
                    self._record_simple_event(
                        event_type="POSTPONED_API_UNAVAILABLE",
                        status="POSTPONED_API_UNAVAILABLE",
                        message=f"Collection postponed: {err}. Rechecking at {next_recheck.isoformat()}.",
                        cycle_date=effective_date,
                        trigger_type=effective_trigger,
                        scheduled_time=due_dt.isoformat(),
                        started_at=current,
                        completed_at=current,
                        errors=[err],
                        next_scheduled_run=next_recheck.isoformat(),
                    )
                    return None

                acct = conn_info.get("account") or {}
                remaining = int(acct.get("free_downloads_remaining") or 0)
                prev_seen = self.db.get_setting(self.KEY_LAST_ALLOWANCE_SEEN)
                prev_postponed = self.db.get_setting(self.KEY_POSTPONED_DATE)
                self.db.set_setting(self.KEY_LAST_ALLOWANCE_SEEN, str(remaining))

                if remaining <= 0:
                    next_recheck = current + timedelta(minutes=self.reset_recheck_minutes)
                    reason = "Daily free download allowance is 0; waiting for BlendSwap reset."
                    self.db.set_setting(self.KEY_STATE, "POSTPONED_WAITING_RESET")
                    self.db.set_setting(self.KEY_POSTPONED_DATE, effective_date)
                    self.db.set_setting(self.KEY_POSTPONED_REASON, reason)
                    self.db.set_setting(self.KEY_NEXT_RECHECK_AT, next_recheck.isoformat())
                    self.db.set_setting(self.KEY_LAST_ERROR, "")
                    self._record_simple_event(
                        event_type="POSTPONED_WAITING_RESET",
                        status="POSTPONED_WAITING_RESET",
                        message=(
                            f"Collection at {due_dt.strftime('%H:%M')} postponed because free allowance is 0. "
                            f"Next reset check at {next_recheck.strftime('%H:%M %Z')}."
                        ),
                        cycle_date=effective_date,
                        trigger_type=effective_trigger,
                        scheduled_time=due_dt.isoformat(),
                        started_at=current,
                        completed_at=current,
                        free_allowance_remaining=0,
                        errors=[reason],
                        next_scheduled_run=next_recheck.isoformat(),
                    )
                    return None

                if prev_postponed or prev_seen == "0":
                    self.db.set_setting(self.KEY_ALLOWANCE_RESET_DETECTED_AT, current.isoformat())
                self._clear_postponement()
            else:
                effective_date = today_str
                effective_trigger = trigger_type

            # 1. Always recover interrupted/incomplete cycles cleanly before launching a new one
            self.db.set_setting(self.KEY_STATE, "RUNNING")
            recovered = self.collector.recover_incomplete_cycles(sync_to_drive=sync_to_drive)
            recovered_completed_target = False
            for rec in recovered:
                if rec.status in {"COMPLETED", "PARTIAL"}:
                    self.db.set_setting(self.KEY_LAST_CYCLE_ID, rec.cycle_id)
                    self.db.set_setting(self.KEY_LAST_CYCLE_STATUS, rec.status)
                    if rec.cycle_date == effective_date:
                        self.db.set_setting(self.KEY_LAST_DATE, rec.cycle_date)
                        recovered_completed_target = True
                self._record_cycle_notification(
                    rec,
                    scheduled_time=due_dt.isoformat(),
                    next_scheduled_run=self.compute_next_run_at(now=current).isoformat(),
                )

            if not force and recovered_completed_target:
                self.db.set_setting(self.KEY_STATE, "SCHEDULED" if model.enabled else "PAUSED")
                return recovered[-1]

            if not force and any(rec.status == "RETRY_WAIT" for rec in recovered):
                next_recheck = current + timedelta(minutes=self.reset_recheck_minutes)
                self.db.set_setting(self.KEY_STATE, "POSTPONED_WAITING_RESET")
                self.db.set_setting(self.KEY_POSTPONED_DATE, effective_date)
                self.db.set_setting(self.KEY_POSTPONED_REASON, "Recovered cycle still waiting for download retries/allowance.")
                self.db.set_setting(self.KEY_NEXT_RECHECK_AT, next_recheck.isoformat())
                return None

            if not force and recovered:
                # Re-check whether recovering an older interrupted cycle consumed remaining allowance today
                try:
                    post_rec_acct = self.collector.client.get_me()
                    self.db.set_setting(self.KEY_LAST_ALLOWANCE_SEEN, str(post_rec_acct.free_downloads_remaining))
                    if post_rec_acct.free_downloads_remaining <= 0:
                        next_recheck = current + timedelta(minutes=self.reset_recheck_minutes)
                        self.db.set_setting(self.KEY_STATE, "POSTPONED_WAITING_RESET")
                        self.db.set_setting(self.KEY_POSTPONED_DATE, effective_date)
                        self.db.set_setting(
                            self.KEY_POSTPONED_REASON,
                            "Allowance exhausted finishing interrupted cycle; waiting for BlendSwap reset.",
                        )
                        self.db.set_setting(self.KEY_NEXT_RECHECK_AT, next_recheck.isoformat())
                        return None
                except Exception:
                    pass

            if not force and target_date is None:
                self.db.set_setting(self.KEY_STATE, "SCHEDULED" if model.enabled else "PAUSED")
                return recovered[-1] if recovered else None

            effective_cap = max_downloads if max_downloads is not None else model.target_downloads
            cycle = self.collector.run_daily_cycle(
                cycle_date=effective_date,
                trigger_type=effective_trigger,
                max_downloads=effective_cap,
                sync_to_drive=sync_to_drive,
            )
            self.db.set_setting(self.KEY_LAST_CYCLE_ID, cycle.cycle_id)
            self.db.set_setting(self.KEY_LAST_CYCLE_STATUS, cycle.status)
            if cycle.free_allowance_end is not None:
                self.db.set_setting(self.KEY_LAST_ALLOWANCE_SEEN, str(cycle.free_allowance_end))

            if cycle.status in {"COMPLETED", "PARTIAL"}:
                self.db.set_setting(self.KEY_LAST_DATE, cycle.cycle_date)
                self._clear_postponement()
                self.db.set_setting(self.KEY_STATE, "SCHEDULED" if model.enabled else "PAUSED")
            elif cycle.status == "SKIPPED_NO_ALLOWANCE" and not force:
                next_recheck = current + timedelta(minutes=self.reset_recheck_minutes)
                self.db.set_setting(self.KEY_STATE, "POSTPONED_WAITING_RESET")
                self.db.set_setting(self.KEY_POSTPONED_DATE, effective_date)
                self.db.set_setting(self.KEY_POSTPONED_REASON, cycle.error_message or "Allowance exhausted; waiting for reset.")
                self.db.set_setting(self.KEY_NEXT_RECHECK_AT, next_recheck.isoformat())
            elif cycle.status == "FAILED":
                self.db.set_setting(self.KEY_LAST_ERROR, cycle.error_message or "Cycle failed")
                self.db.set_setting(self.KEY_STATE, "SCHEDULED" if model.enabled else "PAUSED")
            else:
                self.db.set_setting(self.KEY_STATE, "SCHEDULED" if model.enabled else "PAUSED")

            next_run = self.compute_next_run_at(now=current).isoformat()
            self._record_cycle_notification(
                cycle,
                scheduled_time=due_dt.isoformat() if not force else None,
                next_scheduled_run=next_run,
            )
            return cycle

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, name="ref-daily-scheduler", daemon=True)
        self._thread.start()
        logger.info("Reference Engine daily scheduler started (%s @ %s).", self.settings.collection_timezone, self.settings.collection_schedule_time)

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.check_and_run_due()
            except Exception:
                logger.exception("Scheduled reference collection loop error.")
            self._stop_event.wait(self.poll_interval_seconds)
