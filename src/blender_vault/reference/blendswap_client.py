"""Official BlendSwap API v1 REST client with strict allowance protection, rate-limit tracking, and download verification."""

from __future__ import annotations

import logging
import random
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

from ..config import Settings
from ..hashing import compute_md5, compute_sha256
from .models import (
    BlendSwapAccountStatus,
    BlendSwapAssetSummary,
    BlendSwapAuthor,
    BlendSwapCategoryInfo,
    BlendSwapCounts,
    BlendSwapDownloadTicket,
    BlendSwapFileInfo,
    BlendSwapLicenseInfo,
)
from .ref_db import ReferenceDatabase

logger = logging.getLogger("blender_vault.reference.blendswap")


class BlendSwapAPIError(RuntimeError):
    """Base error for BlendSwap API failures."""

    def __init__(self, message: str, *, status_code: Optional[int] = None, error_key: Optional[str] = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_key = error_key


class BlendSwapAuthError(BlendSwapAPIError):
    """Raised when BLENDSWAP_KEY is missing, malformed, or revoked (401)."""


class BlendSwapCreditError(BlendSwapAPIError):
    """Raised when BlendSwap rejects a download due to insufficient credits (402)."""


class BlendSwapAllowanceExhaustedError(BlendSwapAPIError):
    """Raised proactively before spending credits when daily free allowance is 0."""


class BlendSwapNotFoundError(BlendSwapAPIError):
    """Raised when an asset, file, or endpoint is not found (404)."""


class BlendSwapValidationError(BlendSwapAPIError):
    """Raised on invalid search/request parameters (422)."""


class BlendSwapRateLimitError(BlendSwapAPIError):
    """Raised when rate limits persist across retries (429)."""


class ExpiredDownloadURLError(BlendSwapAPIError):
    """Raised when a signed download URL has expired or returns 403/404/410."""


def _parse_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _extract_first_int(data: dict[str, Any], keys: tuple[str, ...], default: int = 0) -> int:
    for key in keys:
        if key in data and data[key] is not None:
            parsed = _parse_int(data[key])
            if parsed is not None:
                return parsed
    return default


def _origin_tuple(raw_url: str) -> Optional[tuple[str, str, int]]:
    """Return normalized (scheme, hostname, port) for origin comparison, rejecting embedded userinfo."""
    try:
        parsed = urlparse(raw_url)
    except ValueError:
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    scheme = (parsed.scheme or "").lower()
    host = (parsed.hostname or "").lower()
    if scheme not in {"http", "https"} or not host:
        return None
    port = parsed.port
    if port is None:
        port = 443 if scheme == "https" else 80
    return (scheme, host, port)


def _is_same_origin(url_a: str, url_b: str) -> bool:
    """Return True only when two URLs share the exact scheme, hostname, and port."""
    origin_a = _origin_tuple(url_a)
    origin_b = _origin_tuple(url_b)
    return origin_a is not None and origin_a == origin_b


def is_url_expired(expires_at: Optional[str], *, skew_seconds: int = 30) -> bool:
    """Return True if ISO timestamp expires_at is in the past (with safety skew)."""
    if not expires_at:
        return False
    try:
        cleaned = expires_at.strip()
        if cleaned.endswith("Z"):
            cleaned = cleaned[:-1] + "+00:00"
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return (dt - now).total_seconds() <= skew_seconds
    except ValueError:
        return False


class BlendSwapClient:
    """HTTP client for https://blendswap.com/api/v1 using Bearer token authentication."""

    TRANSIENT_CODES = {408, 429, 500, 502, 503, 504}

    def __init__(
        self,
        settings: Settings,
        ref_db: Optional[ReferenceDatabase] = None,
        *,
        http_client: Optional[httpx.Client] = None,
        max_retry_sleep: float = 15.0,
    ) -> None:
        self.settings = settings
        self.ref_db = ref_db
        self.base_url = settings.blendswap_base_url.rstrip("/")
        self._owns_client = http_client is None
        is_loopback = (urlparse(self.base_url).hostname or "").lower() in {"127.0.0.1", "localhost", "::1"}
        self._client = http_client or httpx.Client(
            timeout=settings.blendswap_timeout_seconds,
            follow_redirects=True,
            trust_env=not is_loopback,
        )
        self.max_retry_sleep = max_retry_sleep
        self.last_rate_limit: dict[str, Optional[int]] = {"limit": None, "remaining": None, "reset": None}
        self._download_request_lock = threading.Lock()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _require_key(self) -> str:
        key = (self.settings.blendswap_key or "").strip()
        if not key:
            raise BlendSwapAuthError(
                "BLENDSWAP_KEY is not configured. Set BLENDSWAP_KEY in .env or environment variables.",
                status_code=401,
                error_key="missing_api_key",
            )
        return key

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._require_key()}",
            "Accept": "application/json",
            "User-Agent": "BlenderAssetVault-ReferenceEngine/2.0",
        }

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict[str, Any]] = None,
        json_body: Optional[dict[str, Any]] = None,
        max_attempts: int = 4,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}" if path.startswith("/") else f"{self.base_url}/{path}"
        cleaned_params = {k: v for k, v in (params or {}).items() if v is not None and v != ""}
        delay = 0.75

        for attempt in range(1, max_attempts + 1):
            t0 = time.perf_counter()
            status_code: Optional[int] = None
            err_key: Optional[str] = None
            try:
                resp = self._client.request(
                    method=method.upper(),
                    url=url,
                    headers=self._headers(),
                    params=cleaned_params or None,
                    json=json_body,
                )
                status_code = resp.status_code
                duration_ms = (time.perf_counter() - t0) * 1000.0

                rl_limit = _parse_int(resp.headers.get("X-RateLimit-Limit"))
                rl_remaining = _parse_int(resp.headers.get("X-RateLimit-Remaining"))
                rl_reset = _parse_int(resp.headers.get("X-RateLimit-Reset"))
                if rl_limit is not None or rl_remaining is not None:
                    self.last_rate_limit = {"limit": rl_limit, "remaining": rl_remaining, "reset": rl_reset}

                payload: dict[str, Any] = {}
                if resp.content:
                    try:
                        parsed = resp.json()
                        if isinstance(parsed, dict):
                            payload = parsed
                    except ValueError:
                        payload = {}

                if status_code >= 400:
                    err_key = str(payload.get("error") or f"http_{status_code}")
                    err_msg = str(payload.get("message") or resp.text[:300] or f"HTTP {status_code}")

                    if self.ref_db:
                        self.ref_db.log_api_call(
                            method=method,
                            endpoint=path,
                            status_code=status_code,
                            rate_limit_limit=rl_limit,
                            rate_limit_remaining=rl_remaining,
                            rate_limit_reset=rl_reset,
                            duration_ms=duration_ms,
                            error_key=err_key,
                        )

                    if status_code in self.TRANSIENT_CODES and attempt < max_attempts:
                        retry_after = _parse_int(resp.headers.get("Retry-After"))
                        sleep_s = float(retry_after) if retry_after is not None else delay + random.uniform(0.0, 0.25)
                        sleep_s = min(sleep_s, self.max_retry_sleep)
                        logger.warning(
                            "BlendSwap API %s %s returned %s (%s); retrying in %.2fs (%d/%d).",
                            method,
                            path,
                            status_code,
                            err_key,
                            sleep_s,
                            attempt,
                            max_attempts,
                        )
                        time.sleep(sleep_s)
                        delay *= 2.0
                        continue

                    self._raise_mapped_error(status_code, err_key, err_msg)

                if self.ref_db:
                    self.ref_db.log_api_call(
                        method=method,
                        endpoint=path,
                        status_code=status_code,
                        rate_limit_limit=rl_limit,
                        rate_limit_remaining=rl_remaining,
                        rate_limit_reset=rl_reset,
                        duration_ms=duration_ms,
                    )
                return payload
            except httpx.TransportError as exc:
                duration_ms = (time.perf_counter() - t0) * 1000.0
                if self.ref_db:
                    self.ref_db.log_api_call(
                        method=method,
                        endpoint=path,
                        status_code=None,
                        duration_ms=duration_ms,
                        error_key=type(exc).__name__,
                    )
                if attempt == max_attempts:
                    raise BlendSwapAPIError(f"Network error calling BlendSwap {path}: {exc}") from exc
                sleep_s = min(delay + random.uniform(0.0, 0.25), self.max_retry_sleep)
                logger.warning("Network error on BlendSwap %s: %s; retrying in %.2fs.", path, exc, sleep_s)
                time.sleep(sleep_s)
                delay *= 2.0

        raise BlendSwapAPIError(f"BlendSwap request {method} {path} exhausted retries.")

    @staticmethod
    def _raise_mapped_error(status_code: int, err_key: str, err_msg: str) -> None:
        if status_code == 401:
            raise BlendSwapAuthError(f"BlendSwap authentication failed: {err_msg}", status_code=401, error_key=err_key)
        if status_code == 402:
            raise BlendSwapCreditError(f"Insufficient BlendSwap credits: {err_msg}", status_code=402, error_key=err_key)
        if status_code == 404:
            raise BlendSwapNotFoundError(f"BlendSwap resource not found: {err_msg}", status_code=404, error_key=err_key)
        if status_code == 422:
            raise BlendSwapValidationError(f"Invalid BlendSwap request: {err_msg}", status_code=422, error_key=err_key)
        if status_code == 429:
            raise BlendSwapRateLimitError(f"BlendSwap rate limited: {err_msg}", status_code=429, error_key=err_key)
        raise BlendSwapAPIError(f"BlendSwap API error ({status_code} {err_key}): {err_msg}", status_code=status_code, error_key=err_key)

    # ------------------------------------------------------------------
    # Official Endpoints
    # ------------------------------------------------------------------
    def get_me(self) -> BlendSwapAccountStatus:
        """GET /me — account key info, credit balance, and remaining free downloads today."""
        payload = self._request_json("GET", "/me")
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        rate = data.get("rate_limit") if isinstance(data.get("rate_limit"), dict) else {}

        free_remaining = _extract_first_int(
            data,
            ("free_downloads_remaining", "free_downloads_left", "daily_free_remaining", "remaining_free_downloads"),
            default=0,
        )
        free_limit = _extract_first_int(
            data,
            ("free_downloads_limit", "daily_free_limit", "daily_allowance", "free_limit"),
            default=7,
        )
        credits_bal = _extract_first_int(data, ("credits", "credit_balance", "credits_remaining", "balance"), default=0)
        dl_cost = _extract_first_int(data, ("download_credit_cost", "credit_cost", "credits_per_download"), default=10)

        return BlendSwapAccountStatus(
            connected=True,
            username=data.get("username") or (data.get("user") or {}).get("username"),
            plan=data.get("plan") or data.get("tier"),
            credits=credits_bal,
            free_downloads_remaining=max(0, free_remaining),
            free_downloads_limit=max(1, free_limit),
            download_credit_cost=max(1, dl_cost),
            rate_limit_limit=_parse_int(rate.get("limit")) or self.last_rate_limit.get("limit"),
            rate_limit_remaining=_parse_int(rate.get("remaining")) or self.last_rate_limit.get("remaining"),
            rate_limit_reset=_parse_int(rate.get("reset")) or self.last_rate_limit.get("reset"),
            raw=data,
        )

    def check_connectivity(self) -> dict[str, Any]:
        """Verify key authentication, API reachability, and current allowance/credit status."""
        if not (self.settings.blendswap_key or "").strip():
            return {
                "configured": False,
                "connected": False,
                "error": "BLENDSWAP_KEY is not set.",
                "account": None,
            }
        try:
            acct = self.get_me()
            return {
                "configured": True,
                "connected": True,
                "error": None,
                "account": acct.model_dump(),
            }
        except BlendSwapAPIError as exc:
            return {
                "configured": True,
                "connected": False,
                "error": str(exc),
                "status_code": exc.status_code,
                "account": None,
            }

    def get_licenses(self) -> list[dict[str, Any]]:
        """GET /licenses — retrieve supported BlendSwap license keys and URLs."""
        payload = self._request_json("GET", "/licenses")
        items = payload.get("data") if isinstance(payload.get("data"), list) else []
        if self.ref_db and items:
            self.ref_db.upsert_licenses_cache(items)
        return items

    def get_categories(self) -> list[dict[str, Any]]:
        """GET /categories — retrieve 3D model subject categories with slugs."""
        payload = self._request_json("GET", "/categories")
        items = payload.get("data") if isinstance(payload.get("data"), list) else []
        if self.ref_db and items:
            self.ref_db.upsert_categories_cache(items)
        return items

    def search_assets(
        self,
        *,
        q: Optional[str] = None,
        author: Optional[str] = None,
        license: Optional[str] = None,
        asset_type: Optional[str] = "model_3d",
        file_format: Optional[str] = None,
        category: Optional[str] = None,
        tag: Optional[str] = None,
        blender_version: Optional[str] = None,
        render_engine: Optional[str] = None,
        sort: str = "downloads",
        page: int = 1,
        per_page: int = 25,
    ) -> tuple[list[BlendSwapAssetSummary], dict[str, Any]]:
        """GET /assets — search and filter BlendSwap catalog."""
        params = {
            "q": q,
            "author": author,
            "license": license,
            "type": asset_type,
            "format": file_format,
            "category": category,
            "tag": tag,
            "blender_version": blender_version,
            "render_engine": render_engine,
            "sort": sort,
            "page": page,
            "per_page": min(100, max(1, per_page)),
        }
        payload = self._request_json("GET", "/assets", params=params)
        raw_items = payload.get("data") if isinstance(payload.get("data"), list) else []
        pagination = payload.get("pagination") if isinstance(payload.get("pagination"), dict) else {}
        assets = [self._parse_asset_summary(item) for item in raw_items]
        return assets, pagination

    def get_asset(self, asset_id: int) -> BlendSwapAssetSummary:
        """GET /assets/{id} — full metadata and file list for one asset."""
        payload = self._request_json("GET", f"/assets/{asset_id}")
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        return self._parse_asset_summary(data)

    def get_asset_files(self, asset_id: int) -> list[BlendSwapFileInfo]:
        """GET /assets/{id}/files — live downloadable files for one asset."""
        payload = self._request_json("GET", f"/assets/{asset_id}/files")
        raw_files = payload.get("data") if isinstance(payload.get("data"), list) else []
        return [self._parse_file_info(f) for f in raw_files]

    def request_download(
        self,
        asset_id: int,
        file_id: Optional[int] = None,
        *,
        allow_credit_spend: Optional[bool] = None,
        skip_allowance_precheck: bool = False,
    ) -> BlendSwapDownloadTicket:
        """POST /downloads — request a signed download URL (strictly guards against spending credits)."""
        credit_spend_allowed = (
            self.settings.allow_credit_spend if allow_credit_spend is None else allow_credit_spend
        )

        # Serialize allowance precheck and ticket creation within this process.
        with self._download_request_lock:
            is_free_repeat = bool(self.ref_db and self.ref_db.has_free_repeat_window(asset_id, file_id))
            if not credit_spend_allowed and not is_free_repeat and not skip_allowance_precheck:
                acct = self.get_me()
                if acct.free_downloads_remaining <= 0:
                    raise BlendSwapAllowanceExhaustedError(
                        "Daily free BlendSwap download allowance is exhausted (0 remaining) and automatic credit spending is disabled.",
                        status_code=402,
                        error_key="free_allowance_exhausted",
                    )

            body: dict[str, Any] = {"asset_id": int(asset_id)}
            if file_id is not None:
                body["file_id"] = int(file_id)

            payload = self._request_json("POST", "/downloads", json_body=body)
            data = (
                payload["data"]
                if isinstance(payload.get("data"), dict) and "download_url" not in payload
                else payload
            )

            lic_raw = data.get("license")
            lic_info = self._parse_license_info(lic_raw, data.get("license_source"))
            ticket = BlendSwapDownloadTicket(
                download_url=str(data.get("download_url") or ""),
                expires_at=data.get("expires_at"),
                charged=bool(data.get("charged", False)),
                credits_spent=int(data.get("credits_spent") or 0),
                credits_remaining=_parse_int(data.get("credits_remaining")),
                reason=str(data.get("reason") or "free_allowance"),
                preview_urls=[str(u) for u in (data.get("preview_urls") or []) if u],
                asset=data.get("asset") if isinstance(data.get("asset"), dict) else None,
                file=data.get("file") if isinstance(data.get("file"), dict) else None,
                license=lic_info,
                license_source=str(data.get("license_source") or (lic_info.license_source if lic_info else "uploader_declared")),
            )
            if not ticket.download_url:
                raise BlendSwapAPIError("BlendSwap POST /downloads returned an empty download_url.")
            if self.ref_db and not ticket.charged:
                resolved_fid = file_id or _parse_int((ticket.file or {}).get("id"))
                self.ref_db.record_ticket_grant(asset_id, resolved_fid, ticket.reason, ticket.expires_at)
            if ticket.charged and not credit_spend_allowed:
                logger.error(
                    "BlendSwap charged %s credits for asset %s despite credit protection!",
                    ticket.credits_spent,
                    asset_id,
                )
            return ticket

    # ------------------------------------------------------------------
    # Binary Asset & Preview Downloading with Expiry Recovery
    # ------------------------------------------------------------------
    def download_binary(
        self,
        url: str,
        dest_path: Path,
        *,
        expires_at: Optional[str] = None,
        expected_size: Optional[int] = None,
        send_auth_if_same_host: bool = False,
    ) -> tuple[int, str, str]:
        """Stream a binary file to disk safely via .part file and return (size_bytes, sha256, md5)."""
        if is_url_expired(expires_at):
            raise ExpiredDownloadURLError(f"Download URL expired at {expires_at}.", status_code=410, error_key="url_expired")

        dest_path.parent.mkdir(parents=True, exist_ok=True)
        part_path = dest_path.with_suffix(dest_path.suffix + ".part")
        headers: dict[str, str] = {"User-Agent": "BlenderAssetVault-ReferenceEngine/2.0"}
        if send_auth_if_same_host and _is_same_origin(url, self.base_url):
            headers["Authorization"] = f"Bearer {self._require_key()}"

        try:
            with self._client.stream("GET", url, headers=headers) as resp:
                if resp.status_code in (403, 404, 410):
                    raise ExpiredDownloadURLError(
                        f"Download URL unavailable or expired (HTTP {resp.status_code}).",
                        status_code=resp.status_code,
                        error_key="download_url_expired",
                    )
                if resp.status_code >= 400:
                    raise BlendSwapAPIError(
                        f"Failed to download binary from {url}: HTTP {resp.status_code}",
                        status_code=resp.status_code,
                    )
                with part_path.open("wb") as fh:
                    for chunk in resp.iter_bytes(chunk_size=1024 * 256):
                        if chunk:
                            fh.write(chunk)
        except httpx.TransportError as exc:
            part_path.unlink(missing_ok=True)
            raise BlendSwapAPIError(f"Transport failure downloading {url}: {exc}") from exc

        if not part_path.is_file() or part_path.stat().st_size == 0:
            part_path.unlink(missing_ok=True)
            raise BlendSwapAPIError("Downloaded file is empty (0 bytes).")

        actual_size = part_path.stat().st_size
        if expected_size is not None and expected_size > 0 and actual_size != expected_size:
            part_path.unlink(missing_ok=True)
            raise BlendSwapAPIError(
                f"Downloaded size mismatch: expected {expected_size} bytes, got {actual_size} bytes."
            )

        sha256 = compute_sha256(part_path)
        md5 = compute_md5(part_path)
        part_path.replace(dest_path)
        return actual_size, sha256, md5

    def download_with_refresh(
        self,
        *,
        asset_id: int,
        file_id: Optional[int],
        dest_path: Path,
        expected_size: Optional[int] = None,
        existing_ticket: Optional[BlendSwapDownloadTicket] = None,
        allow_credit_spend: Optional[bool] = None,
    ) -> tuple[BlendSwapDownloadTicket, int, str, str]:
        """Download asset binary using an existing ticket or requesting a fresh URL if expired."""
        ticket = existing_ticket
        if ticket is None or is_url_expired(ticket.expires_at):
            ticket = self.request_download(asset_id, file_id, allow_credit_spend=allow_credit_spend)

        try:
            size_b, sha, md5 = self.download_binary(
                ticket.download_url,
                dest_path,
                expires_at=ticket.expires_at,
                expected_size=expected_size,
                send_auth_if_same_host=True,
            )
            return ticket, size_b, sha, md5
        except ExpiredDownloadURLError:
            logger.info("Download URL expired for BlendSwap #%s; refreshing ticket (24h repeat/allowance).", asset_id)
            fresh_ticket = self.request_download(asset_id, file_id, allow_credit_spend=allow_credit_spend)
            size_b, sha, md5 = self.download_binary(
                fresh_ticket.download_url,
                dest_path,
                expires_at=fresh_ticket.expires_at,
                expected_size=expected_size,
                send_auth_if_same_host=True,
            )
            return fresh_ticket, size_b, sha, md5

    def download_previews(self, preview_urls: list[str], previews_dir: Path) -> list[Path]:
        """Download available preview images from ticket.preview_urls safely (non-fatal on single image errors)."""
        saved: list[Path] = []
        previews_dir.mkdir(parents=True, exist_ok=True)
        for idx, url in enumerate(preview_urls):
            ext = ".jpg"
            lower = url.lower().split("?")[0]
            for candidate in (".png", ".webp", ".gif", ".jpeg", ".jpg"):
                if lower.endswith(candidate):
                    ext = candidate
                    break
            dest = previews_dir / f"preview_{idx:02d}{ext}"
            try:
                self.download_binary(url, dest, send_auth_if_same_host=True)
                saved.append(dest)
            except Exception as exc:
                logger.warning("Failed downloading preview %s: %s", url, exc)
        return saved

    # ------------------------------------------------------------------
    # Payload Parsers
    # ------------------------------------------------------------------
    @classmethod
    def _parse_license_info(cls, raw: Any, fallback_source: Optional[str] = None) -> BlendSwapLicenseInfo:
        if isinstance(raw, dict):
            raw_key = str(raw.get("key") or "").strip().lower()
            raw_name = str(raw.get("name") or raw.get("key") or "Unknown")
            return BlendSwapLicenseInfo(
                key=raw_key,
                name=raw_name,
                url=str(raw.get("url") or ""),
                license_source=str(raw.get("license_source") or fallback_source or "uploader_declared"),
            )
        if isinstance(raw, str) and raw.strip():
            return BlendSwapLicenseInfo(
                key=raw.strip().lower().replace("-", "_"),
                name=raw.strip(),
                license_source=fallback_source or "uploader_declared",
            )
        return BlendSwapLicenseInfo(key="", name="Unknown", license_source=fallback_source or "uploader_declared")

    @classmethod
    def _parse_file_info(cls, raw: dict[str, Any]) -> BlendSwapFileInfo:
        return BlendSwapFileInfo(
            id=int(raw["id"]),
            format=str(raw.get("format") or "other").lower(),
            format_label=str(raw.get("format_label") or raw.get("format") or ""),
            size_bytes=int(raw.get("size_bytes") or 0),
            software_version=raw.get("software_version"),
            render_engine=raw.get("render_engine"),
            render_engine_label=raw.get("render_engine_label"),
            render_engine_version=raw.get("render_engine_version"),
            filename=str(raw.get("filename") or f"file_{raw['id']}.blend"),
            position=int(raw.get("position") or 0),
        )

    @classmethod
    def _parse_asset_summary(cls, raw: dict[str, Any]) -> BlendSwapAssetSummary:
        author_raw = raw.get("author") if isinstance(raw.get("author"), dict) else {}
        cat_raw = raw.get("category") if isinstance(raw.get("category"), dict) else {}
        counts_raw = raw.get("counts") if isinstance(raw.get("counts"), dict) else {}
        files_raw = raw.get("files") if isinstance(raw.get("files"), list) else []

        lic_info = cls._parse_license_info(raw.get("license"), raw.get("license_source"))
        return BlendSwapAssetSummary(
            id=int(raw["id"]),
            title=str(raw.get("title") or f"BlendSwap Asset {raw['id']}"),
            url=str(raw.get("url") or f"https://blendswap.com/blend/{raw['id']}"),
            description=str(raw.get("description") or ""),
            asset_type=str(raw.get("asset_type") or "model_3d"),
            asset_type_label=str(raw.get("asset_type_label") or "3D Model"),
            license=lic_info,
            license_source=str(raw.get("license_source") or lic_info.license_source),
            tags=[str(t) for t in (raw.get("tags") or []) if t],
            category=BlendSwapCategoryInfo(
                id=_parse_int(cat_raw.get("id")),
                name=str(cat_raw.get("name") or "Other"),
                slug=str(cat_raw.get("slug") or "other"),
            ),
            author=BlendSwapAuthor(
                id=_parse_int(author_raw.get("id")),
                username=str(author_raw.get("username") or "unknown"),
                url=str(author_raw.get("url") or ""),
            ),
            counts=BlendSwapCounts(
                downloads=int(counts_raw.get("downloads") or 0),
                likes=int(counts_raw.get("likes") or 0),
                comments=int(counts_raw.get("comments") or 0),
            ),
            explicit=bool(raw.get("explicit", False)),
            blender_versions=[str(v) for v in (raw.get("blender_versions") or []) if v],
            size_bytes=int(raw.get("size_bytes") or 0),
            created_at=raw.get("created_at"),
            files=[cls._parse_file_info(f) for f in files_raw if isinstance(f, dict) and "id" in f],
        )
