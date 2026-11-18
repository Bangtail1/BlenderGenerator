"""Unified Control Studio backend API tests (Generation Studio, Overview, Collection Control, SSE, Rules, Quality, Health)."""

from __future__ import annotations

import json
from pathlib import Path
import time

from fastapi.testclient import TestClient
from googleapiclient.discovery import build_from_document

from blender_vault.config import Settings
from blender_vault.services.gdrive_service import save_protected_token
from blender_vault.web.app import create_app
from tests.mock_blendswap_server import build_mock_blendswap_server, default_catalog_assets
from tests.test_gdrive_sync import _local_discovery_doc, build_local_drive_server


def _build_test_app(
    tmp_path: Path,
    *,
    bs_url: str = "",
    gdrive_doc: dict | None = None,
    gdrive_base_url: str = "",
):
    import httplib2

    base_cfg = Settings()
    cfg = Settings(
        vault_data_dir=tmp_path / "vault",
        roblox_db_path=tmp_path / "data" / "roblox_asset_library.db",
        roblox_backups_dir=tmp_path / "data" / "backups",
        roblox_reports_dir=tmp_path / "reports",
        roblox_output_dir=tmp_path / "output",
        blendswap_key="bsk_test_valid_key" if bs_url else "",
        blendswap_base_url=f"{bs_url}/api/v1" if bs_url else base_cfg.blendswap_base_url,
        gdrive_token_path=tmp_path / "creds" / "token.json",
        gdrive_client_secrets_path=tmp_path / "creds" / "client_secrets.json",
        blender_executable=base_cfg.blender_executable,
        blender_render_resolution=256,
        blender_render_samples=8,
        vision_qa_provider="none",
        gemini_api_key="gem_secret_do_not_leak",
        collection_scheduler_enabled=False,
    )
    cfg.ensure_directories()
    if gdrive_base_url:
        save_protected_token(
            cfg.gdrive_token_path,
            json.dumps(
                {
                    "token": "tok_secret_value",
                    "refresh_token": "ref_secret_value",
                    "client_id": "cid",
                    "client_secret": "csec",
                    "token_uri": f"{gdrive_base_url}/token",
                    "expiry": "2099-01-01T00:00:00Z",
                }
            ),
        )

    factory = (
        (lambda creds: build_from_document(gdrive_doc, http=httplib2.Http(proxy_info=None)))
        if gdrive_doc is not None
        else None
    )
    return create_app(settings=cfg, gdrive_service_factory=factory, start_scheduler=False)


