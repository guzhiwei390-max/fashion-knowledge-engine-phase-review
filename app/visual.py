from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

from .database import decode_json, encode_json
from .unknown import UNKNOWN


def image_signature(file_uri: str) -> dict[str, Any]:
    try:
        with Image.open(file_uri) as image:
            rgb = image.convert("RGB")
            small = rgb.resize((8, 8))
            pixels = list(small.getdata())
            avg_luma = sum(luma(pixel) for pixel in pixels) / len(pixels)
            ahash = "".join("1" if luma(pixel) >= avg_luma else "0" for pixel in pixels)
            avg_rgb = [
                round(sum(pixel[index] for pixel in pixels) / len(pixels), 3)
                for index in range(3)
            ]
            histogram = color_histogram(rgb)
            return {
                "result": "Known",
                "width": rgb.width,
                "height": rgb.height,
                "avg_rgb": avg_rgb,
                "ahash": ahash,
                "histogram": histogram,
            }
    except (FileNotFoundError, UnidentifiedImageError, OSError):
        return {"result": UNKNOWN}


def luma(pixel: tuple[int, int, int]) -> float:
    return 0.299 * pixel[0] + 0.587 * pixel[1] + 0.114 * pixel[2]


def color_histogram(image: Image.Image) -> list[float]:
    small = image.resize((32, 32)).convert("RGB")
    bins = [0] * 12
    for red, green, blue in small.getdata():
        bins[min(red // 64, 3)] += 1
        bins[4 + min(green // 64, 3)] += 1
        bins[8 + min(blue // 64, 3)] += 1
    total = sum(bins) or 1
    return [round(value / total, 6) for value in bins]


def signature_similarity(left: dict[str, Any] | str | None, right: dict[str, Any] | str | None) -> float:
    left_sig = decode_signature(left)
    right_sig = decode_signature(right)
    if not left_sig or not right_sig:
        return 0.0
    hash_score = ahash_similarity(left_sig.get("ahash", ""), right_sig.get("ahash", ""))
    color_score = vector_similarity(left_sig.get("histogram", []), right_sig.get("histogram", []))
    rgb_score = rgb_similarity(left_sig.get("avg_rgb", []), right_sig.get("avg_rgb", []))
    return round((hash_score * 0.45) + (color_score * 0.35) + (rgb_score * 0.20), 6)


def decode_signature(value: dict[str, Any] | str | None) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value if value.get("result") != UNKNOWN else None
    if isinstance(value, str):
        decoded = decode_json(value, None)
        return decoded if isinstance(decoded, dict) and decoded.get("result") != UNKNOWN else None
    return None


def ahash_similarity(left: str, right: str) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    matches = sum(1 for a, b in zip(left, right) if a == b)
    return matches / len(left)


def vector_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    distance = math.sqrt(sum((a - b) ** 2 for a, b in zip(left, right)))
    return max(0.0, 1.0 - min(distance, 1.0))


def rgb_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    distance = math.sqrt(sum((a - b) ** 2 for a, b in zip(left, right)))
    max_distance = math.sqrt(3 * (255**2))
    return max(0.0, 1.0 - min(distance / max_distance, 1.0))


def encode_signature_for_db(signature: dict[str, Any]) -> str:
    return encode_json(signature)
