"""Phase 2 tests: BlendSwap REST API integration, auth, allowance guard, 429 retry, and download expiry refresh."""

from __future__ import annotations

from pathlib import Path

import pytest

from blender_vault.config import Settings
from blender_vault.db import VaultDatabase
from blender_vault.reference.blendswap_client import (
    BlendSwapAllowanceExhaustedError,
    BlendSwapAuthError,
    BlendSwapClient,
    BlendSwapCreditError,
)
from blender_vault.reference.ref_db import ReferenceDatabase
from tests.mock_blendswap_server import build_mock_blendswap_server


def test_blendswap_auth_endpoints_and_usage_logging(tmp_path: Path) -> None:
    server, base_url, state = build_mock_blendswap_server(inject_429_once=True)
    try:
        cfg = Settings(
            vault_data_dir=tmp_path / "vault",
            blendswap_key="bsk_test_valid_key",
            blendswap_base_url=f"{base_url}/api/v1",
            allow_credit_spend=False,
        )
        cfg.ensure_directories()
        ref_db = ReferenceDatabase(VaultDatabase(cfg.db_path))
        client = BlendSwapClient(cfg, ref_db, max_retry_sleep=0.05)

        # GET /me handles transient 429 cleanly and returns allowance & credits
        conn_info = client.check_connectivity()
        assert conn_info["connected"] is True
        assert conn_info["account"]["free_downloads_remaining"] == 7
        assert conn_info["account"]["credits"] == 50

        # GET /licenses & GET /categories cache in SQLite
        licenses = client.get_licenses()
        assert len(licenses) == 9
        assert len(ref_db.list_licenses_cache()) == 9

        categories = client.get_categories()
        assert len(categories) >= 10
        assert len(ref_db.list_categories_cache()) >= 10

        # GET /assets with sort=downloads
        assets, pagination = client.search_assets(sort="downloads", per_page=5)
        assert len(assets) == 5
        assert assets[0].counts.downloads >= assets[1].counts.downloads
        assert pagination["count"] >= 10

        # GET /assets/{id} & /assets/{id}/files
        detail = client.get_asset(assets[0].id)
        assert detail.id == assets[0].id
        files = client.get_asset_files(assets[0].id)
        assert len(files) >= 1

        # API usage tracking recorded calls
        usage = ref_db.get_api_usage_summary()
        assert usage["total_calls"] >= 6
        client.close()
    finally:
        server.shutdown()


def test_blendswap_invalid_key_raises_auth_error(tmp_path: Path) -> None:
    server, base_url, _ = build_mock_blendswap_server()
    try:
        cfg = Settings(
            vault_data_dir=tmp_path / "vault",
            blendswap_key="bsk_wrong_key",
            blendswap_base_url=f"{base_url}/api/v1",
        )
        ref_db = ReferenceDatabase(VaultDatabase(cfg.db_path))
        client = BlendSwapClient(cfg, ref_db)
        with pytest.raises(BlendSwapAuthError):
            client.get_me()
        client.close()
    finally:
        server.shutdown()


def test_blendswap_credit_guard_and_expired_url_refresh(tmp_path: Path) -> None:
    server, base_url, state = build_mock_blendswap_server(
        free_downloads_remaining=1,
        credits=50,
        expire_first_download_url=True,
    )
    try:
        cfg = Settings(
            vault_data_dir=tmp_path / "vault",
            blendswap_key="bsk_test_valid_key",
            blendswap_base_url=f"{base_url}/api/v1",
            allow_credit_spend=False,
        )
        ref_db = ReferenceDatabase(VaultDatabase(cfg.db_path))
        client = BlendSwapClient(cfg, ref_db)

        # First download uses the 1 free download; first signed URL returns 410 expired,
        # so client refreshes via POST /downloads (free repeat) and downloads file cleanly!
        dest = tmp_path / "chair.blend"
        ticket, size_b, sha, md5 = client.download_with_refresh(
            asset_id=1001,
            file_id=10011,
            dest_path=dest,
        )
        assert dest.is_file() and size_b > 0
        assert ticket.charged is False
        assert state["credits"] == 50  # Zero credits spent!
        assert state["free_remaining"] == 0

        # Download previews from ticket
        previews = client.download_previews(ticket.preview_urls, tmp_path / "previews")
        assert len(previews) == 1 and previews[0].is_file()

        # Next new asset MUST be blocked proactively because free_remaining == 0 and allow_credit_spend == False
        with pytest.raises(BlendSwapAllowanceExhaustedError):
            client.request_download(asset_id=1002, file_id=10021)
        assert state["credits"] == 50

        # If explicitly tested with skip_allowance_precheck and 0 credits -> 402 raises BlendSwapCreditError
        state["credits"] = 0
        with pytest.raises(BlendSwapCreditError):
            client.request_download(asset_id=1002, file_id=10021, allow_credit_spend=True, skip_allowance_precheck=True)
        client.close()
    finally:
        server.shutdown()


def test_blendswap_origin_check_and_file_scoped_repeat_window(tmp_path: Path) -> None:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    from blender_vault.reference.blendswap_client import _is_same_origin

    assert _is_same_origin("https://blendswap.com/cdn/1.png", "https://blendswap.com/api/v1") is True
    assert _is_same_origin("https://blendswap.com:443/cdn/1.png", "https://blendswap.com/api/v1") is True
    assert _is_same_origin("https://blendswap.com.attacker.example/cdn/1.png", "https://blendswap.com/api/v1") is False
    assert _is_same_origin("https://blendswap.com@attacker.example/cdn/1.png", "https://blendswap.com/api/v1") is False
    assert _is_same_origin("http://blendswap.com/cdn/1.png", "https://blendswap.com/api/v1") is False

    seen_auth: list[str] = []

    class CaptureHandler(BaseHTTPRequestHandler):
        def log_message(self, format, *args) -> None:
            return

        def do_GET(self) -> None:
            seen_auth.append(self.headers.get("Authorization", ""))
            body = b"hello"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), CaptureHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        other_port = srv.server_address[1]
        cfg = Settings(
            vault_data_dir=tmp_path / "vault",
            blendswap_key="bsk_secret_token",
            blendswap_base_url=f"http://127.0.0.1:{other_port + 1}/api/v1",
        )
        ref_db = ReferenceDatabase(VaultDatabase(cfg.db_path))
        client = BlendSwapClient(cfg, ref_db)

        # Different port must NOT receive Bearer Authorization header
        client.download_binary(
            f"http://127.0.0.1:{other_port}/preview.png",
            tmp_path / "out.png",
            send_auth_if_same_host=True,
        )
        assert seen_auth == [""]

        # Same exact origin DOES receive Bearer Authorization header
        client.base_url = f"http://127.0.0.1:{other_port}/api/v1"
        client.download_binary(
            f"http://127.0.0.1:{other_port}/preview.png",
            tmp_path / "out2.png",
            send_auth_if_same_host=True,
        )
        assert seen_auth[-1] == "Bearer bsk_secret_token"

        # Free repeat grant for file_id=10011 must NOT apply to file_id=None or file_id=10012
        ref_db.record_ticket_grant(1001, 10011, "free_allowance", "2099-01-01T00:00:00Z")
        assert ref_db.has_free_repeat_window(1001, 10011) is True
        assert ref_db.has_free_repeat_window(1001, 10012) is False
        assert ref_db.has_free_repeat_window(1001, None) is False
        client.close()
    finally:
        srv.shutdown()
