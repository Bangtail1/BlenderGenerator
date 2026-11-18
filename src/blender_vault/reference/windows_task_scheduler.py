"""Persistent Windows Task Scheduler integration for Branch 2 daily collection."""

from __future__ import annotations

import logging
import subprocess
import sys
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any, Callable, Optional
from xml.sax.saxutils import escape

from ..config import Settings
from .scheduler import parse_schedule_time, resolve_timezone

logger = logging.getLogger("blender_vault.reference.windows_task")

DEFAULT_WINDOWS_TASK_NAME = "BlenderAssetVault-ReferenceDailyCollection"


def _compute_local_start_boundary(collection_time: str, timezone_name: str, now: Optional[datetime] = None) -> str:
    """Compute an ISO-8601 StartBoundary matching the configured target time in Windows local wall time."""
    target_tz = resolve_timezone(timezone_name)
    ref_now = (now or datetime.now(timezone.utc)).astimezone(target_tz)
    h, m = parse_schedule_time(collection_time)
    target_dt = datetime.combine(ref_now.date(), time(h, m), tzinfo=target_tz)
    local_dt = target_dt.astimezone()
    return local_dt.strftime("%Y-%m-%dT%H:%M:%S")


def build_task_command(
    *,
    python_executable: Optional[Path | str] = None,
    working_dir: Optional[Path | str] = None,
) -> tuple[str, str, str]:
    """Return `(executable, arguments, working_directory)` for scheduled CLI invocation."""
    py_exe = str(Path(python_executable or sys.executable).resolve())
    work_dir = str(Path(working_dir or Path.cwd()).resolve())
    args = "-m blender_vault.cli collect-daily --scheduled"
    return py_exe, args, work_dir


def build_windows_task_xml(
    *,
    collection_time: str = "03:00",
    timezone_name: str = "Europe/London",
    python_executable: Optional[Path | str] = None,
    working_dir: Optional[Path | str] = None,
    description: str = "Blender Asset Vault automated BlendSwap daily collection and reset-check task.",
    enabled: bool = True,
    resume_missed_jobs: bool = True,
    recheck_interval_minutes: int = 30,
    now: Optional[datetime] = None,
) -> str:
    """Generate Windows Task Scheduler 2.0 XML XML document (UTF-16 encoded when written for schtasks)."""
    py_exe, args, work_dir = build_task_command(
        python_executable=python_executable,
        working_dir=working_dir,
    )
    start_boundary = _compute_local_start_boundary(collection_time, timezone_name, now=now)
    interval_mins = max(5, min(240, int(recheck_interval_minutes)))
    start_when_available = "true" if resume_missed_jobs else "false"
    enabled_str = "true" if enabled else "false"

    # Strict XSD sequence order for http://schemas.microsoft.com/windows/2004/02/mit/task
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>{escape(description)}</Description>
    <Author>BlenderAssetVault</Author>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>{escape(start_boundary)}</StartBoundary>
      <Repetition>
        <Interval>PT{interval_mins}M</Interval>
        <Duration>PT23H</Duration>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
      <Enabled>{enabled_str}</Enabled>
      <ScheduleByDay>
        <DaysInterval>1</DaysInterval>
      </ScheduleByDay>
    </CalendarTrigger>
    <LogonTrigger>
      <Enabled>{enabled_str}</Enabled>
      <Delay>PT30S</Delay>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>{start_when_available}</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>{enabled_str}</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT2H</ExecutionTimeLimit>
    <Priority>7</Priority>
    <RestartOnFailure>
      <Interval>PT10M</Interval>
      <Count>3</Count>
    </RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(py_exe)}</Command>
      <Arguments>{escape(args)}</Arguments>
      <WorkingDirectory>{escape(work_dir)}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


CommandRunner = Callable[[list[str]], subprocess.CompletedProcess[str]]


