"""Google Drive API v3 integration tests using a real local HTTP Drive server + real googleapiclient."""

from __future__ import annotations

import hashlib
import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from blender_vault.blender_worker.runner import BlenderWorkerRunner
from blender_vault.config import Settings
from blender_vault.db import VaultDatabase
from blender_vault.models import (
    AssetRecord,
    AssetStatus,
    AssetVersion,
    DriveSyncStatus,
    GeometryProfile,
    HumanApprovalStatus,
    HumanReviewRecord,
    ProvenanceInfo,
    TechnicalQAStatus,
)
from blender_vault.services.gdrive_service import (
    DriveIntegrityError,
    GoogleDriveSyncService,
    load_protected_token,
    save_protected_token,
)
from blender_vault.services.storage import VaultStorage


def build_local_drive_server():
    """Spin up a minimal genuine HTTP server implementing Google Drive v3 REST + resumable uploads."""
    store: dict[str, dict[str, Any]] = {
        "files": {},  # file_id -> {id, name, mimeType, parents, content, size, md5Checksum, trashed, webViewLink}
        "sessions": {},
        "inject_503_once": True,
    }
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return

        def _send_json(self, code: int, payload: Any, headers: dict[str, str] | None = None) -> None:
            raw = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            if headers:
                for k, v in headers.items():
                    self.send_header(k, v)
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            if parsed.path == "/drive/v3/about":
                self._send_json(200, {"user": {"emailAddress": "testuser@example.com", "displayName": "Test User"}})
                return

            if parsed.path == "/drive/v3/files":
                q = (qs.get("q") or [""])[0]
                with lock:
                    items = [f for f in store["files"].values() if not f["trashed"]]
                    if "name = '" in q:
                        target_name = q.split("name = '")[1].split("'")[0]
                        items = [f for f in items if f["name"] == target_name]
                    if "in parents" in q:
                        parent = q.split("' in parents")[0].split("'")[-1]
                        items = [f for f in items if parent in f.get("parents", [])]
                    if "mimeType = 'application/vnd.google-apps.folder'" in q:
                        items = [f for f in items if f["mimeType"] == "application/vnd.google-apps.folder"]
                    out = [
                        {k: v for k, v in f.items() if k != "content"}
                        for f in items
                    ]
                self._send_json(200, {"files": out})
                return

            if parsed.path.startswith("/drive/v3/files/"):
                fid = parsed.path.split("/")[-1]
                with lock:
                    f = store["files"].get(fid)
                if not f:
                    self._send_json(404, {"error": {"code": 404, "message": "Not found"}})
                    return
                if qs.get("alt") == ["media"]:
                    content = f["content"]
                    self.send_response(200)
                    self.send_header("Content-Type", f["mimeType"])
                    self.send_header("Content-Length", str(len(content)))
                    self.end_headers()
                    self.wfile.write(content)
                    return
                self._send_json(200, {k: v for k, v in f.items() if k != "content"})
                return

            self._send_json(404, {"error": {"message": f"Unknown path {parsed.path}"}})

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            length = int(self.headers.get("Content-Length") or 0)
            body_raw = self.rfile.read(length) if length > 0 else b"{}"

            if parsed.path == "/drive/v3/files":
                meta = json.loads(body_raw.decode("utf-8"))
                fid = f"drv_{uuid.uuid4().hex[:10]}"
                rec = {
                    "id": fid,
                    "name": meta["name"],
                    "mimeType": meta.get("mimeType", "application/vnd.google-apps.folder"),
                    "parents": meta.get("parents", ["root"]),
                    "trashed": False,
                    "content": b"",
                    "size": "0",
                    "md5Checksum": None,
                    "webViewLink": f"https://drive.google.com/drive/folders/{fid}",
                }
                with lock:
                    store["files"][fid] = rec
                self._send_json(200, {k: v for k, v in rec.items() if k != "content"})
                return

            if parsed.path == "/upload/drive/v3/files" and qs.get("uploadType") == ["resumable"]:
                meta = json.loads(body_raw.decode("utf-8"))
                sid = uuid.uuid4().hex[:10]
                with lock:
                    store["sessions"][sid] = {"meta": meta, "buf": bytearray()}
                host = self.headers.get("Host")
                self.send_response(200)
                self.send_header("Location", f"http://{host}/upload/session/{sid}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return

            self._send_json(404, {"error": {"message": f"Unknown POST {parsed.path}"}})

        def do_PUT(self) -> None:
            parsed = urlparse(self.path)
            length = int(self.headers.get("Content-Length") or 0)
            chunk = self.rfile.read(length) if length > 0 else b""
            if parsed.path.startswith("/upload/session/"):
                sid = parsed.path.split("/")[-1]
                with lock:
                    if store["inject_503_once"]:
                        store["inject_503_once"] = False
                        self._send_json(503, {"error": {"code": 503, "message": "Transient test 503"}})
                        return
                    sess = store["sessions"].pop(sid)
                    meta = sess["meta"]
                    update_fid = sess.get("update_file_id")
                md5_hex = hashlib.md5(chunk).hexdigest()
                if update_fid:
                    with lock:
                        rec = store["files"][update_fid]
                        rec["content"] = chunk
                        rec["size"] = str(len(chunk))
                        rec["md5Checksum"] = md5_hex
                    self._send_json(200, {k: v for k, v in rec.items() if k != "content"})
                    return
                fid = f"drv_{uuid.uuid4().hex[:10]}"
                rec = {
                    "id": fid,
                    "name": meta["name"],
                    "mimeType": "application/octet-stream",
                    "parents": meta.get("parents", ["root"]),
                    "trashed": False,
                    "content": chunk,
                    "size": str(len(chunk)),
                    "md5Checksum": md5_hex,
                    "webViewLink": f"https://drive.google.com/file/d/{fid}/view",
                }
                with lock:
                    store["files"][fid] = rec
                self._send_json(200, {k: v for k, v in rec.items() if k != "content"})
                return

            if parsed.path.startswith("/upload/drive/v3/files/"):
                # MediaFileUpload update
                fid = parsed.path.split("/")[-1]
                with lock:
                    rec = store["files"][fid]
                    rec["content"] = chunk
                    rec["size"] = str(len(chunk))
                    rec["md5Checksum"] = hashlib.md5(chunk).hexdigest()
                self._send_json(200, {k: v for k, v in rec.items() if k != "content"})
                return

            self._send_json(404, {"error": {"message": f"Unknown PUT {parsed.path}"}})

        def do_PATCH(self) -> None:
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            length = int(self.headers.get("Content-Length") or 0)
            body_raw = self.rfile.read(length) if length > 0 else b"{}"
            if parsed.path.startswith("/upload/drive/v3/files/") and qs.get("uploadType") == ["resumable"]:
                fid = parsed.path.split("/")[-1]
                meta = json.loads(body_raw.decode("utf-8"))
                sid = uuid.uuid4().hex[:10]
                with lock:
                    store["sessions"][sid] = {"meta": meta, "update_file_id": fid}
                host = self.headers.get("Host")
                self.send_response(200)
                self.send_header("Location", f"http://{host}/upload/session/{sid}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self._send_json(404, {"error": {"message": f"Unknown PATCH {parsed.path}"}})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{port}", store


def _local_discovery_doc(base_url: str) -> dict[str, Any]:
    return {
        "kind": "discovery#restDescription",
        "discoveryVersion": "v1",
        "id": "drive:v3",
        "name": "drive",
        "version": "v3",
        "title": "Drive API",
        "protocol": "rest",
        "baseUrl": f"{base_url}/drive/v3/",
        "basePath": "/drive/v3/",
        "rootUrl": f"{base_url}/",
        "servicePath": "drive/v3/",
        "parameters": {"alt": {"type": "string", "location": "query"}},
        "schemas": {
            "File": {"id": "File", "type": "object", "properties": {"id": {"type": "string"}, "name": {"type": "string"}, "mimeType": {"type": "string"}, "parents": {"type": "array", "items": {"type": "string"}}}},
            "FileList": {"id": "FileList", "type": "object", "properties": {"files": {"type": "array", "items": {"$ref": "File"}}}},
            "About": {"id": "About", "type": "object", "properties": {"user": {"type": "object"}}},
        },
        "resources": {
            "about": {
                "methods": {
                    "get": {
                        "id": "drive.about.get",
                        "path": "about",
                        "httpMethod": "GET",
                        "parameters": {"fields": {"type": "string", "location": "query"}},
                        "response": {"$ref": "About"},
                    }
                }
            },
            "files": {
                "methods": {
                    "list": {
                        "id": "drive.files.list",
                        "path": "files",
                        "httpMethod": "GET",
                        "parameters": {
                            "q": {"type": "string", "location": "query"},
                            "fields": {"type": "string", "location": "query"},
                            "orderBy": {"type": "string", "location": "query"},
                            "pageSize": {"type": "integer", "location": "query"},
                        },
                        "response": {"$ref": "FileList"},
                    },
                    "get": {
                        "id": "drive.files.get",
                        "path": "files/{fileId}",
                        "httpMethod": "GET",
                        "supportsMediaDownload": True,
                        "parameterOrder": ["fileId"],
                        "parameters": {
                            "fileId": {"type": "string", "required": True, "location": "path"},
                            "fields": {"type": "string", "location": "query"},
                        },
                        "response": {"$ref": "File"},
                    },
                    "create": {
                        "id": "drive.files.create",
                        "path": "files",
                        "httpMethod": "POST",
                        "supportsMediaUpload": True,
                        "mediaUpload": {
                            "accept": ["*/*"],
                            "protocols": {
                                "resumable": {"multipart": True, "path": "/upload/drive/v3/files"},
                                "simple": {"multipart": True, "path": "/upload/drive/v3/files"},
                            },
                        },
                        "parameters": {"fields": {"type": "string", "location": "query"}},
                        "request": {"$ref": "File"},
                        "response": {"$ref": "File"},
                    },
                    "update": {
                        "id": "drive.files.update",
                        "path": "files/{fileId}",
                        "httpMethod": "PATCH",
                        "supportsMediaUpload": True,
                        "mediaUpload": {
                            "accept": ["*/*"],
                            "protocols": {
                                "resumable": {"multipart": True, "path": "/upload/drive/v3/files/{fileId}"},
                                "simple": {"multipart": True, "path": "/upload/drive/v3/files/{fileId}"},
                            },
                        },
                        "parameterOrder": ["fileId"],
                        "parameters": {
                            "fileId": {"type": "string", "required": True, "location": "path"},
                            "fields": {"type": "string", "location": "query"},
                        },
                        "request": {"$ref": "File"},
                        "response": {"$ref": "File"},
                    },
                }
            },
        },
    }


def test_encrypted_token_storage_roundtrip(tmp_path: Path) -> None:
    from cryptography.fernet import Fernet

    key = Fernet.generate_key().decode("ascii")
    token_path = tmp_path / "gdrive_token.json"
    payload = json.dumps({"token": "tok_123", "refresh_token": "ref_456"})
    save_protected_token(token_path, payload, fernet_key=key)
    assert "ref_456" not in token_path.read_text(encoding="utf-8")
    loaded = load_protected_token(token_path, fernet_key=key)
    assert loaded["refresh_token"] == "ref_456"

    # Also test default Windows DPAPI protection
    dpapi_path = tmp_path / "dpapi_token.json"
    save_protected_token(dpapi_path, payload)
    assert "ref_456" not in dpapi_path.read_text(encoding="utf-8")
    assert load_protected_token(dpapi_path)["refresh_token"] == "ref_456"


def test_drive_resumable_upload_retry_verify_and_blender_reopen(tmp_path: Path) -> None:
    server, base_url, store = build_local_drive_server()
    try:
        from googleapiclient.discovery import build_from_document

        doc = _local_discovery_doc(base_url)
        cfg = Settings(
            vault_data_dir=tmp_path / "vault",
            gdrive_token_path=tmp_path / "creds" / "token.json",
            gdrive_client_secrets_path=tmp_path / "creds" / "client_secrets.json",
            blender_executable=Settings().blender_executable,
        )
        cfg.ensure_directories()
        save_protected_token(
            cfg.gdrive_token_path,
            json.dumps(
                {
                    "token": "fake_access_tok",
                    "refresh_token": "fake_refresh_tok",
                    "client_id": "cid",
                    "client_secret": "csec",
                    "token_uri": f"{base_url}/token",
                    "expiry": "2099-01-01T00:00:00Z",
                }
            ),
        )

        db = VaultDatabase(cfg.db_path)
        storage = VaultStorage(cfg.assets_dir, cfg.imports_dir)
        runner = BlenderWorkerRunner(cfg.blender_executable, timeout_seconds=120)

        def svc_factory(creds: Credentials) -> Any:
            import httplib2

            return build_from_document(doc, http=httplib2.Http(proxy_info=None))

        gdrive = GoogleDriveSyncService(cfg, db, storage, service_factory=svc_factory)

        # Create a real Blender .blend asset first
        vdir = storage.version_dir("chair01", 1)
        blend_file = vdir / "oak_chair.blend"
        runner.run_script(
            "generate.py",
            {
                "asset_name": "Oak Chair",
                "prompt": "test cube",
                "template_id": "test_cube",
                "output_blend": str(blend_file.resolve()),
                "output_glb": str((vdir / "oak_chair.glb").resolve()),
            },
            tmp_path / "gen",
        )
        fsize, sha, md5 = storage.compute_file_hashes(blend_file)

        v1 = AssetVersion(
            asset_id="chair01",
            version=1,
            status=AssetStatus.APPROVED,
            blend_path=str(blend_file),
            glb_path=str(vdir / "oak_chair.glb"),
            file_formats=["blend", "glb"],
            file_size_bytes=fsize,
            sha256=sha,
            md5=md5,
            human_review=HumanReviewRecord(status=HumanApprovalStatus.APPROVED, reviewer="Azlan"),
        )
        asset = AssetRecord(
            asset_id="chair01",
            slug="oak_chair",
            name="Oak Chair",
            description="Test chair",
            category="Furniture",
            geometry_profile=GeometryProfile.FURNITURE_PROP,
            tags=["chair"],
            provenance=ProvenanceInfo.for_generated_original("test_cube"),
            current_version=1,
        )
        db.create_asset_with_version(asset, v1)

        # Upload triggers one transient 503 and must retry cleanly
        updated_v = gdrive.upload_asset_version(asset, v1)
        assert updated_v.drive_sync_status == DriveSyncStatus.VERIFIED
        assert updated_v.status == AssetStatus.UPLOADED
        assert len(updated_v.drive_files) >= 4
        assert all(df.verified for df in updated_v.drive_files)

        # Verify remote integrity + download + Blender reopen
        ver_res = gdrive.verify_remote_version_integrity(updated_v, tmp_path / "dl_verify", runner)
        assert ver_res["verified"] is True
        assert ver_res["blender_reopen_ok"] is True
        assert ver_res["downloaded_sha256"] == sha

        # Tamper remote MD5 in test store and verify integrity check catches corruption!
        blend_id = next(df.remote_file_id for df in updated_v.drive_files if df.role == "blend")
        store["files"][blend_id]["md5Checksum"] = "00000000000000000000000000000000"
        with pytest.raises(DriveIntegrityError):
            gdrive.verify_remote_version_integrity(updated_v, tmp_path / "dl_tamper", runner)
    finally:
        server.shutdown()
