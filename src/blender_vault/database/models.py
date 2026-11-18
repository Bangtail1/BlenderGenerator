"""Domain models, status state machine, and ID formatting for the Roblox Asset Library SQLite DB."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


def utc_now_iso() -> str:
    """Return an ISO-8601 UTC timestamp without microseconds."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class VisualStyle(str, Enum):
    LP = "LP"  # Low Poly
    SD = "SD"  # Stylized Detailed
    SR = "SR"  # Semi-Realistic
    SF = "SF"  # Sci-Fi / Hard Surface
    FD = "FD"  # Fantasy Detailed


class LibraryType(str, Enum):
    UNIVERSAL = "UNIVERSAL"
    GENRE = "GENRE"
    GAME_SPECIFIC = "GAME_SPECIFIC"


class ExportFormat(str, Enum):
    FBX = "FBX"
    GLB = "GLB"
    OBJ = "OBJ"


class ProductionStatus(str, Enum):
    PLANNED = "PLANNED"
    QUEUED = "QUEUED"
    MODELLING = "MODELLING"
    TEXTURING = "TEXTURING"
    EXPORTING = "EXPORTING"
    QA_PENDING = "QA_PENDING"
    QA_RUNNING = "QA_RUNNING"
    QA_PASSED = "QA_PASSED"
    QA_FAILED = "QA_FAILED"
    AWAITING_HUMAN_APPROVAL = "AWAITING_HUMAN_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    PUBLISHED = "PUBLISHED"
    ARCHIVED = "ARCHIVED"


class ValidationStatus(str, Enum):
    PENDING = "PENDING"
    PASSED = "PASSED"
    WARNING = "WARNING"
    FAILED = "FAILED"
    NOT_REQUIRED = "NOT_REQUIRED"


class StudioVerificationStatus(str, Enum):
    PENDING = "PENDING"
    PASSED = "PASSED"
    PASSED_WITH_WARNINGS = "PASSED_WITH_WARNINGS"
    FAILED = "FAILED"


