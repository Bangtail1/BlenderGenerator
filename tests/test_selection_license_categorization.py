"""Phase 3 & Phase 5 tests: intelligent 7-slot selection, diversity, shortage reporting, licenses A-G, and categorization."""

from __future__ import annotations

from pathlib import Path

from blender_vault.config import Settings
from blender_vault.db import VaultDatabase
from blender_vault.reference.blendswap_client import BlendSwapClient
from blender_vault.reference.categorizer import (
    classify_primary_category,
    classify_quality_category,
    classify_technical_categories,
)
from blender_vault.reference.license_engine import assess_license, build_attribution_record
from blender_vault.reference.models import (
    BlendSwapAssetSummary,
    BlendSwapAuthor,
    BlendSwapCategoryInfo,
    BlendSwapCounts,
    BlendSwapFileInfo,
    BlendSwapLicenseInfo,
    LicenseClass,
    ReferenceAssetRecord,
    ReferenceAssetStatus,
    ReferenceInspectionReport,
)
from blender_vault.reference.ref_db import ReferenceDatabase
from blender_vault.reference.selection_engine import SelectionEngine
from tests.mock_blendswap_server import build_mock_blendswap_server, default_catalog_assets


def test_all_nine_licenses_and_attribution_records() -> None:
    expected = {
        "cc0": (LicenseClass.A_TECHNICAL_BENCHMARK_SUITABLE, False, True),
        "cc_by": (LicenseClass.B_ATTRIBUTION_REQUIRED, True, True),
        "cc_by_sa": (LicenseClass.C_SHARE_ALIKE, True, True),
        "cc_by_nd": (LicenseClass.E_NO_DERIVATIVES, True, True),
        "cc_by_nc": (LicenseClass.D_NON_COMMERCIAL, True, True),
        "cc_by_nc_sa": (LicenseClass.D_NON_COMMERCIAL, True, True),
        "cc_by_nc_nd": (LicenseClass.E_NO_DERIVATIVES, True, True),
        "gal": (LicenseClass.F_GAL_ADDITIONAL_REVIEW, True, True),
        "commercial": (LicenseClass.G_COMMERCIAL_ADDITIONAL_REVIEW, True, False),
    }
    for key, (primary_cls, req_attr, bench_ok) in expected.items():
        assessment = assess_license(BlendSwapLicenseInfo(key=key, name=key.upper()))
        assert assessment.legally_verified is False  # Never claims legal verification
        assert assessment.license_source == "uploader_declared"
        assert assessment.primary_class == primary_cls
        assert assessment.requires_attribution is req_attr
        assert assessment.suitable_for_benchmarking is bench_ok

        summary = BlendSwapAssetSummary(
            id=999,
            title="Test Asset",
            url="https://blendswap.com/blend/999",
            description="Reference mesh",
            tags=["chair", "wood"],
            author=BlendSwapAuthor(id=1, username="artist_one", url="https://blendswap.com/profile/1"),
        )
        attr = build_attribution_record(summary, assessment)
        assert attr.legally_verified is False
        assert "artist_one" in attr.attribution_text
        assert attr.original_tags == ["chair", "wood"]


def test_categorizer_auto_extends_and_refines_from_inspection(tmp_path: Path) -> None:
    cfg = Settings(vault_data_dir=tmp_path / "vault")
    ref_db = ReferenceDatabase(VaultDatabase(cfg.db_path))

    # Unknown BlendSwap subject category registers automatically
    custom_summary = BlendSwapAssetSummary(
        id=501,
        title="Quantum Widget",
        description="A futuristic gizmo",
        category=BlendSwapCategoryInfo(id=99, name="Sci-Fi Holograms", slug="sci-fi-holograms"),
        tags=["procedural", "hard-surface"],
    )
    primary = classify_primary_category(custom_summary, ref_db=ref_db, allow_auto_extend=True)
    assert primary == "Sci-Fi Holograms"
    assert "Sci-Fi Holograms" in ref_db.list_custom_categories("primary")

    # Technical tags refine accurately using real inspection stats
    insp = ReferenceInspectionReport(
        inspection_id="i1",
        blendswap_id=501,
        inspection_completed_ok=True,
        file_opened_ok=True,
        triangle_count=85_000,
        has_rig=True,
        has_animations=True,
        procedural_shader_count=2,
        texture_reference_count=0,
    )
    tech = classify_technical_categories(custom_summary, inspection=insp, ref_db=ref_db)
    assert "high-poly" in tech
    assert "rigged" in tech
    assert "animated" in tech
    assert "procedural" in tech
    assert classify_quality_category("A+") == "A+ reference"


