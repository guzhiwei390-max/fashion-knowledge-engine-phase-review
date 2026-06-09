import re
import uuid
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

from .catalog import list_catalog, match_official_product, match_official_product_by_visual_signature
from .database import connect, decode_json, encode_json, utc_now
from .openai_vision import analyze_image_with_openai, empty_product_structure
from .structure import structure_evidence_from_observation
from .unknown import UNKNOWN
from .visual import image_signature


ASSET_TYPE_MARKERS = {
    "zipper": "zipper_detail",
    "logo": "logo_detail",
    "sleeve": "sleeve_detail",
    "fabric": "fabric_detail",
    "front": "front",
    "back": "back",
    "side": "side",
    "white": "official_white_bg",
    "model": "official_model",
}

SCENE_MARKERS = {
    "metro": "Metro",
    "elevator": "Elevator",
    "mall": "Mall",
    "coffee": "Coffee",
    "airport": "Airport",
    "costco": "Costco",
    "sams": "Sam's Club",
    "disney": "Disney",
    "hotel": "Hotel",
    "office": "Office",
}


def normalize_tokens(text: str) -> str:
    return re.sub(r"[_\-]+", " ", text.lower())


def classify_from_evidence(
    original_name: str,
    file_uri: str,
    source_type: str,
    asset_id: str | None = None,
) -> dict[str, Any]:
    evidence_text = normalize_tokens(f"{original_name} {file_uri}")
    unknown_fields: list[str] = []

    signature = image_signature(file_uri)
    openai_analysis = {
        "result": UNKNOWN,
        "product_match": {"result": UNKNOWN, "why": []},
        "product_structure": empty_product_structure(),
    }
    product_structure = openai_analysis["product_structure"]
    structure_source = UNKNOWN
    official_product = None
    match_method = UNKNOWN
    match_confidence = 0.0

    if signature.get("result") != UNKNOWN:
        official_product = match_official_product_by_visual_signature(signature)
        if official_product:
            match_method = "visual_reference"
            match_confidence = float(official_product.get("visual_match_confidence", 0.0))

    needs_structure_detail = needs_structure_analysis_from_signature(official_product, signature)
    should_call_openai = should_call_openai_vision(official_product, match_confidence, needs_structure_detail)
    if should_call_openai:
        openai_analysis = analyze_image_with_openai(file_uri, list_catalog())
        product_structure = openai_analysis.get("product_structure", empty_product_structure())
        structure_source = "openai_vision_structure"

    if not official_product:
        openai_match = openai_analysis.get("product_match", {})
        if openai_match.get("result") == "Known" and float(openai_match.get("confidence", 0.0) or 0.0) >= 0.86:
            candidate_text = f"{openai_match.get('brand', '')} {openai_match.get('product_name', '')}"
            official_product = match_official_product(candidate_text)
            if official_product:
                match_method = "openai_vision_structure"
                match_confidence = float(openai_match.get("confidence", 0.0))

    if not official_product:
        official_product = match_official_product(evidence_text)
        if official_product:
            match_method = "text_evidence_assist"
            match_confidence = 0.65

    if official_product:
        brand = official_product["brand"]
        product_name = official_product["product_name"]
        aliases = official_product["aliases"]
        category = official_product["category"]
        material = official_product["material"]
    else:
        brand = UNKNOWN
        product_name = UNKNOWN
        aliases = []
        category = UNKNOWN
        material = UNKNOWN
        unknown_fields.extend(["brand", "product_name", "official_product_match"])

    product_structure_evidence = structure_evidence_from_observation(
        product_structure,
        asset_id=asset_id,
        source=structure_source if structure_source != UNKNOWN else "official_visual_reference",
        confidence=match_confidence,
    )

    asset_type = UNKNOWN
    for marker, value in ASSET_TYPE_MARKERS.items():
        if marker in evidence_text:
            asset_type = value
            break
    if asset_type == UNKNOWN:
        unknown_fields.append("asset_type")

    scene = UNKNOWN
    for marker, value in SCENE_MARKERS.items():
        if marker in evidence_text:
            scene = value
            break

    return {
        "brand": brand,
        "product_name": product_name,
        "product_alias": aliases,
        "category": category,
        "material": material,
        "asset_type": asset_type,
        "scene": scene,
        "color": UNKNOWN,
        "view_angle": view_angle_from_asset_type(asset_type),
        "source_type": source_type,
        "visual_signature": signature,
        "product_structure": product_structure,
        "structure_evidence": product_structure_evidence,
        "product_match": {
            "method": match_method,
            "confidence": match_confidence,
            "openai_vision_called": should_call_openai,
            "why": openai_analysis.get("product_match", {}).get("why", []),
        },
        "contains_human": UNKNOWN,
        "quality_score": UNKNOWN,
        "unknown_fields": unknown_fields,
    }


