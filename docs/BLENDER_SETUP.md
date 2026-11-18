# Blender Setup Instructions

## Verified Environment

This project has been built and verified with:

- **Blender 5.3.0** (`C:\Program Files\Blender Foundation\Blender 5.3\blender.exe`)
- **System Python 3.13.14** (virtual environment `.venv`)

## Installing / Locating Blender

1. Install **Blender 4.2 LTS** or **Blender 5.x** (verified on **Blender 5.3.0**) from [blender.org](https://www.blender.org/download/).
2. On Windows, the default install path `C:\Program Files\Blender Foundation\Blender <version>\blender.exe` is detected automatically by `blender_vault.config.discover_blender_executable()`.
3. If Blender is installed in a custom directory, set `BLENDER_EXECUTABLE` in `.env`:

```dotenv
BLENDER_EXECUTABLE=C:\Program Files\Blender Foundation\Blender 5.3\blender.exe
```

## Verifying Blender CLI Integration

Activate your virtual environment and run the health check:

```powershell
.\.venv\Scripts\python.exe -m blender_vault.cli health
```

Expected output includes `"available": true` and `"version_line": "Blender 5.3.0"`.

## Supported Import Formats

The worker script `src/blender_vault/blender_worker/scripts/import_convert.py` supports:

- `.blend` (`wm.open_mainfile`)
- `.obj` (`wm.obj_import`)
- `.stl` (`wm.stl_import`)
- `.glb` / `.gltf` (`import_scene.gltf`)
- `.fbx` (`import_scene.fbx` / `wm.fbx_import`)

Unsupported extensions are rejected explicitly with `UnsupportedModelFormatError`.
