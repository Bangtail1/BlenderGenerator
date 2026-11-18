"""Pydantic domain models and DTOs for Branch 2 (BlendSwap Reference Intelligence Engine)."""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

from ..models import DriveFileRecord, DriveSyncStatus, utc_now_iso


# ---------------------------------------------------------------------------
# License & Categorization Taxonomies
# ---------------------------------------------------------------------------

class LicenseClass(str, Enum):
    """Standardized license classification classes A–G."""

    A_TECHNICAL_BENCHMARK_SUITABLE = "A"
    B_ATTRIBUTION_REQUIRED = "B"
    C_SHARE_ALIKE = "C"
    D_NON_COMMERCIAL = "D"
    E_NO_DERIVATIVES = "E"
    F_GAL_ADDITIONAL_REVIEW = "F"
    G_COMMERCIAL_ADDITIONAL_REVIEW = "G"


class LicenseCategory(str, Enum):
    CC0 = "CC0"
    CC_BY = "CC-BY"
    CC_BY_SA = "CC-BY-SA"
    OTHER_CC = "Other Creative Commons"
    GAL = "GAL"
    COMMERCIAL = "Commercial"


PRIMARY_CATEGORIES: tuple[str, ...] = (
    "Architecture",
    "Animals",
    "Characters",
    "Vehicles",
    "Furniture",
    "Electronics",
    "Food",
    "Clothing",
    "Plants",
    "Weapons",
    "Props and objects",
    "Environments",
    "Mechanical components",
    "Other",
)

TECHNICAL_CATEGORIES: tuple[str, ...] = (
    "low-poly",
    "mid-poly",
    "high-poly",
    "sculpted",
    "hard-surface",
    "organic",
    "modular",
    "rigged",
    "animated",
    "procedural",
    "game-ready",
    "photorealistic",
)


class LicenseAssessment(BaseModel):
    license_key: str
    license_name: str
    license_url: str = ""
    license_source: str = "uploader_declared"
    legally_verified: bool = False
    verification_disclaimer: str = (
        "Uploader-declared license only; BlendSwap does not independently verify license accuracy or grant legal rights."
    )
    license_category: LicenseCategory
    primary_class: LicenseClass
    applicable_classes: list[LicenseClass] = Field(default_factory=list)
    suitable_for_benchmarking: bool = True
    requires_attribution: bool = False
    share_alike_obligation: bool = False
    non_commercial_only: bool = False
    no_derivatives: bool = False
    requires_additional_review: bool = False
    review_warnings: list[str] = Field(default_factory=list)


class AttributionRecord(BaseModel):
    blendswap_id: int
    asset_title: str
    asset_url: str
    author_username: str
    author_profile_url: str = ""
    download_date: str
    license_key: str
    license_name: str
    license_url: str = ""
    license_source: str = "uploader_declared"
    legally_verified: bool = False
    license_classes: list[str] = Field(default_factory=list)
    attribution_text: str
    original_description: str = ""
    original_tags: list[str] = Field(default_factory=list)
    benchmark_only_notice: str = (
        "Reference asset stored strictly for independent technical benchmarking; "
        "do not copy reference geometry, textures, or materials into generated models."
    )


# ---------------------------------------------------------------------------
# BlendSwap REST API Models
# ---------------------------------------------------------------------------

class BlendSwapAccountStatus(BaseModel):
    connected: bool = False
    username: Optional[str] = None
    plan: Optional[str] = None
    credits: int = 0
    free_downloads_remaining: int = 0
    free_downloads_limit: int = 7
    download_credit_cost: int = 10
    rate_limit_limit: Optional[int] = None
    rate_limit_remaining: Optional[int] = None
    rate_limit_reset: Optional[int] = None
    raw: dict[str, Any] = Field(default_factory=dict)
    checked_at: str = Field(default_factory=utc_now_iso)


class BlendSwapAuthor(BaseModel):
    id: Optional[int] = None
    username: str = "unknown"
    url: str = ""


