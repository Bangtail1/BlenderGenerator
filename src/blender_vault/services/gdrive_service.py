"""Official Google Drive API v3 integration with OAuth 2.0, resumable uploads, verification, and versioned vault layout."""

from __future__ import annotations

import base64
import ctypes
import ctypes.wintypes
import io
import json
import logging
import mimetypes
import os
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

from ..config import STANDARD_CATEGORIES, Settings
from ..db import VaultDatabase
from ..hashing import compute_md5, compute_sha256
from ..models import (
    AssetManifest,
    AssetRecord,
    AssetStatus,
    AssetVersion,
    DriveFileRecord,
    DriveSyncStatus,
    utc_now_iso,
)
from .storage import VaultStorage

logger = logging.getLogger("blender_vault.gdrive")

FOLDER_MIME = "application/vnd.google-apps.folder"
TRANSIENT_HTTP_CODES = {408, 429, 500, 502, 503, 504}


class DriveNotConfiguredError(RuntimeError):
    """Raised when Google OAuth client_secrets.json is missing."""


class DriveNotAuthenticatedError(RuntimeError):
    """Raised when no valid Google OAuth token exists yet."""


class DriveIntegrityError(RuntimeError):
    """Raised when uploaded/downloaded Drive file fails size or MD5 verification."""


# ---------------------------------------------------------------------------
# Secure local token storage (Fernet key or Windows DPAPI envelope)
# ---------------------------------------------------------------------------

def _dpapi_protect(raw_bytes: bytes) -> bytes:
    """Encrypt bytes using Windows DPAPI (current user scope)."""
    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", ctypes.wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    in_buf = ctypes.create_string_buffer(raw_bytes)
    blob_in = DATA_BLOB(len(raw_bytes), ctypes.cast(in_buf, ctypes.POINTER(ctypes.c_byte)))
    blob_out = DATA_BLOB()
    if not crypt32.CryptProtectData(ctypes.byref(blob_in), "BlenderAssetVaultToken", None, None, None, 0, ctypes.byref(blob_out)):
        raise OSError("Windows DPAPI CryptProtectData failed.")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def _dpapi_unprotect(enc_bytes: bytes) -> bytes:
    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", ctypes.wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    in_buf = ctypes.create_string_buffer(enc_bytes)
    blob_in = DATA_BLOB(len(enc_bytes), ctypes.cast(in_buf, ctypes.POINTER(ctypes.c_byte)))
    blob_out = DATA_BLOB()
    if not crypt32.CryptUnprotectData(ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
        raise OSError("Windows DPAPI CryptUnprotectData failed.")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def save_protected_token(token_path: Path, token_json: str, fernet_key: str = "") -> None:
    token_path.parent.mkdir(parents=True, exist_ok=True)
    raw = token_json.encode("utf-8")
    if fernet_key.strip():
        from cryptography.fernet import Fernet

        cipher = Fernet(fernet_key.strip().encode("utf-8"))
        envelope = {"encoding": "fernet", "payload": cipher.encrypt(raw).decode("ascii")}
    elif os.name == "nt":
        protected = _dpapi_protect(raw)
        envelope = {"encoding": "win_dpapi", "payload": base64.b64encode(protected).decode("ascii")}
    else:
        envelope = {"encoding": "plain_json", "payload": json.loads(token_json)}

    tmp = token_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(envelope, indent=2), encoding="utf-8")
    tmp.replace(token_path)
    if os.name != "nt":
        os.chmod(token_path, 0o600)