def test_selection_engine_diversifies_seven_slots_and_avoids_duplicates(tmp_path: Path) -> None:
    # Add 5 extra furniture items with huge downloads to prove engine still diversifies categories!
    catalog = default_catalog_assets()
    for extra_id in range(2001, 2006):
        catalog.append(
            {
                "id": extra_id,
                "title": f"Extra Popular Chair {extra_id}",
                "url": f"https://blendswap.com/blend/{extra_id}",
                "description": "Yet another very popular chair model.",
                "asset_type": "model_3d",
                "asset_type_label": "3D Model",
                "license": {"key": "cc0", "name": "CC0", "url": "https://creativecommons.org/publicdomain/zero/1.0/", "license_source": "uploader_declared"},
                "license_source": "uploader_declared",
                "tags": ["chair", "furniture"],
                "category": {"id": 7, "name": "Furniture", "slug": "furniture"},
                "author": {"id": 88, "username": "chair_maker", "url": "https://blendswap.com/profile/88"},
                "counts": {"downloads": 50000 - extra_id, "likes": 100, "comments": 5},
                "explicit": False,
                "blender_versions": ["4.2"],
                "size_bytes": 102400,
                "created_at": "2026-06-01T12:00:00Z",
                "files": [{"id": extra_id * 10 + 1, "format": "blender", "format_label": "Blender", "size_bytes": 102400, "software_version": "4.2", "render_engine": "cycles", "filename": f"c_{extra_id}.blend", "position": 0}],
                "_blob": b"BLENDER-v305" + b"\x00" * 512,
            }
        )
    # Add a commercial asset (must be filtered out) & an empty/metadata-poor asset
    catalog.append(
        {
            "id": 3001,
            "title": "Commercial Paid Kit",
            "url": "https://blendswap.com/blend/3001",
            "description": "Paid commercial kit.",
            "asset_type": "model_3d",
            "license": {"key": "commercial", "name": "BlendSwap Commercial", "url": "", "license_source": "uploader_declared"},
            "tags": ["architecture"],
            "category": {"id": 1, "name": "Architecture", "slug": "architecture"},
            "author": {"id": 99, "username": "seller", "url": ""},
            "counts": {"downloads": 99999, "likes": 500, "comments": 50},
            "blender_versions": ["4.2"],
            "size_bytes": 102400,
            "files": [{"id": 30011, "format": "blender", "size_bytes": 102400, "filename": "paid.blend", "position": 0}],
            "_blob": b"BLENDER-v305" + b"\x00" * 512,
        }
    )

    server, base_url, _ = build_mock_blendswap_server(assets=catalog)
    try:
        cfg = Settings(
            vault_data_dir=tmp_path / "vault",
            blendswap_key="bsk_test_valid_key",
            blendswap_base_url=f"{base_url}/api/v1",
        )
        ref_db = ReferenceDatabase(VaultDatabase(cfg.db_path))

        # Pre-seed asset 2001 as already collected -> must NEVER be re-selected
        ref_db.upsert_reference_asset(
            ReferenceAssetRecord(
                blendswap_id=2001,
                title="Extra Popular Chair 2001",
                url="https://blendswap.com/blend/2001",
                license_assessment=assess_license(BlendSwapLicenseInfo(key="cc0", name="CC0")),
                primary_category="Furniture",
                status=ReferenceAssetStatus.GRADED,
                primary_file_path="/tmp/c_2001.blend",
            )
        )

        client = BlendSwapClient(cfg, ref_db)
        selector = SelectionEngine(cfg, ref_db, client)
        result = selector.discover_and_select(target_count=7)
        client.close()

        assert len(result.selected) == 7
        assert result.shortage_count == 0
        selected_ids = [s.asset.id for s in result.selected]
        assert len(set(selected_ids)) == 7
        assert 2001 not in selected_ids  # duplicate excluded
        assert 3001 not in selected_ids  # commercial excluded

        # Must achieve multi-category diversity rather than picking 7 Furniture items
        distinct_cats = {s.mapped_primary_category for s in result.selected}
        assert len(distinct_cats) >= 5
    finally:
        server.shutdown()


