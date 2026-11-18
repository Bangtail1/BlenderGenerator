# PROJECT AUDIT — Existing Blender Asset Vault System

**Audit Date:** 2026-11-18  
**Repository Root:** `C:\Users\azlan\Documents\Antigravity_Project\BlenderGenerator`  
**Audit Scope:** Complete read-only inspection prior to extending the system with the Roblox Asset Database (Pipeline 1) and Automated 3D Quality Assurance System (Pipeline 2).

---

## 1. Executive Summary

The existing repository implements **Blender Asset Vault (`blender-asset-vault` v0.1.0)**, a local-first Python 3.13 + FastAPI + SQLite + Blender 5.3.0 asset generation, inspection, multi-angle rendering, human approval, and Google Drive synchronization system.

It currently contains two functional branches inside the same application:
1. **Branch 1 — Local Asset Generation & Vault Pipeline (`src/blender_vault/`)**: Procedural Blender 5.3 generation (including `wooden_chair`), multi-format model import, 19-check technical QA + GLB round-trip check, 9-angle + turntable visual rendering, Stage 2 human approval, and OAuth 2.0 Google Drive upload/verification.
2. **Branch 2 — BlendSwap Reference Intelligence Engine (`src/blender_vault/reference/`)**: BlendSwap API v1 collector, license classifier (`A`–`G`), isolated Blender inspection (`inspect_reference.py`), 100-point reference grading (`grading_engine.py`), daily scheduler, and Branch 1 <-> Branch 2 benchmarking.

The baseline test suite runs cleanly in the local environment (`34 passed in 288.74s` via `.\.venv\Scripts\python.exe -m pytest -q`), and Blender `5.3.0` is verified at `C:\Program Files\Blender Foundation\Blender 5.3\blender.exe` with `.blend`, `.glb`/`.gltf`, `.fbx`, `.obj`, and `.stl` import/export operators available.

---

## 2. Existing Architecture & Technology Stack

### 2.1 Language, Runtime, and Tooling
- **Language:** Python `>=3.12` (running in `.venv` on Python 3.13).
- **3D Engine:** Blender `5.3.0` (`bpy`, `bmesh`, `mathutils`) executed strictly via isolated headless subprocesses (`blender.exe --background --factory-startup --python-exit-code 1 --python <script> -- <input.json> <output.json>`).
- **Web Framework:** FastAPI (`0.163.2`) + Uvicorn (`0.54.0`) serving a vanilla JS + Three.js frontend (`src/blender_vault/web/static/`).
- **Database:** Python built-in `sqlite3` stored at `vault_storage/vault.db` (WAL mode, `PRAGMA foreign_keys = ON`).
- **Configuration:** `pydantic-settings` (`Settings` in `src/blender_vault/config.py`), reading environment variables and optional `.env`.
- **Google Drive SDK:** `google-api-python-client`, `google-auth`, `google-auth-oauthlib`, `google-auth-httplib2`, plus `cryptography` (Fernet) and Windows DPAPI (`CryptProtectData` / `CryptUnprotectData`) for local OAuth token encryption.
- **Testing:** `pytest` (`9.2.0`) with real Blender subprocess tests and local HTTP mock servers for Google Drive v3 and BlendSwap API v1.

### 2.2 High-Level Component Architecture
```text
CLI (cli.py) / Web UI + FastAPI (web/app.py)
   │
   ▼
AssetPipelineService (services/pipeline.py)
   ├── BlenderWorkerRunner (blender_worker/runner.py)
   │      ├── scripts/generate.py       -> generators/registry.py -> wooden_chair.py, primitive_test.py
   │      ├── scripts/import_convert.py -> .blend / .obj / .fbx / .stl / .glb / .gltf
   │      ├── scripts/inspect_qa.py     -> bmesh metrics + GLB export/reimport round-trip
   │      └── scripts/render_views.py   -> 9 studio views + 12-frame turntable
   ├── Technical QA (services/qa_rules.py)
   ├── Visual QA (services/vision_qa.py) -> render sanity + contact sheet + WebP + optional VLM
   ├── VaultDatabase (db.py -> vault_storage/vault.db)
   ├── VaultStorage (services/storage.py -> vault_storage/assets/<id>/v<N>/)
   └── GoogleDriveSyncService (services/gdrive_service.py -> Drive API v3)
```

