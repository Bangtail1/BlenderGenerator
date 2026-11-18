-- Migration 002: Summary views and composite performance indexes

CREATE INDEX IF NOT EXISTS idx_assets_cat_status ON assets(category_id, production_status);
CREATE INDEX IF NOT EXISTS idx_assets_human_approved ON assets(human_approved, production_status);

CREATE VIEW IF NOT EXISTS v_asset_summary AS
SELECT
    a.id,
    a.asset_id,
    a.asset_name,
    a.display_name,
    c.category_code,
    c.category_name,
    c.library_type,
    a.visual_style,
    a.production_status,
    a.current_version,
    a.triangle_count,
    a.vertex_count,
    a.polygon_count,
    a.qa_score,
    a.qa_status,
    a.qa_grade,
    a.human_approved,
    a.google_drive_file_id,
    a.google_drive_folder_id,
    a.export_format,
    a.created_at,
    a.updated_at
FROM assets a
JOIN categories c ON c.category_id = a.category_id;
