# Troubleshooting Guide (Branch 1 & Branch 2)

## 1. BlendSwap API Issues

- **`BLENDSWAP_KEY is not configured` / HTTP `401 unauthorized`**
  - Ensure `.env` exists in the project root and contains a valid `BLENDSWAP_KEY=bsk_live_...` from <https://blendswap.com/dashboard/api>.
  - Run `.\.venv\Scripts\python.exe -m blender_vault.cli blendswap-health` to verify authentication.
- **`SKIPPED_NO_ALLOWANCE` / Daily free downloads = 0**
  - BlendSwap shares the 7/day free download allowance with manual browser downloads on blendswap.com.
  - Because `ALLOW_CREDIT_SPEND=false` by default, the collector will never spend credits automatically. Wait until BlendSwap resets your daily free quota.
- **HTTP `429 rate_limited`**
  - The client automatically reads `Retry-After` and backs off. Avoid running concurrent manual `discover` bursts during an active cycle.
- **Expired Signed Download URL (`403` / `404` / `410`)**
  - BlendSwap signed URLs expire after 1 hour. `BlendSwapClient.download_with_refresh()` automatically re-requests `POST /api/v1/downloads` (free within the 24-hour repeat window).

## 2. Blender Inspection Failures

- **`Blender executable not found`**
  - Verify `BLENDER_EXECUTABLE` in `.env` points to `blender.exe` (see [BLENDER_SETUP.md](BLENDER_SETUP.md)).
- **Downloaded asset marked `FAILED` after inspection**
  - Check the persisted Blender subprocess log path recorded in `ref_inspections.log_path` (under `vault_storage/logs/inspections/`).
  - Corrupted downloads, password-protected `.rar`/`.7z` archives, or empty `.blend` scenes are intentionally logged and marked `FAILED` rather than silently discarded.
- **Original file safety**
  - Headless Blender always operates on an isolated copy inside `vault_storage/tmp/ref_inspect_*`; originals under `vault_storage/reference_library/assets/` are never modified.

## 3. Timezone & Scheduler Recovery

- **Windows `ZoneInfoNotFoundError: Europe/London`**
  - `tzdata>=2024.1` is listed in `requirements.txt` and `pyproject.toml`. In addition, `blender_vault.reference.scheduler.resolve_timezone()` includes a built-in DST-aware GMT/BST `Europe/London` fallback so scheduling works even without external `tzdata`.
- **Crash or shutdown during a daily cycle**
  - On startup (`blender-vault serve`, `blender-vault scheduler`, or `blender-vault collect-daily`), `CollectionCycleService.recover_incomplete_cycles()` automatically resumes `PENDING`/`RUNNING` cycles from `ref_download_queue` without re-downloading `COMPLETED` slots.

## 4. Database & Google Drive Recovery

- **Rebuilding SQLite `ref_*` tables from disk**
  - Run `.\.venv\Scripts\python.exe -m blender_vault.cli ref-reindex` (or `POST /api/reference/reindex`) to reconstruct `ref_assets`, `ref_inspections`, `ref_quality_grades`, and `ref_benchmarks` from `vault_storage/reference_library/`.
- **Google Drive OAuth / Sync Failures**
  - Verify `credentials/client_secrets.json` and token status via `blender-vault health` or the Web UI **Google Drive** modal.
  - Retry deferred reference uploads via `POST /api/reference/drive/sync-pending` or the **Sync Pending to Drive** button in Dashboard Section A.
