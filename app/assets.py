import hashlib
import shutil
import uuid
import zipfile
from pathlib import Path
from typing import Any, BinaryIO

from fastapi import HTTPException, UploadFile
from PIL import Image, ImageFilter, ImageStat, UnidentifiedImageError

from .classification import coarse_classify_asset
from .config import ALLOWED_IMAGE_EXTENSIONS, MAX_UPLOAD_BYTES, UPLOAD_DIR, max_vision_calls_per_batch, vision_cost_limit, vision_require_confirm_above
from .database import connect, decode_json, encode_json, utc_now
from .pipelines import PIPELINE_INTERNAL_UPLOAD, TRUTH_COMMUNITY, TRUTH_OFFICIAL, TRUTH_REALITY
from .review import enqueue_review_item
from .visual import image_signature, signature_similarity


NEAR_DUPLICATE_THRESHOLD = 0.97


def is_allowed_image(filename: str) -> bool:
    return Path(filename).suffix.lower() in ALLOWED_IMAGE_EXTENSIONS


def source_type_from_name(filename: str) -> str:
    # Ordinary batch uploads are Reality Truth even if filenames contain "official".
    # Official Truth can only enter through Official Catalog / Visual Reference import paths.
    return "uploaded"


def knowledge_layer_from_source(source_type: str) -> str:
    if source_type == "official":
        return "official_catalog"
    if source_type in {"social"}:
        return "community"
    return "user_reality"


def truth_layer_from_source(source_type: str) -> str:
    if source_type == "official":
        return TRUTH_OFFICIAL
    if source_type == "social":
        return TRUTH_COMMUNITY
    return TRUTH_REALITY


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_asset_path(batch_id: str, asset_id: str, original_name: str) -> Path:
    suffix = Path(original_name).suffix.lower()
    return UPLOAD_DIR / batch_id / f"{asset_id}{suffix}"


def _write_stream_to_path(stream: BinaryIO, destination: Path) -> int:
    destination.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    with destination.open("wb") as output:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                destination.unlink(missing_ok=True)
                raise HTTPException(status_code=413, detail="File is too large")
            output.write(chunk)
    return size


