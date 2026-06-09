import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .config import DB_PATH, UPLOAD_DIR
from .pipelines import SOURCE_TYPES, TRUTH_PRIORITY


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
                pipeline_type TEXT NOT NULL DEFAULT 'internal_upload',
                truth_layer TEXT NOT NULL DEFAULT 'reality_truth',
                source_id TEXT,
                external_ref_uri TEXT,
                ingestion_metadata TEXT NOT NULL DEFAULT '{}',
                ingestion_status TEXT NOT NULL DEFAULT 'ingested',
                asset_type TEXT NOT NULL DEFAULT 'unknown',
                quality_status TEXT NOT NULL DEFAULT 'unknown',
                width INTEGER,
                height INTEGER,
                exif_json TEXT NOT NULL DEFAULT '{}',
                thumbnail_uri TEXT,
                visual_signature TEXT NOT NULL DEFAULT '{}',
                duplicate_of_asset_id TEXT,
                duplicate_status TEXT NOT NULL DEFAULT 'unique',
                upload_batch_id TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS source_type_registry (
                source_type TEXT PRIMARY KEY,
                pipeline_type TEXT NOT NULL,
                truth_layer TEXT NOT NULL,
                truth_priority INTEGER NOT NULL,
                description TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ingestion_sources (
                id TEXT PRIMARY KEY,
                source_type TEXT NOT NULL REFERENCES source_type_registry(source_type),
                pipeline_type TEXT NOT NULL,
                truth_layer TEXT NOT NULL,
                source_uri TEXT,
                source_name TEXT NOT NULL DEFAULT '',
                source_metadata TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'reserved',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS pipeline_runs (
                id TEXT PRIMARY KEY,
                pipeline_type TEXT NOT NULL,
                source_id TEXT REFERENCES ingestion_sources(id),
                status TEXT NOT NULL,
                total_items INTEGER NOT NULL DEFAULT 0,
                processed_items INTEGER NOT NULL DEFAULT 0,
                unknown_items INTEGER NOT NULL DEFAULT 0,
                failed_items INTEGER NOT NULL DEFAULT 0,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS asset_batches (
                id TEXT PRIMARY KEY,
                pipeline_type TEXT NOT NULL DEFAULT 'internal_upload',
                truth_layer TEXT NOT NULL DEFAULT 'reality_truth',
                status TEXT NOT NULL DEFAULT 'queued',
                total_files INTEGER NOT NULL DEFAULT 0,
                ingested INTEGER NOT NULL DEFAULT 0,
                duplicated INTEGER NOT NULL DEFAULT 0,
                corrupted INTEGER NOT NULL DEFAULT 0,
                low_quality INTEGER NOT NULL DEFAULT 0,
                coarse_classified INTEGER NOT NULL DEFAULT 0,
                matched INTEGER NOT NULL DEFAULT 0,
                unknown INTEGER NOT NULL DEFAULT 0,
                review_needed INTEGER NOT NULL DEFAULT 0,
                failed INTEGER NOT NULL DEFAULT 0,
                vision_calls_used INTEGER NOT NULL DEFAULT 0,
                openai_vision_calls_used INTEGER NOT NULL DEFAULT 0,
                max_vision_calls_per_batch INTEGER NOT NULL DEFAULT 100,
                estimated_cost REAL NOT NULL DEFAULT 0,
                cost_limit REAL NOT NULL DEFAULT 0.30,
                require_manual_confirm_before_large_vision_run INTEGER NOT NULL DEFAULT 1,
                vision_status TEXT NOT NULL DEFAULT 'within_budget',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS external_knowledge_items (
                id TEXT PRIMARY KEY,
                source_id TEXT REFERENCES ingestion_sources(id),
                source_type TEXT NOT NULL,
                pipeline_type TEXT NOT NULL DEFAULT 'external_knowledge',
                truth_layer TEXT NOT NULL DEFAULT 'community_truth',
                brand TEXT NOT NULL DEFAULT 'Unknown',
                product_family TEXT NOT NULL DEFAULT 'Unknown',
                product_name TEXT NOT NULL DEFAULT 'Unknown',
                variant TEXT NOT NULL DEFAULT 'Unknown',
                color TEXT NOT NULL DEFAULT 'Unknown',
                material TEXT NOT NULL DEFAULT 'Unknown',
                category TEXT NOT NULL DEFAULT 'Unknown',
                content_uri TEXT,
                raw_content TEXT NOT NULL DEFAULT '',
                extracted_json TEXT NOT NULL DEFAULT '{}',
                confidence REAL NOT NULL DEFAULT 0,
                review_status TEXT NOT NULL DEFAULT 'reserved',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS official_products (
                id TEXT PRIMARY KEY,
                brand TEXT NOT NULL,
                product_name TEXT NOT NULL,
                product_family TEXT NOT NULL DEFAULT 'Unknown',
                variant TEXT NOT NULL DEFAULT 'Unknown',
                aliases TEXT NOT NULL,
                category TEXT NOT NULL,
                description TEXT NOT NULL,
                colors TEXT NOT NULL,
                material TEXT NOT NULL,
                official_url TEXT NOT NULL,
                import_type TEXT NOT NULL DEFAULT 'manual_import',
                truth_layer TEXT NOT NULL DEFAULT 'official_truth',
                truth_locked INTEGER NOT NULL DEFAULT 1,
                official_fields_json TEXT NOT NULL DEFAULT '{}',
                supplemental_fields_json TEXT NOT NULL DEFAULT '{}',
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
                source_type TEXT NOT NULL DEFAULT 'official_visual_reference',
                pipeline_type TEXT NOT NULL DEFAULT 'official_catalog',
                truth_layer TEXT NOT NULL DEFAULT 'official_truth',
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
                truth_layer TEXT NOT NULL DEFAULT 'official_truth',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS product_aliases (
                id TEXT PRIMARY KEY,
                product_id TEXT NOT NULL REFERENCES official_products(id) ON DELETE CASCADE,
                alias TEXT NOT NULL,
                alias_type TEXT NOT NULL DEFAULT 'name_alias',
                source_type TEXT NOT NULL DEFAULT 'official_catalog_import',
                truth_layer TEXT NOT NULL DEFAULT 'official_truth',
                created_at TEXT NOT NULL,
                UNIQUE(product_id, alias, alias_type)
            );

            CREATE TABLE IF NOT EXISTS review_queue (
                id TEXT PRIMARY KEY,
                item_type TEXT NOT NULL,
                item_id TEXT NOT NULL,
                reason TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                confidence REAL NOT NULL DEFAULT 0,
                conflict_json TEXT NOT NULL DEFAULT '{}',
                review_payload TEXT NOT NULL DEFAULT '{}',
                resolution_json TEXT NOT NULL DEFAULT '{}',
                resolved_by TEXT,
                resolved_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS asset_product_regions (
                id TEXT PRIMARY KEY,
                asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
                region_index INTEGER NOT NULL DEFAULT 0,
                crop_uri TEXT,
                bbox_json TEXT NOT NULL DEFAULT '{}',
                candidate_product_ids TEXT NOT NULL DEFAULT '[]',
                confidence REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'reserved',
                evidence_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
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
                source_type TEXT NOT NULL DEFAULT 'internal_upload_image',
                pipeline_type TEXT NOT NULL DEFAULT 'internal_upload',
                truth_layer TEXT NOT NULL DEFAULT 'reality_truth',
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
                source_type TEXT NOT NULL DEFAULT 'internal_upload_image',
                pipeline_type TEXT NOT NULL DEFAULT 'internal_upload',
                truth_layer TEXT NOT NULL DEFAULT 'reality_truth',
                dna_json TEXT NOT NULL,
                evidence_asset_ids TEXT NOT NULL,
                unknown_fields TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(dna_type, subject_key)
            );

            CREATE TABLE IF NOT EXISTS retrieval_queries (
                id TEXT PRIMARY KEY,
                pipeline_type TEXT NOT NULL DEFAULT 'internal_upload',
                truth_layer TEXT NOT NULL DEFAULT 'reality_truth',
                query_json TEXT NOT NULL,
                result_json TEXT NOT NULL,
                returned_unknown INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS knowledge_source_index (
                id TEXT PRIMARY KEY,
                knowledge_type TEXT NOT NULL,
                knowledge_id TEXT NOT NULL,
                source_type TEXT NOT NULL,
                pipeline_type TEXT NOT NULL,
                truth_layer TEXT NOT NULL,
                evidence_ref_id TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS success_library_items (
                id TEXT PRIMARY KEY,
                subject_key TEXT NOT NULL,
                product_id TEXT REFERENCES official_products(id),
                source_type TEXT NOT NULL,
                pipeline_type TEXT NOT NULL,
                truth_layer TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'reserved',
                evidence_ref_ids TEXT NOT NULL DEFAULT '[]',
                criteria_json TEXT NOT NULL DEFAULT '{}',
                reserved_metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS negative_library_items (
                id TEXT PRIMARY KEY,
                subject_key TEXT NOT NULL,
                product_id TEXT REFERENCES official_products(id),
                source_type TEXT NOT NULL,
                pipeline_type TEXT NOT NULL,
                truth_layer TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'reserved',
                evidence_ref_ids TEXT NOT NULL DEFAULT '[]',
                criteria_json TEXT NOT NULL DEFAULT '{}',
                reserved_metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS commercial_score_records (
                id TEXT PRIMARY KEY,
                subject_key TEXT NOT NULL,
                product_id TEXT REFERENCES official_products(id),
                source_type TEXT NOT NULL,
                pipeline_type TEXT NOT NULL,
                truth_layer TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'reserved',
                score_json TEXT NOT NULL DEFAULT '{}',
                evidence_ref_ids TEXT NOT NULL DEFAULT '[]',
                reserved_metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS trend_timeline_events (
                id TEXT PRIMARY KEY,
                subject_key TEXT NOT NULL,
                product_id TEXT REFERENCES official_products(id),
                source_type TEXT NOT NULL,
                pipeline_type TEXT NOT NULL,
                truth_layer TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'reserved',
                event_time TEXT,
                event_type TEXT NOT NULL DEFAULT 'reserved',
                signal_json TEXT NOT NULL DEFAULT '{}',
                evidence_ref_ids TEXT NOT NULL DEFAULT '[]',
                reserved_metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS region_layers (
                id TEXT PRIMARY KEY,
                region_key TEXT NOT NULL,
                subject_key TEXT NOT NULL DEFAULT 'Unknown',
                product_id TEXT REFERENCES official_products(id),
                source_type TEXT NOT NULL,
                pipeline_type TEXT NOT NULL,
                truth_layer TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'reserved',
                locale_json TEXT NOT NULL DEFAULT '{}',
                evidence_ref_ids TEXT NOT NULL DEFAULT '[]',
                reserved_metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS learning_feedback_events (
                id TEXT PRIMARY KEY,
                target_type TEXT NOT NULL,
                target_id TEXT NOT NULL,
                feedback_type TEXT NOT NULL DEFAULT 'reserved',
                source_type TEXT NOT NULL,
                pipeline_type TEXT NOT NULL,
                truth_layer TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'reserved',
                feedback_json TEXT NOT NULL DEFAULT '{}',
                applied_to_model INTEGER NOT NULL DEFAULT 0,
                reserved_metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS material_reality_patterns (
                id TEXT PRIMARY KEY,
                subject_key TEXT NOT NULL,
                product_id TEXT REFERENCES official_products(id),
                source_type TEXT NOT NULL DEFAULT 'internal_upload_image',
                pipeline_type TEXT NOT NULL DEFAULT 'internal_upload',
                truth_layer TEXT NOT NULL DEFAULT 'reality_truth',
                status TEXT NOT NULL DEFAULT 'reserved',
                pattern_json TEXT NOT NULL DEFAULT '{}',
                evidence_asset_ids TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS human_reality_patterns (
                id TEXT PRIMARY KEY,
                subject_key TEXT NOT NULL DEFAULT 'Unknown',
                source_type TEXT NOT NULL DEFAULT 'internal_upload_image',
                pipeline_type TEXT NOT NULL DEFAULT 'internal_upload',
                truth_layer TEXT NOT NULL DEFAULT 'reality_truth',
                status TEXT NOT NULL DEFAULT 'reserved',
                pattern_json TEXT NOT NULL DEFAULT '{}',
                evidence_asset_ids TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS scene_reality_patterns (
                id TEXT PRIMARY KEY,
                scene_key TEXT NOT NULL DEFAULT 'Unknown',
                source_type TEXT NOT NULL DEFAULT 'internal_upload_image',
                pipeline_type TEXT NOT NULL DEFAULT 'internal_upload',
                truth_layer TEXT NOT NULL DEFAULT 'reality_truth',
                status TEXT NOT NULL DEFAULT 'reserved',
                pattern_json TEXT NOT NULL DEFAULT '{}',
                evidence_asset_ids TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS moment_patterns (
                id TEXT PRIMARY KEY,
                moment_key TEXT NOT NULL DEFAULT 'Unknown',
                scene_key TEXT NOT NULL DEFAULT 'Unknown',
                source_type TEXT NOT NULL DEFAULT 'internal_upload_image',
                pipeline_type TEXT NOT NULL DEFAULT 'internal_upload',
                truth_layer TEXT NOT NULL DEFAULT 'reality_truth',
                status TEXT NOT NULL DEFAULT 'reserved',
                pattern_json TEXT NOT NULL DEFAULT '{}',
                evidence_asset_ids TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS outfit_reality_patterns (
                id TEXT PRIMARY KEY,
                subject_key TEXT NOT NULL DEFAULT 'Unknown',
                source_type TEXT NOT NULL DEFAULT 'internal_upload_image',
                pipeline_type TEXT NOT NULL DEFAULT 'internal_upload',
                truth_layer TEXT NOT NULL DEFAULT 'reality_truth',
                status TEXT NOT NULL DEFAULT 'reserved',
                pattern_json TEXT NOT NULL DEFAULT '{}',
                evidence_asset_ids TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS reality_score_schema (
                id TEXT PRIMARY KEY,
                schema_name TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'reserved',
                schema_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS video_assets (
                id TEXT PRIMARY KEY,
                file_uri TEXT NOT NULL,
                original_name TEXT NOT NULL,
                sha256 TEXT NOT NULL UNIQUE,
                content_type TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                source_type TEXT NOT NULL DEFAULT 'uploaded_video',
                pipeline_type TEXT NOT NULL DEFAULT 'internal_upload',
                truth_layer TEXT NOT NULL DEFAULT 'reality_truth',
                status TEXT NOT NULL DEFAULT 'reserved',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS video_frame_assets (
                id TEXT PRIMARY KEY,
                video_asset_id TEXT REFERENCES video_assets(id) ON DELETE CASCADE,
                asset_id TEXT REFERENCES assets(id) ON DELETE SET NULL,
                frame_time_ms INTEGER NOT NULL DEFAULT 0,
                frame_index INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'reserved',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS frame_extraction_jobs (
                id TEXT PRIMARY KEY,
                video_asset_id TEXT REFERENCES video_assets(id) ON DELETE CASCADE,
                status TEXT NOT NULL DEFAULT 'reserved',
                requested_frame_rate REAL,
                extracted_frames INTEGER NOT NULL DEFAULT 0,
                error_message TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        ensure_column(conn, "assets", "knowledge_layer", "TEXT NOT NULL DEFAULT 'user_reality'")
        ensure_column(conn, "assets", "pipeline_type", "TEXT NOT NULL DEFAULT 'internal_upload'")
        ensure_column(conn, "assets", "truth_layer", "TEXT NOT NULL DEFAULT 'reality_truth'")
        ensure_column(conn, "assets", "source_id", "TEXT")
        ensure_column(conn, "assets", "external_ref_uri", "TEXT")
        ensure_column(conn, "assets", "ingestion_metadata", "TEXT NOT NULL DEFAULT '{}'")
        ensure_column(conn, "assets", "ingestion_status", "TEXT NOT NULL DEFAULT 'ingested'")
        ensure_column(conn, "assets", "asset_type", "TEXT NOT NULL DEFAULT 'unknown'")
        ensure_column(conn, "assets", "quality_status", "TEXT NOT NULL DEFAULT 'unknown'")
        ensure_column(conn, "assets", "width", "INTEGER")
        ensure_column(conn, "assets", "height", "INTEGER")
        ensure_column(conn, "assets", "exif_json", "TEXT NOT NULL DEFAULT '{}'")
        ensure_column(conn, "assets", "thumbnail_uri", "TEXT")
        ensure_column(conn, "assets", "visual_signature", "TEXT NOT NULL DEFAULT '{}'")
        ensure_column(conn, "assets", "duplicate_of_asset_id", "TEXT")
        ensure_column(conn, "assets", "duplicate_status", "TEXT NOT NULL DEFAULT 'unique'")
        ensure_column(conn, "official_product_assets", "local_file_uri", "TEXT")
        ensure_column(conn, "official_product_assets", "visual_signature", "TEXT NOT NULL DEFAULT '{}'")
        ensure_column(conn, "official_products", "import_type", "TEXT NOT NULL DEFAULT 'manual_import'")
        ensure_column(conn, "official_products", "product_family", "TEXT NOT NULL DEFAULT 'Unknown'")
        ensure_column(conn, "official_products", "variant", "TEXT NOT NULL DEFAULT 'Unknown'")
        ensure_column(conn, "official_products", "truth_layer", "TEXT NOT NULL DEFAULT 'official_truth'")
        ensure_column(conn, "official_products", "truth_locked", "INTEGER NOT NULL DEFAULT 1")
        ensure_column(conn, "official_products", "official_fields_json", "TEXT NOT NULL DEFAULT '{}'")
        ensure_column(conn, "official_products", "supplemental_fields_json", "TEXT NOT NULL DEFAULT '{}'")
        ensure_column(conn, "official_product_assets", "import_type", "TEXT NOT NULL DEFAULT 'manual_import'")
        ensure_column(conn, "official_product_assets", "source_type", "TEXT NOT NULL DEFAULT 'official_visual_reference'")
        ensure_column(conn, "official_product_assets", "pipeline_type", "TEXT NOT NULL DEFAULT 'official_catalog'")
        ensure_column(conn, "official_product_assets", "truth_layer", "TEXT NOT NULL DEFAULT 'official_truth'")
        ensure_column(conn, "official_product_visual_references", "truth_layer", "TEXT NOT NULL DEFAULT 'official_truth'")
        ensure_column(conn, "vision_observations", "product_structure", "TEXT NOT NULL DEFAULT '{}'")
        ensure_column(conn, "knowledge_cards", "source_type", "TEXT NOT NULL DEFAULT 'internal_upload_image'")
        ensure_column(conn, "knowledge_cards", "pipeline_type", "TEXT NOT NULL DEFAULT 'internal_upload'")
        ensure_column(conn, "knowledge_cards", "truth_layer", "TEXT NOT NULL DEFAULT 'reality_truth'")
        ensure_column(conn, "dna_records", "source_type", "TEXT NOT NULL DEFAULT 'internal_upload_image'")
        ensure_column(conn, "dna_records", "pipeline_type", "TEXT NOT NULL DEFAULT 'internal_upload'")
        ensure_column(conn, "dna_records", "truth_layer", "TEXT NOT NULL DEFAULT 'reality_truth'")
        ensure_column(conn, "retrieval_queries", "pipeline_type", "TEXT NOT NULL DEFAULT 'internal_upload'")
        ensure_column(conn, "retrieval_queries", "truth_layer", "TEXT NOT NULL DEFAULT 'reality_truth'")
        ensure_column(conn, "review_queue", "review_payload", "TEXT NOT NULL DEFAULT '{}'")
        ensure_column(conn, "review_queue", "resolution_json", "TEXT NOT NULL DEFAULT '{}'")
        ensure_column(conn, "review_queue", "resolved_by", "TEXT")
        ensure_column(conn, "review_queue", "resolved_at", "TEXT")
        ensure_column(conn, "asset_batches", "max_vision_calls_per_batch", "INTEGER NOT NULL DEFAULT 100")
        ensure_column(conn, "asset_batches", "vision_calls_used", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(conn, "asset_batches", "cost_limit", "REAL NOT NULL DEFAULT 0.30")
        ensure_column(conn, "asset_batches", "require_manual_confirm_before_large_vision_run", "INTEGER NOT NULL DEFAULT 1")
        ensure_column(conn, "asset_batches", "vision_status", "TEXT NOT NULL DEFAULT 'within_budget'")
        ensure_reserved_extension_columns(conn)
        seed_source_type_registry(conn)


def ensure_reserved_extension_columns(conn: sqlite3.Connection) -> None:
    for table in (
        "success_library_items",
        "negative_library_items",
        "commercial_score_records",
        "trend_timeline_events",
        "region_layers",
        "learning_feedback_events",
    ):
        ensure_column(conn, table, "source_type", "TEXT NOT NULL DEFAULT 'reserved_extension'")
        ensure_column(conn, table, "pipeline_type", "TEXT NOT NULL DEFAULT 'reserved_future'")
        ensure_column(conn, table, "truth_layer", "TEXT NOT NULL DEFAULT 'community_truth'")
        ensure_column(conn, table, "status", "TEXT NOT NULL DEFAULT 'reserved'")
        ensure_column(conn, table, "reserved_metadata_json", "TEXT NOT NULL DEFAULT '{}'")


def ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def seed_source_type_registry(conn: sqlite3.Connection) -> None:
    now = utc_now()
    for source_type, config in SOURCE_TYPES.items():
        truth_layer = config["truth_layer"]
        conn.execute(
            """
            INSERT INTO source_type_registry (
                source_type, pipeline_type, truth_layer, truth_priority,
                description, is_active, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT(source_type) DO UPDATE SET
                pipeline_type = excluded.pipeline_type,
                truth_layer = excluded.truth_layer,
                truth_priority = excluded.truth_priority,
                description = excluded.description,
                is_active = excluded.is_active,
                updated_at = excluded.updated_at
            """,
            (
                source_type,
                config["pipeline_type"],
                truth_layer,
                TRUTH_PRIORITY[truth_layer],
                config["description"],
                now,
                now,
            ),
        )


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
