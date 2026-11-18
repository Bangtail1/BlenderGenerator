import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { createReferenceDashboard } from "/static/reference_dashboard.js";

const state = {
  health: null,
  assets: [],
  activeCategory: "All",
  activeStatus: "All",
  search: "",
  selectedAsset: null,
  selectedVersionNum: null,
};

// Three.js Viewer Singleton
const viewer = {
  renderer: null,
  scene: null,
  camera: null,
  controls: null,
  modelRoot: null,
  wireframe: false,
  autoRotate: false,
  animId: null,
  defaultCamPos: new THREE.Vector3(1.6, 1.3, 1.8),
  defaultTarget: new THREE.Vector3(0, 0.4, 0),
};

async function api(url, opts = {}) {
  const res = await fetch(url, opts);
  const contentType = res.headers.get("content-type") || "";
  const body = contentType.includes("application/json") ? await res.json() : await res.text();
  if (!res.ok) {
    const msg = typeof body === "object" ? (body.detail?.message || JSON.stringify(body.detail) || res.statusText) : body;
    throw new Error(msg);
  }
  return body;
}

function escapeHtml(s) {
  return String(s ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function badge(text, kind) {
  return `<span class="badge badge-${escapeHtml(kind || text)}">${escapeHtml(text)}</span>`;
}

// ---------------------------------------------------------------------------
// Initial Load & Catalogue
// ---------------------------------------------------------------------------
async function loadHealth() {
  state.health = await api("/api/health");
  const h = state.health;
  const pills = [];
  if (h.blender?.available) {
    pills.push(`<span class="pill pill-ok">Blender: ${escapeHtml(h.blender.version_line)}</span>`);
  } else {
    pills.push(`<span class="pill pill-fail">Blender: Not Found</span>`);
  }
  pills.push(
    h.vision_configured
      ? `<span class="pill pill-info">Vision QA: ${escapeHtml(h.vision_provider)}</span>`
      : `<span class="pill pill-warn">Vision QA: Human Review Only</span>`
  );
  if (h.gdrive?.authenticated) {
    pills.push(`<span class="pill pill-ok">Drive: ${escapeHtml(h.gdrive.user_email || "Connected")}</span>`);
  } else if (h.gdrive?.client_secrets_configured) {
    pills.push(`<span class="pill pill-warn">Drive: Ready to Sign In</span>`);
  } else {
    pills.push(`<span class="pill pill-muted">Drive: Setup Needed</span>`);
  }
  const refEng = h.reference_engine || {};
  pills.push(
    refEng.blendswap_configured
      ? `<span class="pill pill-info">BlendSwap Ref: ${refEng.reference_count || 0}</span>`
      : `<span class="pill pill-muted">BlendSwap Key: Unset</span>`
  );
  document.getElementById("system-pills").innerHTML = pills.join("");

  // Populate category filters & modal dropdowns
  const cats = ["All", ...(h.categories || [])];
  document.getElementById("category-bar").innerHTML = cats
    .map(
      (c) =>
        `<button class="cat-btn ${state.activeCategory === c ? "active" : ""}" data-cat="${escapeHtml(c)}">${escapeHtml(c)}</button>`
    )
    .join("");
  document.querySelectorAll(".cat-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.activeCategory = btn.dataset.cat;
      loadHealth();
      loadAssets();
    });
  });

  const catOptions = (h.categories || []).map((c) => `<option value="${escapeHtml(c)}">${escapeHtml(c)}</option>`).join("");
  document.getElementById("gen-category").innerHTML = `<option value="">Auto-detect from prompt</option>` + catOptions;
  document.getElementById("imp-category").innerHTML = catOptions;

  const profOptions = (h.geometry_profiles || []).map((p) => `<option value="${escapeHtml(p)}">${escapeHtml(p)}</option>`).join("");
  document.getElementById("gen-profile").innerHTML = `<option value="">Default for category</option>` + profOptions;
  document.getElementById("imp-profile").innerHTML = `<option value="">Default for category</option>` + profOptions;
}

async function loadAssets() {
  const params = new URLSearchParams();
  if (state.search.trim()) params.set("search", state.search.trim());
  if (state.activeCategory && state.activeCategory !== "All") params.set("category", state.activeCategory);
  if (state.activeStatus && state.activeStatus !== "All") params.set("status", state.activeStatus);

  const data = await api(`/api/assets?${params.toString()}`);
  state.assets = data.assets || [];
  renderStats();
  renderAssetGrid();
}

function renderStats() {
  const bySt = state.health?.counts?.by_status || {};
  const parts = Object.entries(bySt).map(([k, v]) => `<span><strong>${v}</strong> ${escapeHtml(k)}</span>`);
  document.getElementById("stats-strip").innerHTML =
    `<span>Showing <strong>${state.assets.length}</strong> asset(s)</span>` + (parts.length ? " · " + parts.join(" · ") : "");
}