def load_protected_token(token_path: Path, fernet_key: str = "") -> Optional[dict[str, Any]]:
    if not token_path.is_file():
        return None
    data = json.loads(token_path.read_text(encoding="utf-8"))
    enc = data.get("encoding")
    if enc == "fernet":
        from cryptography.fernet import Fernet

        if not fernet_key.strip():
            raise DriveNotAuthenticatedError("VAULT_TOKEN_ENCRYPTION_KEY is required to decrypt stored Drive token.")
        cipher = Fernet(fernet_key.strip().encode("utf-8"))
        raw = cipher.decrypt(data["payload"].encode("ascii"))
        return json.loads(raw.decode("utf-8"))
    if enc == "win_dpapi":
        raw = _dpapi_unprotect(base64.b64decode(data["payload"]))
        return json.loads(raw.decode("utf-8"))
    if enc == "plain_json":
        return data["payload"]
    # Support raw authorized_user JSON if externally provided
    if "refresh_token" in data or "token" in data:
        return data
    return None


@dataclass
class UploadProgress:
    filename: str
    bytes_sent: int
    total_bytes: int
    fraction: float


class GoogleDriveSyncService:
    """Manages OAuth 2.0 authentication, Drive Vault hierarchy, resumable uploads, verification, and downloads."""

    def __init__(
        self,
        settings: Settings,
        db: VaultDatabase,
        storage: VaultStorage,
        *,
        service_factory: Optional[Callable[[Credentials], Any]] = None,
    ) -> None:
        self.settings = settings
        self.db = db
        self.storage = storage
        self._service_factory = service_factory
        self._pending_oauth_flows: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Auth & Status
    # ------------------------------------------------------------------
    def get_auth_status(self) -> dict[str, Any]:
        secrets_exists = self.settings.gdrive_client_secrets_path.is_file()
        token_exists = self.settings.gdrive_token_path.is_file()
        selected_folder_id = self.db.get_setting("gdrive_root_folder_id") or self.settings.gdrive_root_folder_id or None
        selected_folder_name = self.db.get_setting("gdrive_root_folder_name") or self.settings.gdrive_root_folder_name

        authenticated = False
        user_email = None
        token_error = None
        if token_exists:
            try:
                creds = self._load_credentials(refresh_if_needed=False)
                authenticated = creds is not None and (creds.valid or bool(creds.refresh_token))
            except Exception as exc:
                token_error = str(exc)

        if authenticated:
            try:
                svc = self.get_service()
                about = self._retry(lambda: svc.about().get(fields="user(emailAddress,displayName)").execute())
                user_email = about.get("user", {}).get("emailAddress")
            except Exception as exc:
                token_error = str(exc)

        return {
            "client_secrets_configured": secrets_exists,
            "client_secrets_path": str(self.settings.gdrive_client_secrets_path),
            "token_present": token_exists,
            "authenticated": authenticated,
            "user_email": user_email,
            "scopes": self.settings.parsed_gdrive_scopes,
            "root_folder_id": selected_folder_id,
            "root_folder_name": selected_folder_name,
            "error": token_error,
        }

    def _load_credentials(self, *, refresh_if_needed: bool = True) -> Optional[Credentials]:
        token_info = load_protected_token(
            self.settings.gdrive_token_path,
            self.settings.vault_token_encryption_key,
        )
        if not token_info:
            return None
        creds = Credentials.from_authorized_user_info(token_info, scopes=self.settings.parsed_gdrive_scopes)
        if refresh_if_needed and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            save_protected_token(
                self.settings.gdrive_token_path,
                creds.to_json(),
                self.settings.vault_token_encryption_key,
            )
        return creds

    def authenticate_interactive_local_server(self, port: int = 0) -> dict[str, Any]:
        """Run standard Desktop OAuth 2.0 loopback flow and store encrypted refresh token."""
        secrets_path = self.settings.gdrive_client_secrets_path
        if not secrets_path.is_file():
            raise DriveNotConfiguredError(
                f"Google OAuth client secrets file not found at '{secrets_path}'. "
                "See docs/GOOGLE_DRIVE_OAUTH_SETUP.md to create Desktop OAuth credentials."
            )
        flow = InstalledAppFlow.from_client_secrets_file(
            str(secrets_path),
            scopes=self.settings.parsed_gdrive_scopes,
        )
        creds = flow.run_local_server(port=port, access_type="offline", prompt="consent")
        save_protected_token(
            self.settings.gdrive_token_path,
            creds.to_json(),
            self.settings.vault_token_encryption_key,
        )
        return self.get_auth_status()

    def build_web_auth_url(self, redirect_uri: str) -> str:
        """Start an OAuth 2.0 authorization-code flow from the web UI."""
        secrets_path = self.settings.gdrive_client_secrets_path
        if not secrets_path.is_file():
            raise DriveNotConfiguredError(
                f"Google OAuth client secrets file not found at '{secrets_path}'. "
                "Follow docs/GOOGLE_DRIVE_OAUTH_SETUP.md first."
            )
        flow = InstalledAppFlow.from_client_secrets_file(
            str(secrets_path),
            scopes=self.settings.parsed_gdrive_scopes,
            redirect_uri=redirect_uri,
        )
        auth_url, state = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",
        )
        self._pending_oauth_flows[state] = {"flow": flow, "redirect_uri": redirect_uri}
        return auth_url

    def complete_web_auth_callback(self, state: str, authorization_response_url: str) -> dict[str, Any]:
        entry = self._pending_oauth_flows.pop(state, None)
        if not entry:
            raise DriveNotAuthenticatedError("OAuth state expired or invalid. Restart Google Drive authorization.")
        flow: InstalledAppFlow = entry["flow"]
        flow.fetch_token(authorization_response=authorization_response_url)
        creds = flow.credentials
        save_protected_token(
            self.settings.gdrive_token_path,
            creds.to_json(),
            self.settings.vault_token_encryption_key,
        )
        return self.get_auth_status()

    def disconnect(self) -> None:
        if self.settings.gdrive_token_path.is_file():
            self.settings.gdrive_token_path.unlink()

    def get_service(self) -> Any:
        creds = self._load_credentials(refresh_if_needed=True)
        if creds is None:
            raise DriveNotAuthenticatedError(
                "Google Drive is not connected yet. Authorize via OAuth 2.0 in Settings."
            )
        if self._service_factory is not None:
            return self._service_factory(creds)
        return build("drive", "v3", credentials=creds, cache_discovery=False)

    # ------------------------------------------------------------------
    # Destination Folder Management & Vault Tree
    # ------------------------------------------------------------------
    def list_accessible_folders(self, parent_id: str = "root") -> list[dict[str, str]]:
        svc = self.get_service()
        safe_parent = parent_id.replace("'", "\\'")
        query = f"mimeType = '{FOLDER_MIME}' and '{safe_parent}' in parents and trashed = false"
        res = self._retry(
            lambda: svc.files()
            .list(
                q=query,
                fields="files(id,name,parents,webViewLink)",
                orderBy="name",
                pageSize=100,
            )
            .execute()
        )
        return res.get("files", [])

    def select_destination_folder(self, folder_id: str, folder_name: Optional[str] = None) -> dict[str, Any]:
        svc = self.get_service()
        meta = self._retry(lambda: svc.files().get(fileId=folder_id, fields="id,name,mimeType,trashed").execute())
        if meta.get("trashed"):
            raise ValueError(f"Selected folder '{folder_id}' is in trash.")
        if meta.get("mimeType") != FOLDER_MIME:
            raise ValueError(f"Drive ID '{folder_id}' is not a folder.")
        resolved_name = folder_name or meta["name"]
        self.db.set_setting("gdrive_root_folder_id", meta["id"])
        self.db.set_setting("gdrive_root_folder_name", resolved_name)
        return {"folder_id": meta["id"], "folder_name": resolved_name}

    def ensure_vault_folder_tree(self, parent_root_id: Optional[str] = None) -> dict[str, Any]:
        """Ensure the organized Blender Asset Vault directory hierarchy exists in Drive."""
        svc = self.get_service()
        stored_root_id = parent_root_id or self.db.get_setting("gdrive_root_folder_id") or self.settings.gdrive_root_folder_id
        if stored_root_id:
            vault_root_id = stored_root_id
        else:
            vault_root_id = self._ensure_child_folder(svc, "root", self.settings.gdrive_root_folder_name)
            self.db.set_setting("gdrive_root_folder_id", vault_root_id)
            self.db.set_setting("gdrive_root_folder_name", self.settings.gdrive_root_folder_name)

        approved_id = self._ensure_child_folder(svc, vault_root_id, "Approved Assets")
        category_ids: dict[str, str] = {}
        for cat in STANDARD_CATEGORIES:
            category_ids[cat] = self._ensure_child_folder(svc, approved_id, cat)

        pending_id = self._ensure_child_folder(svc, vault_root_id, "Pending Review")
        rejected_id = self._ensure_child_folder(svc, vault_root_id, "Rejected Assets")
        previews_id = self._ensure_child_folder(svc, vault_root_id, "Preview Images")
        qa_reports_id = self._ensure_child_folder(svc, vault_root_id, "QA Reports")
        index_id = self._ensure_child_folder(svc, vault_root_id, "Library Index")

        return {
            "root_id": vault_root_id,
            "approved_id": approved_id,
            "categories": category_ids,
            "pending_id": pending_id,
            "rejected_id": rejected_id,
            "previews_id": previews_id,
            "qa_reports_id": qa_reports_id,
            "index_id": index_id,
        }

    def _ensure_child_folder(self, svc: Any, parent_id: str, name: str) -> str:
        safe_name = name.replace("'", "\\'")
        safe_parent = parent_id.replace("'", "\\'")
        q = (
            f"name = '{safe_name}' and mimeType = '{FOLDER_MIME}' "
            f"and '{safe_parent}' in parents and trashed = false"
        )
        res = self._retry(lambda: svc.files().list(q=q, fields="files(id,name)", pageSize=5).execute())
        files = res.get("files", [])
        if files:
            return files[0]["id"]
        created = self._retry(
            lambda: svc.files()
            .create(
                body={"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]},
                fields="id,name",
            )
            .execute()
        )
        return created["id"]

    # ------------------------------------------------------------------
    # Resumable Upload & Strict Integrity Verification
    # ------------------------------------------------------------------
    def _upload_and_verify_file(
        self,
        svc: Any,
        *,
        local_path: Path,
        remote_name: str,
        parent_folder_id: str,
        role: str,
        progress_cb: Optional[Callable[[UploadProgress], None]] = None,
    ) -> DriveFileRecord:
        if not local_path.is_file():
            raise FileNotFoundError(f"Local file missing for Drive upload: {local_path}")

        local_size = local_path.stat().st_size
        local_md5 = compute_md5(local_path)
        local_sha256 = compute_sha256(local_path)

        # Duplicate check inside immutable version folder
        safe_name = remote_name.replace("'", "\\'")
        safe_parent = parent_folder_id.replace("'", "\\'")
        q = f"name = '{safe_name}' and '{safe_parent}' in parents and trashed = false"
        existing = self._retry(
            lambda: svc.files()
            .list(q=q, fields="files(id,name,size,md5Checksum,webViewLink,trashed)", pageSize=5)
            .execute()
        ).get("files", [])

        if existing:
            ex = existing[0]
            ex_size = int(ex.get("size") or -1)
            ex_md5 = ex.get("md5Checksum")
            if ex_size == local_size and ex_md5 == local_md5:
                logger.info("Remote file '%s' already exists with matching MD5 (%s); skipping duplicate upload.", remote_name, local_md5)
                return DriveFileRecord(
                    role=role,
                    local_path=str(local_path),
                    remote_file_id=ex["id"],
                    remote_name=remote_name,
                    remote_folder_id=parent_folder_id,
                    expected_size=local_size,
                    remote_size=ex_size,
                    local_md5=local_md5,
                    remote_md5=ex_md5,
                    local_sha256=local_sha256,
                    verified=True,
                    web_view_link=ex.get("webViewLink"),
                    synced_at=utc_now_iso(),
                )

        mime_type, _ = mimetypes.guess_type(str(local_path))
        if not mime_type:
            mime_type = "application/octet-stream"

        def _do_resumable_upload() -> dict[str, Any]:
            media = MediaFileUpload(
                str(local_path),
                mimetype=mime_type,
                resumable=True,
                chunksize=4 * 1024 * 1024,
            )
            request = svc.files().create(
                body={"name": remote_name, "parents": [parent_folder_id]},
                media_body=media,
                fields="id,name,size,md5Checksum,webViewLink",
            )
            response = None
            while response is None:
                status, response = request.next_chunk()
                if status and progress_cb:
                    progress_cb(
                        UploadProgress(
                            filename=remote_name,
                            bytes_sent=int(status.resumable_progress),
                            total_bytes=local_size,
                            fraction=float(status.progress()),
                        )
                    )
            if progress_cb:
                progress_cb(UploadProgress(filename=remote_name, bytes_sent=local_size, total_bytes=local_size, fraction=1.0))
            return response

        uploaded = self._retry(_do_resumable_upload)
        remote_id = uploaded.get("id")
        if not remote_id:
            raise DriveIntegrityError(f"Drive API did not return a file ID for '{remote_name}'.")

        # Explicit metadata verification GET
        meta = self._retry(
            lambda: svc.files()
            .get(fileId=remote_id, fields="id,name,size,md5Checksum,parents,trashed,webViewLink")
            .execute()
        )
        remote_size = int(meta.get("size") or -1)
        remote_md5 = meta.get("md5Checksum")
        if meta.get("trashed"):
            raise DriveIntegrityError(f"Uploaded file '{remote_name}' ({remote_id}) is marked trashed.")
        if remote_size != local_size:
            raise DriveIntegrityError(
                f"Remote size mismatch for '{remote_name}': local={local_size} vs remote={remote_size}."
            )
        if remote_md5 and remote_md5 != local_md5:
            raise DriveIntegrityError(
                f"Remote MD5 mismatch for '{remote_name}': local={local_md5} vs remote={remote_md5}."
            )

        return DriveFileRecord(
            role=role,
            local_path=str(local_path),
            remote_file_id=remote_id,
            remote_name=remote_name,
            remote_folder_id=parent_folder_id,
            expected_size=local_size,
            remote_size=remote_size,
            local_md5=local_md5,
            remote_md5=remote_md5,
            local_sha256=local_sha256,
            verified=True,
            web_view_link=meta.get("webViewLink"),
            synced_at=utc_now_iso(),
        )

    def upload_asset_version(
        self,
        asset: AssetRecord,
        version: AssetVersion,
        *,
        progress_cb: Optional[Callable[[UploadProgress], None]] = None,
    ) -> AssetVersion:
        """Upload an asset version into its appropriate Google Drive Vault folder with strict verification."""
        if version.status == AssetStatus.FAILED:
            raise ValueError("Cannot upload a FAILED asset version to Approved Assets.")

        svc = self.get_service()
        tree = self.ensure_vault_folder_tree()

        if version.status in (AssetStatus.APPROVED, AssetStatus.UPLOADED):
            parent_bucket = tree["categories"].get(asset.category) or tree["categories"]["Miscellaneous"]
        elif version.status == AssetStatus.REJECTED:
            parent_bucket = tree["rejected_id"]
        else:
            parent_bucket = tree["pending_id"]

        asset_folder_name = f"{asset.slug}_{asset.asset_id[:8]}"
        asset_folder_id = self._ensure_child_folder(svc, parent_bucket, asset_folder_name)
        version_folder_id = self._ensure_child_folder(svc, asset_folder_id, f"v{version.version}")

        # Never overwrite or delete an already populated immutable approved version folder
        existing_v_items = self._retry(
            lambda: svc.files()
            .list(q=f"'{version_folder_id}' in parents and trashed = false", fields="files(id,name,md5Checksum)", pageSize=50)
            .execute()
        ).get("files", [])
        if existing_v_items and version.drive_folder_id and version.drive_folder_id != version_folder_id:
            logger.info("Existing remote version folder %s detected; preserving existing artifacts.", version_folder_id)

        version.drive_sync_status = DriveSyncStatus.UPLOADING
        version.drive_folder_id = version_folder_id
        self.db.update_version(version)

        uploaded_records: list[DriveFileRecord] = []
        try:
            # Refresh local metadata artifacts before uploading
            vdir = self.storage.version_dir(asset.asset_id, version.version)
            artifacts = self.storage.write_version_artifacts(asset, version)

            upload_queue: list[tuple[str, Path, str, str]] = []
            if version.blend_path and Path(version.blend_path).is_file():
                p = Path(version.blend_path)
                upload_queue.append(("blend", p, p.name, version_folder_id))
            if version.glb_path and Path(version.glb_path).is_file():
                p = Path(version.glb_path)
                upload_queue.append(("glb", p, p.name, version_folder_id))
            for role, p in artifacts.items():
                upload_queue.append((role, p, p.name, version_folder_id))

            # Previews subfolder
            previews_local_dir = vdir / "previews"
            if previews_local_dir.is_dir():
                remote_previews_id = self._ensure_child_folder(svc, version_folder_id, "previews")
                for img in sorted(previews_local_dir.iterdir()):
                    if img.is_file() and img.suffix.lower() in {".png", ".webp", ".jpg", ".gif"}:
                        role = "turntable" if "turntable" in img.stem else "preview"
                        upload_queue.append((role, img, img.name, remote_previews_id))

            for role, local_p, rname, parent_id in upload_queue:
                rec = self._upload_and_verify_file(
                    svc,
                    local_path=local_p,
                    remote_name=rname,
                    parent_folder_id=parent_id,
                    role=role,
                    progress_cb=progress_cb,
                )
                uploaded_records.append(rec)

            # Mirror QA report & hero preview into top-level QA Reports / Preview Images indexes
            if "qa_report" in artifacts:
                self._upload_and_verify_file(
                    svc,
                    local_path=artifacts["qa_report"],
                    remote_name=f"{asset.slug}_{asset.asset_id[:8]}_v{version.version}_qa_report.json",
                    parent_folder_id=tree["qa_reports_id"],
                    role="qa_report_index",
                )
            hero_preview = previews_local_dir / "three_quarter.png"
            if hero_preview.is_file():
                self._upload_and_verify_file(
                    svc,
                    local_path=hero_preview,
                    remote_name=f"{asset.slug}_{asset.asset_id[:8]}_v{version.version}_three_quarter.png",
                    parent_folder_id=tree["previews_id"],
                    role="preview_index",
                )

            version.drive_files = uploaded_records
            version.drive_sync_status = DriveSyncStatus.VERIFIED
            if version.status == AssetStatus.APPROVED:
                version.status = AssetStatus.UPLOADED

            # Re-write manifest with final drive file IDs & upload updated manifest + library_index.json
            final_artifacts = self.storage.write_version_artifacts(asset, version)
            if "manifest" in final_artifacts:
                # Upload latest manifest cleanly only if changed (create new or update content in place for manifest.json)
                manifest_rec = self._upload_or_update_metadata_file(
                    svc,
                    local_path=final_artifacts["manifest"],
                    remote_name="manifest.json",
                    parent_folder_id=version_folder_id,
                    role="manifest",
                )
                version.drive_files = [r for r in uploaded_records if r.role != "manifest"] + [manifest_rec]

            self.db.update_version(version)

            # Update consolidated Library Index/library_index.json
            idx_path = self.settings.tmp_dir / "library_index.json"
            self.storage.export_library_index(self.db, idx_path)
            self._upload_or_update_metadata_file(
                svc,
                local_path=idx_path,
                remote_name="library_index.json",
                parent_folder_id=tree["index_id"],
                role="library_index",
            )
            return version
        except Exception as exc:
            version.drive_sync_status = DriveSyncStatus.FAILED
            version.error_message = f"Google Drive upload failed: {exc}"
            version.failed_stage = "google_drive_upload"
            self.db.update_version(version)
            raise

    def _upload_or_update_metadata_file(
        self,
        svc: Any,
        *,
        local_path: Path,
        remote_name: str,
        parent_folder_id: str,
        role: str,
    ) -> DriveFileRecord:
        safe_name = remote_name.replace("'", "\\'")
        safe_parent = parent_folder_id.replace("'", "\\'")
        q = f"name = '{safe_name}' and '{safe_parent}' in parents and trashed = false"
        existing = self._retry(lambda: svc.files().list(q=q, fields="files(id,md5Checksum)", pageSize=1).execute()).get("files", [])
        if not existing:
            return self._upload_and_verify_file(
                svc,
                local_path=local_path,
                remote_name=remote_name,
                parent_folder_id=parent_folder_id,
                role=role,
            )

        file_id = existing[0]["id"]
        local_size = local_path.stat().st_size
        local_md5 = compute_md5(local_path)
        local_sha256 = compute_sha256(local_path)
        media = MediaFileUpload(str(local_path), mimetype="application/json", resumable=True)
        updated = self._retry(
            lambda: svc.files()
            .update(fileId=file_id, media_body=media, fields="id,name,size,md5Checksum,webViewLink")
            .execute()
        )
        return DriveFileRecord(
            role=role,
            local_path=str(local_path),
            remote_file_id=updated["id"],
            remote_name=remote_name,
            remote_folder_id=parent_folder_id,
            expected_size=local_size,
            remote_size=int(updated.get("size") or local_size),
            local_md5=local_md5,
            remote_md5=updated.get("md5Checksum"),
            local_sha256=local_sha256,
            verified=(updated.get("md5Checksum") in (None, local_md5)),
            web_view_link=updated.get("webViewLink"),
            synced_at=utc_now_iso(),
        )

    # ------------------------------------------------------------------
    # Download & Remote Verification
    # ------------------------------------------------------------------
    def download_file(self, file_id: str, dest_path: Path, expected_sha256: Optional[str] = None, expected_md5: Optional[str] = None) -> Path:
        svc = self.get_service()
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = dest_path.with_suffix(dest_path.suffix + ".part")

        def _do_download() -> None:
            req = svc.files().get_media(fileId=file_id)
            with tmp_path.open("wb") as fh:
                downloader = MediaIoBaseDownload(fh, req, chunksize=4 * 1024 * 1024)
                done = False
                while not done:
                    _, done = downloader.next_chunk()

        self._retry(_do_download)
        actual_sha256 = compute_sha256(tmp_path)
        actual_md5 = compute_md5(tmp_path)
        if expected_sha256 and actual_sha256 != expected_sha256:
            tmp_path.unlink(missing_ok=True)
            raise DriveIntegrityError(f"Downloaded SHA-256 mismatch: expected {expected_sha256}, got {actual_sha256}.")
        if expected_md5 and actual_md5 != expected_md5:
            tmp_path.unlink(missing_ok=True)
            raise DriveIntegrityError(f"Downloaded MD5 mismatch: expected {expected_md5}, got {actual_md5}.")
        tmp_path.replace(dest_path)
        return dest_path

    def verify_remote_version_integrity(
        self,
        version: AssetVersion,
        temp_dir: Path,
        blender_runner: Any,
    ) -> dict[str, Any]:
        """Verify remote metadata + download .blend from Drive + reopen inside Blender."""
        svc = self.get_service()
        checked_files: list[dict[str, Any]] = []
        blend_record = next((df for df in version.drive_files if df.role == "blend"), None)
        if not blend_record or not blend_record.remote_file_id:
            raise DriveIntegrityError("No uploaded .blend file ID recorded for this asset version.")

        for df in version.drive_files:
            if not df.remote_file_id:
                continue
            meta = self._retry(
                lambda fid=df.remote_file_id: svc.files()
                .get(fileId=fid, fields="id,name,size,md5Checksum,trashed")
                .execute()
            )
            r_size = int(meta.get("size") or -1)
            r_md5 = meta.get("md5Checksum")
            ok = (not meta.get("trashed")) and (r_size == df.expected_size) and (not df.local_md5 or not r_md5 or r_md5 == df.local_md5)
            checked_files.append(
                {
                    "role": df.role,
                    "remote_file_id": df.remote_file_id,
                    "remote_name": meta.get("name"),
                    "size_match": r_size == df.expected_size,
                    "md5_match": (r_md5 == df.local_md5) if (r_md5 and df.local_md5) else None,
                    "ok": ok,
                }
            )
            if not ok:
                raise DriveIntegrityError(f"Remote integrity check failed for {df.remote_name} ({df.remote_file_id}).")

        # Download .blend copy & reopen in Blender to guarantee round-trip usability
        dl_blend = temp_dir / f"verify_{blend_record.remote_name}"
        self.download_file(
            blend_record.remote_file_id,
            dl_blend,
            expected_sha256=blend_record.local_sha256,
            expected_md5=blend_record.local_md5,
        )
        reopen_res = blender_runner.run_script(
            "inspect_qa.py",
            {
                "blend_path": str(dl_blend.resolve()),
                "roundtrip_glb": str((temp_dir / "verify_rt.glb").resolve()),
            },
            temp_dir / "reopen_check",
        )
        return {
            "verified": True,
            "checked_files": checked_files,
            "downloaded_blend": str(dl_blend),
            "downloaded_sha256": compute_sha256(dl_blend),
            "blender_reopen_ok": bool(reopen_res.get("file_opened_ok")),
            "reopened_mesh_count": reopen_res.get("mesh_object_count"),
        }

    def rebuild_catalogue_from_drive(self, temp_dir: Path) -> dict[str, Any]:
        """Discover remote manifest.json files in Drive and reconstruct local catalogue entries."""
        svc = self.get_service()
        res = self._retry(
            lambda: svc.files()
            .list(
                q="name = 'manifest.json' and trashed = false",
                fields="files(id,name,parents)",
                pageSize=200,
            )
            .execute()
        )
        files = res.get("files", [])
        imported = 0
        errors: list[str] = []
        for idx, f in enumerate(files):
            dest = temp_dir / f"remote_manifest_{idx}.json"
            try:
                self.download_file(f["id"], dest)
                manifest = AssetManifest.model_validate_json(dest.read_text(encoding="utf-8"))
                self.db.upsert_from_manifest(manifest)
                imported += 1
            except Exception as exc:
                errors.append(f"{f['id']}: {exc}")
        return {"remote_manifests_found": len(files), "reindexed": imported, "errors": errors}

    # ------------------------------------------------------------------
    # Retry Helper
    # ------------------------------------------------------------------
    def _retry(self, fn: Callable[[], Any], max_attempts: int = 4) -> Any:
        delay = 0.8
        last_exc: Optional[Exception] = None
        for attempt in range(1, max_attempts + 1):
            try:
                return fn()
            except HttpError as exc:
                last_exc = exc
                status_code = getattr(exc.resp, "status", None)
                if status_code not in TRANSIENT_HTTP_CODES or attempt == max_attempts:
                    raise
                logger.warning("Transient Google Drive HttpError %s (attempt %d/%d); retrying...", status_code, attempt, max_attempts)
            except (TimeoutError, ConnectionError, OSError) as exc:
                last_exc = exc
                if attempt == max_attempts:
                    raise
                logger.warning("Transient Google Drive network error %s (attempt %d/%d); retrying...", exc, attempt, max_attempts)
            time.sleep(delay + random.uniform(0.0, 0.25))
            delay *= 2.0
        if last_exc:
            raise last_exc
        raise RuntimeError("Unexpected retry exit.")