---

## 3. Existing File Structure

```text
BlenderGenerator/
├── .env.example                                   # Environment template (.env is absent; defaults active)
├── .gitignore                                     # Excludes .venv/, .env, credentials/, vault_storage/, *.db
├── pyproject.toml                                 # Package metadata & pytest pythonpath=['src']
├── requirements.txt                               # Runtime & test Python dependencies
├── README.md                                      # Project usage & CLI documentation
├── credentials/
│   ├── client_secrets.json                        # Google Cloud Desktop OAuth client config (gitignored)
│   └── gdrive_token.json                          # Encrypted OAuth refresh/access token (gitignored)
├── docs/
│   ├── ARCHITECTURE.md
│   ├── BLENDER_SETUP.md
│   ├── BLENDSWAP_SETUP.md
│   ├── GOOGLE_DRIVE_OAUTH_SETUP.md
│   ├── REFERENCE_ENGINE_ARCHITECTURE.md
│   └── TROUBLESHOOTING.md
├── samples/sample_wooden_chair/                   # Tracked reference output of the Oak Dining Chair
│   ├── oak_dining_chair.blend / .glb
│   ├── manifest.json / provenance.json / qa_report.json / visual_qa.json
│   └── previews/ (9 PNGs, contact_sheet.png, turntable.webp)
├── src/blender_vault/
│   ├── __init__.py, cli.py, config.py, db.py, hashing.py, logging_config.py, models.py
│   ├── blender_worker/
│   │   ├── __init__.py, runner.py
│   │   └── scripts/
│   │       ├── _common.py, generate.py, import_convert.py, inspect_qa.py, render_views.py, inspect_reference.py
│   │       └── generators/ (__init__.py, registry.py, wooden_chair.py, primitive_test.py, defective_fixtures.py)
│   ├── reference/                                 # Branch 2 BlendSwap Reference Engine (14 modules)
│   ├── services/                                  # desktop.py, gdrive_service.py, pipeline.py, qa_rules.py, storage.py, vision_qa.py
│   └── web/                                       # app.py, static/ (index.html, app.js, reference_dashboard.js, styles.css, vendor/three/)
├── tests/                                         # 10 test modules + mock_blendswap_server.py (34 tests total)
└── vault_storage/                                 # Runtime storage (gitignored)
    ├── vault.db                                   # SQLite DB (18 tables; 1 Branch 1 asset: 522b12bb84f3)
    ├── assets/522b12bb84f3/v1/                    # Oak Dining Chair artifacts & previews
    ├── imports_original/
    ├── logs/blender_vault.log
    ├── reference_library/                         # Branch 2 folders (currently 0 assets)
    └── tmp/library_index.json
```

---

## 4. Existing Blender Generation & Storage Workflow

1. **Trigger:** `AssetPipelineService.generate_procedural_asset(GenerateAssetRequest)` in `src/blender_vault/services/pipeline.py` (invoked via `POST /api/assets/generate`).
2. **ID & Initial DB Record:** Generates a 12-hex UUID (`asset_id = uuid.uuid4().hex[:12]`), slugifies the name (`slugify_name`), maps category from `STANDARD_CATEGORIES` (`Architecture`, `Furniture`, `Characters`, `Mechanical Objects`, `Vehicles`, `Household Objects`, `Nature`, `Miscellaneous`), sets `GeometryProfile`, and inserts rows into `vault.db` (`assets`, `asset_versions` with `status='PROCESSING'`, and `jobs`).
3. **Subprocess Generation:** `BlenderWorkerRunner.run_script("generate.py", ...)` invokes Blender 5.3.0 with `--background --factory-startup --python-exit-code 1`.
   - `generate.py` calls `generators/registry.py:run_procedural_generator()`, which resolves `template_id` or prompt keywords to a procedural template (`wooden_chair`, `wooden_table`, `mechanical_gear`, `ceramic_vase`, `stylized_leaf`, `test_cube`, `test_uv_sphere`).
   - `wooden_chair.py` builds 9 separate watertight `bmesh` components (`Chair_Seat`, `Chair_Leg_Front_Left`, `Chair_Leg_Front_Right`, `Chair_Stile_Rear_Left`, `Chair_Stile_Rear_Right`, `Chair_Aprons_And_Stretchers`, `Chair_Backrest_Crest_Rail`, `Chair_Backrest_Lumbar_Rail`, `Chair_Backrest_Slats`) under collection `Asset_<Name>`, assigns a procedural `ShaderNodeTexWave` + `ShaderNodeTexNoise` + `ShaderNodeBump` wood material (`Wood_Oak`), and applies box-projected UVs (`assign_box_uv`).