function renderAssetGrid() {
  const grid = document.getElementById("asset-grid");
  if (!state.assets.length) {
    grid.innerHTML = `<div class="empty-state">No assets match your current filters. Click <strong>+ Generate 3D</strong> or <strong>+ Import Model</strong> to add one.</div>`;
    return;
  }

  grid.innerHTML = state.assets
    .map((a) => {
      const v = a.latest || {};
      const dims = (v.dimensions || [0, 0, 0]).map((n) => Number(n).toFixed(2)).join(" × ");
      const thumbUrl = v.visual_qa?.render_paths?.three_quarter
        ? `/api/assets/${encodeURIComponent(a.asset_id)}/versions/${v.version}/preview/three_quarter.png?t=${encodeURIComponent(v.updated_at || "")}`
        : "";
      const techStatus = v.technical_qa?.status || "PENDING";
      const humanStatus = v.human_review?.status || "PENDING";
      return `
        <article class="asset-card" data-id="${escapeHtml(a.asset_id)}">
          <div class="card-thumb">
            ${thumbUrl ? `<img src="${thumbUrl}" alt="${escapeHtml(a.name)}" loading="lazy" />` : `<span class="muted small">No Preview</span>`}
            <span class="card-version-chip">v${v.version || 1}</span>
          </div>
          <div class="card-body">
            <div class="card-title-row">
              <h3 class="card-title">${escapeHtml(a.name)}</h3>
              ${badge(v.status || "UNKNOWN", v.status)}
            </div>
            <div class="card-meta">${escapeHtml(a.category)} · ${v.poly_count || 0} tris · ${dims}m</div>
            <div class="card-meta small">${escapeHtml(a.provenance?.license_name || "Unknown")}</div>
            <div class="card-badges">
              ${badge(`QA: ${techStatus}`, techStatus)}
              ${badge(`Human: ${humanStatus}`, humanStatus)}
              ${badge(`Drive: ${v.drive_sync_status || "NOT_SYNCED"}`, v.drive_sync_status)}
            </div>
          </div>
        </article>`;
    })
    .join("");

  grid.querySelectorAll(".asset-card").forEach((card) => {
    card.addEventListener("click", () => openAssetDetail(card.dataset.id));
  });
}

// ---------------------------------------------------------------------------
// Asset Detail & Inspector
// ---------------------------------------------------------------------------
async function openAssetDetail(assetId, versionNum = null) {
  const asset = await api(`/api/assets/${encodeURIComponent(assetId)}`);
  state.selectedAsset = asset;
  const v = versionNum
    ? (asset.versions || []).find((x) => x.version === Number(versionNum)) || asset.latest
    : asset.latest;
  state.selectedVersionNum = v.version;

  document.getElementById("detail-title").textContent = `${asset.name} (v${v.version})`;
  document.getElementById("detail-subtitle").textContent = `${asset.category} · ID ${asset.asset_id} · Profile: ${asset.geometry_profile}`;

  renderOverviewPanel(asset, v);
  renderTechQAPanel(asset, v);
  renderVisualQAPanel(asset, v);
  renderDriveVersionsPanel(asset, v);
  renderVisualEvidence(asset, v);

  const modal = document.getElementById("detail-modal");
  if (!modal.open) modal.showModal();
  if (v.glb_path) {
    loadThreeModel(`/api/assets/${encodeURIComponent(asset.asset_id)}/versions/${v.version}/download/glb`);
  } else {
    clearThreeModel("No GLB available for this version.");
  }
}

function renderVisualEvidence(asset, v) {
  const base = `/api/assets/${encodeURIComponent(asset.asset_id)}/versions/${v.version}/preview`;
  const ts = `?t=${encodeURIComponent(v.updated_at || "")}`;
  const sheetImg = document.getElementById("img-contact-sheet");
  sheetImg.src = v.visual_qa?.contact_sheet_path ? `${base}/contact_sheet.png${ts}` : "";
  const ttImg = document.getElementById("img-turntable");
  ttImg.src = v.visual_qa?.turntable_path ? `${base}/turntable.webp${ts}` : "";

  const angleLabels = [
    ["three_quarter", "3/4 Perspective"],
    ["front", "Front"],
    ["back", "Back"],
    ["left", "Left"],
    ["right", "Right"],
    ["top", "Top"],
    ["closeup", "Close-Up Detail"],
    ["wireframe", "Wireframe"],
    ["material_beauty", "Material Beauty"],
  ];
  document.getElementById("angles-grid").innerHTML = angleLabels
    .map(([k, lbl]) => {
      const exists = Boolean(v.visual_qa?.render_paths?.[k]);
      return exists
        ? `<div class="angle-tile"><a href="${base}/${k}.png${ts}" target="_blank"><img src="${base}/${k}.png${ts}" alt="${lbl}" /></a><span>${lbl}</span></div>`
        : `<div class="angle-tile"><div style="padding:28px" class="muted small">N/A</div><span>${lbl}</span></div>`;
    })
    .join("");
}

