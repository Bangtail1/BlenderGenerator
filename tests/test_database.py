"""Phase 2 unit tests: Roblox Asset Library SQLite DB, migrations, ID generation, status & versioning."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from blender_vault.config import Settings
from blender_vault.database import (
    AssetRepository,
    CategoryRepository,
    DuplicateAssetIDError,
    ExportFormat,
    ImmutableApprovedVersionError,
    InvalidStatusTransitionError,
    LibraryType,
    LogRepository,
    ProductionStatus,
    QAReportRepository,
    RobloxDatabaseManager,
    StudioVerificationRepository,
    StudioVerificationStatus,
    VersionRepository,
    VisualStyle,
    format_asset_id,
)
from blender_vault.database.migrations.migrator import MigrationSpec, load_sql_migrations


def test_db_initialization_foreign_keys_and_master_inventory(tmp_path: Path) -> None:
    db_path = tmp_path / "data" / "roblox_asset_library.db"
    db = RobloxDatabaseManager(db_path, backups_dir=tmp_path / "backups")

    integrity = db.verify_integrity()
    assert integrity["ok"] is True
    assert integrity["foreign_keys_enabled"] is True
    assert integrity["integrity_check"] == ["ok"]
    assert integrity["foreign_key_violations"] == []
    assert integrity["schema_version"] >= 2

    cat_repo = CategoryRepository(db)
    seeded = cat_repo.seed_master_inventory(Path("config/master_inventory.json"))
    assert seeded["universal_seeded"] == 25
    assert seeded["genre_seeded"] == 15

    # Re-seeding is idempotent and creates zero empty asset rows
    cat_repo.seed_master_inventory(Path("config/master_inventory.json"))
    assert len(cat_repo.list_categories(library_type=LibraryType.UNIVERSAL)) == 25
    assert len(cat_repo.list_categories(library_type=LibraryType.GENRE)) == 15
    assert db.verify_integrity()["table_counts"]["assets"] == 0

    # Hierarchical child category & GAME_SPECIFIC support
    fur = cat_repo.get_by_code("FUR")
    assert fur is not None
    sub = cat_repo.register_category(
        category_code="FUR_SEAT",
        category_name="Seating",
        library_type=LibraryType.GAME_SPECIFIC,
        parent_category_id=fur.category_id,
        category_description="Chairs, benches, and stools",
    )
    chain = cat_repo.get_hierarchy_path(sub.category_id)
    assert [c.category_code for c in chain] == ["FUR", "FUR_SEAT"]

    # Foreign key violation enforced
    with pytest.raises(sqlite3.IntegrityError):
        with db.connect() as conn:
            conn.execute(
                """
                INSERT INTO assets (
                    asset_id, asset_name, display_name, category_id, visual_style,
                    production_status, created_at, updated_at
                ) VALUES ('BAD_00001', 'Bad', 'Bad', 999999, 'LP', 'PLANNED', '2026-01-01', '2026-01-01')
                """
            )


def test_central_id_generation_non_reuse_and_duplicate_prevention(tmp_path: Path) -> None:
    db = RobloxDatabaseManager(tmp_path / "roblox.db")
    cats = CategoryRepository(db)
    cats.seed_master_inventory(Path("config/master_inventory.json"))
    assets = AssetRepository(db)

    fur = cats.get_by_code("FUR")
    env = cats.get_by_code("ENV")
    veh = cats.get_by_code("VEH")
    wpn = cats.get_by_code("WPN")
    assert fur and env and veh and wpn

    chair = assets.register_asset(
        asset_name="Wooden Chair",
        object_name="Wooden Chair",
        variant_name="Basic",
        category_id=fur.category_id,
        visual_style=VisualStyle.LP,
    )
    assert chair.asset_id == "FUR_00001_Wooden_Chair_Basic_LP_v001"

    tree = assets.register_asset(
        asset_name="Oak Tree",
        object_name="Oak Tree",
        variant_name="Large",
        category_id=env.category_id,
        visual_style=VisualStyle.LP,
    )
    assert tree.asset_id == "ENV_00001_Oak_Tree_Large_LP_v001"

    car = assets.register_asset(
        asset_name="Sports Car",
        object_name="Sports Car",
        variant_name="Red",
        category_id=veh.category_id,
        visual_style=VisualStyle.SD,
    )
    assert car.asset_id == "VEH_00001_Sports_Car_Red_SD_v001"

    sword = assets.register_asset(
        asset_name="Iron Sword",
        object_name="Iron Sword",
        variant_name="Basic",
        category_id=wpn.category_id,
        visual_style=VisualStyle.FD,
    )
    assert sword.asset_id == "WPN_00001_Iron_Sword_Basic_FD_v001"

    # Duplicate ID is rejected
    with pytest.raises(DuplicateAssetIDError):
        assets.register_asset(
            asset_id=chair.asset_id,
            asset_name="Duplicate Chair",
            category_id=fur.category_id,
            visual_style=VisualStyle.LP,
        )

    # Even if first chair row is removed, sequence never reuses 00001
    with db.connect() as conn:
        conn.execute("DELETE FROM assets WHERE asset_id = ?", (chair.asset_id,))
    chair_2 = assets.register_asset(
        asset_name="Wooden Chair",
        object_name="Wooden Chair",
        variant_name="Basic",
        category_id=fur.category_id,
        visual_style=VisualStyle.LP,
    )
    assert chair_2.asset_id == "FUR_00002_Wooden_Chair_Basic_LP_v001"
    assert format_asset_id("FUR", 3, "Oak Stool", "Tall", "SR", 1) == "FUR_00003_Oak_Stool_Tall_SR_v001"


def test_status_transitions_tags_genres_versions_and_qa_reports(tmp_path: Path) -> None:
    db = RobloxDatabaseManager(tmp_path / "roblox.db")
    cats = CategoryRepository(db)
    cats.seed_master_inventory(Path("config/master_inventory.json"))
    assets = AssetRepository(db)
    versions = VersionRepository(db)
    qa_repo = QAReportRepository(db)
    logs = LogRepository(db)

    fur = cats.get_by_code("FUR")
    fantasy = cats.get_by_code("GNR_FAN")
    medieval = cats.get_by_code("GNR_MED")
    assert fur and fantasy and medieval

    asset = assets.register_asset(
        asset_name="Tavern Chair",
        category_id=fur.category_id,
        visual_style=VisualStyle.LP,
        production_status=ProductionStatus.MODELLING,
        tags=["chair", "wood"],
        genre_category_ids=[fantasy.category_id],
    )
    # Multi-genre relationship without duplicating the model
    genres = assets.add_genre_relationship(asset.asset_id, medieval.category_id)
    assert {g.category_code for g in genres} == {"GNR_FAN", "GNR_MED"}
    assert len(assets.list_assets(genre_category_id=medieval.category_id)) == 1
    assert len(assets.list_assets(tag="wood")) == 1

    # Valid status transition chain
    assets.transition_status(asset.asset_id, ProductionStatus.QA_PENDING, message="Queued for QA")
    assets.transition_status(asset.asset_id, ProductionStatus.QA_RUNNING, message="Running QA")

    # Illegal transition directly from QA_RUNNING -> APPROVED must raise
    with pytest.raises(InvalidStatusTransitionError):
        assets.transition_status(asset.asset_id, ProductionStatus.APPROVED)

    v1 = versions.get_latest_version(asset.asset_id)
    assert v1 is not None

    # Save failing QA report -> QA_FAILED cannot be approved
    qa_repo.save_qa_report(
        asset_id=asset.asset_id,
        version_id=v1.version_id,
        overall_score=54.0,
        mesh_integrity_score=7.0,
        geometry_score=6.0,
        polygon_efficiency_score=6.0,
        uv_score=4.0,
        material_score=5.0,
        scale_score=6.0,
        roblox_compatibility_score=8.0,
        collision_score=3.0,
        visual_score=6.0,
        organisation_score=3.0,
        final_status="QA_FAILED",
        grade="D",
        failure_reasons=["Zero-area faces found"],
        recommendations=["Remove degenerate faces"],
    )
    with pytest.raises(ValueError):
        versions.mark_version_approved(asset.asset_id, 1, approved_by="Azlan")

    # Create v2 retaining original permanent asset_id -> passes QA -> human approves
    v2 = versions.create_new_version(
        asset.asset_id,
        blender_source_path="chair_v2.blend",
        export_format=ExportFormat.FBX,
        export_path="chair_v2.fbx",
        change_description="Cleaned degenerate faces",
    )
    assert v2.version_number == 2
    assert v2.asset_id == asset.asset_id

    qa_repo.save_qa_report(
        asset_id=asset.asset_id,
        version_id=v2.version_id,
        overall_score=93.5,
        mesh_integrity_score=15.0,
        geometry_score=9.5,
        polygon_efficiency_score=9.0,
        uv_score=9.0,
        material_score=9.0,
        scale_score=9.5,
        roblox_compatibility_score=14.5,
        collision_score=5.0,
        visual_score=8.5,
        organisation_score=4.5,
        final_status="QA_PASSED",
        grade="A",
        recommendations=[],
        target_production_status=ProductionStatus.AWAITING_HUMAN_APPROVAL,
    )
    approved_v2 = versions.mark_version_approved(asset.asset_id, 2, approved_by="Azlan")
    assert approved_v2.is_approved is True

    # Approved version cannot be overwritten
    with pytest.raises(ImmutableApprovedVersionError):
        versions.update_version(approved_v2.version_id, blender_source_path="tamper.blend")

    # Creating v3 preserves approved v2 untouched while keeping permanent asset_id
    v3 = versions.create_new_version(asset.asset_id, change_description="Added cushion")
    assert v3.version_number == 3
    assert versions.get_asset_version(asset.asset_id, 2).is_approved is True
    assert len(logs.list_logs(asset_id=asset.asset_id)) >= 5


def test_pre_migration_automatic_backup_and_restore(tmp_path: Path) -> None:
    db_path = tmp_path / "roblox.db"
    backups_dir = tmp_path / "backups"
    base_migrations = [m for m in load_sql_migrations() if m.version == 1]

    db = RobloxDatabaseManager(db_path, backups_dir=backups_dir, auto_migrate=False)
    res1 = db.apply_migrations(base_migrations)
    assert res1["applied_versions"] == [1]

    cats = CategoryRepository(db)
    cats.seed_master_inventory(Path("config/master_inventory.json"))
    fur = cats.get_by_code("FUR")
    assets = AssetRepository(db)
    created = assets.register_asset(asset_name="Backup Chair", category_id=fur.category_id)

    # Apply a new v2 migration -> must automatically create a backup first
    v2_sql = "ALTER TABLE assets ADD COLUMN custom_note TEXT NOT NULL DEFAULT '';"
    v2_spec = MigrationSpec(version=2, name="add_custom_note", sql=v2_sql, checksum="test_v2")
    res2 = db.apply_migrations(base_migrations + [v2_spec])
    assert res2["applied_versions"] == [2]
    assert res2["backup_path"] is not None
    backup_file = Path(str(res2["backup_path"]))
    assert backup_file.is_file()

    # Corrupt/delete data in live DB, then restore from pre-migration backup
    with db.connect() as conn:
        conn.execute("DELETE FROM assets")
    assert assets.get_asset(created.asset_id) is None

    db.restore_from_backup(backup_file)
    restored = assets.get_asset(created.asset_id)
    assert restored is not None
    assert restored.asset_name == "Backup Chair"
    assert db.verify_integrity()["ok"] is True


def test_configurable_database_path_via_settings(tmp_path: Path) -> None:
    custom_db = tmp_path / "custom_dir" / "my_roblox.db"
    cfg = Settings(roblox_db_path=custom_db)
    assert cfg.resolved_roblox_db_path == custom_db
    default_cfg = Settings()
    assert default_cfg.resolved_roblox_db_path == Path("./data/roblox_asset_library.db")


def test_studio_verification_default_pending_and_version_tracking(tmp_path: Path) -> None:
    db = RobloxDatabaseManager(tmp_path / "roblox_studio.db", backups_dir=tmp_path / "backups")
    assert db.verify_integrity()["schema_version"] >= 3

    cats = CategoryRepository(db)
    cats.seed_master_inventory(Path("config/master_inventory.json"))
    fur = cats.get_by_code("FUR")
    assert fur is not None

    assets = AssetRepository(db)
    versions = VersionRepository(db)
    qa_repo = QAReportRepository(db)
    studio_repo = StudioVerificationRepository(db)

    chair = assets.register_asset(
        asset_name="Studio Verified Chair",
        category_id=fur.category_id,
        production_status=ProductionStatus.QA_PENDING,
    )
    v1 = versions.get_asset_version(chair.asset_id, 1)
    assert v1 is not None
    # Default Studio verification is always strictly PENDING
    assert v1.studio_verification_status == "PENDING"
    assert studio_repo.get_latest_verification(chair.asset_id, version_id=v1.version_id) is None

    qa_repo.save_qa_report(
        asset_id=chair.asset_id,
        version_id=v1.version_id,
        overall_score=95.0,
        mesh_integrity_score=15.0,
        geometry_score=10.0,
        polygon_efficiency_score=10.0,
        uv_score=10.0,
        material_score=10.0,
        scale_score=10.0,
        roblox_compatibility_score=15.0,
        collision_score=5.0,
        visual_score=5.0,
        organisation_score=5.0,
        final_status="QA_PASSED",
        grade="A",
        target_production_status=ProductionStatus.AWAITING_HUMAN_APPROVAL,
    )
    versions.mark_version_approved(chair.asset_id, 1, approved_by="Owner")
    # Human approval must NOT fake Studio verification as PASSED
    assert versions.get_asset_version(chair.asset_id, 1).studio_verification_status == "PENDING"
    with pytest.raises(InvalidStatusTransitionError):
        assets.transition_status(chair.asset_id, ProductionStatus.QA_PENDING)

    # Record a FAILED Studio test (scale mismatch)
    rec_fail = studio_repo.record_verification(
        chair.asset_id,
        version_number=1,
        fbx_import_ok=True,
        orientation_ok=True,
        scale_ok=False,
        material_appearance_ok=True,
        collision_test_ok=True,
        verified_by="Owner",
        studio_errors=["Imported mesh was 100x too small in Studio"],
        notes="Scale check failed",
    )
    assert rec_fail.status == StudioVerificationStatus.FAILED
    assert versions.get_asset_version(chair.asset_id, 1).studio_verification_status == "FAILED"

    # Record a PASSED verification on v2 cleanly
    v2 = versions.create_new_version(chair.asset_id, change_description="Fixed scale unit factor")
    assert v2.studio_verification_status == "PENDING"
    rec_pass = studio_repo.record_verification(
        chair.asset_id,
        version_number=2,
        fbx_import_ok=True,
        orientation_ok=True,
        scale_ok=True,
        material_appearance_ok=True,
        collision_test_ok=True,
        verified_by="Owner",
        notes="Verified in Roblox Studio R15 place",
    )
    assert rec_pass.status == StudioVerificationStatus.PASSED
    assert rec_pass.version_id == v2.version_id
    assert versions.get_asset_version(chair.asset_id, 2).studio_verification_status == "PASSED"
    # v1 verification remains FAILED in history
    assert versions.get_asset_version(chair.asset_id, 1).studio_verification_status == "FAILED"