def create_asset_record(
    *,
    file_path: Path,
    original_name: str,
    content_type: str,
    size_bytes: int,
    batch_id: str,
) -> dict:
    asset_hash = sha256_file(file_path)
    metadata = inspect_upload_image(file_path)
    visual_signature = image_signature(str(file_path)) if metadata["ingestion_status"] != "corrupted" else {"result": "Unknown"}
    now = utc_now()
    asset_id = str(uuid.uuid4())
    final_path = _safe_asset_path(batch_id, asset_id, original_name)
    final_path.parent.mkdir(parents=True, exist_ok=True)

    source_type = source_type_from_name(original_name)
    knowledge_layer = knowledge_layer_from_source(source_type)
    truth_layer = truth_layer_from_source(source_type)

    with connect() as conn:
        duplicate = conn.execute(
            "SELECT * FROM assets WHERE sha256 = ?",
            (asset_hash,),
        ).fetchone()
        if duplicate:
            file_path.unlink(missing_ok=True)
            ensure_batch_record(conn, batch_id)
            enqueue_review_item(
                conn,
                item_type="asset",
                item_id=duplicate["id"],
                reason="duplicate",
                confidence=1.0,
                payload={
                    "duplicate_type": "exact",
                    "existing_asset_id": duplicate["id"],
                    "incoming_original_name": original_name,
                    "sha256": asset_hash,
                },
            )
            record_duplicate_attempt(conn, batch_id)
            return dict(duplicate)

        near_duplicate = find_near_duplicate(conn, visual_signature, batch_id=batch_id)
        duplicate_of_asset_id = near_duplicate["id"] if near_duplicate else None
        duplicate_status = "near_duplicate" if near_duplicate else "unique"
        classification = classify_with_content(
            original_name=original_name,
            source_type=source_type,
            metadata=metadata,
            duplicate_status=duplicate_status,
        )

        shutil.move(str(file_path), final_path)
        thumbnail_uri = create_thumbnail(final_path, batch_id, asset_id) if metadata["ingestion_status"] != "corrupted" else None
        ingestion_metadata = {
            "file_hash": asset_hash,
            "perceptual_hash": visual_signature.get("ahash", "Unknown"),
            "coarse_classification_signals": classification["signals"],
            "content_signals": metadata.get("content_signals", {}),
            "original_saved": True,
        }
        conn.execute(
            """
            INSERT INTO assets (
                id, file_uri, original_name, sha256, content_type, size_bytes,
                source_type, knowledge_layer, pipeline_type, truth_layer,
                ingestion_metadata, ingestion_status, asset_type, quality_status,
                width, height, exif_json, thumbnail_uri, visual_signature,
                duplicate_of_asset_id, duplicate_status, upload_batch_id, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                asset_id,
                str(final_path),
                original_name,
                asset_hash,
                content_type or "application/octet-stream",
                size_bytes,
                source_type,
                knowledge_layer,
                PIPELINE_INTERNAL_UPLOAD,
                truth_layer,
                encode_json(ingestion_metadata),
                metadata["ingestion_status"],
                classification["asset_type"],
                classification["quality_status"],
                metadata.get("width"),
                metadata.get("height"),
                encode_json(metadata.get("exif", {})),
                thumbnail_uri,
                encode_json(visual_signature),
                duplicate_of_asset_id,
                duplicate_status,
                batch_id,
                now,
            ),
        )
        ensure_batch_record(conn, batch_id)
        if metadata["ingestion_status"] == "corrupted":
            enqueue_review_item(
                conn,
                item_type="asset",
                item_id=asset_id,
                reason="unknown",
                confidence=0.0,
                payload={"ingestion_status": "corrupted", "original_name": original_name},
            )
        else:
            job_status = "queued" if classification["asset_type"] == "multi_product_photo" else "pending"
            conn.execute(
                """
                INSERT INTO analysis_jobs (
                    id, asset_id, status, model_name, attempts, created_at
                )
                VALUES (?, ?, ?, 'phase1-local-vision', 0, ?)
                """,
                (str(uuid.uuid4()), asset_id, job_status, now),
            )
            if classification["asset_type"] == "multi_product_photo":
                create_reserved_product_region(conn, asset_id)
                enqueue_review_item(
                    conn,
                    item_type="asset",
                    item_id=asset_id,
                    reason="low_confidence",
                    confidence=0.0,
                    payload={"asset_type": "multi_product_photo", "message": "Multi-product photo requires product region review."},
                )
        if near_duplicate:
            enqueue_review_item(
                conn,
                item_type="asset",
                item_id=asset_id,
                reason="near_duplicate",
                confidence=float(near_duplicate["score"]),
                payload={
                    "duplicate_type": "near",
                    "existing_asset_id": near_duplicate["id"],
                    "incoming_original_name": original_name,
                    "similarity": near_duplicate["score"],
                },
            )
        refresh_batch_progress(conn, batch_id)

    return {
        "id": asset_id,
        "file_uri": str(final_path),
        "original_name": original_name,
        "sha256": asset_hash,
        "content_type": content_type,
        "size_bytes": size_bytes,
        "source_type": source_type,
        "knowledge_layer": knowledge_layer,
        "pipeline_type": PIPELINE_INTERNAL_UPLOAD,
        "truth_layer": truth_layer,
        "ingestion_status": metadata["ingestion_status"],
        "asset_type": classification["asset_type"],
        "quality_status": classification["quality_status"],
        "width": metadata.get("width"),
        "height": metadata.get("height"),
        "thumbnail_uri": thumbnail_uri,
        "visual_signature": visual_signature,
        "duplicate_of_asset_id": duplicate_of_asset_id,
        "duplicate_status": duplicate_status,
        "upload_batch_id": batch_id,
        "created_at": now,
    }


def inspect_upload_image(path: Path) -> dict[str, Any]:
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            width, height = image.size
            exif = {}
            try:
                exif = {str(key): str(value) for key, value in image.getexif().items()}
            except (AttributeError, OSError, ValueError):
                exif = {}
            return {
                "ingestion_status": "ingested",
                "width": width,
                "height": height,
                "exif": exif,
                "content_signals": analyze_image_content(image),
            }
    except (UnidentifiedImageError, OSError, ValueError):
        return {
            "ingestion_status": "corrupted",
            "width": None,
            "height": None,
            "exif": {},
            "content_signals": {},
        }


def analyze_image_content(image: Image.Image) -> dict[str, Any]:
    sample = image.convert("RGB")
    sample.thumbnail((160, 160))
    width, height = sample.size
    pixels = list(sample.getdata())
    total = max(1, len(pixels))
    white_pixels = sum(1 for r, g, b in pixels if r > 235 and g > 235 and b > 235)
    dark_pixels = sum(1 for r, g, b in pixels if r < 80 and g < 80 and b < 80)
    saturated_pixels = sum(1 for r, g, b in pixels if max(r, g, b) - min(r, g, b) > 70)
    edge_image = sample.convert("L").filter(ImageFilter.FIND_EDGES)
    edge_mean = ImageStat.Stat(edge_image).mean[0]
    blur_score = ImageStat.Stat(sample.convert("L").filter(ImageFilter.FIND_EDGES)).var[0]
    object_count = estimate_object_count(sample)
    white_background = white_pixels / total > 0.72
    detail_like = edge_mean > 28 and max(width, height) / max(1, min(width, height)) < 1.8
    scene_like = not white_background and saturated_pixels / total > 0.22 and edge_mean > 18
    human_like = estimate_human_like(sample)
    return {
        "white_background": white_background,
        "object_count": object_count,
        "multi_subject": object_count >= 3,
        "detail_like": detail_like,
        "scene_like": scene_like,
        "human_like": human_like,
        "dark_ratio": round(dark_pixels / total, 4),
        "white_ratio": round(white_pixels / total, 4),
        "edge_mean": round(edge_mean, 4),
        "blur_score": round(blur_score, 4),
    }


def estimate_object_count(image: Image.Image) -> int:
    width, height = image.size
    pixels = image.load()
    visited: set[tuple[int, int]] = set()
    components = 0
    min_component = max(20, int(width * height * 0.015))
    for y in range(0, height, 2):
        for x in range(0, width, 2):
            if (x, y) in visited:
                continue
            r, g, b = pixels[x, y]
            if not (r < 120 and g < 120 and b < 120):
                continue
            stack = [(x, y)]
            visited.add((x, y))
            size = 0
            while stack:
                cx, cy = stack.pop()
                size += 1
                for nx, ny in ((cx + 2, cy), (cx - 2, cy), (cx, cy + 2), (cx, cy - 2)):
                    if nx < 0 or ny < 0 or nx >= width or ny >= height or (nx, ny) in visited:
                        continue
                    nr, ng, nb = pixels[nx, ny]
                    if nr < 120 and ng < 120 and nb < 120:
                        visited.add((nx, ny))
                        stack.append((nx, ny))
            if size >= min_component:
                components += 1
    return components


def estimate_human_like(image: Image.Image) -> bool:
    width, height = image.size
    if height <= width * 1.15:
        return False
    pixels = list(image.getdata())
    skin_like = sum(1 for r, g, b in pixels if r > 120 and g > 70 and b > 45 and r > g and g > b)
    return skin_like / max(1, len(pixels)) > 0.025


def classify_with_content(
    *,
    original_name: str,
    source_type: str,
    metadata: dict[str, Any],
    duplicate_status: str,
) -> dict[str, Any]:
    return coarse_classify_asset(
        original_name=original_name,
        source_type=source_type,
        width=metadata.get("width"),
        height=metadata.get("height"),
        duplicate_status=duplicate_status,
        corrupted=metadata["ingestion_status"] == "corrupted",
        content_signals=metadata.get("content_signals", {}),
    )


def create_thumbnail(path: Path, batch_id: str, asset_id: str) -> str | None:
    try:
        thumb_path = UPLOAD_DIR / batch_id / "thumbs" / f"{asset_id}.jpg"
        thumb_path.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(path) as image:
            thumb = image.convert("RGB")
            thumb.thumbnail((320, 320))
            thumb.save(thumb_path, "JPEG", quality=82)
        return str(thumb_path)
    except (UnidentifiedImageError, OSError, ValueError):
        return None


def find_near_duplicate(conn, signature: dict, batch_id: str | None = None) -> dict | None:
    if signature.get("result") == "Unknown":
        return None
    if batch_id:
        rows = conn.execute(
            """
            SELECT id, visual_signature
            FROM assets
            WHERE upload_batch_id = ?
              AND visual_signature IS NOT NULL
              AND visual_signature != '{}'
            ORDER BY created_at ASC
            """,
            (batch_id,),
        ).fetchall()
    else:
        rows = []
    if not rows:
        rows = conn.execute(
            """
            SELECT id, visual_signature
            FROM assets
            WHERE visual_signature IS NOT NULL
              AND visual_signature != '{}'
            ORDER BY created_at DESC
            LIMIT 2000
            """
        ).fetchall()
    best: dict | None = None
    for row in rows:
        score = signature_similarity(signature, row["visual_signature"])
        if score >= NEAR_DUPLICATE_THRESHOLD and (best is None or score > best["score"]):
            best = {"id": row["id"], "score": score}
    return best


def ensure_batch_record(conn, batch_id: str) -> None:
    now = utc_now()
    conn.execute(
        """
        INSERT INTO asset_batches (
            id, status, max_vision_calls_per_batch, cost_limit,
            require_manual_confirm_before_large_vision_run, created_at, updated_at
        )
        VALUES (?, 'queued', ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET updated_at = excluded.updated_at
        """,
        (
            batch_id,
            max_vision_calls_per_batch(),
            vision_cost_limit(),
            1 if max_vision_calls_per_batch() >= vision_require_confirm_above() else 0,
            now,
            now,
        ),
    )


def record_unsupported_files(conn, batch_id: str, unsupported_files: list[str]) -> None:
    if not unsupported_files:
        return
    ensure_batch_record(conn, batch_id)
    row = conn.execute("SELECT unsupported_files_json FROM asset_batches WHERE id = ?", (batch_id,)).fetchone()
    current = decode_json(row["unsupported_files_json"], []) if row else []
    merged = list(dict.fromkeys([*current, *unsupported_files]))
    conn.execute(
        """
        UPDATE asset_batches
        SET unsupported_count = ?,
            unsupported_files_json = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (len(merged), encode_json(merged), utc_now(), batch_id),
    )


def create_reserved_product_region(conn, asset_id: str) -> None:
    now = utc_now()
    conn.execute(
        """
        INSERT INTO asset_product_regions (id, asset_id, region_index, status, created_at, updated_at)
        VALUES (?, ?, 0, 'reserved', ?, ?)
        """,
        (str(uuid.uuid4()), asset_id, now, now),
    )


def refresh_batch_progress(conn, batch_id: str) -> None:
    ensure_batch_record(conn, batch_id)
    asset_rows = conn.execute("SELECT * FROM assets WHERE upload_batch_id = ?", (batch_id,)).fetchall()
    total = len(asset_rows)
    duplicated = sum(1 for row in asset_rows if row["duplicate_status"] != "unique")
    corrupted = sum(1 for row in asset_rows if row["ingestion_status"] == "corrupted")
    low_quality = sum(1 for row in asset_rows if row["quality_status"] == "low_quality")
    coarse_classified = sum(1 for row in asset_rows if row["asset_type"] != "unknown")
    failed_row = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM analysis_jobs
        JOIN assets ON assets.id = analysis_jobs.asset_id
        WHERE assets.upload_batch_id = ? AND analysis_jobs.status = 'failed'
        """,
        (batch_id,),
    ).fetchone()
    matched_row = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM vision_observations
        JOIN assets ON assets.id = vision_observations.asset_id
        WHERE assets.upload_batch_id = ?
          AND vision_observations.structured_output NOT LIKE '%"product_name": "Unknown"%'
        """,
        (batch_id,),
    ).fetchone()
    unknown_row = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM vision_observations
        JOIN assets ON assets.id = vision_observations.asset_id
        WHERE assets.upload_batch_id = ?
          AND vision_observations.structured_output LIKE '%"product_name": "Unknown"%'
        """,
        (batch_id,),
    ).fetchone()
    review_row = conn.execute(
        """
        SELECT COUNT(DISTINCT review_queue.id) AS count
        FROM review_queue
        WHERE review_queue.status = 'pending'
          AND (
            review_queue.item_id IN (SELECT id FROM assets WHERE upload_batch_id = ?)
            OR review_queue.item_id IN (
                SELECT vision_observations.id
                FROM vision_observations
                JOIN assets ON assets.id = vision_observations.asset_id
                WHERE assets.upload_batch_id = ?
            )
          )
        """,
        (batch_id, batch_id),
    ).fetchone()
    vision_row = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM vision_observations
        JOIN assets ON assets.id = vision_observations.asset_id
        WHERE assets.upload_batch_id = ?
          AND (
            vision_observations.structured_output LIKE '%"vision_called": true%'
            OR vision_observations.structured_output LIKE '%"openai_vision_called": true%'
          )
        """,
        (batch_id,),
    ).fetchone()
    status = "completed" if total and (matched_row["count"] + unknown_row["count"] + corrupted) >= total else "processing"
    conn.execute(
        """
        UPDATE asset_batches
        SET status = ?, total_files = ?, ingested = ?, duplicated = ?, corrupted = ?,
            low_quality = ?, coarse_classified = ?, matched = ?, unknown = ?,
            review_needed = ?, failed = ?, vision_calls_used = ?, openai_vision_calls_used = ?,
            estimated_cost = ?,
            vision_status = CASE
                WHEN ? >= max_vision_calls_per_batch OR ? >= cost_limit THEN 'paused_budget'
                ELSE 'within_budget'
            END,
            updated_at = ?
        WHERE id = ?
        """,
        (
            status,
            total,
            total - corrupted,
            duplicated,
            corrupted,
            low_quality,
            coarse_classified,
            int(matched_row["count"]),
            int(unknown_row["count"]) + corrupted,
            int(review_row["count"]),
            int(failed_row["count"]),
            int(vision_row["count"]),
            int(vision_row["count"]),
            round(int(vision_row["count"]) * 0.002, 6),
            int(vision_row["count"]),
            round(int(vision_row["count"]) * 0.002, 6),
            utc_now(),
            batch_id,
        ),
    )


