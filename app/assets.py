import hashlib
import shutil
import uuid
import zipfile
from pathlib import Path
from typing import BinaryIO

from fastapi import HTTPException, UploadFile

from .config import ALLOWED_IMAGE_EXTENSIONS, MAX_UPLOAD_BYTES, UPLOAD_DIR
from .database import connect, utc_now
from .pipelines import PIPELINE_INTERNAL_UPLOAD, TRUTH_COMMUNITY, TRUTH_OFFICIAL, TRUTH_REALITY


def is_allowed_image(filename: str) -> bool:
    return Path(filename).suffix.lower() in ALLOWED_IMAGE_EXTENSIONS


def source_type_from_name(filename: str) -> str:
    normalized = filename.lower().replace("-", "_").replace(" ", "_")
    markers = {
        "official_white_bg": "official",
        "official_model": "official",
        "xiaohongshu": "social",
        "instagram": "social",
        "tiktok": "social",
        "pinterest": "social",
        "employee": "employee",
        "buyer": "buyer",
        "realuser": "real_user",
    }
    for marker, source in markers.items():
        if marker in normalized:
            return source
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
            return dict(duplicate)

        shutil.move(str(file_path), final_path)
        conn.execute(
            """
            INSERT INTO assets (
                id, file_uri, original_name, sha256, content_type, size_bytes,
                source_type, knowledge_layer, pipeline_type, truth_layer,
                ingestion_metadata, upload_batch_id, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '{}', ?, ?)
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
                batch_id,
                now,
            ),
        )
        conn.execute(
            """
            INSERT INTO analysis_jobs (
                id, asset_id, status, model_name, attempts, created_at
            )
            VALUES (?, ?, 'pending', 'phase1-local-vision', 0, ?)
            """,
            (str(uuid.uuid4()), asset_id, now),
        )

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
        "upload_batch_id": batch_id,
        "created_at": now,
    }


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


def import_zip_file(zip_path: Path, batch_id: str) -> list[dict]:
    imported: list[dict] = []
    temp_dir = UPLOAD_DIR / "tmp" / f"zip-{uuid.uuid4()}"
    temp_dir.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(zip_path) as archive:
            for member in archive.infolist():
                member_name = member.filename
                if member.is_dir() or not is_allowed_image(member_name):
                    continue
                normalized = Path(member_name)
                if normalized.is_absolute() or ".." in normalized.parts:
                    continue
                destination = temp_dir / normalized.name
                with archive.open(member) as source, destination.open("wb") as output:
                    size = 0
                    while True:
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > MAX_UPLOAD_BYTES:
                            destination.unlink(missing_ok=True)
                            raise HTTPException(status_code=413, detail=f"File is too large: {member_name}")
                        output.write(chunk)
                imported.append(
                    create_asset_record(
                        file_path=destination,
                        original_name=member_name,
                        content_type="application/octet-stream",
                        size_bytes=member.file_size,
                        batch_id=batch_id,
                    )
                )
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
    return imported
