from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "fashion_knowledge.db"

ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
MAX_UPLOAD_BYTES = 30 * 1024 * 1024

VISION_PROVIDERS = {"openai", "mimo", "qwen_vl", "gemini", "local"}
DEFAULT_VISION_PROVIDER = "local"
DEFAULT_MAX_VISION_CALLS_PER_BATCH = 100
DEFAULT_VISION_COST_LIMIT = 0.30
DEFAULT_VISION_REQUIRE_CONFIRM_ABOVE = 100


def vision_provider() -> str:
    provider = os.getenv("VISION_PROVIDER", DEFAULT_VISION_PROVIDER).strip().lower()
    return provider if provider in VISION_PROVIDERS else DEFAULT_VISION_PROVIDER


def max_vision_calls_per_batch() -> int:
    return int(os.getenv("MAX_VISION_CALLS_PER_BATCH", str(DEFAULT_MAX_VISION_CALLS_PER_BATCH)))


def vision_cost_limit() -> float:
    return float(os.getenv("VISION_COST_LIMIT", str(DEFAULT_VISION_COST_LIMIT)))


def vision_require_confirm_above() -> int:
    return int(os.getenv("VISION_REQUIRE_CONFIRM_ABOVE", str(DEFAULT_VISION_REQUIRE_CONFIRM_ABOVE)))

KNOWN_BRANDS = {
    "lululemon": "Lululemon",
    "on": "On",
    "arcteryx": "Arc'teryx",
    "arc'teryx": "Arc'teryx",
    "arc-teryx": "Arc'teryx",
    "ralphlauren": "Ralph Lauren",
    "ralph lauren": "Ralph Lauren",
    "alo": "Alo",
}

KNOWN_PRODUCTS = {
    "define": "Define Jacket",
    "define jacket": "Define Jacket",
    "scuba": "Scuba",
    "align": "Align",
    "wundertrain": "Wunder Train",
    "wunder train": "Wunder Train",
    "dancestudio": "Dance Studio",
    "dance studio": "Dance Studio",
}
