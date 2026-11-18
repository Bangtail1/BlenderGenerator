"""License intelligence engine: normalization, A-G classification, and attribution generation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..models import utc_now_iso
from .models import (
    AttributionRecord,
    BlendSwapAssetSummary,
    BlendSwapLicenseInfo,
    LicenseAssessment,
    LicenseCategory,
    LicenseClass,
)


@dataclass(frozen=True)
class _LicenseRule:
    key: str
    display_name: str
    default_url: str
    category: LicenseCategory
    primary_class: LicenseClass
    applicable_classes: tuple[LicenseClass, ...]
    suitable_for_benchmarking: bool
    requires_attribution: bool
    share_alike_obligation: bool
    non_commercial_only: bool
    no_derivatives: bool
    requires_additional_review: bool
    warnings: tuple[str, ...]


_LICENSE_RULES: dict[str, _LicenseRule] = {
    "cc0": _LicenseRule(
        key="cc0",
        display_name="CC0",
        default_url="https://creativecommons.org/publicdomain/zero/1.0/",
        category=LicenseCategory.CC0,
        primary_class=LicenseClass.A_TECHNICAL_BENCHMARK_SUITABLE,
        applicable_classes=(LicenseClass.A_TECHNICAL_BENCHMARK_SUITABLE,),
        suitable_for_benchmarking=True,
        requires_attribution=False,
        share_alike_obligation=False,
        non_commercial_only=False,
        no_derivatives=False,
        requires_additional_review=False,
        warnings=("Uploader-declared CC0; not legally verified by BlendSwap.",),
    ),
    "cc_by": _LicenseRule(
        key="cc_by",
        display_name="CC-BY",
        default_url="https://creativecommons.org/licenses/by/4.0/",
        category=LicenseCategory.CC_BY,
        primary_class=LicenseClass.B_ATTRIBUTION_REQUIRED,
        applicable_classes=(LicenseClass.A_TECHNICAL_BENCHMARK_SUITABLE, LicenseClass.B_ATTRIBUTION_REQUIRED),
        suitable_for_benchmarking=True,
        requires_attribution=True,
        share_alike_obligation=False,
        non_commercial_only=False,
        no_derivatives=False,
        requires_additional_review=False,
        warnings=("Attribution required if referenced publicly; uploader-declared only.",),
    ),
    "cc_by_sa": _LicenseRule(
        key="cc_by_sa",
        display_name="CC-BY-SA",
        default_url="https://creativecommons.org/licenses/by-sa/4.0/",
        category=LicenseCategory.CC_BY_SA,
        primary_class=LicenseClass.C_SHARE_ALIKE,
        applicable_classes=(
            LicenseClass.A_TECHNICAL_BENCHMARK_SUITABLE,
            LicenseClass.B_ATTRIBUTION_REQUIRED,
            LicenseClass.C_SHARE_ALIKE,
        ),
        suitable_for_benchmarking=True,
        requires_attribution=True,
        share_alike_obligation=True,
        non_commercial_only=False,
        no_derivatives=False,
        requires_additional_review=False,
        warnings=("Share-Alike & attribution apply to derivatives; keep strictly isolated for independent benchmarking.",),
    ),
    "cc_by_nd": _LicenseRule(
        key="cc_by_nd",
        display_name="CC-BY-ND",
        default_url="https://creativecommons.org/licenses/by-nd/4.0/",
        category=LicenseCategory.OTHER_CC,
        primary_class=LicenseClass.E_NO_DERIVATIVES,
        applicable_classes=(
            LicenseClass.A_TECHNICAL_BENCHMARK_SUITABLE,
            LicenseClass.B_ATTRIBUTION_REQUIRED,
            LicenseClass.E_NO_DERIVATIVES,
        ),
        suitable_for_benchmarking=True,
        requires_attribution=True,
        share_alike_obligation=False,
        non_commercial_only=False,
        no_derivatives=True,
        requires_additional_review=False,
        warnings=("No-Derivatives license: never modify/redistribute or derive geometry from this asset.",),
    ),
    "cc_by_nc": _LicenseRule(
        key="cc_by_nc",
        display_name="CC-BY-NC",
        default_url="https://creativecommons.org/licenses/by-nc/4.0/",
        category=LicenseCategory.OTHER_CC,
        primary_class=LicenseClass.D_NON_COMMERCIAL,
        applicable_classes=(
            LicenseClass.A_TECHNICAL_BENCHMARK_SUITABLE,
            LicenseClass.B_ATTRIBUTION_REQUIRED,
            LicenseClass.D_NON_COMMERCIAL,
        ),
        suitable_for_benchmarking=True,
        requires_attribution=True,
        share_alike_obligation=False,
        non_commercial_only=True,
        no_derivatives=False,
        requires_additional_review=False,
        warnings=("Non-Commercial restriction: internal technical benchmarking only; never redistribute commercially.",),
    ),
    "cc_by_nc_sa": _LicenseRule(
        key="cc_by_nc_sa",
        display_name="CC-BY-NC-SA",
        default_url="https://creativecommons.org/licenses/by-nc-sa/4.0/",
        category=LicenseCategory.OTHER_CC,
        primary_class=LicenseClass.D_NON_COMMERCIAL,
        applicable_classes=(
            LicenseClass.A_TECHNICAL_BENCHMARK_SUITABLE,
            LicenseClass.B_ATTRIBUTION_REQUIRED,
            LicenseClass.C_SHARE_ALIKE,
            LicenseClass.D_NON_COMMERCIAL,
        ),
        suitable_for_benchmarking=True,
        requires_attribution=True,
        share_alike_obligation=True,
        non_commercial_only=True,
        no_derivatives=False,
        requires_additional_review=False,
        warnings=("Non-Commercial + Share-Alike + Attribution restrictions apply.",),
    ),
    "cc_by_nc_nd": _LicenseRule(
        key="cc_by_nc_nd",
        display_name="CC-BY-NC-ND",
        default_url="https://creativecommons.org/licenses/by-nc-nd/4.0/",
        category=LicenseCategory.OTHER_CC,
        primary_class=LicenseClass.E_NO_DERIVATIVES,
        applicable_classes=(
            LicenseClass.A_TECHNICAL_BENCHMARK_SUITABLE,
            LicenseClass.B_ATTRIBUTION_REQUIRED,
            LicenseClass.D_NON_COMMERCIAL,
            LicenseClass.E_NO_DERIVATIVES,
        ),
        suitable_for_benchmarking=True,
        requires_attribution=True,
        share_alike_obligation=False,
        non_commercial_only=True,
        no_derivatives=True,
        requires_additional_review=False,
        warnings=("Non-Commercial + No-Derivatives + Attribution restrictions apply.",),
    ),
    "gal": _LicenseRule(
        key="gal",
        display_name="GAL",
        default_url="https://blendswap.com/terms/gal",
        category=LicenseCategory.GAL,
        primary_class=LicenseClass.F_GAL_ADDITIONAL_REVIEW,
        applicable_classes=(
            LicenseClass.A_TECHNICAL_BENCHMARK_SUITABLE,
            LicenseClass.B_ATTRIBUTION_REQUIRED,
            LicenseClass.F_GAL_ADDITIONAL_REVIEW,
        ),
        suitable_for_benchmarking=True,
        requires_attribution=True,
        share_alike_obligation=False,
        non_commercial_only=False,
        no_derivatives=False,
        requires_additional_review=True,
        warnings=("BlendSwap General Asset License (GAL) requires additional review before any external reuse.",),
    ),
    "commercial": _LicenseRule(
        key="commercial",
        display_name="BlendSwap Commercial",
        default_url="https://blendswap.com/terms/commercial",
        category=LicenseCategory.COMMERCIAL,
        primary_class=LicenseClass.G_COMMERCIAL_ADDITIONAL_REVIEW,
        applicable_classes=(LicenseClass.G_COMMERCIAL_ADDITIONAL_REVIEW,),
        suitable_for_benchmarking=False,
        requires_attribution=True,
        share_alike_obligation=False,
        non_commercial_only=False,
        no_derivatives=True,
        requires_additional_review=True,
        warnings=("Commercial license requires additional legal/purchase review; excluded from automated free collection.",),
    ),
}


def normalize_license_key(raw_key: Optional[str]) -> str:
    """Normalize BlendSwap or legacy license identifiers (e.g., CC-BY-NC-SA -> cc_by_nc_sa)."""
    text = (raw_key or "").strip().lower().replace("-", "_").replace(" ", "_")
    for suffix in ("_4.0", "_3.0", "_2.0", "_1.0"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
    if text in _LICENSE_RULES:
        return text
    if "commercial" in text:
        return "commercial"
    if "cc0" in text or "public_domain" in text:
        return "cc0"
    if "gal" in text:
        return "gal"
    # Fallback conservative classification
    return "gal"


def assess_license(info: BlendSwapLicenseInfo, *, license_source_override: Optional[str] = None) -> LicenseAssessment:
    """Classify a BlendSwap uploader-declared license into Classes A–G with explicit verification disclaimer."""
    key = normalize_license_key(info.key or info.name)
    rule = _LICENSE_RULES[key]
    source = (license_source_override or info.license_source or "uploader_declared").strip() or "uploader_declared"

    return LicenseAssessment(
        license_key=rule.key,
        license_name=info.name.strip() if info.name and info.name.strip() else rule.display_name,
        license_url=info.url.strip() if info.url and info.url.strip() else rule.default_url,
        license_source=source,
        legally_verified=False,  # BlendSwap licenses are NEVER independently legally verified
        verification_disclaimer=(
            f"Declared by uploader ({source}); BlendSwap and this system have NOT legally verified this license."
        ),
        license_category=rule.category,
        primary_class=rule.primary_class,
        applicable_classes=list(rule.applicable_classes),
        suitable_for_benchmarking=rule.suitable_for_benchmarking,
        requires_attribution=rule.requires_attribution,
        share_alike_obligation=rule.share_alike_obligation,
        non_commercial_only=rule.non_commercial_only,
        no_derivatives=rule.no_derivatives,
        requires_additional_review=rule.requires_additional_review,
        review_warnings=list(rule.warnings),
    )


def build_attribution_record(
    summary: BlendSwapAssetSummary,
    assessment: LicenseAssessment,
    *,
    download_date: Optional[str] = None,
) -> AttributionRecord:
    """Build a complete attribution and provenance record for a downloaded BlendSwap asset."""
    dl_date = download_date or utc_now_iso()
    author_name = summary.author.username or "Unknown Author"
    attribution_text = (
        f'"{summary.title}" (BlendSwap #{summary.id}) by {author_name} '
        f"({summary.author.url or summary.url}), declared license: {assessment.license_name} "
        f"({assessment.license_url}) [source: {assessment.license_source}; unverified]."
    )
    return AttributionRecord(
        blendswap_id=summary.id,
        asset_title=summary.title,
        asset_url=summary.url,
        author_username=author_name,
        author_profile_url=summary.author.url,
        download_date=dl_date,
        license_key=assessment.license_key,
        license_name=assessment.license_name,
        license_url=assessment.license_url,
        license_source=assessment.license_source,
        legally_verified=False,
        license_classes=[c.value for c in assessment.applicable_classes],
        attribution_text=attribution_text,
        original_description=summary.description,
        original_tags=list(summary.tags),
    )