4. **Saving & Export:** `generate.py` saves `.blend` via `bpy.ops.wm.save_as_mainfile` and exports `.glb` via `bpy.ops.export_scene.gltf` into `vault_storage/assets/<asset_id>/v<N>/<slug>.blend` and `<slug>.glb`. `AssetPipelineService` computes file size, SHA-256, and MD5 (`VaultStorage.compute_file_hashes`) and updates `asset_versions`.
5. **Technical QA:** `_run_qa_and_visual_stages()` runs `inspect_qa.py` (including GLB export -> reimport round-trip check) and evaluates 19 pass/warn/fail rules in `services/qa_rules.py:evaluate_technical_qa()` (binary status `PASS`/`PASS_WITH_WARNINGS`/`FAIL`, not a 100-point Roblox score).
6. **Visual Rendering & QA:** If meshes exist, `render_views.py` renders 9 views (`front`, `back`, `left`, `right`, `top`, `three_quarter`, `material_beauty`, `closeup`, `wireframe`) plus 12 turntable frames. `services/vision_qa.py` builds `contact_sheet.png` and `turntable.webp`, validates non-blank pixel contrast (`verify_render_sanity`), and marks `VisualQAReport.status = UNCONFIGURED` when `VISION_QA_PROVIDER=none`.
7. **Human Gate:** Passing assets transition to `AWAITING_HUMAN_APPROVAL`; `submit_human_review()` transitions to `APPROVED` or `REJECTED`. Failed assets (`AssetStatus.FAILED`) are explicitly blocked from human approval (`ValueError`) and from Google Drive upload.
8. **Artifact Persistence:** `VaultStorage.write_version_artifacts()` atomically writes `manifest.json`, `provenance.json`, `qa_report.json`, and `visual_qa.json`.

---

## 5. Existing Google Drive Integration & File IDs

- **Module:** `src/blender_vault/services/gdrive_service.py` (`GoogleDriveSyncService`).
- **Authentication:** OAuth 2.0 via `credentials/client_secrets.json` and scope `https://www.googleapis.com/auth/drive.file`. Tokens are stored at `credentials/gdrive_token.json` protected by Windows DPAPI (`CryptProtectData`) or optional Fernet (`VAULT_TOKEN_ENCRYPTION_KEY`).
- **Remote Folder Hierarchy:** Created under root folder `Blender Asset Vault` (stored in `app_settings` keys `gdrive_root_folder_id` and `gdrive_root_folder_name`):
  - `Approved Assets/<Category>/<slug>_<asset_id[:8]>/v<N>/` (with `previews/` subfolder)
  - `Pending Review/`, `Rejected Assets/`, `Preview Images/`, `QA Reports/`, `Library Index/library_index.json`
- **Upload & Verification:**
  - Uses `MediaFileUpload(..., resumable=True, chunksize=4MB)` wrapped in `_retry()` (4 attempts with jittered exponential backoff on HTTP `408, 429, 500, 502, 503, 504` and transient network errors).
  - Performs duplicate checks inside the version folder by matching `name`, `size`, and `md5Checksum`.
  - After upload, executes `svc.files().get(...)` to verify `size`, `md5Checksum`, and `trashed == False`, raising `DriveIntegrityError` on mismatch.
  - Captures `remote_file_id`, `remote_folder_id`, `webViewLink`, `remote_size`, `remote_md5`, and `synced_at` in `DriveFileRecord` objects stored in `asset_versions.drive_files_json` and `manifest.json`.
  - On `APPROVED` upload success, transitions status to `UPLOADED` and `drive_sync_status` to `VERIFIED`.
  - Supports remote download + Blender reopen integrity checks (`verify_remote_version_integrity`) and full catalogue reconstruction from remote `manifest.json` files (`rebuild_catalogue_from_drive`).

---

## 6. Existing Chair Test Asset Inventory

Verified in `vault_storage/vault.db` and `vault_storage/assets/522b12bb84f3/v1/manifest.json`:

| Field | Observed Value |
|---|---|
| **Asset Name / Slug** | `Oak Dining Chair` / `oak_dining_chair` |
| **Existing Vault `asset_id`** | `522b12bb84f3` (Version `1`) |
| **Category / Profile** | `Furniture` / `furniture_prop` |
| **Status / Drive Sync** | `UPLOADED` / `VERIFIED` (Human approved by `Owner` at `2026-11-18T04:13:11+00:00`) |
| **Geometry** | `9` mesh objects, `8,080` vertices, `7,978` faces, `16,092` triangles |
| **Dimensions (m)** | `[0.44386, 0.53521, 0.8947]` (Width × Depth × Height) |
| **Materials / UVs** | `["Wood_Oak"]` (procedural node shader); `has_uv = true` (`Float2` box UV on all 9 parts) |
| **Local `.blend` Path** | `vault_storage/assets/522b12bb84f3/v1/oak_dining_chair.blend` (`356,614` bytes, SHA-256 `da4d93d2...`) |
| **Local `.glb` Path** | `vault_storage/assets/522b12bb84f3/v1/oak_dining_chair.glb` (`414,436` bytes) |
| **Drive Folder ID** | `12pti84VJ_vdi9qY0UaZQIfN2IRVwKr5d` (`previews/`: `1fLQlsOSU1_NGYAvDSKazFxXeuBdiDynk`) |
| **Drive `.blend` File ID** | `1oHEQ_Nc3aPmfrUXbBzKU5xqtrpkHBbtI` |
| **Drive `.glb` File ID** | `1lnwqIjQKjOoKhgF_c6G0-EHqEd_BoQuw` |
| **Sample Mirror** | `samples/sample_wooden_chair/` (identical `.blend`/`.glb` and previews; `manifest.json` snapshot pre-approval/upload) |

---

## 7. Existing Configuration, Error Handling, Logging & Dependencies

### 7.1 Configuration & Environment
- `src/blender_vault/config.py`: `Settings` (`BaseSettings`) resolves `vault_data_dir` (`./vault_storage`), `blender_executable`, `gdrive_client_secrets_path`, `gdrive_token_path`, `gdrive_scopes`, `gdrive_root_folder_id`, `vision_qa_provider`, and Branch 2 keys.
- `.env.example` exists in root; `.env` does not exist yet on disk, so runtime defaults and `discover_blender_executable()` are used.
- `credentials/client_secrets.json` and `credentials/gdrive_token.json` exist locally and are gitignored.

### 7.2 Error Handling & Logging
- `src/blender_vault/logging_config.py`: `RotatingFileHandler` (`5 MB`, 5 backups) writing to `vault_storage/logs/blender_vault.log` plus stream output.
- Structured exceptions: `BlenderWorkerError`, `DriveNotConfiguredError`, `DriveNotAuthenticatedError`, `DriveIntegrityError`, `UnsupportedModelFormatError`, `DuplicateAssetError`, `UnsupportedGenerationRequestError`.
- Failed pipeline steps mark `AssetVersion.status = FAILED`, populate `failed_stage` and `error_message`, write `jobs` failure rows, and leave local files intact for retry (`retry_asset_version`).

### 7.3 Existing Dependencies (`requirements.txt` / `pyproject.toml`)
- `fastapi`, `uvicorn[standard]`, `pydantic`, `pydantic-settings`, `python-multipart`, `pillow`, `httpx`, `google-api-python-client`, `google-auth`, `google-auth-oauthlib`, `google-auth-httplib2`, `cryptography`, `tzdata`, `pytest`, `setuptools`.
- Standard library `sqlite3`, `json`, `html`, `shutil`, `subprocess`, `logging`, `pathlib`, `argparse`, `uuid`, `hashlib` are all available without extra packages.

---

## 8. Potential Integration Risks & Discrepancies Identified

1. **Existing `vault.db` Schema vs. Required `data/roblox_asset_library.db` Schema**
   - `vault.db` already has `assets` and `asset_versions` tables with Branch 1 schemas (`asset_id` 12-hex primary key, JSON blobs, `version` integer) plus 13 Branch 2 `ref_*` tables. Replacing `vault.db` tables directly would break existing Branch 1/2 functionality and the 34 passing tests.
   - **Mitigation:** Implement `data/roblox_asset_library.db` as a dedicated, configurable relational SQLite database (via `ROBLOX_DB_PATH`) with the exact required tables (`assets`, `categories`, `asset_versions`, `qa_reports`, `asset_tags`, `asset_genre_relationships`, `production_logs`, plus `schema_migrations` and `drive_upload_queue`), and bridge `AssetPipelineService` cleanly to synchronize/register generated assets without breaking `vault.db`.