class BlendSwapLicenseInfo(BaseModel):
    key: str = "gal"
    name: str = "GAL"
    url: str = ""
    license_source: str = "uploader_declared"


class BlendSwapCategoryInfo(BaseModel):
    id: Optional[int] = None
    name: str = "Other"
    slug: str = "other"


class BlendSwapFileInfo(BaseModel):
    id: int
    format: str = "blender"
    format_label: str = "Blender"
    size_bytes: int = 0
    software_version: Optional[str] = None
    render_engine: Optional[str] = None
    render_engine_label: Optional[str] = None
    render_engine_version: Optional[str] = None
    filename: str = "asset.blend"
    position: int = 0


class BlendSwapCounts(BaseModel):
    downloads: int = 0
    likes: int = 0
    comments: int = 0


class BlendSwapAssetSummary(BaseModel):
    id: int
    title: str
    url: str = ""
    description: str = ""
    asset_type: str = "model_3d"
    asset_type_label: str = "3D Model"
    license: BlendSwapLicenseInfo = Field(default_factory=BlendSwapLicenseInfo)
    license_source: str = "uploader_declared"
    tags: list[str] = Field(default_factory=list)
    category: BlendSwapCategoryInfo = Field(default_factory=BlendSwapCategoryInfo)
    author: BlendSwapAuthor = Field(default_factory=BlendSwapAuthor)
    counts: BlendSwapCounts = Field(default_factory=BlendSwapCounts)
    explicit: bool = False
    blender_versions: list[str] = Field(default_factory=list)
    size_bytes: int = 0
    created_at: Optional[str] = None
    files: list[BlendSwapFileInfo] = Field(default_factory=list)


class BlendSwapDownloadTicket(BaseModel):
    download_url: str
    expires_at: Optional[str] = None
    charged: bool = False
    credits_spent: int = 0
    credits_remaining: Optional[int] = None
    reason: str = "free_allowance"  # free_allowance | paid | repeat | own_asset
    preview_urls: list[str] = Field(default_factory=list)
    asset: Optional[dict[str, Any]] = None
    file: Optional[dict[str, Any]] = None
    license: Optional[BlendSwapLicenseInfo] = None
    license_source: str = "uploader_declared"


# ---------------------------------------------------------------------------
# Reference Inspection & Grading Models
# ---------------------------------------------------------------------------

