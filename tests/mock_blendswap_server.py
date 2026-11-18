"""Local HTTP server simulating the official BlendSwap v1 REST API for safe offline testing."""

from __future__ import annotations

import json
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


def make_minimal_png_bytes() -> bytes:
    # Valid 1x1 RGB PNG bytes
    return (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0"
        b"\x00\x00\x03\x01\x01\x00\xc9\xfe\x92\xef\x00\x00\x00\x00IEND\xaeB`\x82"
    )


def make_sample_blend_bytes(label: str = "SAMPLE_BLEND") -> bytes:
    """Create deterministic binary payload (or use real sample .blend if available)."""
    return f"BLENDER-v305{label}".encode("utf-8") + b"\x00" * 512


def make_sample_zip_bytes(inner_filename: str, inner_bytes: bytes) -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(inner_filename, inner_bytes)
    return buf.getvalue()


DEFAULT_LICENSES = [
    {"key": "cc0", "name": "CC0", "url": "https://creativecommons.org/publicdomain/zero/1.0/"},
    {"key": "cc_by", "name": "CC-BY", "url": "https://creativecommons.org/licenses/by/4.0/"},
    {"key": "cc_by_sa", "name": "CC-BY-SA", "url": "https://creativecommons.org/licenses/by-sa/4.0/"},
    {"key": "cc_by_nd", "name": "CC-BY-ND", "url": "https://creativecommons.org/licenses/by-nd/4.0/"},
    {"key": "cc_by_nc", "name": "CC-BY-NC", "url": "https://creativecommons.org/licenses/by-nc/4.0/"},
    {"key": "cc_by_nc_sa", "name": "CC-BY-NC-SA", "url": "https://creativecommons.org/licenses/by-nc-sa/4.0/"},
    {"key": "cc_by_nc_nd", "name": "CC-BY-NC-ND", "url": "https://creativecommons.org/licenses/by-nc-nd/4.0/"},
    {"key": "gal", "name": "GAL", "url": "https://blendswap.com/terms/gal"},
    {"key": "commercial", "name": "BlendSwap Commercial", "url": "https://blendswap.com/terms/commercial"},
]

DEFAULT_CATEGORIES = [
    {"id": 1, "name": "Architecture", "slug": "architecture"},
    {"id": 2, "name": "Animals", "slug": "animals"},
    {"id": 3, "name": "Characters", "slug": "characters"},
    {"id": 4, "name": "Vehicles", "slug": "vehicles"},
    {"id": 7, "name": "Furniture", "slug": "furniture"},
    {"id": 8, "name": "Electronics", "slug": "electronics"},
    {"id": 9, "name": "Plants", "slug": "plants"},
    {"id": 10, "name": "Weapons", "slug": "weapons"},
    {"id": 11, "name": "Props", "slug": "props"},
    {"id": 12, "name": "Environments", "slug": "environments"},
    {"id": 13, "name": "Mechanical", "slug": "mechanical"},
]


