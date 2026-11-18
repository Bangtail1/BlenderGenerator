# QA Pipeline Review — Chair Test & Production Readiness

**Reviewed Asset:** `FUR_00001_Wooden_Chair_Basic_LP_v001` ("Oak Dining Chair")  
**Database:** [data/roblox_asset_library.db](file:///C:/Users/azlan/Documents/Antigravity_Project/BlenderGenerator/data/roblox_asset_library.db) (`schema_version = 3`)  
**Review Date:** 2026-11-18  

---

## Executive Summary

Technical review of the SQLite asset library, Blender 5.3.0 QA engine, procedural generator, and Google Drive synchronization pipeline confirmed the core architecture works reliably while uncovering **five concrete production-readiness gaps** during Phase 1–5 inspection—all five were resolved and verified without modifying or overwriting the immutable `v001` baseline asset:

1. **Approved-Version Re-QA Status Drift (`workflow.py`, `database/models.py`):** Running QA again on already-approved `v001` (observed in live logs at `06:09:42Z`) transitioned `assets.production_status` from `APPROVED -> QA_PENDING -> QA_RUNNING` before `save_qa_report()` threw `ImmutableApprovedVersionError`, leaving `assets.production_status` stuck on `QA_PENDING`. Fixed with an up-front `if version.is_approved:` check before any status mutation and tightened `ALLOWED_STATUS_TRANSITIONS`.
2. **Test Environment Leakage Into Live DB (`config.py`):** `.env` defaults (`ROBLOX_DB_PATH`, `ROBLOX_REPORTS_DIR`, `ROBLOX_BACKUPS_DIR`, `ROBLOX_OUTPUT_DIR`) bled into tests instantiating `Settings(vault_data_dir=tmp_path / "vault")`. Updated `Settings` to isolate `resolved_roblox_*` inside `vault_data_dir` whenever `vault_data_dir` is overridden without explicit `roblox_*` kwargs or process env vars, and cleaned the leaked test rows.
3. **Triangle Inflation (`wooden_chair.py`):** `95.7%` (`15,408` of `16,092` tris) resulted from `bmesh.ops.bevel(bm, geom=list(bm.edges), ...)` beveling all interior grid edges rather than silhouette/crease edges. Added an opt-in optimization profile (`optimize_topology=True`, `clean_uv_pack=True`) and produced separate version **`v002`** at **`2,016` triangles** (**87.5% reduction**) scoring **`99.5 / 100` (Grade A, zero warnings)**.
4. **False Positive UV Overlap Penalty (`inspect_roblox_qa.py`, `uv_checks.py`):** Coarse AABB cell splatting and edge-sharing inside continuous islands misclassified procedural box-projected UVs (`assign_box_uv`), penalizing valid procedural assets. Replaced with 2D triangle rasterization, strict face-centroid self-overlap detection, and material-aware classification distinguishing intentional procedural/tiled UVs (`INFO`, 0 penalty) from unpacked overlap on image-textured assets (`WARN`).
5. **Missing Persistent Roblox Studio Verification Tracking (`003_roblox_studio_verifications.sql`, `studio_repo.py`, `router.py`, `cli.py`, `report_generator.py`):** Added migration `003`, SQLite table `roblox_studio_verifications`, explicit `PENDING` default per version, CLI/API recording (`roblox-studio-verify`, `POST /api/roblox/assets/{asset_id}/studio-verify`), and fixed per-version Drive links in reports so `v002` never inherits `v001`'s Drive file ID.

---

## Phase 1 — Initial Implementation Verification & Evidence

Read-only inspection of [roblox_asset_library.db](file:///C:/Users/azlan/Documents/Antigravity_Project/BlenderGenerator/data/roblox_asset_library.db), [vault_storage/logs/blender_vault.log](file:///C:/Users/azlan/Documents/Antigravity_Project/BlenderGenerator/vault_storage/logs/blender_vault.log), and `reports/FUR_00001_Wooden_Chair_Basic_LP_v001/v001/` verified the 8 audit criteria:

| Check | Status at Initial Review | Concrete Database / File Evidence |
|---|---|---|
| 1. Registered in SQLite | **PASS** | `assets.id = 1`, `asset_id = 'FUR_00001_Wooden_Chair_Basic_LP_v001'`, `asset_name = 'Oak Dining Chair'`, `category_id = 1` (`FUR`), `legacy_vault_id = '522b12bb84f3'`, `5` rows in `asset_tags` (`chair`, `furniture`, `oak`, `procedural`, `sample`). |
| 2. Permanent & unique asset ID | **PASS** | Enforced by SQLite `UNIQUE` constraint on `assets(asset_id)` and central sequence counter `id_sequences(category_code='FUR', last_seq=1)`. |
| 3. Overall & 10 category QA scores stored | **PASS** | `qa_reports.report_id = 1`: `overall_score = 97.15` (formatted `97.2/100` in logs/HTML), `grade = 'A'`, `final_status = 'QA_PASSED'`; sub-scores: `mesh_integrity=15.0`, `geometry=10.0`, `polygon_efficiency=8.65`, `uv=9.0`, `material=9.5`, `scale=10.0`, `roblox_compatibility=15.0`, `collision=5.0`, `visual=10.0`, `organisation=5.0`. |
| 4. QA report linked to exact version | **PASS** | `qa_reports.version_id = 1` FK -> `asset_versions.version_id = 1` (`version_number = 1`, `sha256 = da4d93d2b91f8c2da54d39031fec5014e177104fd0e781f7cd23629b33cb8d6a`). |
| 5. Google Drive IDs & source paths stored | **PASS** | `google_drive_file_id = '1oHEQ_Nc3aPmfrUXbBzKU5xqtrpkHBbtI'`, `google_drive_folder_id = '12pti84VJ_vdi9qY0UaZQIfN2IRVwKr5d'`, `google_drive_url = 'https://drive.google.com/file/d/1oHEQ_Nc3aPmfrUXbBzKU5xqtrpkHBbtI/view?usp=drivesdk'`; `source_blend_path = .../v1/oak_dining_chair.blend`, `export_path = .../v1/oak_dining_chair.fbx`. |
| 6. Approval status integrity | **BUG FOUND -> FIXED** | Prior CLI approval at `05:41:20Z` (`is_approved=1`, `approved_by='Owner'`) succeeded, but a subsequent `roblox-qa` invocation at `06:09:42Z` logged `ImmutableApprovedVersionError` (`production_logs.log_id = 8..11`) and reverted `assets.production_status` to `QA_PENDING`. Fixed upfront in `run_qa_for_asset()` and restored `v001` status before creating `v002`. |
| 7. Reports & previews linked to asset | **PASS** | `qa_reports.report_json_path` & `report_html_path` point to [v001/QA_Report.json](file:///C:/Users/azlan/Documents/Antigravity_Project/BlenderGenerator/reports/FUR_00001_Wooden_Chair_Basic_LP_v001/v001/QA_Report.json) and [v001/QA_Report.html](file:///C:/Users/azlan/Documents/Antigravity_Project/BlenderGenerator/reports/FUR_00001_Wooden_Chair_Basic_LP_v001/v001/QA_Report.html); 9 angle/material/wireframe PNGs + `contact_sheet.png` exist in `v001/previews/`. |
| 8. Database backups & version history | **GAP FOUND -> VERIFIED** | `data/backups/` was initially empty (`backups: []`) because pre-migration backup only triggers on pending migrations over non-empty DBs and no manual backup CLI existed. Added `roblox-backup` CLI and triggered real online backups during migration `003` and post-review (`data/backups/roblox_asset_library_pre_migration_v003_20261118T064240Z.db`, `208,896` bytes; `roblox_asset_library_phase2_review_verified_20261118T064600Z.db`, `278,528` bytes). |

### Observed Live `production_logs` Evidence (Key Entries)
- `log_id=1..2` (`05:24:51Z`): `asset_registration` / `vault_onboarding` for `FUR_00001_Wooden_Chair_Basic_LP_v001` v001.
- `log_id=3..5` (`05:40:50Z–05:40:55Z`): `qa_pipeline` (`QA_RUNNING` -> `QA_PASSED` `97.2/100 Grade A` -> `AWAITING_HUMAN_APPROVAL`). Also corroborated by `vault_storage/logs/blender_vault.log` (`inspect_roblox_qa.py`, `export_roblox.py`).
- `log_id=6..7` (`05:41:20Z`): `human_approval` (`APPROVED` by `Owner`).
- `log_id=8..11` (`06:09:42Z–06:09:46Z`): Revealed the re-QA immutability crash (`ImmutableApprovedVersionError`) and status rollback bug (now fixed and verified).
- `log_id=12..25` (`06:43:07Z–06:46:36Z`): Clean `v001` recovery, `v002` registration (`version_registration`), and `v002` `QA_PASSED` (`99.5/100 Grade A` -> `AWAITING_HUMAN_APPROVAL`).

---

## Phase 2 — Triangle Count Investigation (`v001` vs Optimized `v002`)

### Root Cause on `v001` (`16,092` Triangles)
Headless Blender per-object inspection on `v1/oak_dining_chair.blend` showed zero active runtime modifiers (`modifiers: []`)—all subdivision/bevel geometry was baked during generation in [wooden_chair.py](file:///C:/Users/azlan/Documents/Antigravity_Project/BlenderGenerator/src/blender_vault/blender_worker/scripts/generators/wooden_chair.py):
- `_create_mesh_obj(..., bevel_width=..., bevel_segments=2)` executed `bmesh.ops.bevel(bm, geom=list(bm.edges), ...)` unconditionally on **every internal edge** of the contoured `12×12` seat grid and curved backrest planks, multiplying polygon count ~9× across flat/smooth interior quads.
- **`4` objects generated `95.7%` (`15,408 / 16,092`)** of all triangles (`Chair_Seat`: `6,048`, `Chair_Backrest_Slats`: `4,752`, `Chair_Backrest_Crest_Rail`: `3,024`, `Chair_Backrest_Lumbar_Rail`: `1,584`), whereas legs/stiles/stretchers totaled only `684` tris.

### Optimization Implemented as Separate `v002` (Preserving Untouched `v001`)
Added optional `optimize_topology=True` & `clean_uv_pack=True` parameters to `generate_wooden_chair()` (keeping legacy default `optimize_topology=False` for full backwards compatibility):
- Bevel only crease/perimeter edges (`edge.calc_face_angle(0.0) >= 28°`) with `bevel_segments=1`.
- Balanced grid divisions (`seat 8×8`, `crest 12×2`, `lumbar 10×2`, `slats 2×5`, `legs 12 sides`).
- Packed clean non-overlapping UVs (`Smart UV Project`, margin `0.025`).
- Generated [oak_dining_chair_v2.blend](file:///C:/Users/azlan/Documents/Antigravity_Project/BlenderGenerator/vault_storage/assets/522b12bb84f3/v2/oak_dining_chair_v2.blend) and exported [oak_dining_chair_v2.fbx](file:///C:/Users/azlan/Documents/Antigravity_Project/BlenderGenerator/vault_storage/assets/522b12bb84f3/v2/oak_dining_chair_v2.fbx) while keeping `v1/oak_dining_chair.blend`, `v1/oak_dining_chair.fbx`, and DB `asset_versions` row `v001` untouched.

### Side-by-Side Comparison (`v001` vs `v002`)

| Metric | `v001` (Original, Preserved) | `v002` (Optimized) | Delta / Improvement |
|---|---|---|---|
| **Total Triangles** | `16,092` | **`2,016`** | **`-87.5%`** (`-14,076` tris, well within `<8,000` optimal) |
| Polygons / Vertices | `7,978` / `8,080` | `972` / `1,042` | `-87.8%` / `-87.1%` |
| `Chair_Seat` tris | `6,048` | `464` | `-92.3%` |
| `Chair_Backrest_Slats` tris | `4,752` | `420` | `-91.2%` |
| `Chair_Backrest_Crest_Rail` tris | `3,024` | `280` | `-90.7%` |
| `Chair_Backrest_Lumbar_Rail` tris | `1,584` | `240` | `-84.8%` |
| `Chair_Aprons_And_Stretchers` tris | `196` | `196` | `0%` |
| `Chair_Stile_Rear_Left` + `Right` | `164` + `164` | `140` + `140` | `-14.6%` |
| `Chair_Leg_Front_Left` + `Right` | `80` + `80` | `68` + `68` | `-15.0%` |
| Dimensions (meters) | `0.44386 × 0.53521 × 0.8947` | `0.44514 × 0.53526 × 0.8947` | Identical height, `~1 mm` rim chamfer delta |
| FBX File Size | `857,820` bytes | `128,140` bytes | **`-85.1%`** (`tri_delta = 0`, `dim_delta = 0.0` round-trip) |
| UV Mode & Max Part Overlap | `INTENTIONAL_BOX_OR_TILED` (`99.5%`) | `UNIQUE_PACKED` (`0.45%` max) | Bake-ready `[0, 1]` islands |
| UV Quality / Polygon Efficiency | `9.0` (stored) -> `10.0`* / `8.65` | `10.0` / `10.0` | `+1.35` on Polygon Efficiency |
| Overall QA Score & Grade | `97.15` (`95.15` fast* / `98.15` updated full) — **Grade A** | **`99.5 / 100` — Grade A** | **Zero warnings**, Visual silhouette preserved (`v002/previews/contact_sheet.png`) |

*\*Note: Untouched `v001` was re-evaluated read-only under updated UV rules with `render_previews=False` (`visual_quality=7.0` instead of `10.0`), yielding `95.15` (or `98.15` with full renders) without overwriting `v001`'s stored `97.15` DB record.*

---

## Phase 3 — UV Quality Investigation & Fixes

- **Why `v001` triggered 85% overlap warning:** [\_common.py](file:///C:/Users/azlan/Documents/Antigravity_Project/BlenderGenerator/src/blender_vault/blender_worker/scripts/_common.py) (`assign_box_uv(obj, scale=2.0)`) projects opposite face normals (`+Z`/`-Z`, `+X`/`-X`, `+Y`/`-Y`) onto identical UV coordinates exceeding `[0, 1]`. Because `Wood_Oak` uses `ShaderNodeTexCoord.outputs["Object"]` (3D procedural shader), overlapping UVs cause **no visual defect** and work cleanly for seamless procedural/tiled materials. However, **unique PBR baking or lightmapping on `v001`** *would* suffer cross-face texture bleeding.
- **Classifier bugs fixed in [inspect_roblox_qa.py](file:///C:/Users/azlan/Documents/Antigravity_Project/BlenderGenerator/src/blender_vault/blender_worker/scripts/inspect_roblox_qa.py) and [uv_checks.py](file:///C:/Users/azlan/Documents/Antigravity_Project/BlenderGenerator/src/blender_vault/roblox_qa/uv_checks.py):**
  1. Replaced coarse AABB cell splatting with exact **2D triangle scanline/half-space cell rasterization**.
  2. Replaced naive intra-island cell collision (which flagged adjacent neighbor triangles sharing mesh edges as self-overlap) with **strict interior face-centroid point-in-triangle testing** via spatial buckets.
  3. Added material context (`image_texture_count`, `require_pbr_maps`) so intentional box/tiled/mirrored UVs on procedural assets report `INFO` (`uv_score = 10.0`, `uv_status = "PASSED"`) with an explicit baking caveat recommendation, while image-textured assets with unpacked overlap still trigger a real `WARN` and score deduction.

---

## Phase 4 — Manual Roblox Studio Verification Workflow

Headless Roblox Studio import automation remains unavailable in this environment (`roblox_studio_automation_available: false`). Implemented full version-scoped manual Studio verification persistence in SQLite:

- **Schema Migration [003_roblox_studio_verifications.sql](file:///C:/Users/azlan/Documents/Antigravity_Project/BlenderGenerator/src/blender_vault/database/migrations/003_roblox_studio_verifications.sql):** Adds `asset_versions.studio_verification_status` (strict default `'PENDING'`) and table `roblox_studio_verifications` linked by FK to `(asset_id, version_id)`.
- **Data Access & Services:** [studio_repo.py](file:///C:/Users/azlan/Documents/Antigravity_Project/BlenderGenerator/src/blender_vault/database/repositories/studio_repo.py) records `fbx_import_ok`, `orientation_ok`, `scale_ok`, `material_appearance_ok`, `collision_test_ok`, `studio_warnings`, `studio_errors`, `notes`, `verified_by`, and `verified_at`, computing `FAILED`, `PASSED_WITH_WARNINGS`, or `PASSED`. Approving an asset in Blender QA **never** marks Studio verification as passed.
- **Interfaces:** CLI `roblox-studio-verify`, API `POST /api/roblox/assets/{asset_id}/studio-verify`, and live status in [v002/QA_Report.html](file:///C:/Users/azlan/Documents/Antigravity_Project/BlenderGenerator/reports/FUR_00001_Wooden_Chair_Basic_LP_v001/v002/QA_Report.html) & [v002/QA_Report.json](file:///C:/Users/azlan/Documents/Antigravity_Project/BlenderGenerator/reports/FUR_00001_Wooden_Chair_Basic_LP_v001/v002/QA_Report.json).

---

## Phase 5 — Approval Workflow & Drive Synchronization Verification

Verified end-to-end (and covered by automated integration assertions in `test_integration.py` and `test_database.py`):
- **Automated QA != Approval:** `run_qa_for_asset()` moves passed assets to `AWAITING_HUMAN_APPROVAL` with `human_approved = 0`, `is_approved = 0`.
- **Human Sign-Off Required:** `approve_asset()` rejects empty reviewer names, `QA_FAILED`, non-A/B grades, or assets with mandatory failures (`400` / `ValueError`).
- **Audit Fields Preserved:** Approval stores `asset_id`, `version_number`, `approved_by`, `approved_at` in `assets`, `asset_versions`, `production_logs`, and regenerated `QA_Report.{json,html}`.
- **Rejected & Prior Versions Preserved:** `reject_asset()` preserves `.blend`, `.fbx`, previews, and QA reports; `v001` (`is_approved = 1`) remained untouched across `v002` creation and simulated `v003` rejection.
- **Production Drive Gating:** Tightened `RobloxDriveSyncManager.sync_asset_version()` (`storage/sync_manager.py`) so `require_approved_for_production=True` refuses any version where `not (version.is_approved and version.qa_status == "APPROVED" and asset.production_status in (APPROVED, PUBLISHED))`, and prevents stale `vault_db` flags from routing unapproved/rejected versions to `Approved Assets`.

---

## QA Engine Weaknesses & Recommended Future Improvements

1. **Remaining Limitations:**
   - No automated Roblox Studio CLI execution (`roblox_studio_automation_available = false`).
   - Vision VLM inspection is unconfigured (`ai_visual_inspection_performed = false`, `VISION_QA_PROVIDER=none`), so semantic look/silhouette judgment requires human preview review.
   - Live Google Drive upload outside tests could not be exercised from the network-isolated sandbox (verified via local Drive v3 mock HTTP + existing `v001` Drive ID preservation).
2. **Recommended Next Steps for Thousands of Assets:**
   - Expose a clean per-generator `optimize_topology` / `clean_uv_pack` setting or PBR bake step (`Cycles` bake of Base Color / Normal / Roughness PNGs from procedural shaders) so SurfaceAppearance maps upload automatically for Roblox `>= SR` styles.
   - Optionally check Roblox's MeshPart streaming/asset limits per individual object *and* per LOD group, and add scheduled periodic SQLite online backups (`roblox-backup`) before batch generation runs.

---

## Files Modified & Created (Review Scope)

**Created:**
- [src/blender_vault/database/migrations/003_roblox_studio_verifications.sql](file:///C:/Users/azlan/Documents/Antigravity_Project/BlenderGenerator/src/blender_vault/database/migrations/003_roblox_studio_verifications.sql)
- [src/blender_vault/database/repositories/studio_repo.py](file:///C:/Users/azlan/Documents/Antigravity_Project/BlenderGenerator/src/blender_vault/database/repositories/studio_repo.py)
- [vault_storage/assets/522b12bb84f3/v2/oak_dining_chair_v2.blend](file:///C:/Users/azlan/Documents/Antigravity_Project/BlenderGenerator/vault_storage/assets/522b12bb84f3/v2/oak_dining_chair_v2.blend) & `oak_dining_chair_v2.fbx`
- [reports/FUR_00001_Wooden_Chair_Basic_LP_v001/v002/QA_Report.json](file:///C:/Users/azlan/Documents/Antigravity_Project/BlenderGenerator/reports/FUR_00001_Wooden_Chair_Basic_LP_v001/v002/QA_Report.json), `QA_Report.html`, and `previews/`
- `data/backups/roblox_asset_library_pre_migration_v003_20261118T064240Z.db` & `roblox_asset_library_phase2_review_verified_20261118T064600Z.db`
- [QA_PIPELINE_REVIEW.md](file:///C:/Users/azlan/Documents/Antigravity_Project/BlenderGenerator/QA_PIPELINE_REVIEW.md)

**Modified:**
- [src/blender_vault/config.py](file:///C:/Users/azlan/Documents/Antigravity_Project/BlenderGenerator/src/blender_vault/config.py)
- [src/blender_vault/database/__init__.py](file:///C:/Users/azlan/Documents/Antigravity_Project/BlenderGenerator/src/blender_vault/database/__init__.py), [models.py](file:///C:/Users/azlan/Documents/Antigravity_Project/BlenderGenerator/src/blender_vault/database/models.py), [repositories/__init__.py](file:///C:/Users/azlan/Documents/Antigravity_Project/BlenderGenerator/src/blender_vault/database/repositories/__init__.py), [version_repo.py](file:///C:/Users/azlan/Documents/Antigravity_Project/BlenderGenerator/src/blender_vault/database/repositories/version_repo.py)
- [src/blender_vault/blender_worker/scripts/generators/wooden_chair.py](file:///C:/Users/azlan/Documents/Antigravity_Project/BlenderGenerator/src/blender_vault/blender_worker/scripts/generators/wooden_chair.py) & [inspect_roblox_qa.py](file:///C:/Users/azlan/Documents/Antigravity_Project/BlenderGenerator/src/blender_vault/blender_worker/scripts/inspect_roblox_qa.py)
- [src/blender_vault/roblox_qa/uv_checks.py](file:///C:/Users/azlan/Documents/Antigravity_Project/BlenderGenerator/src/blender_vault/roblox_qa/uv_checks.py), [workflow.py](file:///C:/Users/azlan/Documents/Antigravity_Project/BlenderGenerator/src/blender_vault/roblox_qa/workflow.py), [router.py](file:///C:/Users/azlan/Documents/Antigravity_Project/BlenderGenerator/src/blender_vault/roblox_qa/router.py)
- [src/blender_vault/reports/report_generator.py](file:///C:/Users/azlan/Documents/Antigravity_Project/BlenderGenerator/src/blender_vault/reports/report_generator.py)
- [src/blender_vault/storage/sync_manager.py](file:///C:/Users/azlan/Documents/Antigravity_Project/BlenderGenerator/src/blender_vault/storage/sync_manager.py)
- [src/blender_vault/cli.py](file:///C:/Users/azlan/Documents/Antigravity_Project/BlenderGenerator/src/blender_vault/cli.py)
- [tests/test_database.py](file:///C:/Users/azlan/Documents/Antigravity_Project/BlenderGenerator/tests/test_database.py), [tests/test_qa.py](file:///C:/Users/azlan/Documents/Antigravity_Project/BlenderGenerator/tests/test_qa.py), [tests/test_integration.py](file:///C:/Users/azlan/Documents/Antigravity_Project/BlenderGenerator/tests/test_integration.py)

---

## Tests Performed & Results (Post-Final Edit)

| Command | Outcome | Observed Coverage |
|---|---|---|
| `.\.venv\Scripts\python.exe -m pytest -v` | **`60 passed, 1 warning in 514.10s`** | Zero failures/skips across all 34 original Branch 1/2 tests and 26 Roblox DB/QA/Integration tests (including pre-migration backup/restore, `StudioVerification` tracking, re-QA immutability guard, rejected version Drive blocking, optimized chair <6k tris & UV packing, UV classifier modes, and `.env` isolation). |
| `.\.venv\Scripts\python.exe -m blender_vault.cli roblox-status` | **Exit `0`, `integrity.ok = true`** | Confirmed clean single live asset `FUR_00001_Wooden_Chair_Basic_LP_v001` (`current_version = 2`, `status = AWAITING_HUMAN_APPROVAL`, `qa_score = 99.5`, `qa_grade = A`, `human_approved = false`, `studio_verification_status = PENDING`, `foreign_keys_enabled = true`, `schema_version = 3`, `pending_drive_uploads = 0`). |
| `.\.venv\Scripts\python.exe -m blender_vault.cli roblox-backup --reason phase2_review_verified` | **Exit `0`** | Verified consistent online SQLite backup creation (`278,528` bytes) with clean `integrity_check = ["ok"]`. |

---

## Outstanding Manual Tasks for Production Sign-Off

1. **Visually inspect `v002` renders:** Review [reports/FUR_00001_Wooden_Chair_Basic_LP_v001/v002/QA_Report.html](file:///C:/Users/azlan/Documents/Antigravity_Project/BlenderGenerator/reports/FUR_00001_Wooden_Chair_Basic_LP_v001/v002/QA_Report.html) and `v002/previews/contact_sheet.png`.
2. **Test FBX import in Roblox Studio:** Import [oak_dining_chair_v2.fbx](file:///C:/Users/azlan/Documents/Antigravity_Project/BlenderGenerator/vault_storage/assets/522b12bb84f3/v2/oak_dining_chair_v2.fbx) into Roblox Studio, check scale against an R15 dummy (`3.195` studs tall), orientation (`-Z` front, `+Y` up), material rendering, and `Hull` collision playtest.
3. **Record real Studio results in SQLite:**
   ```powershell
   .\.venv\Scripts\python.exe -m blender_vault.cli roblox-studio-verify FUR_00001_Wooden_Chair_Basic_LP_v001 --version 2 --verifier Owner --fbx-import pass --orientation pass --scale pass --materials pass --collision pass --notes "Verified in Studio"
   ```
4. **Approve & sync `v002` to Google Drive (outside offline sandbox):**
   ```powershell
   .\.venv\Scripts\python.exe -m blender_vault.cli roblox-approve FUR_00001_Wooden_Chair_Basic_LP_v001 --version 2 --reviewer Owner --notes "Optimized v002 verified visually and in Studio"
   ```