class QAGrade(str, Enum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"


# Safe production status transitions (never allows QA_FAILED/REJECTED/QA_PENDING -> APPROVED directly)
ALLOWED_STATUS_TRANSITIONS: dict[ProductionStatus, frozenset[ProductionStatus]] = {
    ProductionStatus.PLANNED: frozenset({
        ProductionStatus.QUEUED,
        ProductionStatus.MODELLING,
        ProductionStatus.QA_PENDING,
        ProductionStatus.ARCHIVED,
    }),
    ProductionStatus.QUEUED: frozenset({
        ProductionStatus.MODELLING,
        ProductionStatus.TEXTURING,
        ProductionStatus.EXPORTING,
        ProductionStatus.QA_PENDING,
        ProductionStatus.ARCHIVED,
    }),
    ProductionStatus.MODELLING: frozenset({
        ProductionStatus.TEXTURING,
        ProductionStatus.EXPORTING,
        ProductionStatus.QA_PENDING,
        ProductionStatus.QA_FAILED,
        ProductionStatus.ARCHIVED,
    }),
    ProductionStatus.TEXTURING: frozenset({
        ProductionStatus.EXPORTING,
        ProductionStatus.QA_PENDING,
        ProductionStatus.MODELLING,
        ProductionStatus.QA_FAILED,
        ProductionStatus.ARCHIVED,
    }),
    ProductionStatus.EXPORTING: frozenset({
        ProductionStatus.QA_PENDING,
        ProductionStatus.MODELLING,
        ProductionStatus.QA_FAILED,
        ProductionStatus.ARCHIVED,
    }),
    ProductionStatus.QA_PENDING: frozenset({
        ProductionStatus.QA_RUNNING,
        ProductionStatus.QA_FAILED,
        ProductionStatus.MODELLING,
        ProductionStatus.ARCHIVED,
    }),
    ProductionStatus.QA_RUNNING: frozenset({
        ProductionStatus.QA_PASSED,
        ProductionStatus.QA_FAILED,
        ProductionStatus.QA_PENDING,
    }),
    ProductionStatus.QA_PASSED: frozenset({
        ProductionStatus.AWAITING_HUMAN_APPROVAL,
        ProductionStatus.QA_PENDING,
        ProductionStatus.ARCHIVED,
    }),
    ProductionStatus.QA_FAILED: frozenset({
        ProductionStatus.MODELLING,
        ProductionStatus.TEXTURING,
        ProductionStatus.EXPORTING,
        ProductionStatus.QA_PENDING,
        ProductionStatus.ARCHIVED,
    }),
    ProductionStatus.AWAITING_HUMAN_APPROVAL: frozenset({
        ProductionStatus.APPROVED,
        ProductionStatus.REJECTED,
        ProductionStatus.QA_FAILED,
        ProductionStatus.QA_PENDING,
        ProductionStatus.ARCHIVED,
    }),
    ProductionStatus.APPROVED: frozenset({
        ProductionStatus.PUBLISHED,
        ProductionStatus.ARCHIVED,
        ProductionStatus.MODELLING,
    }),
    ProductionStatus.REJECTED: frozenset({
        ProductionStatus.MODELLING,
        ProductionStatus.TEXTURING,
        ProductionStatus.EXPORTING,
        ProductionStatus.QA_PENDING,
        ProductionStatus.ARCHIVED,
    }),
    ProductionStatus.PUBLISHED: frozenset({
        ProductionStatus.ARCHIVED,
        ProductionStatus.MODELLING,
    }),
    ProductionStatus.ARCHIVED: frozenset({
        ProductionStatus.PLANNED,
        ProductionStatus.QUEUED,
        ProductionStatus.QA_PENDING,
    }),
}


class DatabaseError(RuntimeError):
    """Base error for Roblox Asset Library database operations."""


class DuplicateAssetIDError(DatabaseError):
    """Raised when attempting to register a duplicate permanent asset_id."""


class InvalidStatusTransitionError(ValueError):
    """Raised when attempting a disallowed production status transition."""


class ImmutableApprovedVersionError(ValueError):
    """Raised when attempting to overwrite or mutate a previously approved version."""


class CategoryNotFoundError(KeyError):
    """Raised when a referenced category cannot be found."""


class AssetNotFoundError(KeyError):
    """Raised when a referenced asset cannot be found."""


def sanitize_id_token(raw: str, *, default: str = "Asset", max_len: int = 32) -> str:
    """Normalize an object or variant string into Title_Case_Tokens safe for permanent asset IDs."""
    parts = [p for p in re.split(r"[^a-zA-Z0-9]+", (raw or "").strip()) if p]
    if not parts:
        return default
    formatted = "_".join(p[:1].upper() + p[1:] if p[0].isalpha() else p for p in parts)
    return formatted[:max_len].strip("_") or default


def format_asset_id(
    category_code: str,
    sequence_num: int,
    object_name: str,
    variant: str = "Basic",
    visual_style: VisualStyle | str = VisualStyle.LP,
    version_num: int = 1,
) -> str:
    """Format permanent ID: CATEGORY_ID_OBJECT_VARIANT_STYLE_VERSION (e.g. FUR_00001_Wooden_Chair_Basic_LP_v001)."""
    cat = re.sub(r"[^A-Z0-9]", "", (category_code or "").upper()) or "GEN"
    seq = f"{max(1, int(sequence_num)):05d}"
    obj = sanitize_id_token(object_name, default="Asset", max_len=36)
    var = sanitize_id_token(variant, default="Basic", max_len=24)
    style = visual_style.value if isinstance(visual_style, VisualStyle) else str(visual_style).upper()
    ver = f"v{max(1, int(version_num)):03d}"
    return f"{cat}_{seq}_{obj}_{var}_{style}_{ver}"


def format_version_tag(version_num: int) -> str:
    return f"v{max(1, int(version_num)):03d}"


def validate_status_transition(current: ProductionStatus, target: ProductionStatus) -> None:
    """Enforce safe production lifecycle transitions."""
    if current == target:
        return
    allowed = ALLOWED_STATUS_TRANSITIONS.get(current, frozenset())
    if target not in allowed:
        raise InvalidStatusTransitionError(
            f"Disallowed production status transition: {current.value} -> {target.value}. "
            f"Allowed targets: {', '.join(sorted(s.value for s in allowed)) or '(none)'}"
        )


class CategoryRecord(BaseModel):
    category_id: int
    category_code: str
    category_name: str
    parent_category_id: Optional[int] = None
    category_description: str = ""
    library_type: LibraryType = LibraryType.UNIVERSAL
    legacy_category: Optional[str] = None
    created_at: str = Field(default_factory=utc_now_iso)


class RobloxAssetRecord(BaseModel):
    id: int
    asset_id: str
    asset_name: str
    display_name: str
    category_id: int
    asset_description: str = ""
    visual_style: VisualStyle = VisualStyle.LP
    production_status: ProductionStatus = ProductionStatus.PLANNED
    source_blend_path: Optional[str] = None
    google_drive_file_id: Optional[str] = None
    google_drive_folder_id: Optional[str] = None
    google_drive_url: Optional[str] = None
    export_format: Optional[ExportFormat] = None
    export_path: Optional[str] = None
    triangle_count: int = 0
    vertex_count: int = 0
    polygon_count: int = 0
    dimensions_x: float = 0.0
    dimensions_y: float = 0.0
    dimensions_z: float = 0.0
    texture_resolution: str = ""
    material_count: int = 0
    uv_status: str = ValidationStatus.PENDING.value
    collision_status: str = ValidationStatus.PENDING.value
    qa_score: Optional[float] = None
    qa_status: str = ValidationStatus.PENDING.value
    qa_grade: Optional[str] = None
    human_approved: bool = False
    approved_by: Optional[str] = None
    approved_at: Optional[str] = None
    current_version: int = 1
    object_name: str = ""
    variant_name: str = "Basic"
    legacy_vault_id: Optional[str] = None
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)
    tags: list[str] = Field(default_factory=list)
    genre_category_ids: list[int] = Field(default_factory=list)