def record_duplicate_attempt(conn, batch_id: str) -> None:
    ensure_batch_record(conn, batch_id)
    conn.execute(
        """
        UPDATE asset_batches
        SET total_files = total_files + 1,
            duplicated = duplicated + 1,
            review_needed = review_needed + 1,
            status = 'processing',
            updated_at = ?
        WHERE id = ?
        """,
        (utc_now(), batch_id),
    )


def batch_progress(batch_id: str | None = None) -> list[dict[str, Any]]:
    with connect() as conn:
        if batch_id:
            rows = conn.execute("SELECT * FROM asset_batches WHERE id = ? ORDER BY created_at DESC", (batch_id,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM asset_batches ORDER BY created_at DESC").fetchall()
    results = []
    for row in rows:
        item = dict(row)
        item["metadata_json"] = decode_json(item.get("metadata_json"), {})
        item["unsupported_files_json"] = decode_json(item.get("unsupported_files_json"), [])
        results.append(item)
    return results


async def save_upload_file(file: UploadFile, batch_id: str) -> dict:
    if not file.filename or not is_allowed_image(file.filename):
        raise HTTPException(status_code=400, detail=f"Unsupported image file: {file.filename}")

    temp_id = str(uuid.uuid4())
    temp_path = UPLOAD_DIR / "tmp" / f"{temp_id}{Path(file.filename).suffix.lower()}"
    size = _write_stream_to_path(file.file, temp_path)
    return create_asset_record(
        file_path=temp_path,
        original_name=file.filename,
        content_type=file.content_type or "application/octet-stream",
        size_bytes=size,
        batch_id=batch_id,
    )


def import_zip_file(zip_path: Path, batch_id: str) -> dict[str, Any]:
    imported_count = 0
    unsupported_files: list[str] = []
    temp_dir = UPLOAD_DIR / "tmp" / f"zip-{uuid.uuid4()}"
    temp_dir.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(zip_path) as archive:
            for member in archive.infolist():
                member_name = member.filename
                normalized = Path(member_name)
                if member.is_dir():
                    continue
                if normalized.is_absolute() or ".." in normalized.parts:
                    unsupported_files.append(member_name)
                    continue
                if normalized.name.startswith(".") or "__MACOSX" in normalized.parts or not is_allowed_image(member_name):
                    unsupported_files.append(member_name)
                    continue
                if member.file_size > MAX_UPLOAD_BYTES:
                    unsupported_files.append(member_name)
                    continue
                destination = temp_dir / normalized.name
                exceeded_size = False
                with archive.open(member) as source, destination.open("wb") as output:
                    size = 0
                    while True:
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > MAX_UPLOAD_BYTES:
                            destination.unlink(missing_ok=True)
                            unsupported_files.append(member_name)
                            exceeded_size = True
                            break
                        output.write(chunk)
                if destination.exists() and not exceeded_size:
                    create_asset_record(
                        file_path=destination,
                        original_name=member_name,
                        content_type="application/octet-stream",
                        size_bytes=member.file_size,
                        batch_id=batch_id,
                    )
                    imported_count += 1
        with connect() as conn:
            ensure_batch_record(conn, batch_id)
            record_unsupported_files(conn, batch_id, unsupported_files)
            refresh_batch_progress(conn, batch_id)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
    return {
        "batch_id": batch_id,
        "total_received": imported_count,
        "unsupported_count": len(unsupported_files),
        "unsupported_files": unsupported_files[:100],
        "status": "queued" if imported_count else "needs_manual_review",
    }