function renderOverviewPanel(asset, v) {
  const dims = (v.dimensions || [0, 0, 0]).map((x) => `${Number(x).toFixed(3)}m`).join(" × ");
  const prov = asset.provenance || {};
  const provBanner = prov.provenance_verified
    ? `<div class="feedback ok"><strong>Provenance (${escapeHtml(prov.provenance_type)}):</strong> ${escapeHtml(prov.license_name)}</div>`
    : `<div class="feedback info"><strong>Provenance Notice:</strong> ${escapeHtml(prov.license_name)} — ${escapeHtml(prov.usage_restrictions)}</div>`;

  const errBanner = v.error_message
    ? `<div class="feedback err"><strong>Failure (${escapeHtml(v.failed_stage || "pipeline")}):</strong> ${escapeHtml(v.error_message)}</div>`
    : "";

  const verOpts = (asset.versions || [])
    .map((ver) => `<option value="${ver.version}" ${ver.version === v.version ? "selected" : ""}>v${ver.version} (${ver.status})</option>`)
    .join("");

  document.getElementById("ipanel-overview").innerHTML = `
    ${errBanner}
    ${provBanner}
    <div class="panel-card">
      <h3>Metadata &amp; Geometry</h3>
      <div class="kv-grid">
        <div class="kv-item"><strong>Status</strong>${badge(v.status, v.status)}</div>
        <div class="kv-item"><strong>Version</strong><select id="detail-ver-select">${verOpts}</select></div>
        <div class="kv-item"><strong>Dimensions (W × D × H)</strong>${dims}</div>
        <div class="kv-item"><strong>Triangles / Vertices</strong>${v.poly_count} tris / ${v.vertex_count} verts</div>
        <div class="kv-item"><strong>Blender Version</strong>${escapeHtml(v.blender_version || "N/A")}</div>
        <div class="kv-item"><strong>Materials</strong>${escapeHtml((v.materials || []).join(", ") || "None")}</div>
        <div class="kv-item"><strong>Tags</strong>${escapeHtml((asset.tags || []).join(", "))}</div>
        <div class="kv-item"><strong>File Size</strong>${((v.file_size_bytes || 0) / 1024).toFixed(1)} KB</div>
      </div>
      <div class="kv-item"><strong>SHA-256</strong><span class="mono">${escapeHtml(v.sha256 || "N/A")}</span></div>
      <div class="kv-item"><strong>Description</strong><span class="small">${escapeHtml(asset.description)}</span></div>
    </div>

    <div class="panel-card">
      <h3>Asset Actions</h3>
      <div class="action-row">
        <button class="btn btn-primary" id="act-open-blender" ${v.blend_path ? "" : "disabled"}>Open in Blender</button>
        <a class="btn btn-secondary" href="/api/assets/${asset.asset_id}/versions/${v.version}/download/blend">Download .blend</a>
        ${v.glb_path ? `<a class="btn btn-secondary" href="/api/assets/${asset.asset_id}/versions/${v.version}/download/glb">Download .glb</a>` : ""}
        ${v.source_original_path ? `<a class="btn btn-ghost" href="/api/assets/${asset.asset_id}/versions/${v.version}/download/original">Original File</a>` : ""}
        <button class="btn btn-ghost" id="act-retry">Re-run / Retry QA</button>
        <button class="btn btn-secondary" id="act-benchmark" ${v.blend_path ? "" : "disabled"}>Benchmark vs Ref Library</button>
      </div>
      <div id="overview-action-msg" class="feedback hidden"></div>
    </div>
  `;

  document.getElementById("detail-ver-select").addEventListener("change", (e) => {
    openAssetDetail(asset.asset_id, Number(e.target.value));
  });
  document.getElementById("act-benchmark").addEventListener("click", async () => {
    const msgEl = document.getElementById("overview-action-msg");
    showFeedback(msgEl, "Benchmarking against Branch 2 Reference Library...", "info");
    try {
      const bmk = await api(`/api/reference/benchmarks/run/${asset.asset_id}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ version_num: v.version }),
      });
      showFeedback(
        msgEl,
        `Benchmark Grade: ${bmk.local_grade.grade_letter} (${bmk.local_grade.total_score}/100) vs ${bmk.selected_references.length} ref(s). ${bmk.efficiency_note}`,
        "ok"
      );
    } catch (e) {
      showFeedback(msgEl, e.message, "err");
    }
  });
  document.getElementById("act-open-blender").addEventListener("click", async () => {
    const msgEl = document.getElementById("overview-action-msg");
    try {
      const res = await api(`/api/assets/${asset.asset_id}/versions/${v.version}/open-blender`, { method: "POST" });
      showFeedback(msgEl, `Launched Blender GUI (PID ${res.pid}).`, "ok");
    } catch (e) {
      showFeedback(msgEl, e.message, "err");
    }
  });
  document.getElementById("act-retry").addEventListener("click", async () => {
    const msgEl = document.getElementById("overview-action-msg");
    showFeedback(msgEl, "Re-running Blender pipeline...", "info");
    try {
      await api(`/api/assets/${asset.asset_id}/versions/${v.version}/retry`, { method: "POST" });
      await openAssetDetail(asset.asset_id, v.version);
      await loadAssets();
    } catch (e) {
      showFeedback(msgEl, e.message, "err");
    }
  });
}

function renderTechQAPanel(asset, v) {
  const qa = v.technical_qa;
  if (!qa) {
    document.getElementById("ipanel-tech-qa").innerHTML = `<div class="panel-card"><p class="muted">No Technical QA report recorded yet.</p></div>`;
    return;
  }
  const rows = (qa.checks || [])
    .map(
      (c) => `<tr>
        <td><strong>${escapeHtml(c.name)}</strong><div class="small muted">${escapeHtml(c.category)}</div></td>
        <td>${badge(c.status, c.status === "WARN" ? "PASS_WITH_WARNINGS" : c.status)}</td>
        <td class="small">${escapeHtml(c.message)}</td>
      </tr>`
    )
    .join("");

  const rt = qa.round_trip || {};
  document.getElementById("ipanel-tech-qa").innerHTML = `
    <div class="panel-card">
      <h3>Technical QA Summary ${badge(qa.status, qa.status)}</h3>
      <p class="small">${escapeHtml(qa.summary)}</p>
      <div class="kv-grid">
        <div class="kv-item"><strong>Geometry Profile</strong>${escapeHtml(qa.geometry_profile)}</div>
        <div class="kv-item"><strong>GLB Round-Trip</strong>${rt.success ? `OK (max dim Δ ${rt.dimension_delta_max}m)` : escapeHtml(rt.error || "Failed")}</div>
      </div>
      <div class="action-row">
        <a class="btn btn-xs" href="/api/assets/${asset.asset_id}/versions/${v.version}/download/qa_report">Download qa_report.json</a>
      </div>
    </div>
    <div class="panel-card">
      <h3>Detailed Checks (${qa.checks?.length || 0})</h3>
      <table class="qa-table">
        <thead><tr><th>Check</th><th>Result</th><th>Details</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  `;
}

function renderVisualQAPanel(asset, v) {
  const vqa = v.visual_qa;
  const hr = v.human_review || {};
  const canApprove = v.technical_qa && v.technical_qa.status !== "FAIL" && v.status !== "FAILED";
  const catOptions = (state.health?.categories || [])
    .map((c) => `<option value="${escapeHtml(c)}" ${c === asset.category ? "selected" : ""}>${escapeHtml(c)}</option>`)
    .join("");

  document.getElementById("ipanel-visual-qa").innerHTML = `
    <div class="panel-card">
      <h3>Stage 1 — Automated Multimodal Visual QA</h3>
      ${
        vqa
          ? `<div class="kv-grid">
              <div class="kv-item"><strong>Provider / Model</strong>${escapeHtml(vqa.provider)} (${escapeHtml(vqa.model)})</div>
              <div class="kv-item"><strong>Automated Verdict</strong>${badge(vqa.status, vqa.status)}</div>
              <div class="kv-item"><strong>Render Pixel Sanity</strong>${vqa.render_sanity_ok ? badge("All 9 Views Valid", "PASS") : badge("Sanity Issue", "FAIL")}</div>
              <div class="kv-item"><strong>Score / Confidence</strong>${vqa.score ?? "N/A"} / ${(Number(vqa.confidence || 0) * 100).toFixed(0)}%</div>
            </div>
            <p class="small">${escapeHtml(vqa.summary)}</p>
            ${vqa.issues_found?.length ? `<ul class="small">${vqa.issues_found.map((i) => `<li>${escapeHtml(i)}</li>`).join("")}</ul>` : ""}`
          : `<p class="muted small">Visual inspection has not run yet.</p>`
      }
    </div>

    <div class="panel-card">
      <h3>Stage 2 — Human Visual Approval (Separate Gate)</h3>
      <div class="kv-grid">
        <div class="kv-item"><strong>Human Status</strong>${badge(hr.status || "PENDING", hr.status || "PENDING")}</div>
        <div class="kv-item"><strong>Reviewed By</strong>${escapeHtml(hr.reviewer || "Not yet inspected by human")}</div>
      </div>
      ${!canApprove ? `<div class="feedback err">Approval blocked: Technical QA must pass (or pass with warnings) before an asset can enter Approved Vault.</div>` : ""}
      <label class="small"><span>Reviewer Name</span><input type="text" id="hr-reviewer" value="${escapeHtml(hr.reviewer || "Owner")}" /></label>
      <label class="small"><span>Category Confirmation</span><select id="hr-category">${catOptions}</select></label>
      <label class="small"><span>Visual Inspection Notes</span><textarea id="hr-notes" rows="2" placeholder="Record silhouette, proportions, material, and usability observations...">${escapeHtml(hr.notes || "")}</textarea></label>
      <div class="action-row">
        <button class="btn btn-success" id="btn-human-approve" ${canApprove ? "" : "disabled"}>✓ Approve Asset</button>
        <button class="btn btn-danger" id="btn-human-reject">✕ Reject Asset</button>
      </div>
      <div id="hr-msg" class="feedback hidden"></div>
    </div>
  `;

  document.getElementById("btn-human-approve").addEventListener("click", () => submitHumanDecision(asset.asset_id, v.version, "APPROVED"));
  document.getElementById("btn-human-reject").addEventListener("click", () => submitHumanDecision(asset.asset_id, v.version, "REJECTED"));
}

async function submitHumanDecision(assetId, version, decision) {
  const msgEl = document.getElementById("hr-msg");
  try {
    await api(`/api/assets/${assetId}/versions/${version}/review`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        decision,
        reviewer: document.getElementById("hr-reviewer").value || "Owner",
        notes: document.getElementById("hr-notes").value || "",
        category: document.getElementById("hr-category").value,
      }),
    });
    await openAssetDetail(assetId, version);
    await loadHealth();
    await loadAssets();
  } catch (e) {
    showFeedback(msgEl, e.message, "err");
  }
}

function renderDriveVersionsPanel(asset, v) {
  const versionRows = (asset.versions || [])
    .map(
      (ver) => `<tr>
        <td><strong>v${ver.version}</strong></td>
        <td>${badge(ver.status, ver.status)}</td>
        <td>${ver.poly_count} tris</td>
        <td>${badge(ver.drive_sync_status, ver.drive_sync_status)}</td>
        <td><button class="btn btn-xs" data-switch-ver="${ver.version}">Inspect</button></td>
      </tr>`
    )
    .join("");

  const files = (v.drive_files || [])
    .map(
      (df) => `<tr>
        <td>${escapeHtml(df.role)}</td>
        <td class="mono">${escapeHtml(df.remote_name)}</td>
        <td>${df.verified ? badge("VERIFIED", "VERIFIED") : badge("UNVERIFIED", "PENDING")}</td>
        <td>${df.web_view_link ? `<a href="${escapeHtml(df.web_view_link)}" target="_blank">Open</a>` : escapeHtml(df.remote_file_id || "")}</td>
      </tr>`
    )
    .join("");

  document.getElementById("ipanel-drive").innerHTML = `
    <div class="panel-card">
      <h3>Google Drive Remote Storage ${badge(v.drive_sync_status, v.drive_sync_status)}</h3>
      <div class="kv-item"><strong>Drive Version Folder ID</strong><span class="mono">${escapeHtml(v.drive_folder_id || "Not uploaded yet")}</span></div>
      <div class="action-row">
        <button class="btn btn-primary" id="act-upload-drive" ${v.status === "FAILED" ? "disabled" : ""}>Upload v${v.version} to Google Drive</button>
        <button class="btn btn-secondary" id="act-verify-drive" ${v.drive_files?.length ? "" : "disabled"}>Verify Remote Integrity &amp; Reopen</button>
        <a class="btn btn-ghost" href="/api/assets/${asset.asset_id}/versions/${v.version}/download/manifest">manifest.json</a>
      </div>
      <div id="drive-action-msg" class="feedback hidden"></div>
    </div>
    <div class="panel-card">
      <h3>Version History (${asset.versions?.length || 1})</h3>
      <table class="qa-table">
        <thead><tr><th>Ver</th><th>Status</th><th>Geometry</th><th>Drive</th><th></th></tr></thead>
        <tbody>${versionRows}</tbody>
      </table>
      <div class="action-row">
        <label class="small" style="flex:1">
          <span>Upload Updated Model as v${asset.current_version + 1}</span>
          <input type="file" id="new-ver-file" accept=".blend,.obj,.fbx,.stl,.glb,.gltf" />
        </label>
        <button class="btn btn-secondary" id="act-upload-new-ver" style="align-self:end">Create v${asset.current_version + 1}</button>
      </div>
      <div id="new-ver-msg" class="feedback hidden"></div>
    </div>
    <div class="panel-card">
      <h3>Synced Remote Files (${v.drive_files?.length || 0})</h3>
      ${
        v.drive_files?.length
          ? `<table class="qa-table"><thead><tr><th>Role</th><th>File</th><th>Integrity</th><th>Drive</th></tr></thead><tbody>${files}</tbody></table>`
          : `<p class="small muted">No files uploaded to Google Drive for v${v.version} yet.</p>`
      }
    </div>
  `;

  document.querySelectorAll("[data-switch-ver]").forEach((btn) => {
    btn.addEventListener("click", () => openAssetDetail(asset.asset_id, Number(btn.dataset.switchVer)));
  });

  document.getElementById("act-upload-new-ver").addEventListener("click", async () => {
    const fileInput = document.getElementById("new-ver-file");
    const msgEl = document.getElementById("new-ver-msg");
    if (!fileInput.files.length) return showFeedback(msgEl, "Select a 3D file first.", "err");
    showFeedback(msgEl, "Creating new version and running QA pipeline...", "info");
    try {
      const form = new FormData();
      form.append("file", fileInput.files[0]);
      form.append("existing_asset_id", asset.asset_id);
      form.append("category", asset.category);
      const updated = await api("/api/assets/import", { method: "POST", body: form });
      await loadAssets();
      await openAssetDetail(asset.asset_id, updated.current_version);
    } catch (e) {
      showFeedback(msgEl, e.message, "err");
    }
  });

  document.getElementById("act-upload-drive").addEventListener("click", async () => {
    const msgEl = document.getElementById("drive-action-msg");
    showFeedback(msgEl, "Uploading version package via resumable Google Drive API...", "info");
    try {
      await api(`/api/assets/${asset.asset_id}/versions/${v.version}/upload-drive`, { method: "POST" });
      await openAssetDetail(asset.asset_id, v.version);
      await loadAssets();
    } catch (e) {
      showFeedback(msgEl, e.message, "err");
    }
  });
  document.getElementById("act-verify-drive").addEventListener("click", async () => {
    const msgEl = document.getElementById("drive-action-msg");
    showFeedback(msgEl, "Checking remote metadata, downloading .blend, verifying SHA-256, and reopening in Blender...", "info");
    try {
      const res = await api(`/api/assets/${asset.asset_id}/versions/${v.version}/verify-drive`, { method: "POST" });
      showFeedback(
        msgEl,
        `Verified ${res.checked_files.length} remote file(s), SHA-256 matched, Blender reopen OK (${res.reopened_mesh_count} meshes).`,
        "ok"
      );
    } catch (e) {
      showFeedback(msgEl, e.message, "err");
    }
  });
}

// ---------------------------------------------------------------------------
// Google Drive Settings Modal
// ---------------------------------------------------------------------------
async function openDriveModal() {
  const modal = document.getElementById("drive-modal");
  const body = document.getElementById("drive-panel-body");
  modal.showModal();
  body.innerHTML = `<p class="muted">Loading Google Drive status...</p>`;

  const st = await api("/api/gdrive/status");
  let foldersHtml = "";
  if (st.authenticated) {
    try {
      const fRes = await api("/api/gdrive/folders?parent_id=root");
      const opts = (fRes.folders || [])
        .map((f) => `<option value="${escapeHtml(f.id)}">${escapeHtml(f.name)} (${escapeHtml(f.id)})</option>`)
        .join("");
      foldersHtml = `
        <div class="panel-card">
          <h3>Destination Folder Selection</h3>
          <p class="small muted">Current Root: <strong>${escapeHtml(st.root_folder_name)}</strong> (${escapeHtml(st.root_folder_id || "Will create on first upload")})</p>
          <div class="form-row">
            <select id="drive-folder-select"><option value="">-- App-created Root Folders --</option>${opts}</select>
            <input type="text" id="drive-folder-id-input" placeholder="Or paste Google Drive Folder ID" value="${escapeHtml(st.root_folder_id || "")}" />
          </div>
          <div class="action-row">
            <button class="btn btn-primary" id="btn-save-drive-folder">Set Folder &amp; Initialize Vault Tree</button>
            <button class="btn btn-secondary" id="btn-rebuild-from-drive">Rebuild Catalogue from Drive</button>
          </div>
        </div>
      `;
    } catch (e) {
      foldersHtml = `<div class="feedback err">${escapeHtml(e.message)}</div>`;
    }
  }

  body.innerHTML = `
    <div class="panel-card">
      <h3>OAuth 2.0 Connection Status</h3>
      <div class="kv-grid">
        <div class="kv-item"><strong>OAuth Client Secrets</strong>${st.client_secrets_configured ? badge("Found", "PASS") : badge("Missing", "FAIL")}</div>
        <div class="kv-item"><strong>Authentication</strong>${st.authenticated ? badge(`Connected (${st.user_email || "OK"})`, "VERIFIED") : badge("Not Connected", "PENDING")}</div>
        <div class="kv-item"><strong>Scope (Least Privilege)</strong><span class="mono">${escapeHtml((st.scopes || []).join(", "))}</span></div>
        <div class="kv-item"><strong>Credentials Path</strong><span class="mono">${escapeHtml(st.client_secrets_path)}</span></div>
      </div>
      ${
        !st.client_secrets_configured
          ? `<div class="feedback info">Place your Desktop App OAuth 2.0 <code>client_secrets.json</code> at <code>${escapeHtml(st.client_secrets_path)}</code> (see <code>docs/GOOGLE_DRIVE_OAUTH_SETUP.md</code>), then click Connect below.</div>`
          : ""
      }
      <div class="action-row">
        <button class="btn btn-primary" id="btn-connect-drive" ${st.client_secrets_configured ? "" : "disabled"}>Connect Google Drive (OAuth 2.0)</button>
        ${st.authenticated ? `<button class="btn btn-danger" id="btn-disconnect-drive">Disconnect Token</button>` : ""}
      </div>
    </div>
    ${foldersHtml}
    <div id="drive-modal-msg" class="feedback hidden"></div>
  `;

  document.getElementById("btn-connect-drive")?.addEventListener("click", async () => {
    const res = await api("/api/gdrive/auth-url");
    window.location.href = res.auth_url;
  });
  document.getElementById("btn-disconnect-drive")?.addEventListener("click", async () => {
    await api("/api/gdrive/disconnect", { method: "POST" });
    await loadHealth();
    openDriveModal();
  });
  document.getElementById("drive-folder-select")?.addEventListener("change", (e) => {
    if (e.target.value) document.getElementById("drive-folder-id-input").value = e.target.value;
  });
  document.getElementById("btn-save-drive-folder")?.addEventListener("click", async () => {
    const msgEl = document.getElementById("drive-modal-msg");
    const fid = document.getElementById("drive-folder-id-input").value.trim();
    if (!fid) return showFeedback(msgEl, "Enter or select a destination folder ID first.", "err");
    try {
      const res = await api("/api/gdrive/select-folder", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ folder_id: fid }),
      });
      showFeedback(msgEl, `Selected '${res.selected.folder_name}' and verified Vault subfolder tree.`, "ok");
      await loadHealth();
    } catch (e) {
      showFeedback(msgEl, e.message, "err");
    }
  });
  document.getElementById("btn-rebuild-from-drive")?.addEventListener("click", async () => {
    const msgEl = document.getElementById("drive-modal-msg");
    try {
      const res = await api("/api/catalogue/reindex?include_drive=true", { method: "POST" });
      showFeedback(msgEl, `Reindexed ${res.drive?.reindexed || 0} asset(s) from Google Drive manifests.`, "ok");
      await loadAssets();
    } catch (e) {
      showFeedback(msgEl, e.message, "err");
    }
  });
}

// ---------------------------------------------------------------------------
// Three.js Interactive GLB Viewer
// ---------------------------------------------------------------------------
function initThreeViewer() {
  if (viewer.renderer) return;
  const container = document.getElementById("three-container");
  const w = container.clientWidth || 560;
  const h = container.clientHeight || 460;

  viewer.scene = new THREE.Scene();
  viewer.scene.background = new THREE.Color(0x161920);
  viewer.camera = new THREE.PerspectiveCamera(45, w / h, 0.01, 200);
  viewer.camera.position.copy(viewer.defaultCamPos);

  viewer.renderer = new THREE.WebGLRenderer({ antialias: true });
  viewer.renderer.setSize(w, h);
  viewer.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  viewer.renderer.outputColorSpace = THREE.SRGBColorSpace;
  container.appendChild(viewer.renderer.domElement);

  viewer.controls = new OrbitControls(viewer.camera, viewer.renderer.domElement);
  viewer.controls.enableDamping = true;

  const hemi = new THREE.HemisphereLight(0xfff8ee, 0x2f3545, 1.3);
  const key = new THREE.DirectionalLight(0xffffff, 2.2);
  key.position.set(3, 5, 4);
  const rim = new THREE.DirectionalLight(0xb8d0ff, 0.9);
  rim.position.set(-4, 2, -3);
  viewer.scene.add(hemi, key, rim, new THREE.GridHelper(4, 16, 0x3a4152, 0x252a35));

  const ro = new ResizeObserver(() => {
    const nw = container.clientWidth;
    const nh = container.clientHeight;
    if (nw > 0 && nh > 0) {
      viewer.camera.aspect = nw / nh;
      viewer.camera.updateProjectionMatrix();
      viewer.renderer.setSize(nw, nh);
    }
  });
  ro.observe(container);

  function animate() {
    viewer.animId = requestAnimationFrame(animate);
    viewer.controls.autoRotate = viewer.autoRotate;
    viewer.controls.update();
    viewer.renderer.render(viewer.scene, viewer.camera);
  }
  animate();
}

function clearThreeModel(statusText = "") {
  initThreeViewer();
  if (viewer.modelRoot) {
    viewer.scene.remove(viewer.modelRoot);
    viewer.modelRoot = null;
  }
  document.getElementById("three-status").textContent = statusText;
}

function loadThreeModel(url) {
  clearThreeModel("Loading GLB...");
  const loader = new GLTFLoader();
  loader.load(
    url,
    (gltf) => {
      const root = gltf.scene;
      viewer.modelRoot = root;
      viewer.scene.add(root);

      const box = new THREE.Box3().setFromObject(root);
      const size = box.getSize(new THREE.Vector3());
      const center = box.getCenter(new THREE.Vector3());
      const maxDim = Math.max(size.x, size.y, size.z, 0.25);

      viewer.defaultTarget.copy(center);
      viewer.defaultCamPos.set(center.x + maxDim * 1.4, center.y + maxDim * 0.9, center.z + maxDim * 1.6);
      viewer.camera.position.copy(viewer.defaultCamPos);
      viewer.controls.target.copy(viewer.defaultTarget);
      viewer.controls.update();
      applyWireframeState();
      document.getElementById("three-status").textContent = "Drag to orbit · Scroll to zoom";
    },
    undefined,
    (err) => {
      document.getElementById("three-status").textContent = `3D preview error: ${err?.message || err}`;
    }
  );
}

function applyWireframeState() {
  if (!viewer.modelRoot) return;
  viewer.modelRoot.traverse((obj) => {
    if (obj.isMesh && obj.material) {
      const mats = Array.isArray(obj.material) ? obj.material : [obj.material];
      mats.forEach((m) => { m.wireframe = viewer.wireframe; });
    }
  });
}

// ---------------------------------------------------------------------------
// Helpers & Event Listeners
// ---------------------------------------------------------------------------
function showFeedback(el, msg, kind = "info") {
  el.textContent = msg;
  el.className = `feedback ${kind}`;
}

function bindTabs(selector, panelPrefix) {
  document.querySelectorAll(selector).forEach((btn) => {
    btn.addEventListener("click", () => {
      const key = btn.dataset.vtab || btn.dataset.itab;
      document.querySelectorAll(selector).forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      document.querySelectorAll(`[id^="${panelPrefix}"]`).forEach((p) => p.classList.remove("active"));
      document.getElementById(`${panelPrefix}${key}`).classList.add("active");
    });
  });
}

document.addEventListener("DOMContentLoaded", async () => {
  bindTabs(".vtab", "vpanel-");
  bindTabs(".itab", "ipanel-");

  document.querySelectorAll("[data-close]").forEach((btn) => {
    btn.addEventListener("click", () => document.getElementById(btn.dataset.close).close());
  });

  document.getElementById("search-input").addEventListener("input", (e) => {
    state.search = e.target.value;
    loadAssets();
  });
  document.getElementById("status-filter").addEventListener("change", (e) => {
    state.activeStatus = e.target.value;
    loadAssets();
  });

  document.getElementById("btn-open-generate").addEventListener("click", () => document.getElementById("generate-modal").showModal());
  document.getElementById("btn-open-import").addEventListener("click", () => document.getElementById("import-modal").showModal());
  document.getElementById("btn-open-drive").addEventListener("click", openDriveModal);
  document.getElementById("btn-reindex").addEventListener("click", async () => {
    await api("/api/catalogue/reindex", { method: "POST" });
    await loadHealth();
    await loadAssets();
  });

  document.querySelectorAll(".chip[data-prompt]").forEach((chip) => {
    chip.addEventListener("click", () => {
      document.getElementById("gen-prompt").value = chip.dataset.prompt;
      document.getElementById("gen-name").value = chip.dataset.name;
      document.getElementById("gen-category").value = chip.dataset.cat;
    });
  });

  document.getElementById("generate-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const fb = document.getElementById("gen-feedback");
    const btn = document.getElementById("btn-submit-generate");
    btn.disabled = true;
    showFeedback(fb, "Generating 3D geometry, running Technical QA, and rendering 9 inspection views in Blender...", "info");
    try {
      const tags = document.getElementById("gen-tags").value.split(",").map((s) => s.trim()).filter(Boolean);
      const asset = await api("/api/assets/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          prompt: document.getElementById("gen-prompt").value,
          name: document.getElementById("gen-name").value || null,
          category: document.getElementById("gen-category").value || null,
          geometry_profile: document.getElementById("gen-profile").value || null,
          tags,
        }),
      });
      document.getElementById("generate-modal").close();
      fb.classList.add("hidden");
      await loadHealth();
      await loadAssets();
      await openAssetDetail(asset.asset_id);
    } catch (err) {
      showFeedback(fb, err.message, "err");
    } finally {
      btn.disabled = false;
    }
  });

  document.getElementById("import-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const fb = document.getElementById("imp-feedback");
    const btn = document.getElementById("btn-submit-import");
    const fileInput = document.getElementById("imp-file");
    if (!fileInput.files.length) return showFeedback(fb, "Select a 3D model file.", "err");

    btn.disabled = true;
    showFeedback(fb, "Preserving original file, importing via Blender, running Technical QA & multi-angle renders...", "info");
    try {
      const form = new FormData();
      form.append("file", fileInput.files[0]);
      if (document.getElementById("imp-name").value) form.append("name", document.getElementById("imp-name").value);
      form.append("description", document.getElementById("imp-description").value);
      form.append("category", document.getElementById("imp-category").value);
      if (document.getElementById("imp-profile").value) form.append("geometry_profile", document.getElementById("imp-profile").value);
      form.append("tags", document.getElementById("imp-tags").value);
      form.append("provenance_type", document.getElementById("imp-provenance-type").value);
      if (document.getElementById("imp-author").value) form.append("author", document.getElementById("imp-author").value);
      if (document.getElementById("imp-source-url").value) form.append("source_url", document.getElementById("imp-source-url").value);
      if (document.getElementById("imp-license").value) form.append("license_name", document.getElementById("imp-license").value);
      if (document.getElementById("imp-restrictions").value) form.append("usage_restrictions", document.getElementById("imp-restrictions").value);
      form.append("allow_duplicate", document.getElementById("imp-allow-dup").checked ? "true" : "false");

      const asset = await api("/api/assets/import", { method: "POST", body: form });
      document.getElementById("import-modal").close();
      fb.classList.add("hidden");
      await loadHealth();
      await loadAssets();
      await openAssetDetail(asset.asset_id);
    } catch (err) {
      showFeedback(fb, err.message, "err");
    } finally {
      btn.disabled = false;
    }
  });

  document.getElementById("btn-toggle-wire").addEventListener("click", () => {
    viewer.wireframe = !viewer.wireframe;
    applyWireframeState();
  });
  document.getElementById("btn-toggle-spin").addEventListener("click", () => {
    viewer.autoRotate = !viewer.autoRotate;
  });
  document.getElementById("btn-reset-cam").addEventListener("click", () => {
    viewer.camera.position.copy(viewer.defaultCamPos);
    viewer.controls.target.copy(viewer.defaultTarget);
    viewer.controls.update();
  });

  const refDashboard = createReferenceDashboard({
    api,
    escapeHtml,
    badge,
    showFeedback,
    getBranch1Assets: () => state.assets || [],
  });
  refDashboard.initEvents();

  await loadHealth();
  await loadAssets();
});
