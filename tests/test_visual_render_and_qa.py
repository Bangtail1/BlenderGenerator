"""Tests for multi-angle visual rendering, Stage 1 VLM confidence gating, and Stage 2 human approval."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from blender_vault.blender_worker.runner import BlenderWorkerRunner
from blender_vault.config import Settings
from blender_vault.db import VaultDatabase
from blender_vault.models import (
    AssetStatus,
    GenerateAssetRequest,
    HumanApprovalStatus,
    HumanReviewRequest,
    VisualQAStatus,
)
from blender_vault.services.gdrive_service import GoogleDriveSyncService
from blender_vault.services.pipeline import AssetPipelineService
from blender_vault.services.storage import VaultStorage
from blender_vault.services.vision_qa import REQUIRED_INSPECTION_VIEWS, _parse_vision_json


def test_vision_json_parser_downgrades_low_confidence() -> None:
    raw = json.dumps(
        {
            "score": 88,
            "confidence": 0.45,
            "recommendation": "APPROVE_CANDIDATE",
            "matches_description": True,
            "component_check": "All parts present",
            "proportion_check": "Balanced",
            "surface_and_materials_check": "Wood shader visible",
            "issues_found": ["Slightly uncertain back lighting"],
            "summary": "Looks good overall but confidence is low.",
        }
    )
    report = _parse_vision_json(
        raw,
        provider="gemini",
        model="gemini-2.5-flash",
        render_paths={},
        turntable_path=None,
        contact_sheet_path=None,
        render_sanity_ok=True,
        render_sanity_details={},
    )
    assert report.recommendation == "NEEDS_HUMAN_REVIEW"
    assert report.status == VisualQAStatus.NEEDS_HUMAN_REVIEW


def test_visual_renders_and_human_approval_gate(tmp_path: Path) -> None:
    base_cfg = Settings()
    cfg = Settings(
        vault_data_dir=tmp_path / "vault",
        blender_executable=base_cfg.blender_executable,
        blender_render_resolution=256,
        blender_render_samples=8,
        vision_qa_provider="none",
    )
    cfg.ensure_directories()
    db = VaultDatabase(cfg.db_path)
    storage = VaultStorage(cfg.assets_dir, cfg.imports_dir)
    runner = BlenderWorkerRunner(cfg.blender_executable, timeout_seconds=120)
    pipe = AssetPipelineService(cfg, db, storage, runner, GoogleDriveSyncService(cfg, db, storage))

    vase = pipe.generate_procedural_asset(
        GenerateAssetRequest(prompt="Create a glazed ceramic vase", name="Sage Vase", template_id="ceramic_vase")
    )
    v = vase.latest
    assert v is not None
    assert v.status == AssetStatus.AWAITING_HUMAN_APPROVAL
    assert v.visual_qa is not None
    assert v.visual_qa.status == VisualQAStatus.UNCONFIGURED
    assert v.visual_qa.render_sanity_ok is True
    for view in REQUIRED_INSPECTION_VIEWS:
        assert Path(v.visual_qa.render_paths[view]).is_file()
    assert Path(v.visual_qa.contact_sheet_path).is_file()
    assert Path(v.visual_qa.turntable_path).is_file()
    # Human approval must NEVER be set automatically
    assert v.human_review.status == HumanApprovalStatus.PENDING

    # Stage 2 Human Approval succeeds
    approved = pipe.submit_human_review(
        vase.asset_id,
        1,
        HumanReviewRequest(decision=HumanApprovalStatus.APPROVED, reviewer="Azlan", notes="Clean glaze & silhouette."),
    )
    assert approved.latest.status == AssetStatus.APPROVED
    assert approved.latest.human_review.status == HumanApprovalStatus.APPROVED
    assert approved.latest.human_review.reviewer == "Azlan"

    # Failed asset must NEVER be allowed human approval
    defect = pipe.generate_procedural_asset(
        GenerateAssetRequest(prompt="Defective test", name="Bad Mesh", template_id="defect_zero_area_and_loose")
    )
    assert defect.latest.status == AssetStatus.FAILED
    with pytest.raises(ValueError):
        pipe.submit_human_review(
            defect.asset_id,
            1,
            HumanReviewRequest(decision=HumanApprovalStatus.APPROVED, reviewer="Azlan"),
        )