class AssetVersionRecord(BaseModel):
    version_id: int
    asset_id: str
    version_number: int
    blender_source_path: Optional[str] = None
    google_drive_file_id: Optional[str] = None
    google_drive_folder_id: Optional[str] = None
    google_drive_url: Optional[str] = None
    export_format: Optional[str] = None
    export_path: Optional[str] = None
    change_description: str = ""
    created_at: str = Field(default_factory=utc_now_iso)
    qa_status: str = ValidationStatus.PENDING.value
    qa_score: Optional[float] = None
    is_approved: bool = False
    approved_by: Optional[str] = None
    approved_at: Optional[str] = None
    studio_verification_status: str = StudioVerificationStatus.PENDING.value
    sha256: Optional[str] = None


class StudioVerificationRecord(BaseModel):
    verification_id: int
    asset_id: str
    version_id: int
    version_number: Optional[int] = None
    status: StudioVerificationStatus = StudioVerificationStatus.PENDING
    fbx_import_ok: bool = False
    orientation_ok: bool = False
    scale_ok: bool = False
    material_appearance_ok: bool = False
    collision_test_ok: bool = False
    studio_warnings: list[str] = Field(default_factory=list)
    studio_errors: list[str] = Field(default_factory=list)
    notes: str = ""
    verified_by: str = ""
    verified_at: str = Field(default_factory=utc_now_iso)


class QAReportRecord(BaseModel):
    report_id: int
    asset_id: str
    version_id: int
    overall_score: float
    mesh_integrity_score: float
    geometry_score: float
    polygon_efficiency_score: float
    uv_score: float
    material_score: float
    scale_score: float
    roblox_compatibility_score: float
    collision_score: float
    visual_score: float
    organisation_score: float
    final_status: str
    grade: Optional[str] = None
    failure_reasons: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    report_json_path: Optional[str] = None
    report_html_path: Optional[str] = None
    details: dict[str, Any] = Field(default_factory=dict)
    test_timestamp: str = Field(default_factory=utc_now_iso)


class ProductionLogRecord(BaseModel):
    log_id: int
    asset_id: Optional[str] = None
    process_name: str
    status: str
    message: str
    error_details: Optional[str] = None
    timestamp: str = Field(default_factory=utc_now_iso)


class DriveUploadQueueRecord(BaseModel):
    queue_id: int
    asset_id: str
    version_id: int
    payload: dict[str, Any] = Field(default_factory=dict)
    status: str = "PENDING"
    attempts: int = 0
    max_attempts: int = 5
    last_error: Optional[str] = None
    next_retry_at: Optional[str] = None
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)