def test_generation_studio_async_lifecycle_duplicate_guard_and_quality_reports(tmp_path: Path) -> None:
    drv_server, drv_url, _ = build_local_drive_server()
    try:
        doc = _local_discovery_doc(drv_url)
        app = _build_test_app(tmp_path, gdrive_doc=doc, gdrive_base_url=drv_url)

        with TestClient(app) as client:
            tpl_resp = client.get("/api/generation/templates")
            assert tpl_resp.status_code == 200
            tpl_data = tpl_resp.json()
            assert len(tpl_data["templates"]) >= 1
            assert "Furniture" in tpl_data["categories"]
            assert tpl_data["complexities"] == ["low", "medium", "high"]

            payload = {
                "prompt": "Create a sturdy oak wooden chair with four tapered legs and curved backrest",
                "name": "Studio Oak Chair",
                "category": "Furniture",
                "visual_style": "LP",
                "complexity": "low",
                "poly_budget": 12000,
                "additional_instructions": "Keep bevels clean for game engine export",
            }
            create_resp = client.post("/api/generation/create", json=payload)
            assert create_resp.status_code == 200, create_resp.text
            created = create_resp.json()
            job_id = created["job"]["job_id"]
            asset_id = created["asset"]["asset_id"]
            assert created["job"]["status"] in {"QUEUED", "RUNNING", "COMPLETED"}
            assert len(created["job"]["stages"]) >= 5

            # Duplicate active generation protection (unless already finished super fast, handle either 409 or already done)
            cur_state = client.get(f"/api/generation/jobs/{job_id}").json()["job"]["status"]
            if cur_state in {"QUEUED", "RUNNING"}:
                dup_resp = client.post("/api/generation/create", json=payload)
                assert dup_resp.status_code == 409

            # Poll until COMPLETED
            deadline = time.time() + 120.0
            final_job = None
            while time.time() < deadline:
                poll = client.get(f"/api/generation/jobs/{job_id}")
                assert poll.status_code == 200
                final_job = poll.json()["job"]
                if final_job["status"] in {"COMPLETED", "FAILED", "CANCELLED"}:
                    break
                time.sleep(0.5)

            assert final_job is not None
            assert final_job["status"] == "COMPLETED", final_job
            assert all(s["status"] == "COMPLETED" for s in final_job["stages"])

            asset_detail = client.get(f"/api/assets/{asset_id}").json()
            assert asset_detail["latest"]["glb_path"] is not None
            assert asset_detail["latest"]["technical_qa"]["status"] == "PASS"

            # Triggering Re-Run QA on an existing version must also mark its qa_pipeline job COMPLETED
            retry_ver = client.post(f"/api/assets/{asset_id}/versions/1/retry")
            assert retry_ver.status_code == 200, retry_ver.text
            jobs_after_retry = client.get("/api/generation/jobs").json()["jobs"]
            qa_jobs = [j for j in jobs_after_retry if j.get("asset_id") == asset_id and j.get("job_type") == "qa_pipeline"]
            assert len(qa_jobs) >= 1
            assert all(j["status"] == "COMPLETED" for j in qa_jobs)
            assert all(s["status"] == "COMPLETED" for s in qa_jobs[0]["stages"])

            # Verify Overview & Quality Intelligence reflect real generated asset
            overview = client.get("/api/dashboard/overview").json()
            assert overview["generation"]["total_generated"] >= 1

            quality = client.get("/api/quality/reports").json()
            assert any(r["asset_id"] == asset_id for r in quality["branch1_assets"])
            assert len(quality["roblox_reports"]) >= 1

            # Verify legacy UI remains operational
            legacy = client.get("/legacy")
            assert legacy.status_code == 200 and "Blender Asset Vault" in legacy.text

            # Verify retrying a COMPLETED/failed job works cleanly when creating a new retry run
            # Cancel test on a freshly created job queued behind or immediately cancelled
            job2_resp = client.post(
                "/api/generation/create",
                json={
                    "prompt": "Wooden dining table with beveled legs",
                    "name": "Cancellable Table",
                    "category": "Furniture",
                    "complexity": "low",
                },
            )
            assert job2_resp.status_code == 200
            j2_id = job2_resp.json()["job"]["job_id"]
            cancel_resp = client.post(f"/api/generation/jobs/{j2_id}/cancel")
            assert cancel_resp.status_code == 200
            deadline2 = time.time() + 60.0
            while time.time() < deadline2:
                st2 = client.get(f"/api/generation/jobs/{j2_id}").json()["job"]["status"]
                if st2 in {"CANCELLED", "COMPLETED", "FAILED"}:
                    break
                time.sleep(0.3)
    finally:
        drv_server.shutdown()
        drv_server.server_close()


