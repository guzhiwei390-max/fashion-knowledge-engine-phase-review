from typing import Any

from .database import connect, decode_json
from .structure import STRUCTURE_EVIDENCE_FIELDS
from .unknown import UNKNOWN


def official_assets_for_product(product_id: str | None) -> list[dict[str, Any]]:
    if not product_id:
        return []
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT id, product_id, asset_type, uri, local_file_uri, visual_signature
            FROM official_product_assets
            WHERE product_id = ?
            ORDER BY created_at ASC
            """,
            (product_id,),
        ).fetchall()
    assets = []
    for row in rows:
        item = dict(row)
        item["visual_signature"] = decode_json(item.get("visual_signature"), {})
        assets.append(item)
    return assets


def build_match_evidence(
    *,
    official_product: dict[str, Any] | None,
    confidence: float,
    method: str,
    structure_evidence: dict[str, Any],
    vision_reasons: list[str] | None = None,
    candidate_product: dict[str, Any] | None = None,
) -> dict[str, Any]:
    product_id = official_product.get("id") if official_product else candidate_product.get("id") if candidate_product else None
    official_assets = official_assets_for_product(product_id)
    matched_official_assets = [
        {
            "asset_id": item["id"],
            "asset_type": item["asset_type"],
            "uri": item["uri"],
        }
        for item in official_assets
    ]
    evidence_asset_ids = [item["asset_id"] for item in matched_official_assets]
    matched_because = []
    if method not in (None, "", UNKNOWN):
        matched_because.append(f"method:{method}")
    for reason in vision_reasons or []:
        if reason and reason not in matched_because:
            matched_because.append(str(reason))
    for field in STRUCTURE_EVIDENCE_FIELDS:
        item = structure_evidence.get(field, {}) if isinstance(structure_evidence, dict) else {}
        if item.get("result") == "Known":
            matched_because.append(f"{field}:{item.get('value', UNKNOWN)}")
    if official_assets:
        asset_types = sorted({item["asset_type"] for item in official_assets})
        matched_because.append(f"official_assets:{', '.join(asset_types)}")

    uncertain_fields = [
        field
        for field in STRUCTURE_EVIDENCE_FIELDS
        if not isinstance(structure_evidence, dict) or structure_evidence.get(field, {}).get("result") != "Known"
    ]
    if not official_product:
        uncertain_fields.append("official_product_match")

    return {
        "matched_because": matched_because,
        "evidence_asset_ids": evidence_asset_ids,
        "matched_official_assets": matched_official_assets,
        "confidence": float(confidence or 0.0),
        "uncertain_fields": sorted(set(uncertain_fields)),
        "official_product_id": official_product.get("id") if official_product else UNKNOWN,
        "candidate_official_product_id": candidate_product.get("id") if candidate_product else UNKNOWN,
    }
