"""Asset metadata extraction helpers built on BlenderWorkerRunner."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..blender_worker.runner import BlenderWorkerRunner


def extract_blend_metadata(
    runner: BlenderWorkerRunner,
    blend_path: Path,
    work_dir: Path,
) -> dict[str, Any]:
    """Run inspect_qa.py on a .blend file to obtain authoritative mesh and material metrics."""
    rt_glb = (work_dir / "meta_roundtrip.glb").resolve()
    return runner.run_script(
        "inspect_qa.py",
        {
            "blend_path": str(Path(blend_path).resolve()),
            "roundtrip_glb": str(rt_glb),
        },
        work_dir,
    )