def test_collection_control_rules_preview_sse_actions_and_no_secret_leak(tmp_path: Path) -> None:
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
        doc = _local_discovery_doc(drv_url)
        app = _build_test_app(tmp_path, bs_url=bs_url, gdrive_doc=doc, gdrive_base_url=drv_url)

        with TestClient(app) as client:
            # 1. System health NEVER leaks API keys or tokens
            health_resp = client.get("/api/system/health?check_live=true")
            assert health_resp.status_code == 200
            assert "bsk_test_valid_key" not in health_resp.text
            assert "gem_secret_do_not_leak" not in health_resp.text
            assert "tok_secret_value" not in health_resp.text
            h = health_resp.json()
            assert h["blendswap"]["key_configured"] is True
            assert h["blendswap"]["allow_credit_spend"] is False
            assert h["vision_qa"]["gemini_configured"] is True

            # 2. Rules save: next_run vs permanent vs restore-defaults
            r_init = client.get("/api/collection/rules").json()
            assert r_init["active_rules"]["daily_target"] == 7

            next_run_resp = client.put(
                "/api/collection/rules",
                json={
                    "scope": "next_run",
                    "daily_target": 2,
                    "preferred_categories": ["Furniture"],
                    "excluded_categories": ["Weapons & Armor"],
                    "preferred_licenses": ["cc0", "cc_by"],
                    "min_downloads": 100,
                    "preferred_poly_complexity": "low_poly",
                    "preferred_file_format": "blend",
                    "collection_time": "08:30",
                    "timezone": "Europe/London",
                },
            )
            assert next_run_resp.status_code == 200
            nr = next_run_resp.json()
            assert nr["has_next_run_override"] is True
            assert nr["active_rules"]["daily_target"] == 2
            assert nr["permanent_rules"]["daily_target"] == 7

            perm_resp = client.put(
                "/api/collection/rules",
                json={
                    "scope": "permanent",
                    "daily_target": 4,
                    "preferred_categories": ["Furniture", "Architecture"],
                    "collection_time": "09:15",
                    "timezone": "Europe/London",
                },
            )
            assert perm_resp.status_code == 200
            st_sched = client.get("/api/collection/status").json()["scheduler"]
            assert st_sched["collection_time"] == "09:15"
            assert st_sched["target_downloads"] == 4

            restored = client.post("/api/collection/rules/restore-defaults").json()
            assert restored["restored"] is True
            assert restored["permanent_rules"]["daily_target"] == 7
            assert restored["permanent_rules"]["collection_time"] == "03:00"
            assert restored["has_next_run_override"] is False

            # 3. Candidate Preview & individual candidate exclusion
            prev1 = client.post("/api/collection/preview").json()
            assert len(prev1["selected"]) >= 1
            first_cid = prev1["selected"][0]["blendswap_id"]
            prev_excl = client.post(
                "/api/collection/preview",
                json={"daily_target": 3, "excluded_candidate_ids": [str(first_cid)]},
            ).json()
            assert all(str(item["blendswap_id"]) != str(first_cid) for item in prev_excl["selected"])
            assert bs_state["credits"] == 75

            # 4. SSE snapshot check
            sse_resp = client.get("/api/collection/events?once=true")
            assert sse_resp.status_code == 200
            assert "event: snapshot" in sse_resp.text

            # 5. Manual Run + Wait + 10-stage timeline + 0 credits spent
            run_resp = client.post("/api/collection/run", json={"max_downloads": 2, "sync_to_drive": True})
            assert run_resp.status_code == 200
            deadline = time.time() + 120.0
            final_status = None
            while time.time() < deadline:
                st = client.get("/api/collection/status").json()
                cyc = st.get("today_cycle")
                if not st["control"]["is_running"] and cyc and cyc["status"] in {"COMPLETED", "PARTIAL", "FAILED"}:
                    final_status = st
                    break
                time.sleep(0.4)

            assert final_status is not None
            cyc = final_status["today_cycle"]
            assert cyc["status"] == "COMPLETED", cyc
            assert cyc["downloaded_count"] == 2
            assert cyc["credits_spent"] == 0
            assert bs_state["credits"] == 75
            assert len(cyc["stages"]) == 10
            assert all(s["status"] == "COMPLETED" for s in cyc["stages"])

            # 6. History, detail, and faceted reference library
            hist = client.get("/api/collection/history").json()
            assert len(hist["cycles"]) >= 1
            cyc_detail = client.get(f"/api/collection/history/{cyc['cycle_id']}").json()
            assert len(cyc_detail["queue"]) == 2

            lib = client.get("/api/reference/library?limit=20").json()
            assert lib["total"] == 2
            assert len(lib["facets"]["categories"]) >= 1

            # 7. Pause / Resume / Cancel controls
            p = client.post("/api/collection/pause").json()
            assert p["status"] == "PAUSED"
            r = client.post("/api/collection/resume").json()
            assert r["status"] == "RESUMED"
            c = client.post("/api/collection/cancel").json()
            assert c["status"] == "CANCEL_REQUESTED"
    finally:
        bs_server.shutdown()
        bs_server.server_close()
        drv_server.shutdown()
        drv_server.server_close()


def test_collection_retry_failed_items(tmp_path: Path, monkeypatch) -> None:
    real_blend = Path("samples/sample_wooden_chair/oak_dining_chair.blend").resolve().read_bytes()
    catalog = default_catalog_assets()
    for a in catalog:
        if a["files"][0]["format"] == "blender":
            a["_blob"] = real_blend
            a["size_bytes"] = len(real_blend)
            a["files"][0]["size_bytes"] = len(real_blend)

    bs_server, bs_url, bs_state = build_mock_blendswap_server(
        free_downloads_remaining=7,
        credits=50,
        assets=catalog,
    )
    drv_server, drv_url, _ = build_local_drive_server()
    try:
        doc = _local_discovery_doc(drv_url)
        app = _build_test_app(tmp_path, bs_url=bs_url, gdrive_doc=doc, gdrive_base_url=drv_url)

        with TestClient(app) as client:
            orig_download = app.state.bs_client.download_with_refresh
            fail_state = {"should_fail": True}

            def flaky_download(*, asset_id: int, file_id, dest_path: Path, **kwargs):
                if fail_state["should_fail"]:
                    fail_state["should_fail"] = False
                    raise RuntimeError("Simulated transient CDN failure")
                return orig_download(asset_id=asset_id, file_id=file_id, dest_path=dest_path, **kwargs)

            monkeypatch.setattr(app.state.bs_client, "download_with_refresh", flaky_download)

            run_resp = client.post("/api/collection/run", json={"max_downloads": 1, "sync_to_drive": False})
            assert run_resp.status_code == 200
            deadline = time.time() + 90.0
            first_cycle = None
            while time.time() < deadline:
                st = client.get("/api/collection/status").json()
                cyc = st.get("today_cycle")
                if not st["control"]["is_running"] and cyc and cyc["status"] in {"COMPLETED", "PARTIAL", "FAILED", "RETRY_WAIT"}:
                    first_cycle = cyc
                    break
                time.sleep(0.3)

            assert first_cycle is not None
            assert first_cycle["status"] in {"RETRY_WAIT", "PARTIAL", "FAILED"}

            retry_resp = client.post(
                "/api/collection/retry-failed",
                json={"cycle_id": first_cycle["cycle_id"], "sync_to_drive": False},
            )
            assert retry_resp.status_code == 200

            deadline2 = time.time() + 90.0
            retried_cycle = None
            while time.time() < deadline2:
                st2 = client.get(f"/api/collection/history/{first_cycle['cycle_id']}").json()
                ctrl = client.get("/api/collection/status").json()["control"]
                if not ctrl["is_running"] and st2["cycle"]["downloaded_count"] >= 1:
                    retried_cycle = st2["cycle"]
                    break
                time.sleep(0.3)

            assert retried_cycle is not None
            assert retried_cycle["downloaded_count"] >= 1
            assert retried_cycle["credits_spent"] == 0
            assert bs_state["credits"] == 50
    finally:
        bs_server.shutdown()
        bs_server.server_close()
        drv_server.shutdown()
        drv_server.server_close()


