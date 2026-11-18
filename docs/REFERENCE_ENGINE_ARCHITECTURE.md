# Branch 2: BlendSwap Reference Intelligence Engine — Architecture

## 1. Branch Separation & Shared Infrastructure

Branch 2 extends **Blender Asset Vault** without altering Branch 1 (`assets`, `asset_versions`, `jobs`, `vault_storage/assets/`):

| Concern | Branch 1 (Local Generation / Import) | Branch 2 (Reference Intelligence) | Benchmark Reports |
|---|---|---|---|
| **SQLite Tables (`vault.db`)** | `assets`, `asset_versions`, `jobs`, `app_settings` | `ref_assets`, `ref_asset_files`, `ref_inspections`, `ref_quality_grades`, `ref_collection_cycles`, `ref_download_queue`, `ref_download_history`, `ref_download_tickets`, `ref_licenses_cache`, `ref_categories_cache`, `ref_custom_categories`, `ref_api_usage_log` | `ref_benchmarks` |
| **Local Storage** | `vault_storage/assets/` & `vault_storage/imports_original/` | `vault_storage/reference_library/` (`assets/`, `previews/`, `metadata/`, `inspections/`, `attribution/`, `collection_logs/`, `database/`) | `vault_storage/reference_library/benchmark_reports/` |
| **Google Drive** | `Blender Asset Vault/{Approved Assets, Pending Review, ...}` | `Blender Asset Vault/Reference Library/{Assets, Metadata, Inspections, Attribution, Collection Logs, Library Index}` | `Blender Asset Vault/Reference Library/Benchmark Reports/` |
| **Blender Runner** | `generate.py`, `import_convert.py`, `inspect_qa.py`, `render_views.py` | `inspect_reference.py` (isolated copy / archive extraction; never mutates original downloaded files) | `inspect_reference.py` run independently on Branch 1 `.blend` |

## 2. Core Modules (`src/blender_vault/reference/`)

1. **`blendswap_client.py`**: Bearer-authenticated REST client (`GET /me`, `/assets`, `/assets/{id}`, `/assets/{id}/files`, `POST /downloads`, `/licenses`, `/categories`), `X-RateLimit-*` + `429 Retry-After` handling, API call audit logging, 1h signed URL expiry detection/refresh, and a hard `allow_credit_spend=False` allowance guard.
2. **`selection_engine.py`**: Retrieves candidates via `sort=downloads`, scores by weighted criteria (popularity, engagement, native `.blend` priority, Blender compatibility, file size, metadata richness, category underrepresentation, evaluation potential), allocates 7 diverse slots, prevents duplicate asset IDs and 24h repeat-window collisions, and reports shortages rather than downloading unsuitable items.
3. **`collector.py` & `scheduler.py`**: Persistent SQLite-backed daily scheduler (`Europe/London` default, configurable `HH:MM` and timezone with GMT/BST fallback if `tzdata` is absent) and crash/shutdown recovery (`recover_incomplete_cycles`).
4. **`license_engine.py` & `categorizer.py`**:
   - Normalizes all 9 BlendSwap license keys (`cc0`, `cc_by`, `cc_by_sa`, `cc_by_nd`, `cc_by_nc`, `cc_by_nc_sa`, `cc_by_nc_nd`, `gal`, `commercial`).
   - Enforces `legally_verified=False` (`license_source="uploader_declared"`) and maps Classes **A–G** plus per-asset/consolidated `attribution/` exports.
   - Assigns Primary Categories (14 standard + auto-extensible in `ref_custom_categories`), Technical Categories (`low-poly`, `mid-poly`, `high-poly`, `sculpted`, `hard-surface`, `organic`, `modular`, `rigged`, `animated`, `procedural`, `game-ready`, `photorealistic`), Quality Categories (`A+ reference`..`F reference`), and License Categories.
5. **`inspector_service.py` + `inspect_reference.py`**: Extracts geometry, connected components, manifoldness, quad/tri/n-gon ratios, pole ratio, edge flow, subdivision readiness, UV island coverage & overlap, material node graphs, texture resolution/missing dependencies, naming/collection organization, armature/weights, and separate presentation metrics.
6. **`grading_engine.py`** (`methodology_version = "2.0.0"`):
   - 100-point rubric: Geometry integrity (20), Topology quality (20), Polygon efficiency (10), UV quality (10), Material quality (10), Object organization (10), Reusability (10), Blender compatibility (5), Presentation quality (5).
   - Profiles: `character`, `rigged`, `furniture`, `hard_surface`, `organic_sculpted`, `organic_nature`, `architecture`, `material_only`, `general`.
   - `technical_quality_score`, `popularity_score`, and `reference_usefulness_score` are strictly separated.
7. **`reference_storage.py` & `reference_drive_sync.py`**: Immutable local reference library and verified resumable Drive backup with `rebuild_database_from_disk()` recovery.
8. **`benchmark_engine.py`**: Matches Branch 1 models against graded Branch 2 references, runs independent evaluation without copying reference geometry or penalizing efficient lower-poly models, and generates persisted `BenchmarkReport`s.
9. **`dashboard_service.py` & `router.py`**: Exposes `/api/reference/*` endpoints powering Dashboard Sections **A–F** in the Web UI (`reference_dashboard.js`).
