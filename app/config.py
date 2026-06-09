from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "fashion_knowledge.db"

ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
MAX_UPLOAD_BYTES = 30 * 1024 * 1024

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
