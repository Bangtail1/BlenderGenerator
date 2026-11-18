"""Bridge/registration service for onboarding existing Blender Vault assets into Roblox SQLite DB."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from ..config import Settings
from ..db import VaultDatabase
from ..models import AssetRecord
from .db_manager import RobloxDatabaseManager
from .models import (
    ExportFormat,
    ProductionStatus,
    RobloxAssetRecord,
    ValidationStatus,
    VisualStyle,
)
from .repositories import AssetRepository, CategoryRepository, LogRepository, VersionRepository

logger = logging.getLogger("blender_vault.database.registration")


def _extract_primary_drive_refs(asset: AssetRecord) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Extract (file_id, folder_id, web_view_link) from a Vault AssetRecord's latest version."""
    latest = asset.latest
    if not latest:
        return None, None, None
    folder_id = latest.drive_folder_id
    preferred_roles = ("fbx", "glb", "obj", "blend")
    chosen = None
    for role in preferred_roles:
        chosen = next((df for df in latest.drive_files if df.role == role and df.remote_file_id), None)
        if chosen and role == "blend":
            break
        if chosen and role in ("fbx", "glb", "obj"):
            # Prefer blend if available, otherwise export
            blend_df = next((df for df in latest.drive_files if df.role == "blend" and df.remote_file_id), None)
            if blend_df:
                chosen = blend_df
            break
    if not chosen:
        chosen = next((df for df in latest.drive_files if df.remote_file_id), None)
    if not chosen:
        return None, folder_id, None
    return chosen.remote_file_id, chosen.remote_folder_id or folder_id, chosen.web_view_link


