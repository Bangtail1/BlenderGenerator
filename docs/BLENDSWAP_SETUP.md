# BlendSwap API Credential & Collection Setup Guide

Branch 2 (**BlendSwap Reference Intelligence Engine**) connects strictly to the official BlendSwap REST API (`https://blendswap.com/api/v1`) documented at <https://blendswap.com/3d-mcp-api/docs>.

## 1. Obtaining Your `BLENDSWAP_KEY`

1. Sign in to your BlendSwap account (email confirmation required).
2. Navigate to **<https://blendswap.com/dashboard/api>**.
3. Create an API key (up to 5 active keys per account; starts with `bsk_live_...`).
4. Copy `.env.example` to `.env` if you have not already done so:
   ```powershell
   copy .env.example .env
   ```
5. Set `BLENDSWAP_KEY` inside `.env` (or as a Windows/shell environment variable):
   ```dotenv
   BLENDSWAP_KEY=bsk_live_your_key_here
   ```
   > **Security**: Never commit `.env` or share your bearer key. `.env` is already excluded in `.gitignore`.

## 2. Verifying Connectivity & Allowance

Run the CLI health check (free `GET /api/v1/me` call, consumes zero download allowance):

```powershell
.\.venv\Scripts\python.exe -m blender_vault.cli blendswap-health
```

You should see `"connected": true`, your `free_downloads_remaining` (typically `7` per day on standard accounts, shared with blendswap.com web downloads), and your `credits` balance.

## 3. Collection & Credit Safety Settings

Configure these variables in `.env`:

| Variable | Default | Description |
|---|---|---|
| `BLENDSWAP_BASE_URL` | `https://blendswap.com/api/v1` | Official BlendSwap REST API base URL |
| `COLLECTION_DAILY_TARGET` | `7` | Maximum assets collected per daily cycle |
| `COLLECTION_SCHEDULE_FREQUENCY` | `daily` | Collection schedule frequency |
| `COLLECTION_SCHEDULE_TIME` | `03:00` | 24-hour time (`HH:MM`) for the daily cycle |
| `COLLECTION_TIMEZONE` | `Europe/London` | IANA timezone name for daily schedule (GMT/BST aware) |
| `COLLECTION_SCHEDULER_ENABLED` | `true` | Automatically runs daily cycle when active or triggered via Windows Task Scheduler |
| `COLLECTION_RESET_RECHECK_MINUTES` | `15` | Periodic recheck interval when allowance is 0 at `03:00` while waiting for BlendSwap reset |
| `COLLECTION_RESUME_MISSED_JOBS` | `true` | Automatically recovers missed or interrupted runs when the system turns on |
| `ALLOW_CREDIT_SPEND` | `false` | **Safety lock**: prevents spending paid BlendSwap credits automatically |
| `REFERENCE_LICENSE_POLICY` | `all_free` | `all_free` (all free CC + GAL, excludes commercial), `cc_only`, or `permissive_only` (`cc0`, `cc_by`) |
| `REFERENCE_DRIVE_SYNC_ENABLED` | `true` | Automatically mirrors downloaded references and benchmark reports to Google Drive when OAuth is connected |

## 4. Allowance & Website-Sharing Behavior

- BlendSwap grants **7 free downloads/day** on standard accounts (shared between the API and manual website downloads).
- Before every download, the engine queries `GET /api/v1/me`. If fewer than 7 free downloads remain (for instance, 3 remaining because 4 were downloaded on blendswap.com), the cycle caps at `3` and never spends credits.
- Re-downloading the same file within 24 hours uses BlendSwap's free `repeat` window.
- Signed CDN URLs expire after **1 hour**; if a queued URL expires before transfer completes, the collector automatically refreshes the ticket.

## 5. Running & Persisting the Daily Collector

- **Automatic (Web UI Server)**:
  ```powershell
  .\.venv\Scripts\python.exe -m blender_vault.cli serve
  ```
- **Standalone Foreground Scheduler**:
  ```powershell
  .\.venv\Scripts\python.exe -m blender_vault.cli scheduler
  ```
- **Persistent Windows Task Scheduler Integration** (survives reboots, shutdowns, and closed IDE/browser):
  ```powershell
  .\.venv\Scripts\python.exe -m blender_vault.cli scheduler-install
  .\.venv\Scripts\python.exe -m blender_vault.cli scheduler-status
  .\.venv\Scripts\python.exe -m blender_vault.cli scheduler-uninstall
  ```
  - Runs `collect-daily --scheduled` daily at `03:00` (`Europe/London` schedule settings) and at Windows logon with `<StartWhenAvailable>true</StartWhenAvailable>` so missed runs recover automatically once the PC is turned back on.
  - Because execution is local to your Windows machine, collection does **not** run while the computer is switched off; when powered back on, missed cycles and interrupted queues resume safely without duplicating downloads.
- **One-Shot Manual Collection Cycle**:
  ```powershell
  .\.venv\Scripts\python.exe -m blender_vault.cli collect-daily
  ```