def test_selection_engine_reports_shortage_honestly(tmp_path: Path) -> None:
    # Only 2 eligible assets in catalog -> must return 2 and report shortage of 5
    small_catalog = default_catalog_assets()[:2]
    server, base_url, _ = build_mock_blendswap_server(assets=small_catalog)
    try:
        cfg = Settings(
            vault_data_dir=tmp_path / "vault",
            blendswap_key="bsk_test_valid_key",
            blendswap_base_url=f"{base_url}/api/v1",
        )
        ref_db = ReferenceDatabase(VaultDatabase(cfg.db_path))
        client = BlendSwapClient(cfg, ref_db)
        selector = SelectionEngine(cfg, ref_db, client)
        result = selector.discover_and_select(target_count=7)
        client.close()

        assert len(result.selected) == 2
        assert result.shortage_count == 5
        assert result.shortage_reason is not None and "2/7" in result.shortage_reason
    finally:
        server.shutdown()


def test_generic_category_falls_back_to_text_and_unknown_license_rejected(tmp_path: Path) -> None:
    from blender_vault.reference.models import LicenseCategory

    # 1. Generic "Other" category falls through to title/tag classification ("Wooden chair" -> Furniture)
    chair_summary = BlendSwapAssetSummary(
        id=601,
        title="Wooden chair",
        description="Handcrafted dining seat",
        category=BlendSwapCategoryInfo(id=0, name="Other", slug="other"),
        tags=["furniture"],
    )
    assert classify_primary_category(chair_summary) == "Furniture"
    assert "hard-surface" in classify_technical_categories(chair_summary)

    # 2. Unrecognized & missing license identifiers map to unknown (unsuitable for benchmarking)
    unknown_assessment = assess_license(BlendSwapLicenseInfo(key="proprietary_custom", name="Custom EULA"))
    assert unknown_assessment.license_key == "unknown"
    assert unknown_assessment.license_category == LicenseCategory.UNKNOWN
    assert unknown_assessment.primary_class == LicenseClass.UNKNOWN
    assert unknown_assessment.suitable_for_benchmarking is False
    assert unknown_assessment.requires_additional_review is True

    parsed_missing = BlendSwapClient._parse_license_info({})
    missing_assessment = assess_license(parsed_missing)
    assert missing_assessment.license_key == "unknown"
    assert missing_assessment.suitable_for_benchmarking is False

    # 3. Selection skips unknown licenses and pages past already-collected assets
    catalog = default_catalog_assets()
    # Make top 20 items already collected across page 1 (per_page=10), and put fresh item on page 3
    extra_catalog = []
    for idx in range(1, 22):
        base = dict(catalog[0])
        base["id"] = 5000 + idx
        base["title"] = f"Collected Chair {idx}"
        base["counts"] = {"downloads": 100_000 - idx, "likes": 50, "comments": 5}
        base["files"] = [{**catalog[0]["files"][0], "id": (5000 + idx) * 10 + 1}]
        extra_catalog.append(base)
    # Mark asset 5021 with unknown license and asset 5020 as fresh valid
    extra_catalog.append(
        {
            **dict(catalog[1]),
            "id": 5099,
            "title": "Unknown License Model",
            "license": {"key": "custom_eula", "name": "Custom EULA"},
            "counts": {"downloads": 999_999, "likes": 10, "comments": 1},
            "files": [{**catalog[1]["files"][0], "id": 50991}],
        }
    )

    server, base_url, _ = build_mock_blendswap_server(assets=extra_catalog)
    try:
        cfg = Settings(
            vault_data_dir=tmp_path / "vault",
            blendswap_key="bsk_test_valid_key",
            blendswap_base_url=f"{base_url}/api/v1",
        )
        ref_db = ReferenceDatabase(VaultDatabase(cfg.db_path))
        for idx in range(1, 21):
            ref_db.upsert_reference_asset(
                ReferenceAssetRecord(
                    blendswap_id=5000 + idx,
                    title=f"Collected Chair {idx}",
                    url=f"https://blendswap.com/blend/{5000 + idx}",
                    license_assessment=assess_license(BlendSwapLicenseInfo(key="cc0", name="CC0")),
                    primary_category="Furniture",
                    status=ReferenceAssetStatus.GRADED,
                    primary_file_path=f"/tmp/c_{idx}.blend",
                )
            )
        client = BlendSwapClient(cfg, ref_db)
        selector = SelectionEngine(cfg, ref_db, client)
        # per_page=10, target_count=1 -> page 1 (5099 + 9 collected) + page 2 (10 collected) + page 3 (5020 collected, 5021 fresh)
        result = selector.discover_and_select(target_count=1, max_pages=4, per_page=10)
        client.close()

        selected_ids = [c.asset.id for c in result.selected]
        assert selected_ids == [5021]
        assert result.filtered_reasons.get("commercial_or_unsuitable_license", 0) >= 1
    finally:
        server.shutdown()