class RobloxAssetRegistrar:
    """Idempotently initializes Roblox DB departments and registers Blender Vault assets."""

    def __init__(self, settings: Settings, roblox_db: Optional[RobloxDatabaseManager] = None) -> None:
        self.settings = settings
        self.db = roblox_db or RobloxDatabaseManager(
            settings.resolved_roblox_db_path,
            backups_dir=settings.resolved_roblox_backups_dir,
        )
        self.categories = CategoryRepository(self.db)
        self.assets = AssetRepository(self.db)
        self.versions = VersionRepository(self.db)
        self.logs = LogRepository(self.db)
        self._ensure_seeded()

    def _ensure_seeded(self) -> None:
        inv_path = self.settings.roblox_master_inventory_path
        if inv_path.is_file():
            existing_univ = self.categories.list_categories(library_type="UNIVERSAL")
            existing_genre = self.categories.list_categories(library_type="GENRE")
            if len(existing_univ) < 25 or len(existing_genre) < 15:
                self.categories.seed_master_inventory(inv_path)

    def register_vault_asset(
        self,
        vault_asset: AssetRecord,
        *,
        object_name: Optional[str] = None,
        variant_name: str = "Basic",
        visual_style: VisualStyle | str = VisualStyle.LP,
        department_code: Optional[str] = None,
        genre_codes: Optional[list[str]] = None,
        production_status: ProductionStatus = ProductionStatus.QA_PENDING,
    ) -> RobloxAssetRecord:
        """Register an existing Blender Vault AssetRecord into Roblox SQLite idempotently."""
        existing = self.assets.get_by_legacy_vault_id(vault_asset.asset_id)
        if existing:
            return self.sync_vault_asset_metadata(existing.asset_id, vault_asset)

        latest = vault_asset.latest
        if department_code:
            dept = self.categories.get_by_code(department_code)
            if not dept:
                raise KeyError(f"Department code '{department_code}' not found.")
        else:
            dept = self.categories.resolve_department_for_legacy_category(
                vault_asset.category,
                asset_name=vault_asset.name,
                description=vault_asset.description,
            )

        genre_ids: list[int] = []
        for gcode in genre_codes or []:
            gcat = self.categories.get_by_code(gcode)
            if gcat:
                genre_ids.append(gcat.category_id)

        drive_file_id, drive_folder_id, drive_url = _extract_primary_drive_refs(vault_asset)

        blend_path = latest.blend_path if latest else None
        export_path = latest.glb_path if latest else None
        export_fmt = ExportFormat.GLB if export_path else None

        dims = list(latest.dimensions) if (latest and len(latest.dimensions) == 3) else [0.0, 0.0, 0.0]
        tqa = latest.technical_qa if latest else None
        poly_count = tqa.total_faces if tqa else (latest.poly_count if latest else 0)
        tri_count = tqa.total_tris if tqa else (latest.poly_count if latest else 0)
        vert_count = tqa.total_vertices if tqa else (latest.vertex_count if latest else 0)
        mat_count = len(tqa.materials) if tqa else (len(latest.materials) if latest else 0)
        has_uv = bool(tqa and tqa.objects and all(o.has_uv for o in tqa.objects))
        tex_res = "Procedural" if mat_count > 0 else ""

        derived_object = object_name
        if not derived_object:
            if "chair" in vault_asset.name.lower() and dept.category_code == "FUR":
                derived_object = "Wooden Chair"
            else:
                derived_object = vault_asset.name

        rec = self.assets.register_asset(
            asset_name=vault_asset.name,
            display_name=vault_asset.name,
            category_id=dept.category_id,
            asset_description=vault_asset.description,
            visual_style=visual_style,
            production_status=production_status,
            object_name=derived_object,
            variant_name=variant_name,
            source_blend_path=blend_path,
            google_drive_file_id=drive_file_id,
            google_drive_folder_id=drive_folder_id,
            google_drive_url=drive_url,
            export_format=export_fmt,
            export_path=export_path,
            triangle_count=tri_count,
            vertex_count=vert_count,
            polygon_count=poly_count,
            dimensions_x=dims[0],
            dimensions_y=dims[1],
            dimensions_z=dims[2],
            texture_resolution=tex_res,
            material_count=mat_count,
            uv_status=ValidationStatus.PASSED.value if has_uv else ValidationStatus.PENDING.value,
            collision_status=ValidationStatus.PENDING.value,
            qa_status=ValidationStatus.PENDING.value,
            human_approved=False,
            legacy_vault_id=vault_asset.asset_id,
            tags=vault_asset.tags,
            genre_category_ids=genre_ids,
            change_description=f"Onboarded from Blender Vault asset '{vault_asset.asset_id}'",
            sha256=latest.sha256 if latest else None,
        )

        if latest and latest.human_review and latest.human_review.reviewer:
            self.logs.log_event(
                asset_id=rec.asset_id,
                process_name="vault_onboarding",
                status="INFO",
                message=(
                    f"Linked existing Blender Vault asset '{vault_asset.asset_id}' v{latest.version} "
                    f"(prior Vault review: {latest.human_review.status.value} by {latest.human_review.reviewer})."
                ),
            )
        return rec

    def sync_vault_asset_metadata(self, roblox_asset_id: str, vault_asset: AssetRecord) -> RobloxAssetRecord:
        """Refresh storage/Drive references for an already-registered Roblox asset without duplicating."""
        latest = vault_asset.latest
        if not latest:
            rec = self.assets.get_asset(roblox_asset_id)
            if not rec:
                raise KeyError(roblox_asset_id)
            return rec

        existing_rec = self.assets.get_asset(roblox_asset_id)
        if not existing_rec:
            raise KeyError(roblox_asset_id)

        drive_file_id, drive_folder_id, drive_url = _extract_primary_drive_refs(vault_asset)
        dims = list(latest.dimensions) if len(latest.dimensions) == 3 else [0.0, 0.0, 0.0]
        tqa = latest.technical_qa
        poly_count = tqa.total_faces if tqa else latest.poly_count
        tri_count = tqa.total_tris if tqa else latest.poly_count
        vert_count = tqa.total_vertices if tqa else latest.vertex_count
        mat_count = len(tqa.materials) if tqa else len(latest.materials)

        updates: dict[str, Any] = {
            "source_blend_path": latest.blend_path,
            "triangle_count": tri_count,
            "vertex_count": vert_count,
            "polygon_count": poly_count,
            "dimensions_x": dims[0],
            "dimensions_y": dims[1],
            "dimensions_z": dims[2],
            "material_count": mat_count,
        }
        if not existing_rec.export_path and latest.glb_path:
            updates["export_path"] = latest.glb_path
            updates["export_format"] = ExportFormat.GLB.value
        if drive_file_id:
            updates["google_drive_file_id"] = drive_file_id
        if drive_folder_id:
            updates["google_drive_folder_id"] = drive_folder_id
        if drive_url:
            updates["google_drive_url"] = drive_url

        updated = self.assets.update_asset(roblox_asset_id, **updates)
        latest_ver = self.versions.get_asset_version(roblox_asset_id, updated.current_version)
        if latest_ver and not latest_ver.is_approved:
            ver_updates: dict[str, Any] = {}
            if latest.blend_path:
                ver_updates["blender_source_path"] = latest.blend_path
            if not latest_ver.export_path and latest.glb_path:
                ver_updates["export_path"] = latest.glb_path
                ver_updates["export_format"] = ExportFormat.GLB.value
            if drive_file_id:
                ver_updates["google_drive_file_id"] = drive_file_id
            if drive_folder_id:
                ver_updates["google_drive_folder_id"] = drive_folder_id
            if drive_url:
                ver_updates["google_drive_url"] = drive_url
            if ver_updates:
                self.versions.update_version(latest_ver.version_id, **ver_updates)
        return updated

    def sync_all_from_vault_db(self, vault_db: Optional[VaultDatabase] = None) -> list[RobloxAssetRecord]:
        """Register any Branch 1 assets in vault.db into roblox_asset_library.db."""
        vdb = vault_db or VaultDatabase(self.settings.db_path)
        registered: list[RobloxAssetRecord] = []
        for va in vdb.list_assets():
            registered.append(self.register_vault_asset(va))
        return registered