class ReferenceInspectionReport(BaseModel):
    inspection_id: str
    blendswap_id: int
    methodology_version: str = "2.0.0"
    # Compatibility
    source_format: str = "blender"
    is_native_blender: bool = True
    blend_header_version: str = ""
    blender_runtime_version: str = ""
    file_opened_ok: bool = False
    inspection_completed_ok: bool = False
    missing_dependencies: list[str] = Field(default_factory=list)
    missing_textures: list[str] = Field(default_factory=list)
    # Geometry
    object_count: int = 0
    mesh_object_count: int = 0
    non_mesh_object_count: int = 0
    vertex_count: int = 0
    edge_count: int = 0
    face_count: int = 0
    polygon_count: int = 0
    triangle_count: int = 0
    connected_components: int = 0
    is_manifold: bool = False
    non_manifold_edges: int = 0
    boundary_edges: int = 0
    multi_face_edges: int = 0
    loose_verts: int = 0
    loose_edges: int = 0
    degenerate_faces: int = 0
    zero_area_faces: int = 0
    duplicate_verts: int = 0
    inverted_normals_count: int = 0
    mesh_density_tris_per_m3: float = 0.0
    surface_area: float = 0.0
    dimensions: list[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    # Topology
    quad_count: int = 0
    tri_face_count: int = 0
    ngon_count: int = 0
    quad_ratio: float = 0.0
    triangle_ratio: float = 0.0
    quad_to_tri_ratio: float = 0.0
    ngon_ratio: float = 0.0
    pole_vertex_ratio: float = 0.0
    edge_flow_score: float = 0.0
    subdivision_ready: bool = False
    subdivision_readiness_notes: str = ""
    mesh_cleanliness_score: float = 0.0
    # UV
    has_uv_map: bool = False
    uv_layer_count: int = 0
    uv_mapped_mesh_ratio: float = 0.0
    uv_coverage_ratio: float = 0.0
    uv_overlapping_islands_detected: bool = False
    uv_overlap_cell_ratio: float = 0.0
    uv_completeness_ratio: float = 0.0
    # Materials
    material_count: int = 0
    material_names: list[str] = Field(default_factory=list)
    texture_reference_count: int = 0
    available_image_textures: int = 0
    missing_image_textures: int = 0
    procedural_shader_count: int = 0
    principled_bsdf_count: int = 0
    material_node_count_avg: float = 0.0
    unassigned_material_faces: int = 0
    # Organization
    named_objects: list[str] = Field(default_factory=list)
    generic_named_objects: list[str] = Field(default_factory=list)
    naming_consistency_ratio: float = 0.0
    collection_count: int = 0
    collections: list[str] = Field(default_factory=list)
    object_separation_score: float = 0.0
    scene_organization_score: float = 0.0
    unapplied_scale_objects: list[str] = Field(default_factory=list)
    # Rigging & Animation
    armature_count: int = 0
    bone_count: int = 0
    has_rig: bool = False
    has_animations: bool = False
    action_count: int = 0
    weighted_mesh_count: int = 0
    # Presentation (explicitly separated from technical quality)
    camera_count: int = 0
    light_count: int = 0
    has_camera: bool = False
    has_lighting: bool = False
    lighting_quality_score: float = 0.0
    renderable: bool = False
    preview_image_count: int = 0
    # Raw details
    objects: list[dict[str, Any]] = Field(default_factory=list)
    error_message: Optional[str] = None
    log_path: Optional[str] = None
    inspected_at: str = Field(default_factory=utc_now_iso)


class DimensionScore(BaseModel):
    dimension: str
    score: float
    max_score: float
    percentage: float
    findings: list[str] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)


class QualityGradeReport(BaseModel):
    grade_id: str
    blendswap_id: Optional[int] = None
    local_asset_id: Optional[str] = None
    methodology_version: str = "2.0.0"
    grading_profile: str = "general"
    total_score: float
    grade_letter: str
    # Strictly separated scores
    technical_quality_score: float
    popularity_score: float = 0.0
    reference_usefulness_score: float = 0.0
    dimensions: list[DimensionScore] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    graded_at: str = Field(default_factory=utc_now_iso)


# ---------------------------------------------------------------------------
# Reference Library Records, Scheduler & Benchmarking
# ---------------------------------------------------------------------------

class ReferenceAssetStatus(str, Enum):
    DISCOVERED = "DISCOVERED"
    QUEUED = "QUEUED"
    DOWNLOADED = "DOWNLOADED"
    INSPECTED = "INSPECTED"
    GRADED = "GRADED"
    FAILED = "FAILED"
    SHORTAGE_SKIPPED = "SHORTAGE_SKIPPED"


