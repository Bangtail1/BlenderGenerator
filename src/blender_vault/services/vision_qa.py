"""Visual QA processing: render sanity checks, contact sheet, turntable assembly, and multimodal Vision QA."""

from __future__ import annotations

import base64
import json
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Optional

import httpx
from PIL import Image, ImageDraw, ImageStat

from ..config import Settings
from ..models import VisualQAReport, VisualQAStatus, utc_now_iso

logger = logging.getLogger("blender_vault.vision_qa")

REQUIRED_INSPECTION_VIEWS: tuple[str, ...] = (
    "front",
    "back",
    "left",
    "right",
    "top",
    "three_quarter",
    "closeup",
    "wireframe",
    "material_beauty",
)

VIEW_LABELS: dict[str, str] = {
    "three_quarter": "3/4 HERO",
    "front": "FRONT",
    "back": "BACK",
    "left": "LEFT",
    "right": "RIGHT",
    "top": "TOP",
    "closeup": "CLOSE-UP",
    "wireframe": "WIREFRAME",
    "material_beauty": "MATERIAL BEAUTY",
}


def verify_render_sanity(render_paths: dict[str, str]) -> tuple[bool, dict[str, Any]]:
    """Verify rendered inspection PNGs exist, are readable, and contain non-blank visual detail."""
    details: dict[str, Any] = {}
    all_ok = True
    missing_views: list[str] = []

    for view_key in REQUIRED_INSPECTION_VIEWS:
        p_str = render_paths.get(view_key)
        if not p_str:
            missing_views.append(view_key)
            all_ok = False
            continue
        p = Path(p_str)
        if not p.is_file() or p.stat().st_size == 0:
            missing_views.append(view_key)
            all_ok = False
            continue
        try:
            with Image.open(p) as im:
                rgb = im.convert("RGB")
                stat = ImageStat.Stat(rgb)
                mean_std = sum(stat.stddev) / 3.0
                mean_luma = sum(stat.mean) / 3.0
                non_flat = mean_std >= 2.0
                if not non_flat:
                    all_ok = False
                details[view_key] = {
                    "size": list(im.size),
                    "file_bytes": p.stat().st_size,
                    "mean_luma": round(mean_luma, 2),
                    "contrast_stddev": round(mean_std, 2),
                    "non_blank": non_flat,
                }
        except Exception as exc:
            all_ok = False
            details[view_key] = {"error": str(exc), "non_blank": False}

    if missing_views:
        details["missing_views"] = missing_views
    return all_ok, details


def build_contact_sheet(render_paths: dict[str, str], out_path: Path) -> Optional[Path]:
    """Build a labeled 3x3 contact sheet from the 9 multi-angle inspection renders."""
    ordered_keys = [
        "three_quarter",
        "front",
        "back",
        "left",
        "right",
        "top",
        "closeup",
        "wireframe",
        "material_beauty",
    ]
    tiles: list[tuple[str, Image.Image]] = []
    for k in ordered_keys:
        p_str = render_paths.get(k)
        if p_str and Path(p_str).is_file():
            with Image.open(p_str) as im:
                tiles.append((VIEW_LABELS.get(k, k.upper()), im.convert("RGB").resize((420, 420), Image.Resampling.LANCZOS)))

    if not tiles:
        return None

    cols, rows = 3, 3
    tile_w, tile_h = 420, 420
    pad = 12
    sheet_w = cols * tile_w + (cols + 1) * pad
    sheet_h = rows * tile_h + (rows + 1) * pad
    sheet = Image.new("RGB", (sheet_w, sheet_h), (24, 26, 31))
    draw = ImageDraw.Draw(sheet)

    for idx, (label, tile) in enumerate(tiles[:9]):
        r = idx // cols
        c = idx % cols
        x = pad + c * (tile_w + pad)
        y = pad + r * (tile_h + pad)
        sheet.paste(tile, (x, y))
        draw.rectangle([x + 8, y + 8, x + 150, y + 32], fill=(15, 17, 22))
        draw.text((x + 14, y + 14), label, fill=(245, 175, 85))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_path, format="PNG")
    return out_path


def assemble_turntable_webp(frame_paths: list[str], out_path: Path, duration_ms: int = 120) -> Optional[Path]:
    """Stitch rendered turntable PNG frames into an animated WebP loop."""
    frames: list[Image.Image] = []
    for fp in frame_paths:
        p = Path(fp)
        if p.is_file():
            with Image.open(p) as im:
                frames.append(im.convert("RGBA").copy())
    if not frames:
        return None

    out_path.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(
        out_path,
        format="WEBP",
        save_all=True,
        append_images=frames[1:],
        duration=duration_ms,
        loop=0,
        quality=82,
    )
    return out_path


