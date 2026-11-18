"""Phase 10 & Phase 11 E2E tests: FastAPI Branch 2 endpoints, Dashboard Sections A-F, UI assets, and Branch 1+2 integration."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient
from googleapiclient.discovery import build_from_document

from blender_vault.config import Settings
from blender_vault.services.gdrive_service import save_protected_token
from blender_vault.web.app import create_app
from tests.mock_blendswap_server import build_mock_blendswap_server, default_catalog_assets
from tests.test_gdrive_sync import _local_discovery_doc, build_local_drive_server


def test_branch2_api_dashboard_and_branch1_auto_benchmark_e2e(tmp_path: Path) -> None:
    real_blend = Path("samples/sample_wooden_chair/oak_dining_chair.blend").resolve().read_bytes()
    catalog = default_catalog_assets()
    for a in catalog:
        if a["files"][0]["format"] == "blender":
            a["_blob"] = real_blend
            a["size_bytes"] = len(real_blend)
            a["files"][0]["size_bytes"] = len(real_blend)

    bs_server, bs_url, bs_state = build_mock_blendswap_server(
        free_downloads_remaining=7,
        credits=75,
        assets=catalog,
    )
    drv_server, drv_url, _ = build_local_drive_server()
    try:
        import httplib2

        doc = _local_discovery_doc(drv_url)
        cfg = Settings(
            vault_data_dir=tmp_path / "vault",
            blendswap_key="bsk_test_valid_key",
            blendswap_base_url=f"{bs_url}/api/v1",
            gdrive_token_path=tmp_path / "creds" / "token.json",
            gdrive_client_secrets_path=tmp_path / "creds" / "client_secrets.json",
            blender_executable=Settings().blender_executable,
            blender_render_resolution=256,
            blender_render_samples=8,
            collection_scheduler_enabled=False,
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
                    "token_uri": f"{drv_url}/token",
                    "expiry": "2099-01-01T00:00:00Z",
                }
            ),
        )

        app = create_app(
            settings=cfg,
            gdrive_service_factory=lambda creds: build_from_document(doc, http=httplib2.Http(proxy_info=None)),
            start_scheduler=False,
        )

        with TestClient(app) as client:
            # 1. Static UI & scripts load cleanly
            home = client.get("/")
            assert home.status_code == 200 and "Reference Engine (Branch 2)" in home.text
            ref_js = client.get("/static/reference_dashboard.js")
            assert ref_js.status_code == 200 and "createReferenceDashboard" in ref_js.text
            assert "ref-open-btn" in ref_js.text and "ref-library-grid" in ref_js.text

            # 2. Live status & discovery preview
            st = client.get("/api/reference/status").json()
            assert st["blendswap"]["connected"] is True
            assert st["blendswap"]["account"]["free_downloads_remaining"] == 7

            disc = client.get("/api/reference/discover?q=Chair").json()
            assert len(disc["data"]) >= 1

            # 3. Trigger daily collection cycle via API (7 downloads, 0 credits spent)
            cyc = client.post("/api/reference/cycles/run", json={"max_downloads": 7}).json()
            assert cyc["status"] == "COMPLETED"
            assert cyc["downloaded_count"] == 7
            assert bs_state["credits"] == 75

            # 4. List, filter, inspect detail, and preview serving
            list_res = client.get("/api/reference/assets?sort_by=technical_score").json()
            assert list_res["total"] == 7
            first_id = list_res["assets"][0]["blendswap_id"]
            detail = client.get(f"/api/reference/assets/{first_id}").json()
            assert detail["grade"]["total_score"] >= 80.0
            assert detail["inspection"]["file_opened_ok"] is True
            prev_name = Path(detail["preview_paths"][0]).name
            prev_resp = client.get(f"/api/reference/assets/{first_id}/preview/{prev_name}")
            assert prev_resp.status_code == 200 and len(prev_resp.content) > 20

            # 5. Update scheduler settings
            sched = client.post(
                "/api/reference/scheduler",
                json={"schedule_time": "03:30", "timezone": "Europe/London", "enabled": True},
            ).json()
            assert sched["schedule_time"] == "03:30"
            assert sched["timezone"] == "Europe/London"

            # 6. Branch 1 generates a chair -> auto-benchmark hook creates a BenchmarkReport automatically!
            gen_resp = client.post(
                "/api/assets/generate",
                json={
                    "prompt": "Create a detailed wooden chair with four legs, a curved backrest and a realistic wooden material.",
                    "name": "API Benchmarked Chair",
                },
            )
            assert gen_resp.status_code == 200, gen_resp.text
            b1_id = gen_resp.json()["asset_id"]

            matches = client.get(f"/api/reference/benchmarks/matches/{b1_id}").json()
            assert len(matches["matches"]) >= 1

            bmk_list = client.get(f"/api/reference/benchmarks?asset_id={b1_id}").json()
            assert bmk_list["total"] >= 1

            # Manual benchmark trigger also works
            manual_bmk = client.post(
                f"/api/reference/benchmarks/run/{b1_id}",
                json={"intended_usage": "dining room chair", "modeling_style": "hard-surface"},
            ).json()
            assert manual_bmk["local_grade"]["total_score"] >= 88.0

            # 7. Dashboard Sections A-F all populated cleanly
            dash = client.get("/api/reference/dashboard?check_live=true").json()
            assert dash["collection_overview"]["today_downloaded"] == 7
            assert dash["reference_library"]["total_assets"] == 7
            assert len(dash["popularity_analysis"]["most_downloaded"]) == 7
            assert len(dash["quality_intelligence"]["highest_rated"]) >= 1
            assert dash["benchmark_results"]["total_benchmarks"] >= 2
            assert dash["attribution_and_licensing"]["total_records"] == 7
            assert all(
                item["legally_verified"] is False
                for item in dash["attribution_and_licensing"]["items"]
            )
    finally:
        bs_server.shutdown()
        drv_server.shutdown()
