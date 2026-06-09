from typing import Any

from .unknown import UNKNOWN


STRUCTURE_EVIDENCE_FIELDS = (
    "collar",
    "zipper",
    "logo_position",
    "stitching",
    "back_structure",
    "sleeve_structure",
    "hem_shape",
    "fit_shape",
    "pocket",
    "hardware",
    "material_behavior",
)

STRUCTURE_SOURCE_PRIORITY = {
    "official_visual_reference": 3,
    "vision_structure": 2,
    "openai_vision_structure": 2,
    "human_correction": 4,
}


def known(value: Any) -> bool:
    return value not in (None, "", UNKNOWN)


def empty_structure_evidence() -> dict[str, dict[str, Any]]:
    return {
        field: {
            "result": UNKNOWN,
            "value": UNKNOWN,
            "confidence": 0.0,
            "source": UNKNOWN,
            "evidence_asset_ids": [],
            "visible_evidence": [],
        }
        for field in STRUCTURE_EVIDENCE_FIELDS
    }


def normalize_structure_aliases(structure: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(structure or {})
    if known(normalized.get("logo")) and not known(normalized.get("logo_position")):
        normalized["logo_position"] = normalized["logo"]
    if known(normalized.get("material_visual_behavior")) and not known(normalized.get("material_behavior")):
        normalized["material_behavior"] = normalized["material_visual_behavior"]
    if known(normalized.get("sleeve")) and not known(normalized.get("sleeve_structure")):
        normalized["sleeve_structure"] = normalized["sleeve"]
    if known(normalized.get("fit")) and not known(normalized.get("fit_shape")):
        normalized["fit_shape"] = normalized["fit"]
    return normalized


def structure_evidence_from_observation(
    structure: dict[str, Any],
    *,
    asset_id: str | None = None,
    source: str = "vision_structure",
    confidence: float = 0.0,
) -> dict[str, dict[str, Any]]:
    normalized = normalize_structure_aliases(structure)
    evidence = empty_structure_evidence()
    visible = [str(item) for item in normalized.get("visible_evidence", []) if str(item).strip()]
    for field in STRUCTURE_EVIDENCE_FIELDS:
        value = normalized.get(field, UNKNOWN)
        if known(value):
            evidence[field] = {
                "result": "Known",
                "value": str(value),
                "confidence": float(confidence or 0.0),
                "source": source,
                "evidence_asset_ids": [asset_id] if asset_id else [],
                "visible_evidence": visible,
            }
    return evidence


def merge_structure_evidence(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    merged = empty_structure_evidence()
    for item in items:
        source = str(item.get("source", UNKNOWN))
        for field in STRUCTURE_EVIDENCE_FIELDS:
            candidate = item.get(field, {})
            if not isinstance(candidate, dict) or candidate.get("result") != "Known":
                continue
            current = merged[field]
            candidate_score = (
                STRUCTURE_SOURCE_PRIORITY.get(str(candidate.get("source", source)), 0),
                float(candidate.get("confidence", 0.0) or 0.0),
                len(candidate.get("evidence_asset_ids", []) or []),
            )
            current_score = (
                STRUCTURE_SOURCE_PRIORITY.get(str(current.get("source", UNKNOWN)), 0),
                float(current.get("confidence", 0.0) or 0.0),
                len(current.get("evidence_asset_ids", []) or []),
            )
            if current.get("result") != "Known" or candidate_score > current_score:
                merged[field] = {
                    "result": "Known",
                    "value": candidate.get("value", UNKNOWN),
                    "confidence": float(candidate.get("confidence", 0.0) or 0.0),
                    "source": candidate.get("source", source),
                    "evidence_asset_ids": sorted(set(candidate.get("evidence_asset_ids", []) or [])),
                    "visible_evidence": sorted(set(candidate.get("visible_evidence", []) or [])),
                }
    return merged