def _build_vision_prompt(asset_name: str, description: str, category: str) -> str:
    return (
        "You are a senior 3D technical artist reviewing multi-angle Blender inspection renders of a 3D asset.\n"
        f"Asset Name: {asset_name}\n"
        f"Category: {category}\n"
        f"Intended Description: {description or asset_name}\n\n"
        "You are inspecting multiple views (3/4 perspective, front, back, left, right, top, close-up, wireframe, and material beauty).\n"
        "Evaluate carefully and critically:\n"
        "1. Does the object visually match its intended description?\n"
        "2. Is the overall shape and silhouette recognizable and complete from all angles?\n"
        "3. Are all important components present and properly connected?\n"
        "4. Are proportions believable?\n"
        "5. Are there unwanted holes, missing faces, visible geometry defects, or strange intersections?\n"
        "6. Are materials, surface shading, and wireframe topology clean and professional?\n\n"
        "Respond strictly as JSON with keys:\n"
        '{"score": <0-100 float>, "confidence": <0.0-1.0 float>, '
        '"recommendation": "APPROVE_CANDIDATE" | "NEEDS_HUMAN_REVIEW" | "REJECT", '
        '"matches_description": <bool>, "component_check": "<string>", '
        '"proportion_check": "<string>", "surface_and_materials_check": "<string>", '
        '"issues_found": ["<string>"], "summary": "<string>"}'
    )


def _parse_vision_json(
    raw_text: str,
    *,
    provider: str,
    model: str,
    render_paths: dict[str, str],
    turntable_path: Optional[str],
    contact_sheet_path: Optional[str],
    render_sanity_ok: bool,
    render_sanity_details: dict[str, Any],
) -> VisualQAReport:
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        cleaned = "\n".join(line for line in lines if not line.strip().startswith("```"))
    data = json.loads(cleaned)

    score = float(data.get("score", 0.0))
    confidence = max(0.0, min(1.0, float(data.get("confidence", 0.0))))
    rec = str(data.get("recommendation", "NEEDS_HUMAN_REVIEW")).upper()

    # Low confidence must never auto-recommend approval
    if confidence < 0.75 and rec == "APPROVE_CANDIDATE":
        rec = "NEEDS_HUMAN_REVIEW"

    if rec == "REJECT":
        status = VisualQAStatus.FAIL_CANDIDATE
    elif rec == "APPROVE_CANDIDATE":
        status = VisualQAStatus.PASS_CANDIDATE
    else:
        status = VisualQAStatus.NEEDS_HUMAN_REVIEW

    return VisualQAReport(
        provider=provider,
        model=model,
        status=status,
        score=score,
        confidence=confidence,
        recommendation=rec,
        summary=str(data.get("summary", "")),
        matches_description=data.get("matches_description"),
        component_check=str(data.get("component_check", "")),
        proportion_check=str(data.get("proportion_check", "")),
        surface_and_materials_check=str(data.get("surface_and_materials_check", "")),
        issues_found=[str(x) for x in data.get("issues_found", [])],
        render_paths=render_paths,
        turntable_path=turntable_path,
        contact_sheet_path=contact_sheet_path,
        render_sanity_ok=render_sanity_ok,
        render_sanity_details=render_sanity_details,
        analyzed_at=utc_now_iso(),
    )


class VisionQAProvider(ABC):
    provider_name: str = "base"

    @abstractmethod
    def evaluate(
        self,
        *,
        asset_name: str,
        description: str,
        category: str,
        render_paths: dict[str, str],
        turntable_path: Optional[str],
        contact_sheet_path: Optional[str],
        render_sanity_ok: bool,
        render_sanity_details: dict[str, Any],
    ) -> VisualQAReport:
        ...


class UnconfiguredVisionProvider(VisionQAProvider):
    """Honest fallback when no multimodal VLM API key/endpoint is configured."""

    provider_name = "unconfigured"

    def evaluate(
        self,
        *,
        asset_name: str,
        description: str,
        category: str,
        render_paths: dict[str, str],
        turntable_path: Optional[str],
        contact_sheet_path: Optional[str],
        render_sanity_ok: bool,
        render_sanity_details: dict[str, Any],
    ) -> VisualQAReport:
        issues: list[str] = []
        if not render_sanity_ok:
            issues.append("One or more rendered inspection views failed pixel sanity checks.")
        return VisualQAReport(
            provider="unconfigured",
            model="none",
            status=VisualQAStatus.UNCONFIGURED,
            score=None,
            confidence=0.0,
            recommendation="NEEDS_HUMAN_REVIEW",
            summary=(
                "No multimodal vision reviewer is configured (VISION_QA_PROVIDER=none). "
                "Multi-angle renders were generated and verified for pixel sanity; "
                "human visual inspection is required in Stage 2 before approval."
            ),
            matches_description=None,
            component_check="Awaiting multimodal vision model or Stage 2 human visual inspection.",
            proportion_check="Awaiting multimodal vision model or Stage 2 human visual inspection.",
            surface_and_materials_check="Awaiting multimodal vision model or Stage 2 human visual inspection.",
            issues_found=issues,
            render_paths=render_paths,
            turntable_path=turntable_path,
            contact_sheet_path=contact_sheet_path,
            render_sanity_ok=render_sanity_ok,
            render_sanity_details=render_sanity_details,
            analyzed_at=utc_now_iso(),
        )


