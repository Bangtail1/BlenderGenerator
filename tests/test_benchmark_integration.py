"""Phase 9 tests: Branch 1 <-> Branch 2 benchmark matching, efficiency-aware evaluation, and reporting."""

from __future__ import annotations

from pathlib import Path

from blender_vault.blender_worker.runner import BlenderWorkerRunner
from blender_vault.config import Settings
from blender_vault.db import VaultDatabase
from blender_vault.models import GenerateAssetRequest
from blender_vault.reference.benchmark_engine import BenchmarkEngine
from blender_vault.reference.grading_engine import QualityGradingEngine
from blender_vault.reference.inspector_service import ReferenceInspectorService
from blender_vault.reference.license_engine import assess_license
from blender_vault.reference.models import (
    BlendSwapCounts,
    BlendSwapLicenseInfo,
    ReferenceAssetRecord,
    ReferenceAssetStatus,
    ReferenceInspectionReport,
)
from blender_vault.reference.ref_db import ReferenceDatabase
from blender_vault.reference.reference_storage import ReferenceStorage
from blender_vault.services.gdrive_service import GoogleDriveSyncService
from blender_vault.services.pipeline import AssetPipelineService
from blender_vault.services.storage import VaultStorage


def _seed_reference_library(ref_db: ReferenceDatabase, grader: QualityGradingEngine) -> None:
    specs = [
        (
            5001,
            "Modern Ergonomic Office Chair",
            "Furniture",
            ["hard-surface", "mid-poly", "procedural"],
            ["chair", "office", "furniture", "wood"],
            60000,
            0.88,
            0.72,
            2,
        ),
        (
            5002,
            "Sports Coupe Vehicle",
            "Vehicles",
            ["hard-surface", "high-poly"],
            ["car", "vehicle"],
            95000,
            0.78,
            0.60,
            4,
        ),
        (
            5003,
            "Industrial Gear Assembly",
            "Mechanical components",
            ["hard-surface", "mid-poly"],
            ["gear", "mechanical"],
            18000,
            0.80,
            0.55,
            1,
        ),
    ]
    for bid, title, pcat, tcats, tags, tris, qratio, uvcov, mats in specs:
        insp = ReferenceInspectionReport(
            inspection_id=f"insp_{bid}",
            blendswap_id=bid,
            file_opened_ok=True,
            inspection_completed_ok=True,
            mesh_object_count=6,
            object_count=6,
            vertex_count=tris // 2,
            edge_count=tris,
            face_count=tris // 2,
            polygon_count=tris // 2,
            triangle_count=tris,
            connected_components=6,
            is_manifold=True,
            dimensions=[0.6, 0.6, 0.95],
            quad_ratio=qratio,
            triangle_ratio=round(1.0 - qratio, 4),
            ngon_ratio=0.0,
            edge_flow_score=90.0,
            subdivision_ready=True,
            mesh_cleanliness_score=98.0,
            has_uv_map=True,
            uv_layer_count=1,
            uv_mapped_mesh_ratio=1.0,
            uv_coverage_ratio=uvcov,
            uv_completeness_ratio=1.0,
            material_count=mats,
            principled_bsdf_count=mats,
            procedural_shader_count=1,
            material_node_count_avg=6.0,
            naming_consistency_ratio=1.0,
            collection_count=1,
            object_separation_score=92.0,
            scene_organization_score=95.0,
            renderable=True,
        )
        grade = grader.grade(
            insp,
            primary_category=pcat,
            technical_categories=tcats,
            popularity_score=85.0,
            blendswap_id=bid,
        )
        rec = ReferenceAssetRecord(
            blendswap_id=bid,
            title=title,
            url=f"https://blendswap.com/blend/{bid}",
            description=f"Reference asset for {title}",
            license_assessment=assess_license(BlendSwapLicenseInfo(key="cc0", name="CC0")),
            primary_category=pcat,
            technical_categories=tcats,
            tags=tags,
            counts=BlendSwapCounts(downloads=5000, likes=120, comments=10),
            status=ReferenceAssetStatus.GRADED,
        )
        ref_db.upsert_reference_asset(rec)
        ref_db.save_inspection(insp)
        ref_db.save_quality_grade(grade, quality_category=f"{grade.grade_letter} reference", technical_categories=tcats)


def test_branch1_chair_benchmarks_against_branch2_without_poly_penalty(tmp_path: Path) -> None:
    cfg = Settings(
        vault_data_dir=tmp_path / "vault",
        blender_executable=Settings().blender_executable,
        blender_render_resolution=256,
        blender_render_samples=8,
    )
    cfg.ensure_directories()
    db = VaultDatabase(cfg.db_path)
    vault_storage = VaultStorage(cfg.assets_dir, cfg.imports_dir)
    runner = BlenderWorkerRunner(cfg.blender_executable, timeout_seconds=120)
    gdrive = GoogleDriveSyncService(cfg, db, vault_storage)
    pipeline = AssetPipelineService(cfg, db, vault_storage, runner, gdrive)

    ref_db = ReferenceDatabase(db)
    ref_storage = ReferenceStorage(cfg.resolved_reference_library_dir)
    inspector = ReferenceInspectorService(cfg, runner)
    grader = QualityGradingEngine()
    _seed_reference_library(ref_db, grader)

    # Generate Branch 1 wooden chair
    chair = pipeline.generate_procedural_asset(
        GenerateAssetRequest(
            prompt="Create a detailed wooden chair with four legs, a curved backrest and a realistic wooden material.",
            name="Modern Office Chair",
        )
    )

    bench = BenchmarkEngine(db, ref_db, ref_storage, inspector, grader)
    report = bench.benchmark_branch1_asset(chair.asset_id, intended_usage="office seating", modeling_style="hard-surface")

    assert report.branch1_asset_id == chair.asset_id
    assert report.local_grade.total_score >= 88.0
    assert report.local_grade.grade_letter in {"A", "A+"}
    assert len(report.selected_references) >= 1
    # Top matched reference MUST be the Furniture Chair (5001)
    assert report.selected_references[0].blendswap_id == 5001
    # Local chair (~16k tris) vs 60k ref MUST receive an efficiency commendation, NOT a penalty
    assert "efficient" in report.efficiency_note.lower()
    assert Path(report.report_path).is_file()
    assert len(ref_db.list_benchmark_reports(chair.asset_id)) == 1

    # Now benchmark a defective Branch 1 model -> must expose UV, material, and organization weaknesses!
    bad = pipeline.generate_procedural_asset(
        GenerateAssetRequest(
            prompt="defect",
            name="Bad Test Cube",
            template_id="defect_unapplied_scale_and_no_material",
        )
    )
    bad_report = bench.benchmark_branch1_asset(bad.asset_id)
    assert bad_report.local_grade.total_score < 70.0
    assert len(bad_report.uv_weaknesses) >= 1
    assert len(bad_report.material_weaknesses) >= 1
    assert len(bad_report.organization_weaknesses) >= 1
    assert len(bad_report.recommended_improvements) >= 3
