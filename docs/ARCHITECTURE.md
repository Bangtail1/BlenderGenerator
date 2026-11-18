# Blender Asset Vault — System Architecture

## Overview

Blender Asset Vault is a local-first autonomous 3D asset management system built in Python with isolated Blender CLI background workers, SQLite catalogue persistence, Three.js interactive 3D previews, a two-stage visual review workflow, and official Google Drive API v3 synchronization.

```
+-------------------------------------------------------------------+
|                   Web UI (FastAPI + Three.js)                     |
|   Catalogue | 3D GLB Viewer | 9-Angle Review | Drive Settings     |
+-------------------------------------------------------------------+
                                  |
                                  v
+-------------------------------------------------------------------+
|               AssetPipelineService (services/pipeline.py)         |
|  1. Generate / Import  ->  2. Technical QA  ->  3. Visual QA      |
|  4. Human Approval Gate -> 5. Google Drive Sync & Verification    |
+-------------------------------------------------------------------+
       |                     |                     |
       v                     v                     v
+--------------+    +------------------+    +-----------------------+
| Blender      |    | SQLite +         |    | GoogleDriveSyncService|
| WorkerRunner |    | VaultStorage     |    | OAuth 2.0 (drive.file)|
| (subprocess) |    | (manifest.json)  |    | Resumable + MD5 Check |
+--------------+    +------------------+    +-----------------------+
       |
       v
+-------------------------------------------------------------------+
|      Blender 5.3 Background Scripts (blender_worker/scripts/)     |
|  generate.py | import_convert.py | inspect_qa.py | render_views.py|
+-------------------------------------------------------------------+
```

## Core Design Principles

1. **Strict Process Isolation**:
   `bpy` and `bmesh` execute strictly inside Blender's bundled Python via `blender.exe --background --factory-startup --python-exit-code 1`. The FastAPI application runs in an independent Python 3.13 virtual environment (`.venv`).
2. **Primary 3D Files First**:
   Every approved asset stores an editable `.blend` file and an exported `.glb` preview/exchange file. Imported originals are preserved untouched under `vault_storage/imports_original/<asset_id>/v<N>/`.
3. **Category-Aware Technical QA (`services/qa_rules.py`)**:
   Geometry checks are parameterized by `GeometryProfile` (`solid_mechanical`, `furniture_prop`, `organic_nature`, `thin_sheet_cloth`, `architecture`, `character`, `general`). Closed solids enforce watertightness (`boundary_edges == 0`), while organic leaves and cloth permit open boundary edges while still failing zero-area faces, loose vertices, duplicate vertices, missing textures, or GLB round-trip corruption.
4. **Honest Two-Stage Visual QA (`services/vision_qa.py`)**:
   - **Stage 1 (Automated)**: Renders 9 standardized studio angles (`front`, `back`, `left`, `right`, `top`, `three_quarter`, `closeup`, `wireframe`, `material_beauty`), stitches a labeled `contact_sheet.png` and `turntable.webp`, verifies pixel contrast sanity, and optionally queries a configured multimodal VLM (`gemini`, `openai`, or `ollama`). When unconfigured, it explicitly reports `UNCONFIGURED` and `NEEDS_HUMAN_REVIEW`.
   - **Stage 2 (Human Approval)**: Recorded separately in `HumanReviewRecord`. Automated analysis never marks `human_review.status = APPROVED`. Assets failing Technical QA are blocked from approval.
5. **Resilient Metadata & Remote Storage**:
   Every asset version writes a self-contained `manifest.json`, `qa_report.json`, `visual_qa.json`, and `provenance.json` alongside its `.blend` and `.glb`. SQLite (`vault.db`) can be completely rebuilt from local or Google Drive manifests via `POST /api/catalogue/reindex`.
6. **Versioned Non-Destructive Google Drive Sync**:
   Files upload via Google Drive API v3 resumable chunks with retry backoff, remote size and MD5 verification, and optional download + Blender reopen checks. Existing approved version folders (`v1`, `v2`, ...) are never automatically overwritten or deleted.
