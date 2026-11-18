"""Persistent timezone-aware daily collection scheduler (default Europe/London)."""

from __future__ import annotations

import logging
import threading
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from typing import Any, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..config import Settings
from ..db import VaultDatabase
from .collector import CollectionCycleService
from .models import CollectionCycleRecord

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

    def _is_bst(self, dt: datetime) -> bool:
        utc_dt = dt.replace(tzinfo=None)
        start = datetime.combine(self._last_sunday(utc_dt.year, 3), time(1, 0))
        end = datetime.combine(self._last_sunday(utc_dt.year, 10), time(1, 0))
        return start <= utc_dt < end

    def utcoffset(self, dt: Optional[datetime]) -> timedelta:
        if dt is None:
            return timedelta(0)
        return timedelta(hours=1) if self._is_bst(dt) else timedelta(0)

    def dst(self, dt: Optional[datetime]) -> timedelta:
        return self.utcoffset(dt)

    def tzname(self, dt: Optional[datetime]) -> str:
        return "BST" if (dt is not None and self._is_bst(dt)) else "GMT"


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
    parts = (hhmm or "02:00").strip().split(":")
    try:
        hour = max(0, min(23, int(parts[0])))
        minute = max(0, min(59, int(parts[1]))) if len(parts) > 1 else 0
        return hour, minute
    except ValueError:
        return 2, 0


class DailyCollectionScheduler:
    """Persistent daily collection scheduler surviving restarts, shutdowns, and missed windows."""

    KEY_LAST_DATE = "ref_scheduler_last_cycle_date"
    KEY_SCHEDULE_TIME = "ref_scheduler_schedule_time"
    KEY_TIMEZONE = "ref_scheduler_timezone"
    KEY_ENABLED = "ref_scheduler_enabled"

    def __init__(
        self,
        settings: Settings,
        db: VaultDatabase,
        collector: CollectionCycleService,
        *,
        poll_interval_seconds: float = 30.0,
    ) -> None:
        self.settings = settings
        self.db = db
        self.collector = collector
        self.poll_interval_seconds = poll_interval_seconds
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._run_lock = threading.Lock()

    def get_schedule_config(self) -> dict[str, Any]:
        tz_name = self.db.get_setting(self.KEY_TIMEZONE) or self.settings.collection_timezone
        sched_time = self.db.get_setting(self.KEY_SCHEDULE_TIME) or self.settings.collection_schedule_time
        enabled_raw = self.db.get_setting(self.KEY_ENABLED)
        enabled = (
            self.settings.collection_scheduler_enabled
            if enabled_raw is None
            else enabled_raw.lower() == "true"
        )
        return {
            "enabled": enabled,
            "timezone": tz_name,
            "schedule_time": sched_time,
            "daily_target": self.settings.collection_daily_target,
            "last_cycle_date": self.db.get_setting(self.KEY_LAST_DATE),
            "next_run_at": self.compute_next_run_at().isoformat(),
        }

    def update_schedule_config(
        self,
        *,
        enabled: Optional[bool] = None,
        schedule_time: Optional[str] = None,
        timezone_name: Optional[str] = None,
    ) -> dict[str, Any]:
        if enabled is not None:
            self.db.set_setting(self.KEY_ENABLED, "true" if enabled else "false")
        if schedule_time is not None:
            h, m = parse_schedule_time(schedule_time)
            self.db.set_setting(self.KEY_SCHEDULE_TIME, f"{h:02d}:{m:02d}")
        if timezone_name is not None:
            resolve_timezone(timezone_name)
            self.db.set_setting(self.KEY_TIMEZONE, timezone_name.strip())
        return self.get_schedule_config()

    def compute_next_run_at(self, now: Optional[datetime] = None) -> datetime:
        tz_name = self.db.get_setting(self.KEY_TIMEZONE) or self.settings.collection_timezone
        sched_time = self.db.get_setting(self.KEY_SCHEDULE_TIME) or self.settings.collection_schedule_time
        tz = resolve_timezone(tz_name)
        h, m = parse_schedule_time(sched_time)
        current = (now or datetime.now(timezone.utc)).astimezone(tz)
        today_str = current.date().isoformat()
        candidate = datetime.combine(current.date(), time(h, m), tzinfo=tz)
        last_ran = self.db.get_setting(self.KEY_LAST_DATE)
        if candidate <= current and last_ran == today_str:
            candidate = datetime.combine(current.date() + timedelta(days=1), time(h, m), tzinfo=tz)
        elif candidate <= current and last_ran != today_str:
            # Due immediately for today!
            return current
        return candidate

    def check_and_run_due(
        self,
        *,
        now: Optional[datetime] = None,
        force: bool = False,
        trigger_type: str = "scheduled",
        max_downloads: Optional[int] = None,
    ) -> Optional[CollectionCycleRecord]:
        """Run recovery + scheduled collection if due (or forced) under lock."""
        with self._run_lock:
            # Always recover interrupted cycles first
            self.collector.recover_incomplete_cycles()

            cfg = self.get_schedule_config()
            tz = resolve_timezone(cfg["timezone"])
            current = (now or datetime.now(timezone.utc)).astimezone(tz)
            today_str = current.date().isoformat()

            if not force:
                if not cfg["enabled"]:
                    return None
                if not (self.settings.blendswap_key or "").strip():
                    return None
                last_ran = self.db.get_setting(self.KEY_LAST_DATE)
                if last_ran == today_str:
                    return None
                h, m = parse_schedule_time(cfg["schedule_time"])
                due_dt = datetime.combine(current.date(), time(h, m), tzinfo=tz)
                if current < due_dt:
                    return None

            cycle = self.collector.run_daily_cycle(
                cycle_date=today_str,
                trigger_type=trigger_type,
                max_downloads=max_downloads,
            )
            if cycle.status in {"COMPLETED", "PARTIAL", "SKIPPED_NO_ALLOWANCE"}:
                self.db.set_setting(self.KEY_LAST_DATE, today_str)
            return cycle

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, name="ref-daily-scheduler", daemon=True)
        self._thread.start()
        logger.info("Reference Engine daily scheduler started (%s).", self.settings.collection_timezone)

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
