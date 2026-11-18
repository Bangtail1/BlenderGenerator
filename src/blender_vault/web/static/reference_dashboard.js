/* Branch 2: BlendSwap Reference Intelligence Engine Dashboard (Sections A-F) */

export function createReferenceDashboard({ api, escapeHtml, badge, showFeedback, getBranch1Assets }) {
  const refState = {
    dashboard: null,
    assets: [],
    search: "",
    primaryCategory: "All",
    licenseKey: "All",
    gradeLetter: "All",
    sortBy: "updated_at",
    activeTab: "overview",
  };

  function gradeChip(letter) {
    const g = escapeHtml(letter || "—");
    return `<span class="grade-chip grade-${g}">${g}</span>`;
  }

  async function loadDashboard({ checkLive = false } = {}) {
    refState.dashboard = await api(`/api/reference/dashboard?check_live=${checkLive ? "true" : "false"}`);
    renderOverview(refState.dashboard.collection_overview);
    renderPopularity(refState.dashboard.popularity_analysis);
    renderQuality(refState.dashboard.quality_intelligence);
    renderBenchmarks(refState.dashboard.benchmark_results);
    renderAttribution(refState.dashboard.attribution_and_licensing);
    await loadReferenceAssets();
  }

  async function loadReferenceAssets() {
    const p = new URLSearchParams();
    if (refState.search.trim()) p.set("search", refState.search.trim());
    if (refState.primaryCategory !== "All") p.set("primary_category", refState.primaryCategory);
    if (refState.licenseKey !== "All") p.set("license_key", refState.licenseKey);
    if (refState.gradeLetter !== "All") p.set("grade_letter", refState.gradeLetter);
    p.set("sort_by", refState.sortBy);
    const res = await api(`/api/reference/assets?${p.toString()}`);
    refState.assets = res.assets || [];
    renderLibrary(refState.dashboard?.reference_library || {}, refState.assets);
  }

  // ---------------------------------------------------------------------------
  // A. Collection Overview
  // ---------------------------------------------------------------------------
  function renderOverview(ov) {
    if (!ov) return;
    const conn = ov.api_connectivity || {};
    const sched = ov.schedule || {};
    const latest = ov.latest_cycle || null;
    const cycles = ov.recent_cycles || [];
    const notifications = ov.notifications || [];
    const winTask = ov.windows_task_scheduler || {};
    const apiOk = conn.connected === true;
    const connBadge = conn.configured
      ? (apiOk ? badge("Connected", "VERIFIED") : badge(conn.connected === false ? "Disconnected" : "Configured (Check Live)", "PENDING"))
      : badge("BLENDSWAP_KEY Unset", "FAIL");
    const schedState = sched.scheduler_state || (sched.enabled ? "SCHEDULED" : "PAUSED");
    const schedBadgeTone = schedState === "PAUSED" ? "PENDING" : (schedState.startsWith("POSTPONED") ? "PENDING" : "PASS");

    const cycleRows = cycles
      .map(
        (c) => `<tr>
          <td>${escapeHtml(c.cycle_date)}</td>
          <td>${badge(c.status, c.status === "COMPLETED" ? "PASS" : (c.status === "FAILED" ? "FAIL" : "PENDING"))}</td>
          <td>${c.downloaded_count}/${c.target_count}</td>
          <td>${c.failed_count}</td>
          <td>${c.credits_spent}</td>
          <td class="small muted">${escapeHtml(c.shortage_reason || c.error_message || c.trigger_type)}</td>
        </tr>`
      )
      .join("");

    const notifRows = notifications
      .slice(0, 8)
      .map((n) => {
        const assetSummary = (n.assets_selected || [])
          .map((a) => `#${a.blendswap_id} ${a.title} (${a.status})`)
          .join(", ");
        const errText = (n.errors || []).join("; ");
        return `<tr>
          <td>${badge(n.status, n.status === "COMPLETED" ? "PASS" : (n.status.includes("FAIL") || n.status.includes("UNAVAILABLE") ? "FAIL" : "PENDING"))}</td>
          <td class="small">${escapeHtml((n.scheduled_time || "—").replace("T", " ").slice(0, 19))}</td>
          <td class="small">${escapeHtml((n.started_at || "—").replace("T", " ").slice(0, 19))} → ${escapeHtml((n.completed_at || "—").replace("T", " ").slice(0, 19))}</td>
          <td>${n.downloaded_count} / ${n.failed_count} failed</td>
          <td>${n.free_allowance_remaining ?? "—"}</td>
          <td class="small muted">${escapeHtml(assetSummary || n.message || "—")}${errText ? `<br/><span style="color:#f87171">${escapeHtml(errText)}</span>` : ""}</td>
          <td class="small">${escapeHtml((n.next_scheduled_run || "—").replace("T", " ").slice(0, 19))}</td>
        </tr>`;
      })
      .join("");

    document.getElementById("refpanel-overview").innerHTML = `
      <div class="kpi-grid">
        <div class="kpi-card">
          <span class="kpi-label">Today's Progress (${escapeHtml(ov.today_date)})</span>
          <span class="kpi-val">${ov.today_downloaded} / ${ov.daily_target}</span>
          <span class="kpi-sub">${ov.today_failed} failed today · ${ov.total_successful_downloads} total downloaded</span>
        </div>
        <div class="kpi-card">
          <span class="kpi-label">Free Downloads Remaining</span>
          <span class="kpi-val">${ov.free_downloads_remaining ?? "?"} / ${ov.free_downloads_limit ?? 7}</span>
          <span class="kpi-sub">Shared with blendswap.com · Credits: ${ov.credits ?? "?"} (Auto-spend: ${ov.allow_credit_spend ? "ON" : "OFF"})</span>
        </div>
        <div class="kpi-card">
          <span class="kpi-label">API Connectivity</span>
          <span class="kpi-val" style="font-size:1.05rem">${connBadge}</span>
          <span class="kpi-sub">${escapeHtml(conn.error || `API calls logged: ${ov.api_usage?.total_calls || 0}`)}</span>
        </div>
        <div class="kpi-card">
          <span class="kpi-label">Next Scheduled Run</span>
          <span class="kpi-val" style="font-size:1.0rem">${escapeHtml((ov.next_collection_cycle || "").replace("T", " ").slice(0, 19))}</span>
          <span class="kpi-sub">${badge(schedState, schedBadgeTone)} ${escapeHtml(sched.timezone)} @ ${escapeHtml(sched.collection_time || sched.schedule_time)}</span>
        </div>
      </div>

      <div class="two-col">
        <div class="panel-card">
          <h3>Collection Controls &amp; Recovery</h3>
          <p class="small muted">Runs discovery (<code>sort=downloads</code>), diversity selection, free allowance verification, Blender inspection, 100-pt grading, and Drive backup without spending credits.</p>
          <div class="action-row">
            <button class="btn btn-primary" id="btn-ref-check-live">Check Live BlendSwap API</button>
            <button class="btn btn-secondary" id="btn-ref-run-cycle">Run Collection Now</button>
            <button class="btn btn-ghost" id="btn-ref-sync-drive">Sync Pending to Drive</button>
            <button class="btn btn-ghost" id="btn-ref-reindex">Re-index Reference JSONs</button>
          </div>
          <div id="ref-cycle-msg" class="feedback hidden"></div>
          ${latest ? `<div class="small muted">Latest Cycle: <strong>${escapeHtml(latest.cycle_id)}</strong> — ${escapeHtml(latest.status)} (${latest.downloaded_count} downloaded, ${latest.shortage_count} shortage)</div>` : ""}
        </div>

        <div class="panel-card">
          <h3>Scheduler Settings &amp; Reset Detection</h3>
          <div class="form-row">
            <label><span>Daily Time (24h)</span><input type="text" id="sched-time-input" value="${escapeHtml(sched.collection_time || sched.schedule_time || "03:00")}" /></label>
            <label><span>Timezone</span><input type="text" id="sched-tz-input" value="${escapeHtml(sched.timezone || "Europe/London")}" /></label>
            <label><span>Daily Target</span><input type="number" min="1" max="50" id="sched-target-input" value="${sched.target_downloads ?? sched.daily_target ?? 7}" /></label>
          </div>
          <div class="form-row">
            <label class="checkbox-inline">
              <input type="checkbox" id="sched-enabled-input" ${sched.enabled ? "checked" : ""} />
              <span>Enable automatic collection</span>
            </label>
            <label class="checkbox-inline">
              <input type="checkbox" id="sched-resume-missed-input" ${sched.resume_missed_jobs !== false ? "checked" : ""} />
              <span>Resume missed runs on startup</span>
            </label>
          </div>
          <div class="action-row">
            <button class="btn btn-secondary" id="btn-save-sched">Save Collection Time</button>
            <button class="btn btn-ghost" id="btn-pause-sched" ${!sched.enabled ? "disabled" : ""}>Pause Automatic Collection</button>
            <button class="btn btn-ghost" id="btn-resume-sched" ${sched.enabled ? "disabled" : ""}>Resume Automatic Collection</button>
          </div>
          <div id="ref-sched-msg" class="feedback hidden"></div>
          <div class="small muted">
            State: <strong>${escapeHtml(schedState)}</strong> · Next Daily: <code>${escapeHtml((sched.next_daily_run_at || "").replace("T", " ").slice(0, 19))}</code>
            ${sched.next_recheck_at ? ` · Reset Recheck: <code>${escapeHtml(sched.next_recheck_at.replace("T", " ").slice(0, 19))}</code>` : ""}
            ${sched.allowance_reset_detected_at ? ` · Last Reset Detected: <code>${escapeHtml(sched.allowance_reset_detected_at.replace("T", " ").slice(0, 19))}</code>` : ""}
            ${sched.postponed_reason ? `<br/>Postponed: ${escapeHtml(sched.postponed_reason)}` : ""}
          </div>
        </div>
      </div>

      <div class="panel-card">
        <h3>Persistent OS Scheduling (Windows Task Scheduler)</h3>
        <div class="small muted">
          Status: ${winTask.installed ? badge(winTask.status || "Ready", "PASS") : badge(winTask.status || "Not Installed", "PENDING")}
          · Task: <code>${escapeHtml(winTask.task_name || "BlenderAssetVault-ReferenceDailyCollection")}</code>
          ${winTask.next_run_time ? ` · Windows Next Run: <strong>${escapeHtml(winTask.next_run_time)}</strong>` : ""}
          <br/>Command: <code>${escapeHtml(winTask.command || "python -m blender_vault.cli collect-daily --scheduled")}</code>
          <br/>Runs locally on Windows even when Antigravity IDE is closed. Does not execute while the PC is powered off; missed 03:00 windows and interrupted queues resume automatically when Windows starts.
        </div>
        <div class="action-row" style="margin-top:8px">
          <button class="btn btn-secondary" id="btn-win-task-install" ${winTask.supported_os === false ? "disabled" : ""}>Install / Update Windows Task</button>
          <button class="btn btn-ghost" id="btn-win-task-uninstall" ${winTask.supported_os === false ? "disabled" : ""}>Uninstall Windows Task</button>
        </div>
        <div id="ref-wintask-msg" class="feedback hidden"></div>
      </div>

      <div class="panel-card">
        <h3>Scheduler Notifications &amp; Telemetry (${notifications.length})</h3>
        ${
          notifications.length
            ? `<table class="qa-table"><thead><tr><th>Status</th><th>Scheduled Time</th><th>Actual Start → Completed</th><th>Downloads / Failed</th><th>Allowance Left</th><th>Selected Assets &amp; Errors</th><th>Next Scheduled</th></tr></thead><tbody>${notifRows}</tbody></table>`
            : `<p class="small muted">No scheduler events logged yet.</p>`
        }
      </div>

      <div class="panel-card">
        <h3>Collection Cycle History (${cycles.length})</h3>
        ${
          cycles.length
            ? `<table class="qa-table"><thead><tr><th>Date</th><th>Status</th><th>Downloaded</th><th>Failed</th><th>Credits Spent</th><th>Notes</th></tr></thead><tbody>${cycleRows}</tbody></table>`
            : `<p class="small muted">No collection cycles executed yet. Add <code>BLENDSWAP_KEY</code> to <code>.env</code> and click <strong>Run Collection Now</strong>.</p>`
        }
      </div>
    `;

    document.getElementById("btn-ref-check-live").addEventListener("click", async () => {
      await loadDashboard({ checkLive: true });
    });
    document.getElementById("btn-ref-run-cycle").addEventListener("click", async () => {
      const msg = document.getElementById("ref-cycle-msg");
      showFeedback(msg, "Running BlendSwap discovery, allowance verification, download & Blender inspection...", "info");
      try {
        const res = await api("/api/reference/cycles/run", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
        showFeedback(msg, `Cycle ${res.cycle_id || ""} finished: ${res.status} (${res.downloaded_count ?? 0} downloaded).`, res.status === "FAILED" ? "err" : "ok");
        await loadDashboard({ checkLive: true });
      } catch (e) {
        showFeedback(msg, e.message, "err");
      }
    });
    document.getElementById("btn-ref-sync-drive").addEventListener("click", async () => {
      const msg = document.getElementById("ref-cycle-msg");
      try {
        const res = await api("/api/reference/drive/sync-pending", { method: "POST" });
        showFeedback(msg, `Drive sync attempted=${res.attempted}, synced=${res.synced}, failed=${res.failed.length}.`, "ok");
        await loadDashboard();
      } catch (e) {
        showFeedback(msg, e.message, "err");
      }
    });
    document.getElementById("btn-ref-reindex").addEventListener("click", async () => {
      const msg = document.getElementById("ref-cycle-msg");
      const res = await api("/api/reference/reindex", { method: "POST" });
      showFeedback(msg, `Rebuilt ${res.restored}/${res.discovered} reference records from reference_library/.`, "ok");
      await loadDashboard();
    });
    document.getElementById("btn-save-sched").addEventListener("click", async () => {
      const msg = document.getElementById("ref-sched-msg");
      try {
        await api("/api/reference/scheduler", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            collection_time: document.getElementById("sched-time-input").value.trim(),
            timezone: document.getElementById("sched-tz-input").value.trim(),
            target_downloads: Number(document.getElementById("sched-target-input").value || 7),
            enabled: document.getElementById("sched-enabled-input").checked,
            resume_missed_jobs: document.getElementById("sched-resume-missed-input").checked,
          }),
        });
        showFeedback(msg, "Daily scheduler settings saved.", "ok");
        await loadDashboard();
      } catch (e) {
        showFeedback(msg, e.message, "err");
      }
    });
    document.getElementById("btn-pause-sched").addEventListener("click", async () => {
      const msg = document.getElementById("ref-sched-msg");
      try {
        await api("/api/reference/scheduler/pause", { method: "POST" });
        showFeedback(msg, "Automatic daily collection paused.", "ok");
        await loadDashboard();
      } catch (e) {
        showFeedback(msg, e.message, "err");
      }
    });
    document.getElementById("btn-resume-sched").addEventListener("click", async () => {
      const msg = document.getElementById("ref-sched-msg");
      try {
        await api("/api/reference/scheduler/resume", { method: "POST" });
        showFeedback(msg, "Automatic daily collection resumed.", "ok");
        await loadDashboard();
      } catch (e) {
        showFeedback(msg, e.message, "err");
      }
    });
    document.getElementById("btn-win-task-install").addEventListener("click", async () => {
      const msg = document.getElementById("ref-wintask-msg");
      try {
        const res = await api("/api/reference/scheduler/windows-task/install", { method: "POST" });
        showFeedback(msg, res.message || "Windows Task updated.", res.installed ? "ok" : "err");
        await loadDashboard();
      } catch (e) {
        showFeedback(msg, e.message, "err");
      }
    });
    document.getElementById("btn-win-task-uninstall").addEventListener("click", async () => {
      const msg = document.getElementById("ref-wintask-msg");
      try {
        const res = await api("/api/reference/scheduler/windows-task/uninstall", { method: "POST" });
        showFeedback(msg, res.message || "Windows Task removed.", res.uninstalled ? "ok" : "err");
        await loadDashboard();
      } catch (e) {
        showFeedback(msg, e.message, "err");
      }
    });
  }

  // ---------------------------------------------------------------------------
  // B. Reference Library
  // ---------------------------------------------------------------------------
  function renderLibrary(lib, assets) {
    const cats = ["All", ...Object.keys(lib.categories || {})];
    const lics = ["All", "cc0", "cc_by", "cc_by_sa", "cc_by_nd", "cc_by_nc", "cc_by_nc_sa", "cc_by_nc_nd", "gal"];
    const grades = ["All", "A+", "A", "B", "C", "D", "F"];
    const distSummary = (obj) => Object.entries(obj || {}).map(([k, v]) => `${escapeHtml(k)}: <strong>${v}</strong>`).join(" · ") || "None";

    const cards = assets.length
      ? assets
          .map((a) => {
            const firstPrev = a.preview_paths?.[0];
            const prevName = firstPrev ? firstPrev.split(/[/\\]/).pop() : "";
            const thumb = prevName ? `/api/reference/assets/${a.blendswap_id}/preview/${encodeURIComponent(prevName)}` : "";
            return `
            <article class="asset-card ref-card" data-refid="${a.blendswap_id}">
              <div class="card-thumb">
                ${thumb ? `<img src="${thumb}" alt="${escapeHtml(a.title)}" loading="lazy" />` : `<span class="muted small">No Preview Image</span>`}
                <span class="card-version-chip">#${a.blendswap_id}</span>
              </div>
              <div class="card-body">
                <div class="card-title-row">
                  <h3 class="card-title">${escapeHtml(a.title)}</h3>
                  ${gradeChip(a.grade_letter)}
                </div>
                <div class="card-meta">${escapeHtml(a.primary_category)} · ${a.counts?.downloads || 0} dls · Tech ${a.technical_quality_score ?? "—"}/100</div>
                <div class="card-meta small">By ${escapeHtml(a.author?.username)} · ${escapeHtml(a.license_assessment?.license_name)} (${escapeHtml(a.license_assessment?.primary_class)})</div>
                <div class="card-badges">
                  ${badge(a.is_native_blender ? "Native .blend" : a.primary_format.toUpperCase(), a.is_native_blender ? "PASS" : "PENDING")}
                  ${(a.technical_categories || []).slice(0, 3).map((t) => badge(t, "PROCESSING")).join("")}
                  ${badge(`Drive: ${a.drive_sync_status}`, a.drive_sync_status)}
                </div>
                <div class="card-actions">
                  <button type="button" class="btn btn-secondary btn-xs ref-open-btn" data-refid="${a.blendswap_id}">Inspect Details</button>
                </div>
              </div>
            </article>`;
          })
          .join("")
      : `<div class="empty-state">No reference assets match these filters yet.</div>`;

    const panel = document.getElementById("refpanel-library");
    if (!panel.querySelector("#ref-library-grid")) {
      panel.innerHTML = `
        <div id="ref-library-distributions" class="two-col"></div>

        <section class="toolbar">
          <div class="search-wrap"><input type="search" id="ref-search-input" placeholder="Search reference assets by title, ID, author, tags..." value="${escapeHtml(refState.search)}" /></div>
          <div><select id="ref-cat-filter">${cats.map((c) => `<option value="${escapeHtml(c)}" ${c === refState.primaryCategory ? "selected" : ""}>Cat: ${escapeHtml(c)}</option>`).join("")}</select></div>
          <div><select id="ref-lic-filter">${lics.map((l) => `<option value="${escapeHtml(l)}" ${l === refState.licenseKey ? "selected" : ""}>Lic: ${escapeHtml(l)}</option>`).join("")}</select></div>
          <div><select id="ref-grade-filter">${grades.map((g) => `<option value="${escapeHtml(g)}" ${g === refState.gradeLetter ? "selected" : ""}>Grade: ${escapeHtml(g)}</option>`).join("")}</select></div>
          <div><select id="ref-sort-select">
            <option value="updated_at" ${refState.sortBy === "updated_at" ? "selected" : ""}>Sort: Recent</option>
            <option value="technical_score" ${refState.sortBy === "technical_score" ? "selected" : ""}>Sort: Highest Grade</option>
            <option value="downloads" ${refState.sortBy === "downloads" ? "selected" : ""}>Sort: Most Downloaded</option>
            <option value="usefulness" ${refState.sortBy === "usefulness" ? "selected" : ""}>Sort: Most Useful</option>
          </select></div>
        </section>

        <section id="ref-library-grid" class="asset-grid"></section>
      `;

      document.getElementById("ref-search-input").addEventListener("input", (e) => {
        refState.search = e.target.value;
        loadReferenceAssets();
      });
      document.getElementById("ref-cat-filter").addEventListener("change", (e) => {
        refState.primaryCategory = e.target.value;
        loadReferenceAssets();
      });
      document.getElementById("ref-lic-filter").addEventListener("change", (e) => {
        refState.licenseKey = e.target.value;
        loadReferenceAssets();
      });
      document.getElementById("ref-grade-filter").addEventListener("change", (e) => {
        refState.gradeLetter = e.target.value;
        loadReferenceAssets();
      });
      document.getElementById("ref-sort-select").addEventListener("change", (e) => {
        refState.sortBy = e.target.value;
        loadReferenceAssets();
      });
    } else {
      const catSelect = document.getElementById("ref-cat-filter");
      const currentCats = Array.from(catSelect.options).map((o) => o.value);
      if (currentCats.length !== cats.length || currentCats.some((val, idx) => val !== cats[idx])) {
        catSelect.innerHTML = cats
          .map((c) => `<option value="${escapeHtml(c)}" ${c === refState.primaryCategory ? "selected" : ""}>Cat: ${escapeHtml(c)}</option>`)
          .join("");
      }
    }

    document.getElementById("ref-library-distributions").innerHTML = `
      <div class="panel-card">
        <h3>Library Distributions (${lib.total_assets || 0} Collected / ${lib.total_catalog_records || 0} Total)</h3>
        <div class="small"><strong>Categories:</strong> ${distSummary(lib.categories)}</div>
        <div class="small"><strong>Licenses:</strong> ${distSummary(lib.license_distribution)}</div>
      </div>
      <div class="panel-card">
        <h3>Quality &amp; Blender Version Distributions</h3>
        <div class="small"><strong>Grades:</strong> ${distSummary(lib.quality_distribution)}</div>
        <div class="small"><strong>Blender Versions:</strong> ${distSummary(lib.blender_version_distribution)}</div>
      </div>
    `;

    const grid = document.getElementById("ref-library-grid");
    grid.innerHTML = cards;
    grid.querySelectorAll(".ref-open-btn").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        openReferenceDetail(Number(btn.dataset.refid));
      });
    });
    grid.querySelectorAll(".ref-card").forEach((card) => {
      card.addEventListener("click", (e) => {
        if (!e.target.closest(".ref-open-btn")) {
          openReferenceDetail(Number(card.dataset.refid));
        }
      });
    });
  }

  async function openReferenceDetail(blendswapId) {
    const rec = await api(`/api/reference/assets/${blendswapId}`);
    const modal = document.getElementById("ref-detail-modal");
    document.getElementById("ref-detail-title").textContent = `${rec.title} (BlendSwap #${rec.blendswap_id})`;
    document.getElementById("ref-detail-subtitle").textContent = `${rec.primary_category} · Author: ${rec.author?.username} · License: ${rec.license_assessment?.license_name} (${rec.license_assessment?.license_source})`;

    const insp = rec.inspection || {};
    const grade = rec.grade || {};
    const dimBars = (grade.dimensions || [])
      .map(
        (d) => `<div class="score-bar-row">
          <div class="score-bar-head"><strong>${escapeHtml(d.dimension)}</strong><span>${d.score} / ${d.max_score} (${d.percentage}%)</span></div>
          <div class="score-bar-track"><div class="score-bar-fill" style="width:${Math.min(100, d.percentage)}%"></div></div>
          <div class="small muted">${escapeHtml((d.findings || []).join(" "))}</div>
        </div>`
      )
      .join("");

    document.getElementById("ref-detail-body").innerHTML = `
      <div class="feedback info">
        <strong>License Notice (Class ${escapeHtml(rec.license_assessment?.primary_class)}):</strong>
        ${escapeHtml(rec.license_assessment?.verification_disclaimer)}
        ${(rec.license_assessment?.review_warnings || []).map((w) => `<div>• ${escapeHtml(w)}</div>`).join("")}
      </div>

      <div class="two-col">
        <div class="panel-card">
          <h3>Quality Grading (${grade.total_score ?? "N/A"}/100 · ${gradeChip(grade.grade_letter)})</h3>
          <div class="kv-grid">
            <div class="kv-item"><strong>Grading Profile</strong>${escapeHtml(grade.grading_profile || "N/A")} (v${escapeHtml(grade.methodology_version || "2.0.0")})</div>
            <div class="kv-item"><strong>Usefulness / Popularity</strong>${rec.reference_usefulness_score ?? "—"} / ${rec.popularity_score ?? "—"}</div>
          </div>
          ${dimBars || `<p class="small muted">Not graded yet.</p>`}
        </div>

        <div class="panel-card">
          <h3>Blender Inspection Telemetry</h3>
          <div class="kv-grid">
            <div class="kv-item"><strong>Blender Header / Runtime</strong>${escapeHtml(insp.blend_header_version || "N/A")} / ${escapeHtml(insp.blender_runtime_version || "N/A")}</div>
            <div class="kv-item"><strong>Objects / Meshes / Components</strong>${insp.object_count ?? 0} / ${insp.mesh_object_count ?? 0} / ${insp.connected_components ?? 0}</div>
            <div class="kv-item"><strong>Tris / Quads / N-gons</strong>${insp.triangle_count ?? 0} tris (${((insp.quad_ratio || 0) * 100).toFixed(0)}% quads, ${((insp.ngon_ratio || 0) * 100).toFixed(1)}% ngons)</div>
            <div class="kv-item"><strong>Manifold / Non-Manifold Edges</strong>${insp.is_manifold ? "Watertight" : "Open/Non-manifold"} (${insp.multi_face_edges ?? 0} multi, ${insp.boundary_edges ?? 0} boundary)</div>
            <div class="kv-item"><strong>UV Layers / Coverage / Complete</strong>${insp.uv_layer_count ?? 0} / ${((insp.uv_coverage_ratio || 0) * 100).toFixed(0)}% / ${((insp.uv_completeness_ratio || 0) * 100).toFixed(0)}%</div>
            <div class="kv-item"><strong>Materials / Missing Textures</strong>${insp.material_count ?? 0} / ${insp.missing_image_textures ?? 0} missing</div>
            <div class="kv-item"><strong>Rig / Animations</strong>${insp.has_rig ? `${insp.bone_count} bones` : "None"} / ${insp.has_animations ? `${insp.action_count} actions` : "None"}</div>
            <div class="kv-item"><strong>Camera / Lights (Presentation)</strong>${insp.camera_count ?? 0} cam / ${insp.light_count ?? 0} light(s)</div>
          </div>
          <div class="action-row">
            <button class="btn btn-primary" id="btn-ref-open-blender" ${rec.blend_file_path ? "" : "disabled"}>Open in Blender</button>
            <button class="btn btn-secondary" id="btn-ref-reinspect">Re-run Blender Inspection</button>
            <button class="btn btn-ghost" id="btn-ref-sync-one">Sync to Drive</button>
            <a class="btn btn-ghost" href="${escapeHtml(rec.url)}" target="_blank">View on BlendSwap ↗</a>
          </div>
          <div id="ref-detail-msg" class="feedback hidden"></div>
        </div>
      </div>
    `;

    document.getElementById("btn-ref-open-blender").addEventListener("click", async () => {
      const msg = document.getElementById("ref-detail-msg");
      try {
        const res = await api(`/api/reference/assets/${blendswapId}/open-blender`, { method: "POST" });
        showFeedback(msg, `Opened in Blender GUI (PID ${res.pid}).`, "ok");
      } catch (e) {
        showFeedback(msg, e.message, "err");
      }
    });
    document.getElementById("btn-ref-reinspect").addEventListener("click", async () => {
      const msg = document.getElementById("ref-detail-msg");
      showFeedback(msg, "Running isolated Blender inspection & re-grading...", "info");
      await api(`/api/reference/assets/${blendswapId}/reinspect`, { method: "POST" });
      await openReferenceDetail(blendswapId);
      await loadDashboard();
    });
    document.getElementById("btn-ref-sync-one").addEventListener("click", async () => {
      const msg = document.getElementById("ref-detail-msg");
      try {
        await api(`/api/reference/assets/${blendswapId}/sync-drive`, { method: "POST" });
        showFeedback(msg, "Synced reference package to Google Drive.", "ok");
        await loadDashboard();
      } catch (e) {
        showFeedback(msg, e.message, "err");
      }
    });

    if (!modal.open) modal.showModal();
  }

  // ---------------------------------------------------------------------------
  // C. Popularity Analysis
  // ---------------------------------------------------------------------------
  function renderPopularity(pop) {
    if (!pop) return;
    const dlRows = (pop.most_downloaded || [])
      .map(
        (a) => `<tr>
          <td><strong>${escapeHtml(a.title)}</strong> (#${a.blendswap_id})</td>
          <td>${escapeHtml(a.primary_category)}</td>
          <td>${a.downloads.toLocaleString()}</td>
          <td>${a.likes}</td>
          <td>${gradeChip(a.grade_letter)} (${a.technical_quality_score ?? "—"})</td>
        </tr>`
      )
      .join("");
    const trendRows = (pop.historical_trends || [])
      .map((t) => `<tr><td>${escapeHtml(t.date)}</td><td>${t.downloaded}</td><td>${t.failed}</td><td>${t.shortage}</td></tr>`)
      .join("");

    document.getElementById("refpanel-popularity").innerHTML = `
      <div class="two-col">
        <div class="panel-card">
          <h3>Most Downloaded References (vs Technical Grade)</h3>
          <table class="qa-table">
            <thead><tr><th>Asset</th><th>Category</th><th>Downloads</th><th>Likes</th><th>Tech Grade</th></tr></thead>
            <tbody>${dlRows || `<tr><td colspan="5" class="muted">No reference assets collected yet.</td></tr>`}</tbody>
          </table>
        </div>
        <div class="panel-card">
          <h3>Historical Collection Trends</h3>
          <table class="qa-table">
            <thead><tr><th>Date</th><th>Downloaded</th><th>Failed</th><th>Shortage</th></tr></thead>
            <tbody>${trendRows || `<tr><td colspan="4" class="muted">No cycle history yet.</td></tr>`}</tbody>
          </table>
        </div>
      </div>
    `;
  }

  // ---------------------------------------------------------------------------
  // D. Quality Intelligence
  // ---------------------------------------------------------------------------
  function renderQuality(qual) {
    if (!qual) return;
    const listCard = (title, items, metricFn) => `
      <div class="panel-card">
        <h3>${escapeHtml(title)}</h3>
        <table class="qa-table">
          <thead><tr><th>Asset</th><th>Category</th><th>Grade</th><th>Metric</th></tr></thead>
          <tbody>
            ${
              (items || []).length
                ? items.map((a) => `<tr><td>${escapeHtml(a.title)} (#${a.blendswap_id})</td><td>${escapeHtml(a.primary_category)}</td><td>${gradeChip(a.grade_letter)}</td><td class="small">${escapeHtml(metricFn(a))}</td></tr>`).join("")
                : `<tr><td colspan="4" class="muted">No graded assets yet.</td></tr>`
            }
          </tbody>
        </table>
      </div>`;

    document.getElementById("refpanel-quality").innerHTML = `
      <div class="two-col">
        ${listCard("Highest-Rated Technical References", qual.highest_rated, (a) => `${a.technical_quality_score}/100`)}
        ${listCard("Best Topology Examples", qual.best_topology, (a) => `${(a.quad_ratio * 100).toFixed(0)}% quads · ${a.triangles.toLocaleString()} tris`)}
        ${listCard("Best UV Mapping Examples", qual.best_uv, (a) => `${(a.uv_coverage_ratio * 100).toFixed(0)}% coverage`)}
        ${listCard("Best Material Examples", qual.best_materials, (a) => `Grade ${a.grade_letter} (${a.technical_quality_score}/100)`)}
        ${listCard("Best-Organized Blender Scenes", qual.best_organized, (a) => `Org ${a.scene_organization_score}/100`)}
      </div>
    `;
  }

  // ---------------------------------------------------------------------------
  // E. Benchmark Results (Branch 1 <-> Branch 2)
  // ---------------------------------------------------------------------------
  function renderBenchmarks(bm) {
    if (!bm) return;
    const b1Assets = getBranch1Assets ? getBranch1Assets() : [];
    const opts = b1Assets.map((a) => `<option value="${escapeHtml(a.asset_id)}">${escapeHtml(a.name)} (${escapeHtml(a.category)})</option>`).join("");
    const reports = bm.reports || [];

    const cards = reports.length
      ? reports
          .map(
            (r) => `<div class="panel-card">
              <h3>${escapeHtml(r.branch1_name)} (v${r.branch1_version}) — ${gradeChip(r.local_grade?.grade_letter)} ${r.local_grade?.total_score}/100</h3>
              <div class="small muted">Profile: ${escapeHtml(r.grading_profile)} · Methodology v${escapeHtml(r.methodology_version)} · ${escapeHtml(r.efficiency_note)}</div>
              <div class="small"><strong>Selected References (${r.selected_references?.length || 0}):</strong> ${
                (r.selected_references || []).map((ref) => `#${ref.blendswap_id} ${escapeHtml(ref.title)} (${ref.reference_grade_letter})`).join(", ") || "None (evaluated independently)"
              }</div>
              ${r.geometry_differences?.length ? `<div class="small"><strong>Geometry Differences:</strong> ${r.geometry_differences.map(escapeHtml).join(" | ")}</div>` : ""}
              ${r.topology_weaknesses?.length ? `<div class="small"><strong>Topology Weaknesses:</strong> ${r.topology_weaknesses.map(escapeHtml).join(" | ")}</div>` : ""}
              ${r.uv_weaknesses?.length ? `<div class="small"><strong>UV Weaknesses:</strong> ${r.uv_weaknesses.map(escapeHtml).join(" | ")}</div>` : ""}
              ${r.material_weaknesses?.length ? `<div class="small"><strong>Material Weaknesses:</strong> ${r.material_weaknesses.map(escapeHtml).join(" | ")}</div>` : ""}
              ${r.organization_weaknesses?.length ? `<div class="small"><strong>Organization Weaknesses:</strong> ${r.organization_weaknesses.map(escapeHtml).join(" | ")}</div>` : ""}
              <div class="small"><strong>Recommended Improvements:</strong> ${(r.recommended_improvements || []).map(escapeHtml).join(" · ")}</div>
            </div>`
          )
          .join("")
      : `<div class="empty-state">No Branch 1 benchmark reports yet. Select a local Branch 1 asset above and click <strong>Run Benchmark</strong>.</div>`;

    document.getElementById("refpanel-benchmarks").innerHTML = `
      <div class="panel-card">
        <h3>Run Independent Benchmark on Branch 1 Generated Model</h3>
        <div class="form-row">
          <select id="bmk-asset-select">${opts || `<option value="">No Branch 1 assets found</option>`}</select>
          <input type="text" id="bmk-usage-input" placeholder="Intended usage (e.g. office furniture, game prop)" />
        </div>
        <div class="action-row">
          <button class="btn btn-primary" id="btn-run-bmk" ${b1Assets.length ? "" : "disabled"}>Run Benchmark vs Reference Library</button>
        </div>
        <div id="bmk-msg" class="feedback hidden"></div>
      </div>
      ${cards}
    `;

    document.getElementById("btn-run-bmk")?.addEventListener("click", async () => {
      const aid = document.getElementById("bmk-asset-select").value;
      const usage = document.getElementById("bmk-usage-input").value.trim();
      const msg = document.getElementById("bmk-msg");
      if (!aid) return;
      showFeedback(msg, "Inspecting Branch 1 model in Blender and comparing against Branch 2 references...", "info");
      try {
        const res = await api(`/api/reference/benchmarks/run/${encodeURIComponent(aid)}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ intended_usage: usage || null }),
        });
        showFeedback(msg, `Benchmark complete: ${res.branch1_name} scored ${res.local_grade.total_score}/100 (${res.local_grade.grade_letter}).`, "ok");
        await loadDashboard();
      } catch (e) {
        showFeedback(msg, e.message, "err");
      }
    });
  }

  // ---------------------------------------------------------------------------
  // F. Attribution & Licensing
  // ---------------------------------------------------------------------------
  function renderAttribution(attr) {
    if (!attr) return;
    const rows = (attr.items || [])
      .map(
        (i) => `<tr>
          <td><a href="${escapeHtml(i.asset_url)}" target="_blank">${escapeHtml(i.title)} (#${i.blendswap_id})</a></td>
          <td><a href="${escapeHtml(i.author_profile_url || i.asset_url)}" target="_blank">${escapeHtml(i.author_username)}</a></td>
          <td><strong>${escapeHtml(i.license_name)}</strong> (${escapeHtml(i.license_source)}, verified: ${i.legally_verified ? "YES" : "NO"})</td>
          <td>${(i.license_classes || []).join(", ")}</td>
          <td class="small">${i.requires_attribution ? "Attribution Required" : "No Attribution Required"}${i.requires_additional_review ? " · Review Needed" : ""}</td>
          <td class="small muted">${escapeHtml((i.review_warnings || []).join(" "))}</td>
        </tr>`
      )
      .join("");

    document.getElementById("refpanel-attribution").innerHTML = `
      <div class="feedback info">
        <strong>License Integrity Policy:</strong> BlendSwap licenses are <code>uploader_declared</code> and are never claimed to be legally verified. Reference assets are used solely for independent technical benchmarking; reference geometry/textures are never copied into generated models.
      </div>
      <div class="panel-card">
        <h3>Attribution &amp; License Registry (${attr.total_records || 0})</h3>
        <table class="qa-table">
          <thead><tr><th>Asset</th><th>Author</th><th>Declared License</th><th>Classes</th><th>Requirements</th><th>Warnings</th></tr></thead>
          <tbody>${rows || `<tr><td colspan="6" class="muted">No attribution records yet.</td></tr>`}</tbody>
        </table>
      </div>
    `;
  }

  function initEvents() {
    document.querySelectorAll(".branch-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const target = btn.dataset.branch;
        document.querySelectorAll(".branch-btn").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        document.querySelectorAll(".branch-view").forEach((v) => v.classList.remove("active"));
        document.getElementById(`view-${target}`).classList.add("active");
        if (target === "branch2") {
          loadDashboard();
        }
      });
    });

    document.querySelectorAll(".ref-tab").forEach((btn) => {
      btn.addEventListener("click", () => {
        const key = btn.dataset.reftab;
        refState.activeTab = key;
        document.querySelectorAll(".ref-tab").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        document.querySelectorAll(".refpanel").forEach((p) => p.classList.remove("active"));
        document.getElementById(`refpanel-${key}`).classList.add("active");
      });
    });
  }

  return { initEvents, loadDashboard, openReferenceDetail };
}