def _select_multiview_images(render_paths: dict[str, str]) -> list[tuple[str, Path]]:
    keys = ["three_quarter", "front", "back", "left", "top", "closeup", "wireframe", "material_beauty"]
    selected: list[tuple[str, Path]] = []
    for k in keys:
        p = render_paths.get(k)
        if p and Path(p).is_file():
            selected.append((k, Path(p)))
    return selected


class GeminiVisionProvider(VisionQAProvider):
    provider_name = "gemini"

    def __init__(self, api_key: str, model: str = "gemini-2.5-flash") -> None:
        self.api_key = api_key
        self.model = model

    def evaluate(
        self,
        *,
        asset_name: str,
        description: str,
        category: str,
        render_paths: dict[str, str],
        turntable_path: Optional[str],
        contact_sheet_path: Optional[str],
        render_sanity_ok: bool,
        render_sanity_details: dict[str, Any],
    ) -> VisualQAReport:
        prompt = _build_vision_prompt(asset_name, description, category)
        parts: list[dict[str, Any]] = [{"text": prompt}]
        for view_name, path in _select_multiview_images(render_paths):
            parts.append({"text": f"View: {view_name}"})
            parts.append(
                {
                    "inline_data": {
                        "mime_type": "image/png",
                        "data": base64.b64encode(path.read_bytes()).decode("ascii"),
                    }
                }
            )
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        resp = httpx.post(
            url,
            params={"key": self.api_key},
            json={
                "contents": [{"parts": parts}],
                "generationConfig": {"responseMimeType": "application/json", "temperature": 0.1},
            },
            timeout=60.0,
        )
        resp.raise_for_status()
        body = resp.json()
        text = body["candidates"][0]["content"]["parts"][0]["text"]
        return _parse_vision_json(
            text,
            provider=self.provider_name,
            model=self.model,
            render_paths=render_paths,
            turntable_path=turntable_path,
            contact_sheet_path=contact_sheet_path,
            render_sanity_ok=render_sanity_ok,
            render_sanity_details=render_sanity_details,
        )


class OpenAIVisionProvider(VisionQAProvider):
    provider_name = "openai"

    def __init__(self, api_key: str, model: str = "gpt-4o-mini") -> None:
        self.api_key = api_key
        self.model = model

    def evaluate(
        self,
        *,
        asset_name: str,
        description: str,
        category: str,
        render_paths: dict[str, str],
        turntable_path: Optional[str],
        contact_sheet_path: Optional[str],
        render_sanity_ok: bool,
        render_sanity_details: dict[str, Any],
    ) -> VisualQAReport:
        prompt = _build_vision_prompt(asset_name, description, category)
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for view_name, path in _select_multiview_images(render_paths):
            b64 = base64.b64encode(path.read_bytes()).decode("ascii")
            content.append({"type": "text", "text": f"View: {view_name}"})
            content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}})

        resp = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model,
                "response_format": {"type": "json_object"},
                "messages": [{"role": "user", "content": content}],
                "temperature": 0.1,
            },
            timeout=60.0,
        )
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"]
        return _parse_vision_json(
            text,
            provider=self.provider_name,
            model=self.model,
            render_paths=render_paths,
            turntable_path=turntable_path,
            contact_sheet_path=contact_sheet_path,
            render_sanity_ok=render_sanity_ok,
            render_sanity_details=render_sanity_details,
        )


class OllamaVisionProvider(VisionQAProvider):
    provider_name = "ollama"

    def __init__(self, base_url: str, model: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model

    def evaluate(
        self,
        *,
        asset_name: str,
        description: str,
        category: str,
        render_paths: dict[str, str],
        turntable_path: Optional[str],
        contact_sheet_path: Optional[str],
        render_sanity_ok: bool,
        render_sanity_details: dict[str, Any],
    ) -> VisualQAReport:
        prompt = _build_vision_prompt(asset_name, description, category)
        images = [
            base64.b64encode(path.read_bytes()).decode("ascii")
            for _, path in _select_multiview_images(render_paths)[:4]
        ]
        resp = httpx.post(
            f"{self.base_url}/api/chat",
            json={
                "model": self.model,
                "format": "json",
                "stream": False,
                "messages": [{"role": "user", "content": prompt, "images": images}],
            },
            timeout=90.0,
        )
        resp.raise_for_status()
        text = resp.json()["message"]["content"]
        return _parse_vision_json(
            text,
            provider=self.provider_name,
            model=self.model,
            render_paths=render_paths,
            turntable_path=turntable_path,
            contact_sheet_path=contact_sheet_path,
            render_sanity_ok=render_sanity_ok,
            render_sanity_details=render_sanity_details,
        )


def build_vision_provider(settings: Settings) -> VisionQAProvider:
    choice = (settings.vision_qa_provider or "none").strip().lower()
    if choice == "gemini" and settings.gemini_api_key.strip():
        return GeminiVisionProvider(settings.gemini_api_key.strip(), settings.gemini_vision_model)
    if choice == "openai" and settings.openai_api_key.strip():
        return OpenAIVisionProvider(settings.openai_api_key.strip(), settings.openai_vision_model)
    if choice == "ollama":
        return OllamaVisionProvider(settings.ollama_base_url, settings.ollama_vision_model)
    return UnconfiguredVisionProvider()
