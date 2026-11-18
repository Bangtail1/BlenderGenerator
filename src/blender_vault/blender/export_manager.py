"""Roblox export manager supporting FBX, GLB, and OBJ export + round-trip verification."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from ..blender_worker.runner import BlenderWorkerRunner
from ..database.models import ExportFormat

logger = logging.getLogger("blender_vault.blender.export_manager")


class RobloxExportManager:
    """Exports .blend assets to Roblox-supported formats (FBX/GLB/OBJ) via headless Blender."""

    def __init__(self, runner: BlenderWorkerRunner) -> None:
        self.runner = runner

    def export_and_validate(
        self,
        *,
        blend_path: Path,
        output_path: Path,
        export_format: ExportFormat | str = ExportFormat.FBX,
        work_dir: Path,
        verify_roundtrip: bool = True,
    ) -> dict[str, Any]:
        fmt = (
            export_format.value
            if isinstance(export_format, ExportFormat)
            else str(export_format).upper()
        )
        if fmt not in {"FBX", "GLB", "OBJ"}:
            raise ValueError(f"Unsupported Roblox export format '{fmt}'. Must be FBX, GLB, or OBJ.")

        target = Path(output_path).resolve()
        if target.suffix.upper() != f".{fmt}":
            target = target.with_suffix(f".{fmt.lower()}")

        result = self.runner.run_script(
            "export_roblox.py",
            {
                "blend_path": str(Path(blend_path).resolve()),
                "export_path": str(target),
                "export_format": fmt,
                "verify_roundtrip": verify_roundtrip,
            },
            work_dir,
        )
        return result
