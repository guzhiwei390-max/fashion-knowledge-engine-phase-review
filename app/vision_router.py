from typing import Any

from .unknown import UNKNOWN


VISION_ALLOWED_ASSET_TYPES = {
    UNKNOWN,
    "official_product_image",
    "official_model_image",
    "official_detail_image",
    "reality_product_photo",
    "human_wearing_photo",
    "multi_product_photo",
    "detail_image",
}

VISION_BLOCKED_ASSET_TYPES = {
    "duplicate",
    "low_quality",
    "scene_photo",
}


def vision_route_decision(
    *,
    asset_type: str = UNKNOWN,
    quality_status: str = UNKNOWN,
    duplicate_status: str = "unique",
    has_official_candidate: bool,
    match_confidence: float,
    needs_structure_detail: bool,
    budget_allowed: bool = True,
) -> dict[str, Any]:
    if duplicate_status != "unique":
        return blocked("duplicate")
    if quality_status in {"corrupted", "low_quality", "duplicate"}:
        return blocked(quality_status)
    if asset_type in VISION_BLOCKED_ASSET_TYPES:
        return blocked(asset_type)
    if asset_type not in VISION_ALLOWED_ASSET_TYPES and not has_official_candidate:
        return blocked("not_product_candidate")
    if has_official_candidate and match_confidence >= 0.92 and not needs_structure_detail:
        return {
            "decision": "skip",
            "reason": "high_confidence_local_match",
            "requires_manual_confirm": False,
            "allowed": False,
        }

    needs_vision = (
        not has_official_candidate
        or match_confidence < 0.92
        or needs_structure_detail
        or asset_type in {"human_wearing_photo", "multi_product_photo", "official_detail_image", "detail_image"}
    )
    if not needs_vision:
        return blocked("no_vision_needed")
    if not budget_allowed:
        return {
            "decision": "paused_budget",
            "reason": "vision_budget_exhausted",
            "requires_manual_confirm": True,
            "allowed": False,
        }
    return {
        "decision": "allow",
        "reason": "expert_review_required",
        "requires_manual_confirm": False,
        "allowed": True,
    }


def blocked(reason: str) -> dict[str, Any]:
    return {
        "decision": "skip",
        "reason": reason,
        "requires_manual_confirm": False,
        "allowed": False,
    }