def _default_subprocess_runner(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


class WindowsTaskSchedulerManager:
    """Manages a persistent Windows Task Scheduler job using schtasks.exe."""

    def __init__(
        self,
        settings: Settings,
        *,
        task_name: str = DEFAULT_WINDOWS_TASK_NAME,
        working_dir: Optional[Path] = None,
        python_executable: Optional[Path] = None,
        command_runner: Optional[CommandRunner] = None,
        platform_name: Optional[str] = None,
    ) -> None:
        self.settings = settings
        self.task_name = task_name
        self.working_dir = (working_dir or Path.cwd()).resolve()
        self.python_executable = (python_executable or Path(sys.executable)).resolve()
        self._runner = command_runner or _default_subprocess_runner
        self._platform = platform_name or sys.platform

    @property
    def is_windows(self) -> bool:
        return self._platform == "win32"

    @property
    def xml_path(self) -> Path:
        return (self.settings.resolved_reference_library_dir / "database" / "windows_task.xml").resolve()

    def write_task_xml(
        self,
        *,
        collection_time: str,
        timezone_name: str,
        enabled: bool = True,
        resume_missed_jobs: bool = True,
        recheck_interval_minutes: int = 30,
        now: Optional[datetime] = None,
    ) -> Path:
        xml_content = build_windows_task_xml(
            collection_time=collection_time,
            timezone_name=timezone_name,
            python_executable=self.python_executable,
            working_dir=self.working_dir,
            enabled=enabled,
            resume_missed_jobs=resume_missed_jobs,
            recheck_interval_minutes=recheck_interval_minutes,
            now=now,
        )
        self.xml_path.parent.mkdir(parents=True, exist_ok=True)
        # Task Scheduler expects UTF-16 BOM encoding when reading XML files on Windows
        self.xml_path.write_bytes(xml_content.encode("utf-16"))
        return self.xml_path

    def install_task(
        self,
        *,
        collection_time: str = "03:00",
        timezone_name: str = "Europe/London",
        enabled: bool = True,
        resume_missed_jobs: bool = True,
        recheck_interval_minutes: int = 30,
    ) -> dict[str, Any]:
        xml_file = self.write_task_xml(
            collection_time=collection_time,
            timezone_name=timezone_name,
            enabled=enabled,
            resume_missed_jobs=resume_missed_jobs,
            recheck_interval_minutes=recheck_interval_minutes,
        )
        py_exe, args, work_dir = build_task_command(
            python_executable=self.python_executable,
            working_dir=self.working_dir,
        )
        cmd = ["schtasks", "/Create", "/TN", self.task_name, "/XML", str(xml_file), "/F"]
        if not self.is_windows:
            return {
                "installed": False,
                "supported_os": False,
                "task_name": self.task_name,
                "xml_path": str(xml_file),
                "command": f'"{py_exe}" {args}',
                "working_dir": work_dir,
                "message": f"Windows Task Scheduler is only available on Windows (xml exported to {xml_file}).",
            }
        try:
            proc = self._runner(cmd)
        except OSError as exc:
            return {
                "installed": False,
                "supported_os": True,
                "task_name": self.task_name,
                "xml_path": str(xml_file),
                "error": str(exc),
                "message": f"Failed to execute schtasks: {exc}",
            }
        ok = proc.returncode == 0
        output = (proc.stdout or proc.stderr or "").strip()
        return {
            "installed": ok,
            "supported_os": True,
            "task_name": self.task_name,
            "xml_path": str(xml_file),
            "command": f'"{py_exe}" {args}',
            "working_dir": work_dir,
            "returncode": proc.returncode,
            "output": output,
            "error": None if ok else (output or f"schtasks returned exit code {proc.returncode}"),
            "message": (
                f"Registered Windows scheduled task '{self.task_name}' (daily {collection_time} {timezone_name} + startup/logon catch-up)."
                if ok
                else f"Could not register Windows scheduled task '{self.task_name}': {output}"
            ),
        }

    def uninstall_task(self) -> dict[str, Any]:
        if not self.is_windows:
            return {
                "uninstalled": False,
                "supported_os": False,
                "task_name": self.task_name,
                "message": "Windows Task Scheduler is only available on Windows.",
            }
        cmd = ["schtasks", "/Delete", "/TN", self.task_name, "/F"]
        try:
            proc = self._runner(cmd)
        except OSError as exc:
            return {
                "uninstalled": False,
                "supported_os": True,
                "task_name": self.task_name,
                "error": str(exc),
            }
        ok = proc.returncode == 0
        output = (proc.stdout or proc.stderr or "").strip()
        return {
            "uninstalled": ok,
            "supported_os": True,
            "task_name": self.task_name,
            "returncode": proc.returncode,
            "output": output,
            "error": None if ok else output,
            "message": (
                f"Removed Windows scheduled task '{self.task_name}'."
                if ok
                else f"Task '{self.task_name}' was not removed: {output}"
            ),
        }

    def query_task_status(self) -> dict[str, Any]:
        py_exe, args, work_dir = build_task_command(
            python_executable=self.python_executable,
            working_dir=self.working_dir,
        )
        base: dict[str, Any] = {
            "supported_os": self.is_windows,
            "task_name": self.task_name,
            "installed": False,
            "status": "NOT_SUPPORTED" if not self.is_windows else "NOT_INSTALLED",
            "next_run_time": None,
            "last_run_time": None,
            "last_result": None,
            "command": f'"{py_exe}" {args}',
            "working_dir": work_dir,
            "xml_path": str(self.xml_path),
            "install_cli": "python -m blender_vault.cli scheduler-install",
            "note": "Runs locally via Windows Task Scheduler even when Antigravity is closed; does not run while powered off (missed cycles catch up when Windows starts).",
        }
        if not self.is_windows:
            return base

        cmd = ["schtasks", "/Query", "/TN", self.task_name, "/V", "/FO", "LIST"]
        try:
            proc = self._runner(cmd)
        except OSError as exc:
            base["error"] = str(exc)
            return base

        if proc.returncode != 0:
            return base

        parsed: dict[str, str] = {}
        for line in (proc.stdout or "").splitlines():
            if ":" not in line:
                continue
            key, val = line.split(":", 1)
            k = key.strip().lower()
            v = val.strip()
            if k in {"status", "next run time", "last run time", "last result", "scheduled task state"}:
                parsed[k] = v

        base["installed"] = True
        base["status"] = parsed.get("status") or parsed.get("scheduled task state") or "Ready"
        base["next_run_time"] = parsed.get("next run time")
        base["last_run_time"] = parsed.get("last run time")
        base["last_result"] = parsed.get("last result")
        return base
