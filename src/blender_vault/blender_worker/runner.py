"""Subprocess runner for executing Blender Python scripts safely in background mode."""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("blender_vault.blender_worker")

SCRIPTS_DIR = Path(__file__).resolve().parent / "scripts"


class BlenderWorkerError(RuntimeError):
    """Raised when a Blender background process fails or produces invalid output."""

    def __init__(self, message: str, *, log_path: Optional[Path] = None, returncode: Optional[int] = None) -> None:
        super().__init__(message)
        self.log_path = log_path
        self.returncode = returncode


class BlenderWorkerRunner:
    """Runs isolated Blender CLI background processes with structured JSON I/O."""

    def __init__(self, blender_executable: Optional[str], timeout_seconds: int = 300) -> None:
        self.blender_executable = blender_executable
        self.timeout_seconds = timeout_seconds

    def _require_executable(self) -> str:
        if not self.blender_executable or not Path(self.blender_executable).is_file():
            raise BlenderWorkerError(
                f"Blender executable not found: {self.blender_executable!r}. "
                "Set BLENDER_EXECUTABLE in .env to your blender.exe path."
            )
        return self.blender_executable

    def check_health(self) -> dict[str, Any]:
        """Verify Blender executable launches and reports version info."""
        exe = self._require_executable()
        try:
            proc = subprocess.run(
                [exe, "--version"],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except Exception as exc:
            return {"available": False, "executable": exe, "error": str(exc)}

        if proc.returncode != 0:
            return {
                "available": False,
                "executable": exe,
                "error": proc.stderr.strip() or proc.stdout.strip() or f"Exit code {proc.returncode}",
            }

        first_line = (proc.stdout or "").splitlines()[0].strip() if proc.stdout else "Unknown"
        return {
            "available": True,
            "executable": exe,
            "version_line": first_line,
        }

    def run_script(
        self,
        script_filename: str,
        payload: dict[str, Any],
        work_dir: Path,
        *,
        blend_file: Optional[Path] = None,
        factory_startup: bool = True,
        timeout_seconds: Optional[int] = None,
    ) -> dict[str, Any]:
        """Execute a Blender script under scripts/ with input/output JSON files."""
        exe = self._require_executable()
        script_path = SCRIPTS_DIR / script_filename
        if not script_path.is_file():
            raise BlenderWorkerError(f"Blender worker script missing: {script_path}")

        work_dir.mkdir(parents=True, exist_ok=True)
        input_path = work_dir / f"{script_path.stem}_input.json"
        output_path = work_dir / f"{script_path.stem}_output.json"
        log_path = work_dir / f"{script_path.stem}.log"

        input_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        if output_path.exists():
            output_path.unlink()

        cmd: list[str] = [exe, "--background"]
        if factory_startup:
            cmd.append("--factory-startup")
        if blend_file is not None:
            if not blend_file.is_file():
                raise BlenderWorkerError(f"Target .blend file does not exist: {blend_file}")
            cmd.append(str(blend_file))
        cmd.extend(
            [
                "--python-exit-code",
                "1",
                "--python",
                str(script_path),
                "--",
                str(input_path),
                str(output_path),
            ]
        )

        logger.info("Running Blender worker %s in %s", script_filename, work_dir)
        timeout = timeout_seconds or self.timeout_seconds
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            log_text = f"TIMEOUT after {timeout}s\nSTDOUT:\n{exc.stdout or ''}\nSTDERR:\n{exc.stderr or ''}"
            log_path.write_text(log_text, encoding="utf-8")
            raise BlenderWorkerError(
                f"Blender worker '{script_filename}' timed out after {timeout}s.",
                log_path=log_path,
            ) from exc

        combined_log = (
            f"COMMAND: {' '.join(cmd)}\n"
            f"EXIT_CODE: {proc.returncode}\n\n"
            f"=== STDOUT ===\n{proc.stdout}\n\n"
            f"=== STDERR ===\n{proc.stderr}\n"
        )
        log_path.write_text(combined_log, encoding="utf-8")

        if proc.returncode != 0:
            err_tail = "\n".join((proc.stderr or proc.stdout or "").splitlines()[-20:])
            raise BlenderWorkerError(
                f"Blender worker '{script_filename}' failed (exit code {proc.returncode}): {err_tail}",
                log_path=log_path,
                returncode=proc.returncode,
            )

        if not output_path.is_file():
            raise BlenderWorkerError(
                f"Blender worker '{script_filename}' exited 0 but produced no output JSON.",
                log_path=log_path,
                returncode=proc.returncode,
            )

        try:
            result = json.loads(output_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise BlenderWorkerError(
                f"Blender worker '{script_filename}' wrote malformed output JSON: {exc}",
                log_path=log_path,
            ) from exc

        result["_log_path"] = str(log_path)
        if not result.get("ok", True):
            raise BlenderWorkerError(
                result.get("error", f"Blender worker '{script_filename}' reported failure."),
                log_path=log_path,
                returncode=proc.returncode,
            )
        return result
