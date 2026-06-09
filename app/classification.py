from pathlib import Path
from typing import Any

from .unknown import UNKNOWN


COARSE_ASSET_TYPES = {
    "official_product_image",
    "official_model_image",
    "official_detail_image",
    "reality_product_photo",
    "human_wearing_photo",
    "scene_photo",
    "outfit_reference",
    "multi_product_photo",
    "low_quality",
    "duplicate",
    "unknown",
}


def coarse_classify_asset(
    *,
    original_name: str,
    source_type: str,
    width: int | None,
    height: int | None,
    duplicate_status: str = "unique",
    corrupted: bool = False,
    content_signals: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if corrupted:
        return {"asset_type": "unknown", "quality_status": "corrupted", "signals": ["corrupted"]}
    if duplicate_status != "unique":
        return {"asset_type": "duplicate", "quality_status": "duplicate", "signals": [duplicate_status]}

    signals: list[str] = []
    content = content_signals or {}
    normalized = Path(original_name).name.lower().replace("-", "_").replace(" ", "_")
    if width is None or height is None:
        return {"asset_type": "unknown", "quality_status": UNKNOWN, "signals": signals}

    if width < 360 or height < 360:
        return {"asset_type": "low_quality", "quality_status": "low_quality", "signals": ["small_dimensions"]}
    if content.get("blur_score", 999.0) < 6.0:
        return {"asset_type": "low_quality", "quality_status": "low_quality", "signals": ["content_blur"]}
    quality_status = "usable"

    multi_markers = ("multi", "group", "pile", "flatlay", "flat_lay", "desk", "table", "batch")
    if any(marker in normalized for marker in multi_markers) or content.get("object_count", 1) >= 3:
        marker = "multi_product_marker" if any(marker in normalized for marker in multi_markers) else "content_multi_subject"
        return {"asset_type": "multi_product_photo", "quality_status": quality_status, "signals": [marker]}

    if source_type == "official_visual_reference":
        if "model" in normalized:
            return {"asset_type": "official_model_image", "quality_status": quality_status, "signals": ["official_reference"]}
        if any(marker in normalized for marker in ("detail", "logo", "zipper", "fabric", "hardware", "stitch")):
            return {"asset_type": "official_detail_image", "quality_status": quality_status, "signals": ["official_reference"]}
        return {"asset_type": "official_product_image", "quality_status": quality_status, "signals": ["official_reference"]}

    if any(marker in normalized for marker in ("wear", "worn", "tryon", "try_on", "model", "ootd", "fitpic")):
        return {"asset_type": "human_wearing_photo", "quality_status": quality_status, "signals": ["human_marker"]}
    if content.get("human_like"):
        return {"asset_type": "human_wearing_photo", "quality_status": quality_status, "signals": ["content_human_like"]}
    if any(marker in normalized for marker in ("scene", "street", "gym", "office", "airport", "cafe", "coffee")):
        return {"asset_type": "scene_photo", "quality_status": quality_status, "signals": ["scene_marker"]}
    if content.get("scene_like"):
        return {"asset_type": "scene_photo", "quality_status": quality_status, "signals": ["content_scene_like"]}
    if any(marker in normalized for marker in ("outfit", "style", "look")):
        return {"asset_type": "outfit_reference", "quality_status": quality_status, "signals": ["outfit_marker"]}
    if content.get("detail_like"):
        return {"asset_type": "official_detail_image" if source_type == "official_visual_reference" else "reality_product_photo", "quality_status": quality_status, "signals": ["content_detail_like"]}
    if content.get("white_background"):
        return {"asset_type": "reality_product_photo", "quality_status": quality_status, "signals": ["content_white_background"]}
    return {"asset_type": "reality_product_photo", "quality_status": quality_status, "signals": ["default_reality_upload"]}
