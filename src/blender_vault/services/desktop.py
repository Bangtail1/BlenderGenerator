"""Desktop integration helpers (launching Blender GUI safely for an asset)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Optional


def launch_in_blender_gui(blend_path: Path, blender_executable: Optional[str]) -> int:
    """Open a .blend file in interactive Blender GUI as a detached child process."""
    if not blender_executable or not Path(blender_executable).is_file():
        raise FileNotFoundError(f"Blender executable not found: {blender_executable!r}")
    if not blend_path.is_file():
        raise FileNotFoundError(f".blend file not found: {blend_path}")

    creation_flags = 0
    if os.name == "nt":
        creation_flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP

    proc = subprocess.Popen(
        [blender_executable, str(blend_path.resolve())],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creation_flags,
    )
    return proc.pid
