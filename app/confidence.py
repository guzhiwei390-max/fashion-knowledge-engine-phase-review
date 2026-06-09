from dataclasses import dataclass
from typing import Any

from .unknown import UNKNOWN


HIGH_CONFIDENCE_THRESHOLD = 0.86
REVIEW_CONFIDENCE_THRESHOLD = 0.60
VISUAL_PREFILTER_THRESHOLD = 0.74


@dataclass(frozen=True)
class ConfidenceDecision:
    decision: str
    reason: str
    confidence: float

    @property
    def accepted(self) -> bool:
        return self.decision == "accepted"

    @property
    def requires_review(self) -> bool:
        return self.decision == "review"


def evaluate_match_confidence(
    *,
    confidence: float,
    has_official_match: bool,
    conflict: bool = False,
    duplicate: bool = False,
) -> ConfidenceDecision:
    normalized = max(0.0, min(float(confidence or 0.0), 1.0))
    if duplicate:
        return ConfidenceDecision("review", "duplicate", normalized)
    if conflict:
        return ConfidenceDecision("review", "conflict", normalized)
    if not has_official_match:
        return ConfidenceDecision("unknown", "unknown", normalized)
    if normalized >= HIGH_CONFIDENCE_THRESHOLD:
        return ConfidenceDecision("accepted", "high_confidence", normalized)
    if normalized >= REVIEW_CONFIDENCE_THRESHOLD:
        return ConfidenceDecision("review", "low_confidence", normalized)
    return ConfidenceDecision("unknown", "unknown", normalized)


def confidence_status(value: Any) -> str:
    if value in (None, "", UNKNOWN):
        return UNKNOWN
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return UNKNOWN
    return evaluate_match_confidence(confidence=confidence, has_official_match=True).decision
