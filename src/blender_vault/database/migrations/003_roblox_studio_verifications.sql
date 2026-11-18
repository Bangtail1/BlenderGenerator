-- Migration 003: Manual Roblox Studio verification history & per-version tracking

ALTER TABLE asset_versions
    ADD COLUMN studio_verification_status TEXT NOT NULL DEFAULT 'PENDING'
    CHECK (studio_verification_status IN ('PENDING', 'PASSED', 'PASSED_WITH_WARNINGS', 'FAILED'));

CREATE TABLE IF NOT EXISTS roblox_studio_verifications (
    verification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id TEXT NOT NULL REFERENCES assets(asset_id) ON DELETE CASCADE,
    version_id INTEGER NOT NULL REFERENCES asset_versions(version_id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (status IN ('PENDING', 'PASSED', 'PASSED_WITH_WARNINGS', 'FAILED')) DEFAULT 'PENDING',
    fbx_import_ok INTEGER NOT NULL CHECK (fbx_import_ok IN (0, 1)),
    orientation_ok INTEGER NOT NULL CHECK (orientation_ok IN (0, 1)),
    scale_ok INTEGER NOT NULL CHECK (scale_ok IN (0, 1)),
    material_appearance_ok INTEGER NOT NULL CHECK (material_appearance_ok IN (0, 1)),
    collision_test_ok INTEGER NOT NULL CHECK (collision_test_ok IN (0, 1)),
    studio_warnings TEXT NOT NULL DEFAULT '[]',
    studio_errors TEXT NOT NULL DEFAULT '[]',
    notes TEXT NOT NULL DEFAULT '',
    verified_by TEXT NOT NULL,
    verified_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_studio_verifications_asset
    ON roblox_studio_verifications(asset_id, version_id, verified_at DESC);
