"""Generates QA_Report.json and readable visual QA_Report.html with embedded preview gallery."""

from __future__ import annotations

import html
import json
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from ..roblox_qa.models import RobloxQAOutput


def _e(val: Any) -> str:
    return html.escape(str(val if val is not None else ""))


class QAReportGenerator:
    """Writes QA_Report.json, QA_Report.html, and local preview copies for an asset version."""

    def __init__(self, reports_dir: Path) -> None:
        self.reports_dir = Path(reports_dir)
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    def report_dir_for(self, asset_id: str, version_number: int) -> Path:
        path = self.reports_dir / asset_id / f"v{version_number:03d}"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def generate_reports(
        self,
        qa_output: RobloxQAOutput,
        *,
        final_approval_status: Optional[str] = None,
        approved_by: Optional[str] = None,
        approved_at: Optional[str] = None,
        google_drive_file_id: Optional[str] = None,
        google_drive_url: Optional[str] = None,
    ) -> dict[str, Path]:
        out_dir = self.report_dir_for(qa_output.asset_id, qa_output.asset_version)
        previews_out = out_dir / "previews"
        previews_out.mkdir(parents=True, exist_ok=True)

        # Copy preview images locally into report directory so HTML is portable & self-contained
        rel_previews: dict[str, str] = {}
        for view_key, src_str in qa_output.preview_images.items():
            src = Path(src_str)
            if src.is_file():
                dst = previews_out / src.name
                if src.resolve() != dst.resolve():
                    shutil.copy2(src, dst)
                rel_previews[view_key] = f"previews/{dst.name}"

        rel_contact_sheet: Optional[str] = None
        if qa_output.contact_sheet_path:
            cs_src = Path(qa_output.contact_sheet_path)
            if cs_src.is_file():
                cs_dst = previews_out / cs_src.name
                if cs_src.resolve() != cs_dst.resolve():
                    shutil.copy2(cs_src, cs_dst)
                rel_contact_sheet = f"previews/{cs_dst.name}"

        approval_state = final_approval_status or (
            "AWAITING_HUMAN_APPROVAL" if qa_output.final_status == "QA_PASSED" else "QA_FAILED"
        )

        json_payload: dict[str, Any] = {
            "asset_id": qa_output.asset_id,
            "asset_name": qa_output.asset_name,
            "asset_version": qa_output.asset_version,
            "department_code": qa_output.department_code,
            "budget_tier": qa_output.budget_tier,
            "test_timestamp": qa_output.test_timestamp,
            "blender_version": qa_output.blender_version,
            "polygon_count": qa_output.polygon_count,
            "triangle_count": qa_output.triangle_count,
            "vertex_count": qa_output.vertex_count,
            "object_count": qa_output.object_count,
            "dimensions": {
                "meters": qa_output.dimensions_meters,
                "roblox_studs": qa_output.dimensions_studs,
            },
            "texture_information": qa_output.texture_info,
            "material_information": qa_output.material_info,
            "export_validation": qa_output.export_validation,
            "collision_assessment": qa_output.collision_assessment.model_dump(),
            "uv_status": qa_output.uv_status,
            "individual_qa_scores": qa_output.individual_scores,
            "dimensions_breakdown": [d.model_dump() for d in qa_output.dimensions_breakdown],
            "overall_score": qa_output.overall_score,
            "grade": qa_output.grade,
            "qa_status": qa_output.final_status,
            "mandatory_failures": qa_output.mandatory_failures,
            "warnings": qa_output.warnings,
            "recommendations": qa_output.recommendations,
            "final_approval_status": approval_state,
            "human_approved": approval_state == "APPROVED",
            "approved_by": approved_by,
            "approved_at": approved_at,
            "human_approval_required": qa_output.human_approval_required,
            "ai_visual_inspection_performed": qa_output.ai_visual_inspection_performed,
            "visual_inspection_note": qa_output.visual_inspection_note,
            "roblox_studio_automation_available": qa_output.roblox_studio_automation_available,
            "roblox_studio_limitation_note": qa_output.roblox_studio_limitation_note,
            "roblox_studio_checklist": [c.model_dump() for c in qa_output.roblox_studio_checklist],
            "preview_images": rel_previews,
            "contact_sheet": rel_contact_sheet,
            "google_drive": {
                "file_id": google_drive_file_id,
                "url": google_drive_url,
            },
        }

        json_path = out_dir / "QA_Report.json"
        self._atomic_write(json_path, json.dumps(json_payload, indent=2))

        html_path = out_dir / "QA_Report.html"
        html_text = self._render_html(qa_output, json_payload, rel_previews, rel_contact_sheet)
        self._atomic_write(html_path, html_text)

        return {"json": json_path, "html": html_path, "dir": out_dir}

    def _render_html(
        self,
        qa: RobloxQAOutput,
        data: dict[str, Any],
        rel_previews: dict[str, str],
        rel_contact_sheet: Optional[str],
    ) -> str:
        grade_colors = {"A": "#2ea043", "B": "#388bfd", "C": "#d29922", "D": "#f85149"}
        grade_col = grade_colors.get(qa.grade, "#98a2b3")
        approval_state = data["final_approval_status"]
        status_color = "#2ea043" if approval_state == "APPROVED" else ("#388bfd" if qa.final_status == "QA_PASSED" else "#f85149")

        score_rows = []
        for dim in qa.dimensions_breakdown:
            pct = (dim.score / max(dim.max_score, 1.0)) * 100.0
            bar_col = "#2ea043" if pct >= 85 else ("#d29922" if pct >= 65 else "#f85149")
            score_rows.append(
                f"<tr>"
                f"<td><strong>{_e(dim.label)}</strong></td>"
                f"<td>{dim.score:.2f} / {dim.max_score:.0f}</td>"
                f"<td><div class='bar-bg'><div class='bar-fill' style='width:{pct:.1f}%;background:{bar_col}'></div></div></td>"
                f"<td>{'<span class=\"pill fail\">FAIL</span>' if dim.failures else ('<span class=\"pill warn\">WARN</span>' if dim.warnings else '<span class=\"pill pass\">PASS</span>')}</td>"
                f"</tr>"
            )

        def _render_list(items: list[str], empty_text: str) -> str:
            if not items:
                return f"<p class='muted'>{_e(empty_text)}</p>"
            return "<ul>" + "".join(f"<li>{_e(x)}</li>" for x in items) + "</ul>"

        preview_cards = []
        preferred_order = ["three_quarter", "front", "right", "back", "left", "top", "closeup", "wireframe", "material_beauty"]
        ordered_keys = [k for k in preferred_order if k in rel_previews] + [k for k in sorted(rel_previews) if k not in preferred_order]
        for k in ordered_keys:
            preview_cards.append(
                f"<div class='preview-card'><img src='{_e(rel_previews[k])}' alt='{_e(k)}' />"
                f"<div class='preview-label'>{_e(k.replace('_', ' ').upper())}</div></div>"
            )
        if rel_contact_sheet:
            preview_cards.insert(
                0,
                f"<div class='preview-card wide'><img src='{_e(rel_contact_sheet)}' alt='Contact Sheet' />"
                f"<div class='preview-label'>CONTACT SHEET</div></div>",
            )

        checklist_rows = "".join(
            f"<tr><td>{c.step}</td><td>{_e(c.check)}</td><td>{_e(c.expected)}</td>"
            f"<td>{'Manual Studio Check' if c.manual_studio_verification_required else 'Automated'}</td></tr>"
            for c in qa.roblox_studio_checklist
        )

        dims_m = qa.dimensions_meters
        dims_s = qa.dimensions_studs
        mat_names = [
            m["name"] if isinstance(m, dict) and "name" in m else str(m)
            for m in qa.material_info.get("materials", [])
        ]
        mat_summary = ", ".join(mat_names) or "None"

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>QA Report — {_e(qa.asset_id)}</title>
  <style>
    body {{ font-family: "Segoe UI", system-ui, sans-serif; background:#0f1218; color:#e6edf3; margin:0; padding:24px; line-height:1.5; }}
    .wrap {{ max-width: 1180px; margin: 0 auto; }}
    .card {{ background:#161b22; border:1px solid #30363d; border-radius:10px; padding:18px 20px; margin-bottom:18px; }}
    .header {{ display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:16px; }}
    .grade-box {{ display:flex; align-items:center; gap:14px; }}
    .grade {{ font-size:28px; font-weight:700; width:56px; height:56px; border-radius:12px; display:flex; align-items:center; justify-content:center; color:#fff; background:{grade_col}; }}
    .score-big {{ font-size:26px; font-weight:700; }}
    .pill {{ display:inline-block; padding:3px 10px; border-radius:999px; font-size:12px; font-weight:600; }}
    .pill.pass {{ background:#1b4721; color:#56d364; }}
    .pill.warn {{ background:#4d3800; color:#e3b341; }}
    .pill.fail {{ background:#5c1a1b; color:#ff7b72; }}
    .status-pill {{ background:{status_color}; color:#fff; padding:6px 14px; border-radius:999px; font-weight:600; font-size:13px; }}
    .grid-2 {{ display:grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap:16px; }}
    table {{ width:100%; border-collapse:collapse; font-size:14px; }}
    th, td {{ text-align:left; padding:8px 10px; border-bottom:1px solid #21262d; }}
    th {{ color:#8b949e; font-weight:600; }}
    .bar-bg {{ width:140px; height:8px; background:#21262d; border-radius:4px; overflow:hidden; }}
    .bar-fill {{ height:100%; border-radius:4px; }}
    .preview-grid {{ display:grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap:12px; }}
    .preview-card {{ background:#0d1117; border:1px solid #30363d; border-radius:8px; overflow:hidden; }}
    .preview-card.wide {{ grid-column: span 2; }}
    .preview-card img {{ width:100%; display:block; }}
    .preview-label {{ padding:6px 10px; font-size:12px; color:#8b949e; text-align:center; }}
    .muted {{ color:#8b949e; }}
    .notice {{ background:#1c2128; border-left:4px solid #388bfd; padding:10px 14px; border-radius:4px; font-size:13px; margin-top:8px; }}
  </style>
</head>
<body>
<div class="wrap">
  <div class="card header">
    <div>
      <h1 style="margin:0 0 4px 0;">{_e(qa.asset_name)}</h1>
      <div class="muted"><strong>Asset ID:</strong> <code>{_e(qa.asset_id)}</code> &bull; Version v{qa.asset_version:03d} &bull; Dept {_e(qa.department_code)} ({_e(qa.budget_tier)})</div>
      <div class="muted" style="font-size:13px;">Tested: {_e(qa.test_timestamp)} &bull; Blender {_e(qa.blender_version or 'N/A')}</div>
    </div>
    <div class="grade-box">
      <div class="grade">{_e(qa.grade)}</div>
      <div>
        <div class="score-big">{qa.overall_score:.1f} / 100</div>
        <span class="status-pill">{_e(approval_state)}</span>
      </div>
    </div>
  </div>

  <div class="grid-2">
    <div class="card">
      <h2 style="margin-top:0;">Asset &amp; Export Summary</h2>
      <table>
        <tr><th>Triangles</th><td>{qa.triangle_count:,}</td></tr>
        <tr><th>Polygons / Faces</th><td>{qa.polygon_count:,}</td></tr>
        <tr><th>Vertices</th><td>{qa.vertex_count:,}</td></tr>
        <tr><th>Mesh Objects</th><td>{qa.object_count}</td></tr>
        <tr><th>Dimensions (m)</th><td>{dims_m[0]:.3f} &times; {dims_m[1]:.3f} &times; {dims_m[2]:.3f} m</td></tr>
        <tr><th>Roblox Studs</th><td>{dims_s[0]:.2f} &times; {dims_s[1]:.2f} &times; {dims_s[2]:.2f} studs</td></tr>
        <tr><th>Materials</th><td>{_e(mat_summary)}</td></tr>
        <tr><th>Textures</th><td>{_e(qa.texture_info.get('resolution', 'None'))}</td></tr>
        <tr><th>UV Status</th><td>{_e(qa.uv_status)}</td></tr>
        <tr><th>Collision</th><td>{_e(qa.collision_assessment.collision_status)} (Rec: {_e(qa.collision_assessment.recommended_fidelity)})</td></tr>
        <tr><th>Export Format</th><td>{_e(qa.export_validation.get('export_format', 'N/A'))} ({qa.export_validation.get('file_size_bytes', 0):,} bytes)</td></tr>
      </table>
    </div>

    <div class="card">
      <h2 style="margin-top:0;">10-Point Quality Breakdown</h2>
      <table>
        <thead><tr><th>Dimension</th><th>Score</th><th>Progress</th><th>Status</th></tr></thead>
        <tbody>{''.join(score_rows)}</tbody>
      </table>
    </div>
  </div>

  <div class="grid-2">
    <div class="card">
      <h2 style="margin-top:0;">Mandatory Failures</h2>
      {_render_list(qa.mandatory_failures, "No mandatory failures.")}
      <h3>Warnings</h3>
      {_render_list(qa.warnings, "No warnings.")}
    </div>
    <div class="card">
      <h2 style="margin-top:0;">Recommendations &amp; Approval Gate</h2>
      {_render_list(qa.recommendations, "No recommendations.")}
      <div class="notice"><strong>Human Approval Gate:</strong> {_e(qa.visual_inspection_note)} (AI Visual Inspection Performed: {_e(qa.ai_visual_inspection_performed)})</div>
      <div class="notice"><strong>Roblox Studio Notice:</strong> {_e(qa.roblox_studio_limitation_note)}</div>
    </div>
  </div>

  <div class="card">
    <h2 style="margin-top:0;">Roblox Studio Manual Import Checklist</h2>
    <table>
      <thead><tr><th>#</th><th>Check</th><th>Expected Result</th><th>Mode</th></tr></thead>
      <tbody>{checklist_rows}</tbody>
    </table>
  </div>

  <div class="card">
    <h2 style="margin-top:0;">Visual Inspection Previews</h2>
    <div class="preview-grid">
      {''.join(preview_cards) if preview_cards else '<p class="muted">No preview images generated.</p>'}
    </div>
  </div>
</div>
</body>
</html>
"""

    @staticmethod
    def _atomic_write(target: Path, text: str) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(target)