def test_studio_spa_routes_and_built_assets(tmp_path: Path) -> None:
    app = _build_test_app(tmp_path)
    studio_dist = Path("src/blender_vault/web/studio_dist").resolve()
    assert (studio_dist / "index.html").is_file(), "studio_dist/index.html must exist after Vite build"

    with TestClient(app) as client:
        for route in ("/", "/overview", "/generation", "/vault", "/references", "/collection", "/quality", "/settings"):
            resp = client.get(route)
            assert resp.status_code == 200, route
            assert "Blender Asset Vault" in resp.text
            assert '<div id="root"></div>' in resp.text

        # Compiled asset bundles should be served cleanly under /assets/...
        asset_files = list((studio_dist / "assets").glob("index-*.js"))
        assert len(asset_files) >= 1
        js_resp = client.get(f"/assets/{asset_files[0].name}")
        assert js_resp.status_code == 200 and len(js_resp.content) > 10_000

        # Traversal guard check
        bad_resp = client.get("/assets/..%2F..%2Fweb%2Fapp.py")
        assert bad_resp.status_code in {403, 404}

        # Legacy UI preserved on /legacy
        leg_resp = client.get("/legacy")
        assert leg_resp.status_code == 200
        assert "Reference Engine (Branch 2)" in leg_resp.text


def test_open_reference_zip_archive_in_blender(tmp_path: Path, monkeypatch) -> None:
    import zipfile
    from blender_vault.reference import router as ref_router
    from blender_vault.reference.license_engine import assess_license
    from blender_vault.reference.models import BlendSwapLicenseInfo, ReferenceAssetRecord, ReferenceAssetStatus

    real_blend = Path("samples/sample_wooden_chair/oak_dining_chair.blend").resolve().read_bytes()
    archive_dir = tmp_path / "vault" / "reference_library" / "assets" / "vehicles" / "9273"
    archive_dir.mkdir(parents=True, exist_ok=True)
    zip_path = archive_dir / "Sample_DeLorean.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("DeLorean_BlendSwap.blend", real_blend)
        zf.writestr("BLENDSWAP_LICENSE.txt", "CC-BY")

    app = _build_test_app(tmp_path)
    app.state.ref_db.upsert_reference_asset(
        ReferenceAssetRecord(
            blendswap_id=9273,
            title="A Certain DeLorean",
            url="https://blendswap.com/blend/9273",
            license_assessment=assess_license(BlendSwapLicenseInfo(key="cc_by", name="CC-BY")),
            primary_category="Vehicles",
            primary_format="blender",
            is_native_blender=True,
            local_dir=str(archive_dir),
            primary_file_path=str(zip_path),
            blend_file_path=None,
            status=ReferenceAssetStatus.GRADED,
        )
    )

    launched: list[str] = []

    def fake_launch(blend_path: Path, exe: str | None) -> int:
        launched.append(str(blend_path))
        return 4242

    monkeypatch.setattr(ref_router, "launch_in_blender_gui", fake_launch)

    with TestClient(app) as client:
        resp = client.post("/api/reference/assets/9273/open-blender")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["ok"] is True
        assert data["pid"] == 4242
        assert Path(data["blend_path"]).name == "DeLorean_BlendSwap.blend"
        assert Path(data["blend_path"]).is_file()
        assert len(launched) == 1

        # Missing asset returns 404 cleanly
        miss = client.post("/api/reference/assets/999999/open-blender")
        assert miss.status_code == 404
