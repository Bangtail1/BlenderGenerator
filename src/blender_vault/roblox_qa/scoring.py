"""100-point Roblox QA scoring aggregator, grade assignment, and mandatory approval gate."""

from __future__ import annotations

from typing import Optional

from .models import DimensionScoreResult


def score_to_grade(score: float) -> str:
    if score >= 90.0:
        return "A"
    if score >= 80.0:
        return "B"
    if score >= 70.0:
        return "C"
    return "D"


def compute_final_qa_verdict(
    dimensions: list[DimensionScoreResult],
    *,
    minimum_passing_score: float = 80.0,
) -> dict[str, object]:
    """Aggregate 10 dimension scores into a 0..100 overall score, grade, and pass/fail verdict."""
    overall = round(min(100.0, max(0.0, sum(d.score for d in dimensions))), 2)
    grade = score_to_grade(overall)

    mandatory_failures: list[str] = []
    warnings: list[str] = []
    recommendations: list[str] = []

    for d in dimensions:
        for f in d.findings:
            if f.status == "FAIL" and f.mandatory:
                mandatory_failures.append(f"[{d.label}] {f.message}")
            elif f.status == "FAIL" and f.message not in mandatory_failures:
                mandatory_failures.append(f"[{d.label}] {f.message}")
        for w in d.warnings:
            if w not in warnings:
                warnings.append(w)
        for r in d.recommendations:
            if r not in recommendations:
                recommendations.append(r)

    # Mandatory checks MUST pass AND score must be Grade A or B (>= 80) to qualify
    passes_score = overall >= minimum_passing_score and grade in {"A", "B"}
    passes_mandatory = len(mandatory_failures) == 0

    if passes_score and passes_mandatory:
        final_status = "QA_PASSED"
        approval_eligible = True
    else:
        final_status = "QA_FAILED"
        approval_eligible = False
        if not passes_score and not mandatory_failures:
            mandatory_failures.append(
                f"Overall QA score {overall:.1f}/100 (Grade {grade}) is below required Grade B ({minimum_passing_score:.0f}) threshold."
            )

    individual_scores = {d.key: round(d.score, 2) for d in dimensions}
    return {
        "overall_score": overall,
        "grade": grade,
        "final_status": final_status,
        "approval_eligible": approval_eligible,
        "individual_scores": individual_scores,
        "mandatory_failures": mandatory_failures,
        "warnings": warnings,
        "recommendations": recommendations,
    }