2. **Missing Master Inventory Document (25 Universal + 15 Genre Departments)**
   - Part 2.3 specifies *"Register the 25 universal asset departments and 15 genre departments defined in the project's master inventory"*, but no master inventory file exists in the repository (`STANDARD_CATEGORIES` has 8 items; Branch 2 `PRIMARY_CATEGORIES` has 14 items).
   - **Mitigation:** Create a configurable `config/master_inventory.json` defining 25 `UNIVERSAL` departments (explicitly including `FUR`, `ENV`, `VEH`, `WPN` from the spec examples and mapping cleanly to `STANDARD_CATEGORIES`) and 15 `GENRE` departments, seeded idempotently during DB initialization.

3. **Visual Style Code Discrepancy (`LP, SD, SR, SF` vs. `FD`)**
   - Section 2.3 lists `visual_style: LP, SD, SR or SF`, whereas Section 2.4's example includes `WPN_00001_Iron_Sword_Basic_FD_v001`.
   - **Mitigation:** Support `LP`, `SD`, `SR`, `SF` as primary visual styles and permit `FD` (Fantasy/Detailed) in configuration/schema validation (or map configurable style codes in `config/qa_rules.json`) so both Section 2.3 and Section 2.4 examples validate cleanly.

4. **Chair Triangle Budget & Procedural Material Compatibility**
   - The existing `Oak Dining Chair` has `16,092` triangles (watertight multi-part geometry, `Chair_Seat` alone is `6,048` tris) and uses a procedural node shader (`Wood_Oak`) with no external image files.
   - Example budgets in Section 4.4 list Small props `300–1,500`, Medium props `1,000–4,000`, Large props `2,000–8,000`, Buildings `3,000–15,000`, Vehicles `3,000–12,000`. A naive hard failure at 4,000 or 8,000 triangles would fail the existing chair!
   - **Mitigation:** Make category polygon budgets configurable in `config/qa_rules.json` with `min_tris`, `target_max_tris` (optimal score), and `hard_max_tris` (mandatory failure cap). For Furniture (`FUR`), set a realistic target/hard envelope (e.g., `200–8,000` optimal, `<= 25,000` hard cap) so the `16,092`-tri chair receives a polygon efficiency score deduction/recommendation (or passes cleanly as Grade A/B) without false mandatory rejection, while overly dense models (> hard cap) fail cleanly. Allow procedural `ShaderNodeBsdfPrincipled` materials without external image maps as valid simple Roblox materials, while flagging baked texture recommendations where appropriate.

5. **Multi-Part Disconnected Geometry & Intentional Overlapping UVs**
   - `Oak Dining Chair` consists of 9 separate mesh objects (legs, seat, rails, slats). Naive connected-component checks or naive overlap checks on box-projected UVs would produce false failures.
   - **Mitigation:** Distinguish named multi-object / multi-component assemblies from tiny stray/loose geometry islands (`<= 4` faces or floating vertices/edges), and distinguish mirrored/box-tiled UVs (`WARN` / informational note) from missing/NaN/degenerate UV coordinates (`FAIL`).

6. **Export Format Validation (FBX / GLB / OBJ) & Roblox Studio Availability**
   - Existing `generate.py` and `inspect_qa.py` only handle `.glb`. Blender `5.3.0` was verified to have `bpy.ops.export_scene.fbx`, `bpy.ops.wm.obj_export`, and `bpy.ops.export_scene.gltf` (plus their importers).
   - Roblox Studio is not headless-automatable in this environment.
   - **Mitigation:** Implement `export_manager` + Blender worker export/reimport checks for `.fbx`, `.glb`, and `.obj` (defaulting to `.fbx` or `.glb` per config), compare exported mesh counts/triangles/dimensions/materials against the original `.blend`, and explicitly generate a structured **Roblox Studio Manual Import Verification Checklist** inside QA reports rather than falsely claiming Studio import ran.