class ReferenceAssetRecord(BaseModel):
    blendswap_id: int
    title: str
    url: str
    description: str = ""
    asset_type: str = "model_3d"
    asset_type_label: str = "3D Model"
    author: BlendSwapAuthor = Field(default_factory=BlendSwapAuthor)
    license_assessment: LicenseAssessment
    blendswap_category: BlendSwapCategoryInfo = Field(default_factory=BlendSwapCategoryInfo)
    primary_category: str = "Other"
    technical_categories: list[str] = Field(default_factory=list)
    quality_category: str = ""
    tags: list[str] = Field(default_factory=list)
    original_tags: list[str] = Field(default_factory=list)
    blender_versions: list[str] = Field(default_factory=list)
    counts: BlendSwapCounts = Field(default_factory=BlendSwapCounts)
    explicit: bool = False
    primary_format: str = "blender"
    is_native_blender: bool = True
    file_size_bytes: int = 0
    files: list[BlendSwapFileInfo] = Field(default_factory=list)
    local_dir: Optional[str] = None
    primary_file_path: Optional[str] = None
    blend_file_path: Optional[str] = None
    sha256: Optional[str] = None
    md5: Optional[str] = None
    preview_paths: list[str] = Field(default_factory=list)
    metadata_path: Optional[str] = None
    attribution_path: Optional[str] = None
    status: ReferenceAssetStatus = ReferenceAssetStatus.DISCOVERED
    error_message: Optional[str] = None
    popularity_score: float = 0.0
    selection_score: float = 0.0
    technical_quality_score: Optional[float] = None
    reference_usefulness_score: Optional[float] = None
    grade_letter: Optional[str] = None
    inspection: Optional[ReferenceInspectionReport] = None
    grade: Optional[QualityGradeReport] = None
    drive_sync_status: DriveSyncStatus = DriveSyncStatus.NOT_SYNCED
    drive_folder_id: Optional[str] = None
    drive_files: list[DriveFileRecord] = Field(default_factory=list)
    blendswap_created_at: Optional[str] = None
    downloaded_at: Optional[str] = None
    inspected_at: Optional[str] = None
    graded_at: Optional[str] = None
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)


class SelectionCandidate(BaseModel):
    asset: BlendSwapAssetSummary
    selected_file: BlendSwapFileInfo
    slot_number: int = 0
    slot_reason: str = ""
    selection_score: float = 0.0
    popularity_score: float = 0.0
    score_breakdown: dict[str, float] = Field(default_factory=dict)
    mapped_primary_category: str = "Other"
    modeling_style_hint: str = "general"


class CollectionCycleRecord(BaseModel):
    cycle_id: str
    cycle_date: str
    timezone: str = "Europe/London"
    trigger_type: str = "scheduled"
    status: str = "PENDING"
    target_count: int = 7
    free_allowance_start: Optional[int] = None
    free_allowance_end: Optional[int] = None
    credits_start: Optional[int] = None
    credits_end: Optional[int] = None
    credits_spent: int = 0
    downloaded_count: int = 0
    failed_count: int = 0
    shortage_count: int = 0
    shortage_reason: Optional[str] = None
    log_path: Optional[str] = None
    error_message: Optional[str] = None
    started_at: str = Field(default_factory=utc_now_iso)
    completed_at: Optional[str] = None
    updated_at: str = Field(default_factory=utc_now_iso)


class DownloadQueueItem(BaseModel):
    queue_id: str
    cycle_id: str
    blendswap_id: int
    file_id: Optional[int] = None
    slot_number: int
    slot_reason: str
    selection_score: float
    status: str = "PENDING"
    attempts: int = 0
    max_attempts: int = 3
    download_url: Optional[str] = None
    download_url_expires_at: Optional[str] = None
    last_error: Optional[str] = None
    next_retry_at: Optional[str] = None
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)


class ReferenceComparisonItem(BaseModel):
    blendswap_id: int
    title: str
    url: str
    primary_category: str
    reference_grade_letter: str
    reference_technical_score: float
    reference_usefulness_score: float
    reference_triangles: int
    reference_quad_ratio: float
    reference_uv_coverage: float
    reference_material_count: int
    match_reason: str


class BenchmarkReport(BaseModel):
    benchmark_id: str
    branch1_asset_id: str
    branch1_version: int
    branch1_name: str
    branch1_category: str
    grading_profile: str
    methodology_version: str = "2.0.0"
    local_grade: QualityGradeReport
    selected_references: list[ReferenceComparisonItem] = Field(default_factory=list)
    geometry_differences: list[str] = Field(default_factory=list)
    topology_weaknesses: list[str] = Field(default_factory=list)
    uv_weaknesses: list[str] = Field(default_factory=list)
    material_weaknesses: list[str] = Field(default_factory=list)
    organization_weaknesses: list[str] = Field(default_factory=list)
    recommended_improvements: list[str] = Field(default_factory=list)
    efficiency_note: str = ""
    report_path: Optional[str] = None
    created_at: str = Field(default_factory=utc_now_iso)
