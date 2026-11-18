"""Phase 3 tests: Register existing chair asset and verify Drive ID & local .blend accessibility."""

from __future__ import annotations

from pathlib import Path

from blender_vault.config import Settings
from blender_vault.database import RobloxAssetRegistrar, RobloxDatabaseManager
from blender_vault.db import VaultDatabase
from blender_vault.models import AssetManifest


def test_register_existing_chair_from_manifest_and_verify_drive_and_blend(tmp_path: Path) -> None:
    manifest_path = Path("vault_storage/assets/522b12bb84f3/v1/manifest.json")
    if not manifest_path.is_file():
        manifest_path = Path("samples/sample_wooden_chair/manifest.json")
    assert manifest_path.is_file()

    manifest = AssetManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    vault_db_path = tmp_path / "vault.db"
    vdb = VaultDatabase(vault_db_path)
    vdb.upsert_from_manifest(manifest)
    vault_asset = vdb.get_asset("522b12bb84f3")
    assert vault_asset is not None

    cfg = Settings(
        vault_data_dir=tmp_path / "vault",
        roblox_db_path=tmp_path / "data" / "roblox_asset_library.db",
        roblox_backups_dir=tmp_path / "data" / "backups",
    )
    registrar = RobloxAssetRegistrar(cfg)
    reg = registrar.register_vault_asset(vault_asset)

    # TEST 1: Registered in SQLite with structured permanent ID
    assert reg.asset_id == "FUR_00001_Wooden_Chair_Basic_LP_v001"
    assert reg.asset_name == "Oak Dining Chair"
    assert reg.triangle_count == 16092
    assert reg.vertex_count == 8080
    assert reg.polygon_count == 7978
    assert reg.material_count == 1

    # TEST 2: Chair's Google Drive file ID & folder ID registered accurately
    if manifest.version.drive_files:
        assert reg.google_drive_file_id == "1oHEQ_Nc3aPmfrUXbBzKU5xqtrpkHBbtI"
        assert reg.google_drive_folder_id == "12pti84VJ_vdi9qY0UaZQIfN2IRVwKr5d"
        assert reg.google_drive_url is not None and "1oHEQ_Nc3aPmfrUXbBzKU5xqtrpkHBbtI" in reg.google_drive_url

    # TEST 3: Original Blender source remains accessible & non-empty
    assert reg.source_blend_path is not None
    blend_file = Path(reg.source_blend_path)
    assert blend_file.is_file()
    assert blend_file.stat().st_size > 100_000

    # Idempotency: re-registering existing vault asset does not duplicate ID
    reg_again = registrar.register_vault_asset(vault_asset)
    assert reg_again.asset_id == reg.asset_id
    assert len(registrar.assets.list_assets()) == 1
    assert registrar.db.verify_integrity()["ok"] is True


def test_sync_all_from_existing_vault_db_if_present(tmp_path: Path) -> None:
    live_vault = Path("vault_storage/vault.db")
    if not live_vault.is_file():
        return
    vdb = VaultDatabase(live_vault)
    cfg = Settings(
        roblox_db_path=tmp_path / "roblox_copy.db",
        roblox_backups_dir=tmp_path / "backups",
    )
    registrar = RobloxAssetRegistrar(cfg)
    synced = registrar.sync_all_from_vault_db(vdb)
    assert len(synced) >= 1
    chair = registrar.assets.get_by_legacy_vault_id("522b12bb84f3")
    assert chair is not None
    assert chair.google_drive_file_id == "1oHEQ_Nc3aPmfrUXbBzKU5xqtrpkHBbtI"
    assert Path(chair.source_blend_path).is_file()
