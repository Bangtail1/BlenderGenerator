"""Core Pydantic domain models, statuses, QA schemas, and manifest definitions."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class AssetStatus(str, Enum):
    PROCESSING = "PROCESSING"
    TECHNICAL_TESTING = "TECHNICAL_TESTING"
    VISUAL_TESTING = "VISUAL_TESTING"
    AWAITING_HUMAN_APPROVAL = "AWAITING_HUMAN_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    UPLOADED = "UPLOADED"
    FAILED = "FAILED"


class TechnicalQAStatus(str, Enum):
    PENDING = "PENDING"
    PASS = "PASS"
    PASS_WITH_WARNINGS = "PASS_WITH_WARNINGS"
    FAIL = "FAIL"


class VisualQAStatus(str, Enum):
    PENDING = "PENDING"
    UNCONFIGURED = "UNCONFIGURED"
    PASS_CANDIDATE = "PASS_CANDIDATE"
    NEEDS_HUMAN_REVIEW = "NEEDS_HUMAN_REVIEW"
    FAIL_CANDIDATE = "FAIL_CANDIDATE"
    ERROR = "ERROR"


class HumanApprovalStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ProvenanceType(str, Enum):
    GENERATED_ORIGINAL = "generated_original"
    USER_UPLOADED = "user_uploaded"
    EXTERNAL_IMPORT = "external_import"


class DriveSyncStatus(str, Enum):
    NOT_SYNCED = "NOT_SYNCED"
    PENDING = "PENDING"
    UPLOADING = "UPLOADING"
    UPLOADED = "UPLOADED"
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"


class GeometryProfile(str, Enum):
    """Category-aware geometry validation profile."""

    SOLID_MECHANICAL = "solid_mechanical"
    FURNITURE_PROP = "furniture_prop"
    ORGANIC_NATURE = "organic_nature"
    THIN_SHEET_CLOTH = "thin_sheet_cloth"
    ARCHITECTURE = "architecture"
    CHARACTER = "character"
    GENERAL = "general"


CATEGORY_TO_DEFAULT_PROFILE: dict[str, GeometryProfile] = {
    "Architecture": GeometryProfile.ARCHITECTURE,
    "Furniture": GeometryProfile.FURNITURE_PROP,
    "Characters": GeometryProfile.CHARACTER,
    "Mechanical Objects": GeometryProfile.SOLID_MECHANICAL,
    "Vehicles": GeometryProfile.FURNITURE_PROP,
    "Household Objects": GeometryProfile.FURNITURE_PROP,
    "Nature": GeometryProfile.ORGANIC_NATURE,
    "Miscellaneous": GeometryProfile.GENERAL,
}


class ProvenanceInfo(BaseModel):
    provenance_type: ProvenanceType
    author: Optional[str] = None
    source_url: Optional[str] = None
    license_name: str = "Unknown / Unverified"
    usage_restrictions: str = "Provenance unverified — do not assume commercial or redistribution rights."
    provenance_verified: bool = False
    notes: str = ""

    @classmethod
    def for_generated_original(cls, generator_name: str) -> "ProvenanceInfo":
        return cls(
            provenance_type=ProvenanceType.GENERATED_ORIGINAL,
            author="Blender Asset Vault Procedural Engine",
            source_url=None,
            license_name="Original Generated Asset (User Owned)",
            usage_restrictions="Generated locally via procedural Blender Python template.",
            provenance_verified=True,
            notes=f"Generator template: {generator_name}",
        )

    @classmethod
    def for_import(
        cls,
        provenance_type: ProvenanceType,
        author: Optional[str] = None,
        source_url: Optional[str] = None,
        license_name: Optional[str] = None,
        usage_restrictions: Optional[str] = None,
        notes: str = "",
    ) -> "ProvenanceInfo":
        # Never assume ownership or unrestricted rights if unknown
        has_license = bool(license_name and license_name.strip() and license_name.strip().lower() != "unknown")
        has_origin = bool((author and author.strip()) or (source_url and source_url.strip()))
        if provenance_type == ProvenanceType.USER_UPLOADED and not license_name:
            resolved_license = "User Uploaded (License Unspecified)"
            resolved_restrictions = "Uploaded by user; confirm third-party rights before redistribution."
        elif not has_license:
            resolved_license = "Unknown / Unverified"
            resolved_restrictions = "Provenance or license unknown — do not assume ownership or commercial rights."
        else:
            resolved_license = license_name.strip()
            resolved_restrictions = (
                usage_restrictions.strip()
                if usage_restrictions and usage_restrictions.strip()
                else f"Governed by stated license: {resolved_license}"
            )
        return cls(
            provenance_type=provenance_type,
            author=author.strip() if author else None,
            source_url=source_url.strip() if source_url else None,
            license_name=resolved_license,
            usage_restrictions=resolved_restrictions,
            provenance_verified=has_license and (has_origin or provenance_type == ProvenanceType.USER_UPLOADED),
            notes=notes,
        )


class QACheckItem(BaseModel):
    check_id: str
    name: str
    category: str  # geometry | structure | materials | round_trip
    status: str    # PASS | WARN | FAIL | SKIP
    severity: str  # info | warning | error
    measured: Any = None
    threshold: Any = None
    message: str


class MeshObjectMetrics(BaseModel):
    name: str
    collection: str = "Scene Collection"
    vertex_count: int = 0
    edge_count: int = 0
    face_count: int = 0
    tri_count: int = 0
    dimensions: list[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    scale: list[float] = Field(default_factory=lambda: [1.0, 1.0, 1.0])
    rotation_euler: list[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    origin_location: list[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    bbox_min: list[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    bbox_max: list[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    surface_area: float = 0.0
    signed_volume: float = 0.0
    non_manifold_edges: int = 0
    boundary_edges: int = 0
    multi_face_edges: int = 0
    loose_verts: int = 0
    loose_edges: int = 0
    degenerate_faces: int = 0
    zero_area_faces: int = 0
    duplicate_verts: int = 0
    inverted_normals_count: int = 0
    has_uv: bool = False
    uv_layer_names: list[str] = Field(default_factory=list)
    material_count: int = 0
    materials: list[str] = Field(default_factory=list)
    unassigned_material_faces: int = 0
    modifiers: list[dict[str, Any]] = Field(default_factory=list)


class RoundTripReport(BaseModel):
    attempted: bool = False
    success: bool = False
    glb_path: Optional[str] = None
    original_mesh_count: int = 0
    reimported_mesh_count: int = 0
    original_dimensions: list[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    reimported_dimensions: list[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    dimension_delta_max: float = 0.0
    material_count_before: int = 0
    material_count_after: int = 0
    tri_count_before: int = 0
    tri_count_after: int = 0
    error: Optional[str] = None


class TechnicalQAReport(BaseModel):
    status: TechnicalQAStatus = TechnicalQAStatus.PENDING
    geometry_profile: GeometryProfile = GeometryProfile.GENERAL
    blender_version: str = ""
    file_opened_ok: bool = False
    object_count: int = 0
    mesh_object_count: int = 0
    total_vertices: int = 0
    total_edges: int = 0
    total_faces: int = 0
    total_tris: int = 0
    dimensions: list[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    materials: list[str] = Field(default_factory=list)
    collections: list[str] = Field(default_factory=list)
    missing_dependencies: list[str] = Field(default_factory=list)
    objects: list[MeshObjectMetrics] = Field(default_factory=list)
    round_trip: RoundTripReport = Field(default_factory=RoundTripReport)
    checks: list[QACheckItem] = Field(default_factory=list)
    warnings_count: int = 0
    failures_count: int = 0
    summary: str = ""
    inspected_at: str = Field(default_factory=utc_now_iso)


class VisualQAReport(BaseModel):
    provider: str = "unconfigured"
    model: str = "none"
    status: VisualQAStatus = VisualQAStatus.PENDING
    score: Optional[float] = None
    confidence: float = 0.0
    recommendation: str = "NEEDS_HUMAN_REVIEW"
    summary: str = ""
    matches_description: Optional[bool] = None
    component_check: str = ""
    proportion_check: str = ""
    surface_and_materials_check: str = ""
    issues_found: list[str] = Field(default_factory=list)
    render_paths: dict[str, str] = Field(default_factory=dict)
    turntable_path: Optional[str] = None
    contact_sheet_path: Optional[str] = None
    render_sanity_ok: bool = False
    render_sanity_details: dict[str, Any] = Field(default_factory=dict)
    analyzed_at: str = Field(default_factory=utc_now_iso)


class HumanReviewRecord(BaseModel):
    """Distinct Stage 2 human review state — never overwritten by automated VLM."""

    status: HumanApprovalStatus = HumanApprovalStatus.PENDING
    reviewer: Optional[str] = None
    notes: str = ""
    reviewed_at: Optional[str] = None


class DriveFileRecord(BaseModel):
    role: str  # blend | glb | manifest | qa_report | provenance | preview | turntable
    local_path: str
    remote_file_id: Optional[str] = None
    remote_name: str
    remote_folder_id: Optional[str] = None
    expected_size: int = 0
    remote_size: Optional[int] = None
    local_md5: Optional[str] = None
    remote_md5: Optional[str] = None
    local_sha256: Optional[str] = None
    verified: bool = False
    web_view_link: Optional[str] = None
    synced_at: Optional[str] = None


class AssetVersion(BaseModel):
    asset_id: str
    version: int
    status: AssetStatus = AssetStatus.PROCESSING
    blend_path: Optional[str] = None
    glb_path: Optional[str] = None
    source_original_path: Optional[str] = None
    blender_version: str = ""
    file_formats: list[str] = Field(default_factory=list)
    file_size_bytes: int = 0
    sha256: Optional[str] = None
    md5: Optional[str] = None
    poly_count: int = 0
    vertex_count: int = 0
    dimensions: list[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    materials: list[str] = Field(default_factory=list)
    technical_qa: Optional[TechnicalQAReport] = None
    visual_qa: Optional[VisualQAReport] = None
    human_review: HumanReviewRecord = Field(default_factory=HumanReviewRecord)
    drive_sync_status: DriveSyncStatus = DriveSyncStatus.NOT_SYNCED
    drive_folder_id: Optional[str] = None
    drive_files: list[DriveFileRecord] = Field(default_factory=list)
    error_message: Optional[str] = None
    failed_stage: Optional[str] = None
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)


class AssetRecord(BaseModel):
    asset_id: str
    slug: str
    name: str
    description: str = ""
    category: str = "Miscellaneous"
    geometry_profile: GeometryProfile = GeometryProfile.GENERAL
    tags: list[str] = Field(default_factory=list)
    provenance: ProvenanceInfo
    current_version: int = 1
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)
    latest: Optional[AssetVersion] = None
    versions: list[AssetVersion] = Field(default_factory=list)


class AssetManifest(BaseModel):
    """Self-contained manifest stored with every asset version locally & in Google Drive."""

    schema_version: str = "1.0"
    asset_id: str
    slug: str
    name: str
    description: str
    category: str
    geometry_profile: GeometryProfile
    tags: list[str]
    provenance: ProvenanceInfo
    version: AssetVersion
    generated_at: str = Field(default_factory=utc_now_iso)


class GenerateAssetRequest(BaseModel):
    prompt: str
    name: Optional[str] = None
    category: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    template_id: Optional[str] = None
    geometry_profile: Optional[GeometryProfile] = None
    params: dict[str, Any] = Field(default_factory=dict)


class HumanReviewRequest(BaseModel):
    decision: HumanApprovalStatus
    reviewer: str = "Local Reviewer"
    notes: str = ""
    category: Optional[str] = None
