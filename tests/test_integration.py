"""Phase 6, 7 & 8 integration tests: full 15-scenario verification of Roblox DB, QA, Reports, Approval & Drive Sync."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from googleapiclient.discovery import build_from_document

from blender_vault.config import Settings
from blender_vault.database import ProductionStatus
from blender_vault.models import GenerateAssetRequest, HumanApprovalStatus, HumanReviewRequest
from blender_vault.roblox_qa import RobloxWorkflowService
from blender_vault.services.gdrive_service import GoogleDriveSyncService, save_protected_token
from blender_vault.web.app import create_app
from tests.test_gdrive_sync import _local_discovery_doc, build_local_drive_server


def test_complete_end_to_end_15_scenarios_via_api_and_workflow(tmp_path: Path) -> None:
    """Covers TEST 1..TEST 15 end-to-end with real Blender 5.3, SQLite, Reports, Approval, Versioning & Local Drive v3."""
    server, base_url, store = build_local_drive_server()
    try:
        import httplib2

        doc = _local_discovery_doc(base_url)
        base_cfg = Settings()
        cfg = Settings(
            vault_data_dir=tmp_path / "vault",
            roblox_db_path=tmp_path / "data" / "roblox_asset_library.db",
            roblox_backups_dir=tmp_path / "data" / "backups",
            roblox_reports_dir=tmp_path / "reports",
            roblox_output_dir=tmp_path / "output",
            gdrive_token_path=tmp_path / "creds" / "token.json",
            gdrive_client_secrets_path=tmp_path / "creds" / "client_secrets.json",
            blender_executable=base_cfg.blender_executable,
            blender_render_resolution=256,
            blender_render_samples=8,
            roblox_default_export_format="FBX",
            roblox_auto_register=True,
            roblox_auto_qa=True,
            collection_scheduler_enabled=False,
        )
        cfg.ensure_directories()

        # Start WITHOUT token to simulate TEST 14 (Google Drive temporarily unavailable)
        app = create_app(
            settings=cfg,
            gdrive_service_factory=lambda creds: build_from_document(doc, http=httplib2.Http(proxy_info=None)),
            start_scheduler=False,
        )

        with TestClient(app) as client:
            wf: RobloxWorkflowService = app.state.roblox_workflow

            # TEST 1: Generate a chair and register it in SQLite
            gen_resp = client.post(
                "/api/assets/generate",
                json={
                    "prompt": "Create a detailed wooden chair with four legs, a curved backrest and a realistic wooden material.",
                    "name": "Oak Dining Chair",
                    "export_format": "FBX",
                    "genre_codes": ["GNR_FAN", "GNR_MED"],
                },
            )
            assert gen_resp.status_code == 200, gen_resp.text
            vault_asset = gen_resp.json()
            vault_id = vault_asset["asset_id"]
            rbx_id = vault_asset["roblox_asset_id"]
            assert rbx_id == "FUR_00001_Wooden_Chair_Basic_LP_v001"

            detail = client.get(f"/api/roblox/assets/{rbx_id}").json()
            rbx = detail["asset"]
            assert rbx["production_status"] == "AWAITING_HUMAN_APPROVAL"
            assert set(rbx["genre_category_ids"])  # Multi-genre linked without duplicating asset

            # TEST 3: Verify original Blender source remains accessible
            blend_path = Path(rbx["source_blend_path"])
            assert blend_path.is_file() and blend_path.stat().st_size > 100_000

            # TEST 4-11: Verify geometry, UV, material, scale, Roblox export, previews, JSON/HTML reports & DB QA scores
            assert len(detail["qa_reports"]) >= 1
            qa_rep = detail["qa_reports"][0]
            assert qa_rep["overall_score"] >= 85.0
            assert qa_rep["grade"] in {"A", "B"}
            assert qa_rep["final_status"] == "QA_PASSED"
            assert qa_rep["mesh_integrity_score"] == 15.0
            assert qa_rep["geometry_score"] > 0
            assert qa_rep["uv_score"] > 0
            assert qa_rep["material_score"] > 0
            assert qa_rep["scale_score"] > 0
            assert qa_rep["roblox_compatibility_score"] >= 14.0
            assert qa_rep["collision_score"] > 0
            assert qa_rep["visual_score"] > 0
            assert qa_rep["organisation_score"] > 0

            assert rbx["triangle_count"] == 16092
            assert rbx["vertex_count"] == 8080
            assert Path(rbx["export_path"]).suffix.lower() == ".fbx"
            assert Path(rbx["export_path"]).is_file()

            json_rep_path = Path(qa_rep["report_json_path"])
            html_rep_path = Path(qa_rep["report_html_path"])
            assert json_rep_path.is_file() and html_rep_path.is_file()
            json_data = json.loads(json_rep_path.read_text(encoding="utf-8"))
            assert json_data["asset_id"] == rbx_id
            assert json_data["grade"] in {"A", "B"}
            assert json_data["ai_visual_inspection_performed"] is False
            assert json_data["roblox_studio_automation_available"] is False
            assert len(json_data["roblox_studio_checklist"]) >= 4
            assert Path(json_rep_path.parent / json_data["preview_images"]["three_quarter"]).is_file()
            assert "QA Report" in html_rep_path.read_text(encoding="utf-8")

            # Report download endpoints work
            assert client.get(f"/api/roblox/assets/{rbx_id}/report/json").status_code == 200
            assert client.get(f"/api/roblox/assets/{rbx_id}/report/html").status_code == 200

            # TEST 14 (part 1): Approve while Google Drive auth is offline -> approved in DB & queued for retry!
            app_resp = client.post(
                f"/api/roblox/assets/{rbx_id}/approve",
                json={"reviewer": "Azlan", "notes": "Clean geometry and proportions.", "sync_to_drive": True},
            )
            assert app_resp.status_code == 200, app_resp.text
            app_data = app_resp.json()
            assert app_data["asset"]["production_status"] == "APPROVED"
            assert app_data["asset"]["human_approved"] is True
            assert app_data["asset"]["approved_by"] == "Azlan"
            assert app_data["drive_sync"]["synced"] is False
            assert app_data["drive_sync"]["queued"] is True
            # Local source files preserved intact during Drive outage
            assert blend_path.is_file()

            # TEST 14 (part 2) & TEST 2: Restore Drive auth and process retry queue -> Drive file ID registered!
            save_protected_token(
                cfg.gdrive_token_path,
                json.dumps(
                    {
                        "token": "tok",
                        "refresh_token": "ref",
                        "client_id": "cid",
                        "client_secret": "csec",
                        "token_uri": f"{base_url}/token",
                        "expiry": "2099-01-01T00:00:00Z",
                    }
                ),
            )
            retry_resp = client.post("/api/roblox/drive/retry-pending").json()
            assert retry_resp["attempted"] == 1
            assert retry_resp["succeeded"] == 1
            assert retry_resp["remaining"] == 0

            after_sync = client.get(f"/api/roblox/assets/{rbx_id}").json()
            assert after_sync["asset"]["google_drive_file_id"] is not None
            assert after_sync["asset"]["google_drive_folder_id"] is not None
            assert after_sync["asset"]["google_drive_url"] is not None
            # Verify report JSON refreshed with APPROVED state
            approved_json = json.loads(json_rep_path.read_text(encoding="utf-8"))
            assert approved_json["final_approval_status"] == "APPROVED"
            assert approved_json["human_approved"] is True

            # TEST 12: Modify the chair and create a new version (v2) without overwriting approved v1!
            v2_blend = tmp_path / "walnut_chair_v2.blend"
            app.state.runner.run_script(
                "generate.py",
                {
                    "asset_name": "Walnut Dining Chair",
                    "prompt": "Create a walnut wooden chair with 5 slats",
                    "template_id": "wooden_chair",
                    "params": {"wood_tone": "walnut", "slat_count": 5},
                    "output_blend": str(v2_blend.resolve()),
                },
                tmp_path / "gen_v2",
            )
            v2_created = wf.create_modified_version(
                rbx_id,
                new_blend_path=v2_blend,
                change_description="Walnut tone and 5 backrest slats",
                export_format="FBX",
            )
            assert v2_created["version"].version_number == 2
            assert v2_created["asset"].asset_id == rbx_id
            # Approved v1 remains untouched & immutable
            v1_saved = wf.versions.get_asset_version(rbx_id, 1)
            assert v1_saved.is_approved is True
            assert v1_saved.google_drive_file_id == after_sync["asset"]["google_drive_file_id"]
            assert Path(v1_saved.blender_source_path).is_file()

            # Run QA and approve v2 -> syncs to Drive as v2
            v2_qa = client.post(f"/api/roblox/assets/{rbx_id}/qa", json={"version_number": 2}).json()
            assert v2_qa["qa_report"]["final_status"] == "QA_PASSED"
            v2_app = client.post(
                f"/api/roblox/assets/{rbx_id}/approve",
                json={"version_number": 2, "reviewer": "Azlan", "sync_to_drive": True},
            ).json()
            assert v2_app["version"]["version_number"] == 2
            assert v2_app["drive_sync"]["synced"] is True

            # TEST 13: Simulate a QA failure and verify asset is NOT approved and local files preserved
            bad_resp = client.post(
                "/api/assets/generate",
                json={
                    "prompt": "Defective chair geometry test",
                    "name": "Defective Chair",
                    "template_id": "defect_zero_area_and_loose",
                },
            )
            assert bad_resp.status_code == 200
            bad_rbx_id = bad_resp.json()["roblox_asset_id"]
            bad_detail = client.get(f"/api/roblox/assets/{bad_rbx_id}").json()
            assert bad_detail["asset"]["production_status"] == "QA_FAILED"
            assert bad_detail["asset"]["human_approved"] is False
            assert Path(bad_detail["asset"]["source_blend_path"]).is_file()

            # Attempting to approve a QA_FAILED asset must fail cleanly with 400
            bad_app = client.post(
                f"/api/roblox/assets/{bad_rbx_id}/approve",
                json={"reviewer": "Azlan"},
            )
            assert bad_app.status_code == 400
            assert client.get(f"/api/roblox/assets/{bad_rbx_id}").json()["asset"]["human_approved"] is False

            # DB integrity check clean after all 15 scenarios
            health = client.get("/api/roblox/health").json()
            assert health["status"] == "ok"
            assert health["integrity"]["ok"] is True
            assert health["universal_departments"] == 25
            assert health["genre_departments"] == 15
    finally:
        server.shutdown()