def default_catalog_assets() -> list[dict[str, Any]]:
    specs = [
        (1001, "Modern Ergonomic Chair", "furniture", "Furniture", "cc0", "CC0", 15400, 320, 42, "4.2", "blender", "chair.blend", ["chair", "furniture", "office", "hard surface"]),
        (1002, "Stylized Knight Character", "characters", "Characters", "cc_by", "CC-BY", 12200, 410, 55, "4.2", "blender", "knight.blend", ["character", "rigged", "armature", "mid-poly"]),
        (1003, "Classic Sports Coupe", "vehicles", "Vehicles", "cc_by_sa", "CC-BY-SA", 9800, 290, 28, "4.1", "blender", "coupe.blend", ["car", "vehicle", "hard-surface", "high-poly"]),
        (1004, "Gothic Cathedral Archway", "architecture", "Architecture", "cc0", "CC0", 8600, 180, 19, "4.0", "blender", "arch.blend", ["architecture", "modular", "stone"]),
        (1005, "Industrial Robotic Arm", "mechanical", "Mechanical", "cc_by", "CC-BY", 7500, 215, 24, "4.3", "blender", "robot_arm.blend", ["mechanical", "rigged", "hard-surface"]),
        (1006, "Forest Pine Tree Pack", "plants", "Plants", "cc0", "CC0", 6900, 175, 16, "4.2", "blender", "pine_tree.blend", ["tree", "organic", "nature", "foliage"]),
        (1007, "Vintage Studio Camera", "electronics", "Electronics", "cc_by_nc", "CC-BY-NC", 6200, 160, 14, "3.6", "blender", "camera.blend", ["camera", "electronics", "photorealistic"]),
        (1008, "Articulated Red Fox", "animals", "Animals", "gal", "GAL", 5400, 140, 12, "4.2", "blender", "fox.blend", ["animal", "organic", "sculpted", "rigged"]),
        (1009, "Medieval Broadsword", "weapons", "Weapons", "cc0", "CC0", 4900, 110, 9, "4.2", "blender", "sword.blend", ["weapon", "game-ready", "low-poly"]),
        (1010, "Tavern Wooden Barrel", "props", "Props", "cc_by", "CC-BY", 4200, 95, 8, "4.2", "blender", "barrel.blend", ["prop", "wood", "game-ready"]),
        (1011, "Alpine Valley Terrain", "environments", "Environments", "cc_by_sa", "CC-BY-SA", 3800, 88, 7, "4.2", "blender", "valley.blend", ["environment", "terrain", "procedural"]),
        (1012, "Exported OBJ Crate", "props", "Props", "cc0", "CC0", 3100, 40, 3, "4.0", "obj", "crate.obj", ["crate", "prop", "low-poly"]),
    ]
    out: list[dict[str, Any]] = []
    for aid, title, cslug, cname, lkey, lname, dls, likes, cmts, bver, fmt, fname, tags in specs:
        blob = make_sample_blend_bytes(f"{aid}_{fname}")
        out.append(
            {
                "id": aid,
                "title": title,
                "url": f"https://blendswap.com/blend/{aid}",
                "description": f"High-detail reference asset: {title} ({cname}, {fmt}).",
                "asset_type": "model_3d",
                "asset_type_label": "3D Model",
                "license": {
                    "key": lkey,
                    "name": lname,
                    "url": next((l["url"] for l in DEFAULT_LICENSES if l["key"] == lkey), ""),
                    "license_source": "uploader_declared",
                },
                "license_source": "uploader_declared",
                "tags": tags,
                "category": {"id": aid % 10 + 1, "name": cname, "slug": cslug},
                "author": {"id": 500 + (aid % 20), "username": f"creator_{aid % 20}", "url": f"https://blendswap.com/profile/{500 + (aid % 20)}"},
                "counts": {"downloads": dls, "likes": likes, "comments": cmts},
                "explicit": False,
                "blender_versions": [bver],
                "size_bytes": len(blob),
                "created_at": "2026-06-01T12:00:00Z",
                "files": [
                    {
                        "id": aid * 10 + 1,
                        "format": fmt,
                        "format_label": fmt.upper(),
                        "size_bytes": len(blob),
                        "software_version": bver,
                        "render_engine": "cycles",
                        "render_engine_label": "Cycles",
                        "render_engine_version": None,
                        "filename": fname,
                        "position": 0,
                    }
                ],
                "_blob": blob,
            }
        )
    return out