def should_call_openai_vision(
    official_product: dict[str, Any] | None,
    match_confidence: float,
    needs_structure_detail: bool,
) -> bool:
    if official_product is None:
        return True
    if match_confidence < 0.92:
        return True
    return needs_structure_detail


def needs_structure_analysis_from_signature(
    official_product: dict[str, Any] | None,
    signature: dict[str, Any],
) -> bool:
    if official_product is None:
        return True
    if signature.get("result") == UNKNOWN:
        return True
    return False


def view_angle_from_asset_type(asset_type: str) -> str:
    if asset_type in {"front", "back", "side"}:
        return asset_type
    return UNKNOWN


def inspect_image(file_uri: str) -> dict[str, Any]:
    try:
        with Image.open(file_uri) as image:
            width, height = image.size
            quality_score = 90 if width >= 900 and height >= 900 else 70 if width >= 500 and height >= 500 else 45
            return {
                "width": width,
                "height": height,
                "mode": image.mode,
                "format": image.format,
                "quality_score": quality_score,
            }
    except (FileNotFoundError, UnidentifiedImageError, OSError):
        return {
            "width": UNKNOWN,
            "height": UNKNOWN,
            "mode": UNKNOWN,
            "format": UNKNOWN,
            "quality_score": UNKNOWN,
        }


def analyze_asset(asset: dict[str, Any]) -> dict[str, Any]:
    structured = classify_from_evidence(
        asset["original_name"],
        asset["file_uri"],
        asset["source_type"],
        asset_id=asset.get("asset_id") or asset.get("id"),
    )
    image_info = inspect_image(asset["file_uri"])
    structured["quality_score"] = image_info["quality_score"]
    if image_info["quality_score"] == UNKNOWN and "quality_score" not in structured["unknown_fields"]:
        structured["unknown_fields"].append("quality_score")

    return {
        "result": "Unknown" if structured["unknown_fields"] else "Known",
        "structured_output": structured,
        "raw_output": {
            "provider": "phase1-local-vision",
            "note": "Local Phase 1 analyzer only records evidence-backed fields. Unknown is returned instead of guessing.",
            "image": image_info,
            "product_structure": structured.get("product_structure", {}),
            "structure_evidence": structured.get("structure_evidence", {}),
        },
        "confidence_map": build_confidence_map(structured),
    }


def build_confidence_map(structured: dict[str, Any]) -> dict[str, float]:
    unknowns = set(structured.get("unknown_fields", []))
    return {
        key: 0.0 if key in unknowns or value == UNKNOWN else 1.0
        for key, value in structured.items()
        if key != "unknown_fields"
    }


def process_pending_jobs(limit: int = 5000) -> dict[str, Any]:
    processed = 0
    failed = 0
    with connect() as conn:
        jobs = conn.execute(
            """
            SELECT analysis_jobs.*, assets.file_uri, assets.original_name, assets.source_type
            FROM analysis_jobs
            JOIN assets ON assets.id = analysis_jobs.asset_id
            WHERE analysis_jobs.status IN ('pending', 'failed')
            ORDER BY analysis_jobs.created_at ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

        for job in jobs:
            started_at = utc_now()
            conn.execute(
                "UPDATE analysis_jobs SET status = 'running', attempts = attempts + 1, started_at = ?, error_message = NULL WHERE id = ?",
                (started_at, job["id"]),
            )
            try:
                asset = dict(job)
                analysis = analyze_asset(asset)
                structured = analysis["structured_output"]
                conn.execute(
                    """
                    INSERT INTO vision_observations (
                        id, asset_id, job_id, raw_output, structured_output,
                        product_structure, unknown_fields, confidence_map, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        job["asset_id"],
                        job["id"],
                        encode_json(analysis["raw_output"]),
                        encode_json(structured),
                        encode_json(structured.get("product_structure", {})),
                        encode_json(structured["unknown_fields"]),
                        encode_json(analysis["confidence_map"]),
                        utc_now(),
                    ),
                )
                conn.execute(
                    "UPDATE analysis_jobs SET status = 'completed', finished_at = ? WHERE id = ?",
                    (utc_now(), job["id"]),
                )
                processed += 1
            except Exception as exc:  # Keeps the queue inspectable instead of hiding failures.
                conn.execute(
                    "UPDATE analysis_jobs SET status = 'failed', finished_at = ?, error_message = ? WHERE id = ?",
                    (utc_now(), str(exc), job["id"]),
                )
                failed += 1
    return {"processed": processed, "failed": failed}


def latest_observations() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT vision_observations.*, assets.original_name, assets.file_uri
            FROM vision_observations
            JOIN assets ON assets.id = vision_observations.asset_id
            ORDER BY vision_observations.created_at DESC
            """
        ).fetchall()
    observations = []
    for row in rows:
        item = dict(row)
        item["structured_output"] = decode_json(item["structured_output"], {})
        item["unknown_fields"] = decode_json(item["unknown_fields"], [])
        item["confidence_map"] = decode_json(item["confidence_map"], {})
        observations.append(item)
    return observations
