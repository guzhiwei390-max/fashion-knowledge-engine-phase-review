import base64
import json
import os
from pathlib import Path
from typing import Any

import httpx

from .unknown import UNKNOWN


OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
DEFAULT_VISION_MODEL = os.getenv("OPENAI_VISION_MODEL", "gpt-4.1-mini")


def image_data_url(file_uri: str) -> str:
    path = Path(file_uri)
    suffix = path.suffix.lower()
    mime = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(suffix, "application/octet-stream")
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{data}"


def openai_vision_available() -> bool:
    return bool(os.getenv("OPENAI_API_KEY"))


def unknown_openai_result(reason: str) -> dict[str, Any]:
    return {
        "result": UNKNOWN,
        "provider": "openai_vision",
        "reason": reason,
        "product_structure": empty_product_structure(),
    }


def empty_product_structure() -> dict[str, Any]:
    return {
        "garment_type": UNKNOWN,
        "collar": UNKNOWN,
        "zipper": UNKNOWN,
        "sleeve": UNKNOWN,
        "logo": UNKNOWN,
        "back_structure": UNKNOWN,
        "material_visual_behavior": UNKNOWN,
        "fit": UNKNOWN,
        "visible_evidence": [],
        "unknown_fields": [
            "garment_type",
            "collar",
            "zipper",
            "sleeve",
            "logo",
            "back_structure",
            "material_visual_behavior",
            "fit",
        ],
    }


def analyze_image_with_openai(file_uri: str, official_candidates: list[dict[str, Any]]) -> dict[str, Any]:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return unknown_openai_result("OPENAI_API_KEY is not configured")

    candidate_summary = [
        {
            "brand": item.get("brand"),
            "product_name": item.get("product_name"),
            "category": item.get("category"),
            "material": item.get("material"),
            "aliases": item.get("aliases", []),
        }
        for item in official_candidates[:10]
    ]
    prompt = {
        "task": "Analyze a fashion product image for Phase 1 catalog matching.",
        "rules": [
            "Use only visible image evidence and provided official catalog candidates.",
            "Do not guess a product. If uncertain, set product_match.result to Unknown.",
            "Explain why the product matches using visible garment structure.",
            "Return JSON only.",
        ],
        "official_candidates": candidate_summary,
        "required_json_shape": {
            "product_match": {
                "result": "Known or Unknown",
                "brand": "string or Unknown",
                "product_name": "string or Unknown",
                "confidence": "number 0-1",
                "why": ["visible evidence"],
            },
            "product_structure": empty_product_structure(),
        },
    }
    payload = {
        "model": DEFAULT_VISION_MODEL,
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": json.dumps(prompt, ensure_ascii=False)},
                    {"type": "input_image", "image_url": image_data_url(file_uri)},
                ],
            }
        ],
    }
    try:
        with httpx.Client(timeout=45) as client:
            response = client.post(
                OPENAI_RESPONSES_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
    except (OSError, httpx.HTTPError) as exc:
        return unknown_openai_result(f"OpenAI Vision request failed: {exc}")

    return parse_openai_response(response.json())


def parse_openai_response(payload: dict[str, Any]) -> dict[str, Any]:
    text = payload.get("output_text")
    if not text:
        chunks: list[str] = []
        for item in payload.get("output", []):
            for content in item.get("content", []):
                if content.get("type") in {"output_text", "text"}:
                    chunks.append(content.get("text", ""))
        text = "\n".join(chunks)
    try:
        parsed = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return unknown_openai_result("OpenAI Vision did not return valid JSON")

    structure = parsed.get("product_structure") or empty_product_structure()
    return {
        "result": parsed.get("product_match", {}).get("result", UNKNOWN),
        "provider": "openai_vision",
        "product_match": parsed.get("product_match", {"result": UNKNOWN}),
        "product_structure": normalize_product_structure(structure),
    }


def normalize_product_structure(value: dict[str, Any]) -> dict[str, Any]:
    structure = empty_product_structure()
    structure.update({key: item for key, item in value.items() if key in structure})
    unknown_fields = [
        key
        for key, item in structure.items()
        if key not in {"visible_evidence", "unknown_fields"} and item in (None, "", UNKNOWN)
    ]
    structure["unknown_fields"] = unknown_fields
    return structure