7. **Google Drive Offline/Transient Failure & Non-Blocking Generation**
   - In offline environments (such as sandboxed runs) or transient Drive outages, `GoogleDriveSyncService.upload_asset_version()` raises after 4 retries.
   - **Mitigation:** Implement a persistent retry queue (`drive_upload_queue` / `sync_manager`) that preserves local `.blend`, exports, and SQLite metadata when Drive is unreachable, logs the deferred upload in `production_logs`, and retries cleanly when Drive recovers. Also never upload `QA_FAILED` / `REJECTED` assets to the Google Drive `Approved Assets` production folder.

8. **Package Layout Integration**
   - `pyproject.toml` maps `blender_vault` under `src/blender_vault/`. Placing isolated unlinked top-level folders without preserving package imports risks breaking `blender-vault` CLI and tests.
   - **Mitigation:** Keep `src/blender_vault/` as the canonical installed package (adding clean submodules `roblox_db/`, `roblox_qa/`, `reports/`, `storage/` and integrating with `services/pipeline.py`, `services/gdrive_service.py`, `web/app.py`, and `cli.py`), while placing runtime configuration (`config/qa_rules.json`, `config/master_inventory.json`), database (`data/roblox_asset_library.db`), HTML/JSON reports (`reports/` or `output/`), and tests (`tests/`) cleanly in the repository root as specified.

---

## 9. Recommended Phased Implementation Approach

1. **Phase 2 — SQLite Database (`data/roblox_asset_library.db`)**: Implement `settings` extensions, `config/master_inventory.json` (25 universal + 15 genre departments), schema + migration runner with automatic pre-migration backups, central atomic unique ID generator (`CATEGORY_ID_OBJECT_VARIANT_STYLE_VERSION`), safe status state machine, repositories (`assets`, `categories`, `asset_versions`, `qa_reports`, `asset_tags`, `asset_genre_relationships`, `production_logs`), integrity checks, and unit tests.
2. **Phase 3 — Register Existing Chair Asset**: Register `Oak Dining Chair` (`522b12bb84f3` -> permanent ID `FUR_00001_Wooden_Chair_Basic_LP_v001`) into `data/roblox_asset_library.db` preserving its `.blend`/`.glb` paths, Google Drive folder/file IDs (`1oHEQ_Nc3aPmfrUXbBzKU5xqtrpkHBbtI`, `12pti84VJ_vdi9qY0UaZQIfN2IRVwKr5d`), existing metadata, and version record, verified by registration tests.
3. **Phase 4 — Integrate Database with Existing Generator & Drive Sync**: Hook `AssetPipelineService` and `GoogleDriveSyncService` (preserving all Branch 1/2 behavior) to assign permanent IDs on generation, save `.blend` + Roblox exports, queue/retry Drive uploads gracefully when Drive is temporarily unavailable, and log every stage into `production_logs`.
4. **Phase 5 — Independent Automated 3D QA Engine**: Build Blender 5.3 background inspection + Python scoring engine covering the 100-point rubric across all 10 categories (`15+10+10+10+10+10+15+5+10+5 = 100`), Grades `A`/`B`/`C`/`D`, configurable `config/qa_rules.json` budgets, FBX/GLB/OBJ export round-trip validation, collision assessment, and multi-angle preview reuse/generation.
5. **Phase 6 — Integrate QA with Database**: Persist QA sub-scores, overall score, grade, failure reasons, recommendations, and asset summary metrics (`qa_score`, `qa_status`, `uv_status`, `collision_status`, dimensions, poly counts) transactionally into `qa_reports`, `assets`, `asset_versions`, and `production_logs`.
6. **Phase 7 — Reports & Approval Workflow**: Implement `QA_Report.json` and self-contained visual `QA_Report.html` generator (embedding multi-angle previews and Roblox Studio manual verification checklist), enforce `QA_PENDING -> QA_RUNNING -> QA_PASSED/QA_FAILED -> AWAITING_HUMAN_APPROVAL -> APPROVED` (human-only final gate), and sync approved assets to the Google Drive `Approved Assets` production folder while preserving rejected assets locally.
7. **Phase 8 — End-to-End Verification**: Execute all 15 required test scenarios (`TEST 1` through `TEST 15`) plus the full existing regression suite (`pytest`) and verify all 12 final success criteria.