def build_mock_blendswap_server(
    *,
    valid_key: str = "bsk_test_valid_key",
    free_downloads_remaining: int = 7,
    credits: int = 50,
    assets: list[dict[str, Any]] | None = None,
    inject_429_once: bool = False,
    expire_first_download_url: bool = False,
):
    """Spin up a local HTTP server mimicking BlendSwap /api/v1 endpoints & signed file CDN."""
    catalog = assets if assets is not None else default_catalog_assets()
    state: dict[str, Any] = {
        "valid_key": valid_key,
        "free_remaining": free_downloads_remaining,
        "free_limit": 7,
        "credits": credits,
        "download_cost": 10,
        "assets": {a["id"]: dict(a) for a in catalog},
        "inject_429_once": inject_429_once,
        "expire_first_download_url": expire_first_download_url,
        "download_calls": [],
        "recent_repeats": set(),  # (asset_id, file_id)
        "tokens": {},  # token -> {asset_id, file_id, expired}
        "rate_remaining": 999,
    }
    lock = threading.Lock()
    png_bytes = make_minimal_png_bytes()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return

        def _send_json(self, code: int, payload: Any, extra_headers: dict[str, str] | None = None) -> None:
            raw = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("X-RateLimit-Limit", "1000")
            self.send_header("X-RateLimit-Remaining", str(state["rate_remaining"]))
            self.send_header("X-RateLimit-Reset", str(int(time.time()) + 3600))
            if extra_headers:
                for k, v in extra_headers.items():
                    self.send_header(k, v)
            self.end_headers()
            self.wfile.write(raw)

        def _check_auth(self) -> bool:
            auth = self.headers.get("Authorization", "")
            if auth != f"Bearer {state['valid_key']}":
                self._send_json(401, {"error": "unauthorized", "message": "Invalid or missing bearer key."})
                return False
            with lock:
                state["rate_remaining"] = max(0, state["rate_remaining"] - 1)
                if state["inject_429_once"]:
                    state["inject_429_once"] = False
                    self._send_json(
                        429,
                        {"error": "rate_limited", "message": "Rate limit hit."},
                        extra_headers={"Retry-After": "0"},
                    )
                    return False
            return True

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)

            # Signed binary / preview URLs (no auth required like real CDN)
            if parsed.path.startswith("/cdn/file/"):
                token = parsed.path.split("/")[-1]
                with lock:
                    meta = state["tokens"].get(token)
                if not meta or meta.get("expired"):
                    self._send_json(410, {"error": "url_expired", "message": "Signed URL expired."})
                    return
                asset = state["assets"][meta["asset_id"]]
                blob = asset["_blob"]
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(blob)))
                self.end_headers()
                self.wfile.write(blob)
                return

            if parsed.path.startswith("/cdn/preview/"):
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(png_bytes)))
                self.end_headers()
                self.wfile.write(png_bytes)
                return

            if not self._check_auth():
                return

            if parsed.path == "/api/v1/me":
                with lock:
                    self._send_json(
                        200,
                        {
                            "data": {
                                "username": "test_collector",
                                "plan": "free",
                                "credits": state["credits"],
                                "free_downloads_remaining": state["free_remaining"],
                                "free_downloads_limit": state["free_limit"],
                                "download_credit_cost": state["download_cost"],
                                "rate_limit": {"limit": 1000, "remaining": state["rate_remaining"], "reset": int(time.time()) + 3600},
                            }
                        },
                    )
                return

            if parsed.path == "/api/v1/licenses":
                self._send_json(200, {"data": DEFAULT_LICENSES})
                return

            if parsed.path == "/api/v1/categories":
                self._send_json(200, {"data": DEFAULT_CATEGORIES})
                return

            if parsed.path == "/api/v1/assets":
                with lock:
                    items = [
                        {k: v for k, v in a.items() if k != "_blob"}
                        for a in state["assets"].values()
                    ]
                q_str = (qs.get("q") or [""])[0].strip().lower()
                if q_str:
                    items = [a for a in items if q_str in (a["title"] + " " + a["description"] + " " + " ".join(a["tags"])).lower()]
                cat_str = (qs.get("category") or [""])[0].strip().lower()
                if cat_str:
                    items = [a for a in items if a["category"]["slug"].lower() == cat_str]
                lic_str = (qs.get("license") or [""])[0].strip().lower()
                if lic_str:
                    allowed_lics = {x.strip() for x in lic_str.split(",") if x.strip()}
                    items = [a for a in items if a["license"]["key"].lower() in allowed_lics]
                fmt_str = (qs.get("format") or [""])[0].strip().lower()
                if fmt_str:
                    allowed_fmts = {x.strip() for x in fmt_str.split(",") if x.strip()}
                    items = [a for a in items if any(f["format"].lower() in allowed_fmts for f in a["files"])]
                sort = (qs.get("sort") or ["newest"])[0]
                if sort == "downloads":
                    items.sort(key=lambda a: a["counts"]["downloads"], reverse=True)
                elif sort == "likes":
                    items.sort(key=lambda a: a["counts"]["likes"], reverse=True)

                page = max(1, int((qs.get("page") or ["1"])[0]))
                per_page = max(1, min(100, int((qs.get("per_page") or ["25"])[0])))
                start = (page - 1) * per_page
                paged = items[start : start + per_page]
                pages = max(1, (len(items) + per_page - 1) // per_page)
                self._send_json(
                    200,
                    {
                        "data": paged,
                        "pagination": {"page": page, "pages": pages, "count": len(items), "per_page": per_page},
                    },
                )
                return

            if parsed.path.startswith("/api/v1/assets/"):
                parts = [p for p in parsed.path.split("/") if p]
                # api, v1, assets, {id} [, files]
                try:
                    aid = int(parts[3])
                except (IndexError, ValueError):
                    self._send_json(404, {"error": "not_found", "message": "Invalid asset id."})
                    return
                with lock:
                    asset = state["assets"].get(aid)
                if not asset:
                    self._send_json(404, {"error": "not_found", "message": f"Asset {aid} not found."})
                    return
                if len(parts) == 5 and parts[4] == "files":
                    self._send_json(200, {"data": asset["files"]})
                    return
                self._send_json(200, {"data": {k: v for k, v in asset.items() if k != "_blob"}})
                return

            self._send_json(404, {"error": "not_found", "message": f"Unknown GET {parsed.path}"})

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if not self._check_auth():
                return
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length).decode("utf-8")) if length > 0 else {}

            if parsed.path == "/api/v1/downloads":
                aid = int(body.get("asset_id") or 0)
                with lock:
                    asset = state["assets"].get(aid)
                    if not asset:
                        self._send_json(404, {"error": "not_found", "message": "Asset not found."})
                        return
                    files = asset["files"]
                    req_fid = body.get("file_id")
                    finfo = next((f for f in files if f["id"] == req_fid), files[0]) if files else None
                    if not finfo:
                        self._send_json(404, {"error": "not_found", "message": "File not found."})
                        return
                    repeat_key = (aid, finfo["id"])
                    if repeat_key in state["recent_repeats"]:
                        reason = "repeat"
                        charged = False
                        credits_spent = 0
                    elif state["free_remaining"] > 0:
                        state["free_remaining"] -= 1
                        state["recent_repeats"].add(repeat_key)
                        reason = "free_allowance"
                        charged = False
                        credits_spent = 0
                    elif state["credits"] >= state["download_cost"]:
                        state["credits"] -= state["download_cost"]
                        state["recent_repeats"].add(repeat_key)
                        reason = "paid"
                        charged = True
                        credits_spent = state["download_cost"]
                    else:
                        self._send_json(
                            402,
                            {
                                "error": "insufficient_credits",
                                "message": "Insufficient credits.",
                                "credits": state["credits"],
                                "required": state["download_cost"],
                            },
                        )
                        return

                    tok = f"tok_{aid}_{len(state['tokens']) + 1}"
                    expired_flag = bool(state["expire_first_download_url"])
                    if state["expire_first_download_url"]:
                        state["expire_first_download_url"] = False
                    state["tokens"][tok] = {"asset_id": aid, "file_id": finfo["id"], "expired": expired_flag}
                    state["download_calls"].append({"asset_id": aid, "file_id": finfo["id"], "reason": reason, "charged": charged})
                    host = self.headers.get("Host")
                    self._send_json(
                        200,
                        {
                            "download_url": f"http://{host}/cdn/file/{tok}",
                            "expires_at": "2099-01-01T00:00:00Z",
                            "charged": charged,
                            "credits_spent": credits_spent,
                            "credits_remaining": state["credits"],
                            "reason": reason,
                            "asset": {k: v for k, v in asset.items() if k != "_blob"},
                            "file": finfo,
                            "preview_urls": [f"http://{host}/cdn/preview/{aid}.png"],
                            "license": asset["license"],
                            "license_source": "uploader_declared",
                        },
                    )
                return

            self._send_json(404, {"error": "not_found", "message": f"Unknown POST {parsed.path}"})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server, f"http://127.0.0.1:{port}", state
