import json
import os
from typing import Any

import httpx

from .config import vision_provider
from .openai_vision import analyze_image_with_openai, empty_product_structure, image_data_url, normalize_product_structure
from .unknown import UNKNOWN


UNIFIED_VISION_SCHEMA = {
    "result": UNKNOWN,
    "provider": "local",
    "product_match": {
        "result": UNKNOWN,
        "brand": UNKNOWN,
        "product_name": UNKNOWN,
        "confidence": 0.0,
        "why": [],
        "candidate_only": True,
    },
    "product_structure": empty_product_structure(),
    "multi_product": {
        "result": UNKNOWN,
        "candidate_regions": [],
        "needs_region_review": False,
    },
    "quality": {
        "result": UNKNOWN,
        "issues": [],
    },
}


def analyze_image_with_provider(file_uri: str, official_candidates: list[dict[str, Any]]) -> dict[str, Any]:
    provider = vision_provider()
    if provider == "openai":
        return normalize_vision_result(analyze_image_with_openai(file_uri, official_candidates), "openai")
    if provider in {"mimo", "qwen_vl", "gemini"}:
        return analyze_with_http_provider(provider, file_uri, official_candidates)
    return local_unknown_result("local provider does not perform remote vision analysis")


def analyze_with_http_provider(provider: str, file_uri: str, official_candidates: list[dict[str, Any]]) -> dict[str, Any]:
    endpoint = os.getenv(f"{provider.upper()}_VISION_ENDPOINT")
    api_key = os.getenv(f"{provider.upper()}_API_KEY")
    if not endpoint:
        return local_unknown_result(f"{provider} provider endpoint is not configured", provider=provider)

    payload = {
        "task": "candidate_verification_only",
        "rules": [
            "Use only provided official candidates.",
            "Do not invent brands, products, accessories, or categories.",
            "Return the unified JSON schema only.",
        ],
        "official_candidates": official_candidates[:10],
        "image": image_data_url(file_uri),
        "required_json_shape": UNIFIED_VISION_SCHEMA,
    }
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        with httpx.Client(timeout=45) as client:
            response = client.post(endpoint, headers=headers, json=payload)
            response.raise_for_status()
    except (OSError, httpx.HTTPError) as exc:
        return local_unknown_result(f"{provider} Vision request failed: {exc}", provider=provider)
    try:
        parsed = response.json()
    except json.JSONDecodeError:
        return local_unknown_result(f"{provider} Vision did not return valid JSON", provider=provider)
    return normalize_vision_result(parsed, provider)


def normalize_vision_result(value: dict[str, Any], provider: str) -> dict[str, Any]:
    result = {
        "result": value.get("result", UNKNOWN),
        "provider": provider,
        "product_match": normalize_product_match(value.get("product_match", {})),
        "product_structure": normalize_product_structure(value.get("product_structure") or {}),
        "multi_product": normalize_multi_product(value.get("multi_product", {})),
        "quality": normalize_quality(value.get("quality", {})),
    }
    return result


def normalize_product_match(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "result": value.get("result", UNKNOWN),
        "brand": value.get("brand", UNKNOWN),
        "product_name": value.get("product_name", UNKNOWN),
        "confidence": float(value.get("confidence", 0.0) or 0.0),
        "why": [str(item) for item in value.get("why", []) if str(item).strip()],
        "candidate_only": True,
    }


def normalize_multi_product(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "result": value.get("result", UNKNOWN),
        "candidate_regions": value.get("candidate_regions", []) if isinstance(value.get("candidate_regions", []), list) else [],
        "needs_region_review": bool(value.get("needs_region_review", False)),
    }


def normalize_quality(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "result": value.get("result", UNKNOWN),
        "issues": [str(item) for item in value.get("issues", []) if str(item).strip()],
    }


def local_unknown_result(reason: str, provider: str = "local") -> dict[str, Any]:
    result = normalize_vision_result({}, provider)
    result["result"] = UNKNOWN
    result["reason"] = reason
    return result
