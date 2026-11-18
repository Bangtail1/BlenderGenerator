"""Full end-to-end API + Blender + QA + Render + Human Approval + Drive Sync + Reindex test."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient
from googleapiclient.discovery import build_from_document

from blender_vault.config import Settings
from blender_vault.services.gdrive_service import save_protected_token
from blender_vault.web.app import create_app
from tests.test_gdrive_sync import _local_discovery_doc, build_local_drive_server


def test_full_end_to_end_workflow_via_api(tmp_path: Path) -> None:
    server, base_url, _ = build_local_drive_server()
    try:
        doc = _local_discovery_doc(base_url)
        cfg = Settings(
            vault_data_dir=tmp_path / "vault",
            gdrive_token_path=tmp_path / "creds" / "token.json",
            gdrive_client_secrets_path=tmp_path / "creds" / "client_secrets.json",
            blender_executable=Settings().blender_executable,
            blender_render_resolution=256,
            blender_render_samples=8,
            vision_qa_provider="none",
        )
        cfg.ensure_directories()
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

        import httplib2

        app = create_app(
            settings=cfg,
            gdrive_service_factory=lambda creds: build_from_document(doc, http=httplib2.Http(proxy_info=None)),
        )
        client = TestClient(app)

        # 1. Health check
        h = client.get("/api/health").json()
        assert h["blender"]["available"] is True
        assert h["gdrive"]["authenticated"] is True

        # 2. Generate detailed wooden chair from prompt
        gen_resp = client.post(
            "/api/assets/generate",
            json={
                "prompt": "Create a detailed wooden chair with four legs, a curved backrest and a realistic wooden material.",
                "name": "E2E Oak Chair",
            },
        )
        assert gen_resp.status_code == 200, gen_resp.text
        asset = gen_resp.json()
        asset_id = asset["asset_id"]
        assert asset["category"] == "Furniture"
        assert asset["latest"]["status"] == "AWAITING_HUMAN_APPROVAL"
        assert asset["latest"]["technical_qa"]["status"] == "PASS"
        assert asset["latest"]["human_review"]["status"] == "PENDING"
        assert asset["latest"]["visual_qa"]["status"] == "UNCONFIGURED"

        # 3. Preview images, contact sheet, turntable, and GLB downloadable over HTTP
        for fname in ("front.png", "back.png", "three_quarter.png", "wireframe.png", "contact_sheet.png", "turntable.webp"):
            r = client.get(f"/api/assets/{asset_id}/versions/1/preview/{fname}")
            assert r.status_code == 200 and len(r.content) > 100

        glb_resp = client.get(f"/api/assets/{asset_id}/versions/1/download/glb")
        assert glb_resp.status_code == 200 and glb_resp.content[:4] == b"glTF"

        # 4. Stage 2 Human Visual Approval
        rev_resp = client.post(
            f"/api/assets/{asset_id}/versions/1/review",
            json={"decision": "APPROVED", "reviewer": "Azlan", "notes": "Approved after checking 9 angles + wireframe."},
        )
        assert rev_resp.status_code == 200
        assert rev_resp.json()["latest"]["status"] == "APPROVED"

        # 5. Upload to Google Drive & Verify remote integrity + download + Blender reopen
        up_resp = client.post(f"/api/assets/{asset_id}/versions/1/upload-drive")
        assert up_resp.status_code == 200, up_resp.text
        assert up_resp.json()["version"]["drive_sync_status"] == "VERIFIED"
        assert up_resp.json()["asset"]["latest"]["status"] == "UPLOADED"

        ver_resp = client.post(f"/api/assets/{asset_id}/versions/1/verify-drive")
        assert ver_resp.status_code == 200, ver_resp.text
        assert ver_resp.json()["verified"] is True
        assert ver_resp.json()["blender_reopen_ok"] is True

        # 6. Clear SQLite & reconstruct catalogue from Google Drive manifests
        with app.state.db.connect() as conn:
            conn.execute("DELETE FROM assets")
        assert client.get("/api/assets").json()["total"] == 0

        reidx = client.post("/api/catalogue/reindex?include_drive=true").json()
        assert reidx["drive"]["reindexed"] >= 1
        restored = client.get(f"/api/assets/{asset_id}").json()
        assert restored["name"] == "E2E Oak Chair"
        assert restored["latest"]["status"] == "UPLOADED"

        # 7. Reject unsupported file upload cleanly
        bad_up = client.post("/api/assets/import", files={"file": ("readme.txt", b"hello", "text/plain")})
        assert bad_up.status_code == 400
    finally:
        server.shutdown()
