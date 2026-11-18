"""Tests for provenance safety, SQLite catalogue CRUD/search, and manifest re-indexing."""

from __future__ import annotations

from pathlib import Path

from blender_vault.db import VaultDatabase
from blender_vault.models import (
    AssetRecord,
    AssetStatus,
    AssetVersion,
    GeometryProfile,
    ProvenanceInfo,
    ProvenanceType,
)
from blender_vault.services.storage import VaultStorage


def test_provenance_never_assumes_ownership_when_unknown() -> None:
    ext = ProvenanceInfo.for_import(ProvenanceType.EXTERNAL_IMPORT)
    assert ext.provenance_verified is False
    assert "Unknown" in ext.license_name
    assert "do not assume" in ext.usage_restrictions.lower()

    gen = ProvenanceInfo.for_generated_original("wooden_chair")
    assert gen.provenance_verified is True
    assert gen.provenance_type == ProvenanceType.GENERATED_ORIGINAL


def test_catalogue_search_and_manifest_rebuild(tmp_path: Path) -> None:
    db_path = tmp_path / "vault.db"
    storage = VaultStorage(tmp_path / "assets", tmp_path / "imports")
    db = VaultDatabase(db_path)

    prov = ProvenanceInfo.for_generated_original("wooden_chair")
    v1 = AssetVersion(
        asset_id="asset001",
        version=1,
        status=AssetStatus.APPROVED,
        sha256="abc123",
        poly_count=1200,
        vertex_count=700,
    )
    asset = AssetRecord(
        asset_id="asset001",
        slug="oak_chair",
        name="Oak Chair",
        description="Handcrafted oak dining chair",
        category="Furniture",
        geometry_profile=GeometryProfile.FURNITURE_PROP,
        tags=["chair", "oak", "furniture"],
        provenance=prov,
        current_version=1,
    )
    db.create_asset_with_version(asset, v1)
    storage.write_version_artifacts(asset, v1)

    assert len(db.list_assets(search="oak")) == 1
    assert len(db.list_assets(category="Nature")) == 0
    assert db.find_version_by_sha256("abc123") is not None

    # Delete SQLite DB and rebuild entirely from local manifest.json
    db_path.unlink()
    rebuilt_db = VaultDatabase(db_path)
    assert len(rebuilt_db.list_assets()) == 0

    res = storage.rebuild_database_from_manifests(rebuilt_db)
    assert res["discovered"] == 1
    assert res["reindexed"] == 1
    restored = rebuilt_db.get_asset("asset001")
    assert restored is not None
    assert restored.name == "Oak Chair"
    assert restored.latest is not None
    assert restored.latest.status == AssetStatus.APPROVED
