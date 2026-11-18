-- Migration 001: Initial Roblox Asset Management Database Schema

CREATE TABLE IF NOT EXISTS categories (
    category_id INTEGER PRIMARY KEY AUTOINCREMENT,
    category_code TEXT NOT NULL UNIQUE,
    category_name TEXT NOT NULL UNIQUE,
    parent_category_id INTEGER REFERENCES categories(category_id) ON DELETE SET NULL,
    category_description TEXT NOT NULL DEFAULT '',
    library_type TEXT NOT NULL CHECK (library_type IN ('UNIVERSAL', 'GENRE', 'GAME_SPECIFIC')),
    legacy_category TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_categories_library_type ON categories(library_type);
CREATE INDEX IF NOT EXISTS idx_categories_parent ON categories(parent_category_id);

CREATE TABLE IF NOT EXISTS assets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id TEXT NOT NULL UNIQUE,
    asset_name TEXT NOT NULL,
    display_name TEXT NOT NULL,
    category_id INTEGER NOT NULL REFERENCES categories(category_id) ON DELETE RESTRICT,
    asset_description TEXT NOT NULL DEFAULT '',
    visual_style TEXT NOT NULL CHECK (visual_style IN ('LP', 'SD', 'SR', 'SF', 'FD')),
    production_status TEXT NOT NULL CHECK (
        production_status IN (
            'PLANNED', 'QUEUED', 'MODELLING', 'TEXTURING', 'EXPORTING',
            'QA_PENDING', 'QA_RUNNING', 'QA_PASSED', 'QA_FAILED',
            'AWAITING_HUMAN_APPROVAL', 'APPROVED', 'REJECTED',
            'PUBLISHED', 'ARCHIVED'
        )
    ),
    source_blend_path TEXT,
    google_drive_file_id TEXT,
    google_drive_folder_id TEXT,
    google_drive_url TEXT,
    export_format TEXT CHECK (export_format IS NULL OR export_format IN ('FBX', 'GLB', 'OBJ')),
    export_path TEXT,
    triangle_count INTEGER NOT NULL DEFAULT 0 CHECK (triangle_count >= 0),
    vertex_count INTEGER NOT NULL DEFAULT 0 CHECK (vertex_count >= 0),
    polygon_count INTEGER NOT NULL DEFAULT 0 CHECK (polygon_count >= 0),
    dimensions_x REAL NOT NULL DEFAULT 0.0,
    dimensions_y REAL NOT NULL DEFAULT 0.0,
    dimensions_z REAL NOT NULL DEFAULT 0.0,
    texture_resolution TEXT NOT NULL DEFAULT '',
    material_count INTEGER NOT NULL DEFAULT 0 CHECK (material_count >= 0),
    uv_status TEXT NOT NULL DEFAULT 'PENDING',
    collision_status TEXT NOT NULL DEFAULT 'PENDING',
    qa_score REAL CHECK (qa_score IS NULL OR (qa_score >= 0.0 AND qa_score <= 100.0)),
    qa_status TEXT NOT NULL DEFAULT 'PENDING',
    qa_grade TEXT CHECK (qa_grade IS NULL OR qa_grade IN ('A', 'B', 'C', 'D')),
    human_approved INTEGER NOT NULL DEFAULT 0 CHECK (human_approved IN (0, 1)),
    approved_by TEXT,
    approved_at TEXT,
    current_version INTEGER NOT NULL DEFAULT 1 CHECK (current_version >= 1),
    object_name TEXT NOT NULL DEFAULT '',
    variant_name TEXT NOT NULL DEFAULT 'Basic',
    legacy_vault_id TEXT UNIQUE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_assets_category ON assets(category_id);
CREATE INDEX IF NOT EXISTS idx_assets_status ON assets(production_status);
CREATE INDEX IF NOT EXISTS idx_assets_qa_status ON assets(qa_status);
CREATE INDEX IF NOT EXISTS idx_assets_style ON assets(visual_style);
CREATE INDEX IF NOT EXISTS idx_assets_legacy_vault ON assets(legacy_vault_id);
CREATE INDEX IF NOT EXISTS idx_assets_updated_at ON assets(updated_at DESC);

CREATE TABLE IF NOT EXISTS asset_versions (
    version_id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id TEXT NOT NULL REFERENCES assets(asset_id) ON DELETE CASCADE,
    version_number INTEGER NOT NULL CHECK (version_number >= 1),
    blender_source_path TEXT,
    google_drive_file_id TEXT,
    google_drive_folder_id TEXT,
    google_drive_url TEXT,
    export_format TEXT CHECK (export_format IS NULL OR export_format IN ('FBX', 'GLB', 'OBJ')),
    export_path TEXT,
    change_description TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    qa_status TEXT NOT NULL DEFAULT 'PENDING',
    qa_score REAL CHECK (qa_score IS NULL OR (qa_score >= 0.0 AND qa_score <= 100.0)),
    is_approved INTEGER NOT NULL DEFAULT 0 CHECK (is_approved IN (0, 1)),
    approved_by TEXT,
    approved_at TEXT,
    sha256 TEXT,
    UNIQUE(asset_id, version_number)
);

CREATE INDEX IF NOT EXISTS idx_asset_versions_asset ON asset_versions(asset_id, version_number DESC);
CREATE INDEX IF NOT EXISTS idx_asset_versions_qa_status ON asset_versions(qa_status);

CREATE TABLE IF NOT EXISTS qa_reports (
    report_id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id TEXT NOT NULL REFERENCES assets(asset_id) ON DELETE CASCADE,
    version_id INTEGER NOT NULL REFERENCES asset_versions(version_id) ON DELETE CASCADE,
    overall_score REAL NOT NULL CHECK (overall_score >= 0.0 AND overall_score <= 100.0),
    mesh_integrity_score REAL NOT NULL CHECK (mesh_integrity_score >= 0.0 AND mesh_integrity_score <= 15.0),
    geometry_score REAL NOT NULL CHECK (geometry_score >= 0.0 AND geometry_score <= 10.0),
    polygon_efficiency_score REAL NOT NULL CHECK (polygon_efficiency_score >= 0.0 AND polygon_efficiency_score <= 10.0),
    uv_score REAL NOT NULL CHECK (uv_score >= 0.0 AND uv_score <= 10.0),
    material_score REAL NOT NULL CHECK (material_score >= 0.0 AND material_score <= 10.0),
    scale_score REAL NOT NULL CHECK (scale_score >= 0.0 AND scale_score <= 10.0),
    roblox_compatibility_score REAL NOT NULL CHECK (roblox_compatibility_score >= 0.0 AND roblox_compatibility_score <= 15.0),
    collision_score REAL NOT NULL CHECK (collision_score >= 0.0 AND collision_score <= 5.0),
    visual_score REAL NOT NULL CHECK (visual_score >= 0.0 AND visual_score <= 10.0),
    organisation_score REAL NOT NULL CHECK (organisation_score >= 0.0 AND organisation_score <= 5.0),
    final_status TEXT NOT NULL,
    grade TEXT CHECK (grade IS NULL OR grade IN ('A', 'B', 'C', 'D')),
    failure_reasons TEXT NOT NULL DEFAULT '[]',
    recommendations TEXT NOT NULL DEFAULT '[]',
    warnings_json TEXT NOT NULL DEFAULT '[]',
    report_json_path TEXT,
    report_html_path TEXT,
    details_json TEXT NOT NULL DEFAULT '{}',
    test_timestamp TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_qa_reports_asset ON qa_reports(asset_id, test_timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_qa_reports_version ON qa_reports(version_id, test_timestamp DESC);

CREATE TABLE IF NOT EXISTS asset_tags (
    tag_id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id TEXT NOT NULL REFERENCES assets(asset_id) ON DELETE CASCADE,
    tag_name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(asset_id, tag_name)
);

CREATE INDEX IF NOT EXISTS idx_asset_tags_name ON asset_tags(tag_name);

CREATE TABLE IF NOT EXISTS asset_genre_relationships (
    relationship_id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id TEXT NOT NULL REFERENCES assets(asset_id) ON DELETE CASCADE,
    genre_category_id INTEGER NOT NULL REFERENCES categories(category_id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    UNIQUE(asset_id, genre_category_id)
);

CREATE INDEX IF NOT EXISTS idx_asset_genres_genre ON asset_genre_relationships(genre_category_id);

CREATE TABLE IF NOT EXISTS production_logs (
    log_id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id TEXT REFERENCES assets(asset_id) ON DELETE SET NULL,
    process_name TEXT NOT NULL,
    status TEXT NOT NULL,
    message TEXT NOT NULL,
    error_details TEXT,
    timestamp TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_production_logs_asset ON production_logs(asset_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_production_logs_process ON production_logs(process_name, status);

CREATE TABLE IF NOT EXISTS id_sequences (
    category_code TEXT PRIMARY KEY,
    last_seq INTEGER NOT NULL DEFAULT 0 CHECK (last_seq >= 0)
);

CREATE TABLE IF NOT EXISTS drive_upload_queue (
    queue_id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id TEXT NOT NULL REFERENCES assets(asset_id) ON DELETE CASCADE,
    version_id INTEGER NOT NULL REFERENCES asset_versions(version_id) ON DELETE CASCADE,
    payload_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL CHECK (status IN ('PENDING', 'UPLOADING', 'COMPLETED', 'FAILED')) DEFAULT 'PENDING',
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 5,
    last_error TEXT,
    next_retry_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_drive_queue_status ON drive_upload_queue(status, next_retry_at);
