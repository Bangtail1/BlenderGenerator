# Blender Asset Vault — Autonomous 3D Asset Library

A local-first automated 3D asset pipeline and personal library manager powered by **Blender 5.3** (`bpy`/`bmesh`), **FastAPI**, **SQLite**, **Three.js**, optional **Multimodal Vision QA**, and **Google Drive API v3** remote sync.

## Key Capabilities

- **Procedural 3D Asset Generation**: Prompt-driven templates (`wooden_chair`, `wooden_table`, `mechanical_gear`, `ceramic_vase`, `stylized_leaf`, calibration primitives) producing real editable `.blend` and `.glb` meshes with procedural materials, UVs, clean normals, origins, and collections, plus an extensible `ExternalTextTo3DProvider` interface.
- **Safe Multi-Format Importing**: Supports `.blend`, `.obj`, `.fbx`, `.stl`, `.glb`, `.gltf` while preserving untouched originals under `vault_storage/imports_original/`, tracking strict provenance & license metadata, detecting duplicates via SHA-256, and rejecting unsupported formats.
- **Category-Aware Technical QA**: Inspects topology (`bmesh`), normals, degenerate/zero-area faces, loose vertices/edges, duplicate vertices (`KDTree`), non-manifold edges, materials, UVs, missing external dependencies, modifiers, and performs a full **GLB export -> reimport round-trip check**.
- **Two-Stage Visual QA & Human Approval**:
  - Renders 9 standardized views (`front`, `back`, `left`, `right`, `top`, `three_quarter`, `closeup`, `wireframe`, `material_beauty`), a labeled `contact_sheet.png`, and a 360° `turntable.webp`.
  - **Stage 1**: Pixel sanity check + optional Gemini / OpenAI / Ollama multimodal VLM review (explicitly marked `UNCONFIGURED` when no VLM is set).
  - **Stage 2**: Interactive Web UI inspection room with Three.js 3D viewer and explicit human Approve/Reject gate separated from automated results.
- **Google Drive Sync & Verification**: OAuth 2.0 (`drive.file`), resumable chunked uploads with retry backoff, size + MD5 verification, remote `.blend` download + Blender reopen checks, non-overwriting `v1/v2/...` storage, and catalogue reconstruction from remote `manifest.json` files.
- **Branch 2 — BlendSwap Reference Intelligence Engine**: Automated daily free collection (up to 7/day matching official BlendSwap API allowance without spending credits), diversity-driven selection, uploader-declared license intelligence (`A`–`G`) & attribution registry, isolated Blender 5.3 inspection, category-specific 100-point grading (`A+`–`F`), `reference_library/` + Google Drive backup, integrated Sections A–F dashboard, and independent benchmarking against Branch 1 models.

## Installation

1. **Create & activate Python 3.13 virtual environment**:
   ```powershell
   & "C:\Users\azlan\AppData\Local\Programs\Python\Python313\python.exe" -m venv .venv
   .\.venv\Scripts\activate
   ```
2. **Install dependencies & project**:
   ```powershell
   pip install -r requirements.txt
   pip install --no-deps --no-build-isolation -e .
   ```
3. **Configure environment**:
   ```powershell
   copy .env.example .env
   ```
   Edit `.env` to verify `BLENDER_EXECUTABLE`, optional `VISION_QA_PROVIDER`, and `BLENDSWAP_KEY` for Branch 2.
4. **Optional external setups**:
   - [Blender Setup Guide](docs/BLENDER_SETUP.md)
   - [BlendSwap API Setup Guide](docs/BLENDSWAP_SETUP.md)
   - [Google Drive OAuth Setup Guide](docs/GOOGLE_DRIVE_OAUTH_SETUP.md)
   - [System Architecture (Branch 1)](docs/ARCHITECTURE.md)
   - [Reference Engine Architecture (Branch 2)](docs/REFERENCE_ENGINE_ARCHITECTURE.md)
   - [Troubleshooting Guide](docs/TROUBLESHOOTING.md)

## Startup

Verify Blender, Google Drive, and BlendSwap configuration health:

```powershell
.\.venv\Scripts\python.exe -m blender_vault.cli health
```

Additional Branch 2 CLI commands:

```powershell
.\.venv\Scripts\python.exe -m blender_vault.cli blendswap-health
.\.venv\Scripts\python.exe -m blender_vault.cli collect-daily
.\.venv\Scripts\python.exe -m blender_vault.cli scheduler
.\.venv\Scripts\python.exe -m blender_vault.cli benchmark <branch1_asset_id>
.\.venv\Scripts\python.exe -m blender_vault.cli ref-reindex
```

Start the Web UI and API server:

```powershell
.\.venv\Scripts\python.exe -m blender_vault.cli serve
```

Then open **http://127.0.0.1:8765** in your browser.

## Testing

Run the full automated verification suite (real Blender subprocess generation, defective mesh detection, multi-angle rendering, SQLite/manifest rebuild, and local HTTP Google Drive v3 integration):

```powershell
.\.venv\Scripts\python.exe -m pytest -v
```

## Sample Deliverable Asset

An actual generated 3D wooden chair asset (`Oak Dining Chair`), along with its editable `.blend`, `.glb`, `manifest.json`, `qa_report.json`, `visual_qa.json`, `provenance.json`, all 9 inspection PNGs, `contact_sheet.png`, and `turntable.webp`, is included under `samples/sample_wooden_chair/`.
