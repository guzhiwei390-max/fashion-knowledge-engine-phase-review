import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .config import DB_PATH, UPLOAD_DIR


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def encode_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def decode_json(value: str | None, default: Any = None) -> Any:
    if value is None:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


@contextmanager
def connect() -> Iterable[sqlite3.Connection]:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS assets (
                id TEXT PRIMARY KEY,
                file_uri TEXT NOT NULL,
                original_name TEXT NOT NULL,
                sha256 TEXT NOT NULL UNIQUE,
                content_type TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                source_type TEXT NOT NULL,
                knowledge_layer TEXT NOT NULL,
                upload_batch_id TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS official_products (
                id TEXT PRIMARY KEY,
                brand TEXT NOT NULL,
                product_name TEXT NOT NULL,
                aliases TEXT NOT NULL,
                category TEXT NOT NULL,
                description TEXT NOT NULL,
                colors TEXT NOT NULL,
                material TEXT NOT NULL,
                official_url TEXT NOT NULL,
                import_type TEXT NOT NULL DEFAULT 'manual_import',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(brand, product_name)
            );

            CREATE TABLE IF NOT EXISTS official_product_assets (
                id TEXT PRIMARY KEY,
                product_id TEXT NOT NULL REFERENCES official_products(id) ON DELETE CASCADE,
                asset_type TEXT NOT NULL,
                uri TEXT NOT NULL,
                local_file_uri TEXT,
                visual_signature TEXT NOT NULL DEFAULT '{}',
                import_type TEXT NOT NULL DEFAULT 'manual_import',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS official_product_visual_references (
                id TEXT PRIMARY KEY,
                product_id TEXT NOT NULL REFERENCES official_products(id) ON DELETE CASCADE,
                official_product_asset_id TEXT REFERENCES official_product_assets(id) ON DELETE CASCADE,
                asset_type TEXT NOT NULL,
                local_file_uri TEXT NOT NULL,
                visual_signature TEXT NOT NULL,
                structure_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS analysis_jobs (
                id TEXT PRIMARY KEY,
                asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
                status TEXT NOT NULL,
                model_name TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                started_at TEXT,
                finished_at TEXT,
                error_message TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS vision_observations (
                id TEXT PRIMARY KEY,
                asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
                job_id TEXT NOT NULL REFERENCES analysis_jobs(id) ON DELETE CASCADE,
                raw_output TEXT NOT NULL,
                structured_output TEXT NOT NULL,
                product_structure TEXT NOT NULL DEFAULT '{}',
                unknown_fields TEXT NOT NULL,
                confidence_map TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS human_corrections (
                id TEXT PRIMARY KEY,
                target_type TEXT NOT NULL,
                target_id TEXT NOT NULL,
                field_name TEXT NOT NULL,
                old_value TEXT,
                new_value TEXT NOT NULL,
                corrected_by TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS knowledge_cards (
                id TEXT PRIMARY KEY,
                knowledge_type TEXT NOT NULL,
                brand TEXT NOT NULL,
                product_name TEXT NOT NULL,
                card_json TEXT NOT NULL,
                evidence_asset_ids TEXT NOT NULL,
                unknown_fields TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(knowledge_type, brand, product_name)
            );

            CREATE TABLE IF NOT EXISTS dna_records (
                id TEXT PRIMARY KEY,
                dna_type TEXT NOT NULL,
                subject_key TEXT NOT NULL,
                dna_json TEXT NOT NULL,
                evidence_asset_ids TEXT NOT NULL,
                unknown_fields TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(dna_type, subject_key)
            );

            CREATE TABLE IF NOT EXISTS retrieval_queries (
                id TEXT PRIMARY KEY,
                query_json TEXT NOT NULL,
                result_json TEXT NOT NULL,
                returned_unknown INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        ensure_column(conn, "assets", "knowledge_layer", "TEXT NOT NULL DEFAULT 'user_reality'")
        ensure_column(conn, "official_product_assets", "local_file_uri", "TEXT")
        ensure_column(conn, "official_product_assets", "visual_signature", "TEXT NOT NULL DEFAULT '{}'")
        ensure_column(conn, "official_products", "import_type", "TEXT NOT NULL DEFAULT 'manual_import'")
        ensure_column(conn, "official_product_assets", "import_type", "TEXT NOT NULL DEFAULT 'manual_import'")
        ensure_column(conn, "vision_observations", "product_structure", "TEXT NOT NULL DEFAULT '{}'")


def ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def fetch_all(query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with connect() as conn:
        return [row_to_dict(row) for row in conn.execute(query, params).fetchall()]


def fetch_one(query: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(query, params).fetchone()
        return row_to_dict(row) if row else None


def reset_database_for_tests(path: Path) -> None:
    if path.exists():
        path.unlink()
