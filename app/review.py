import uuid
import shutil
from pathlib import Path
from typing import Any

from .config import DATA_DIR
from .database import decode_json, encode_json, utc_now
from .unknown import UNKNOWN
from .visual import encode_signature_for_db, image_signature


REVIEW_REASONS = {
    "unknown",
    "low_confidence",
    "conflict",
    "duplicate",
    "near_duplicate",
    "multi_product_uncertain",
    "low_quality_but_possibly_useful",
    "official_like_candidate",
    "official_candidate_review",
    "conflict_after_matching",
    "low_confidence_after_matching",
    "uncertain_product_identity",
}
ASSET_RESOLUTION_FIELDS = {"asset_type", "quality_status", "duplicate_status"}


def enqueue_review_item(
    conn,
    *,
    item_type: str,
    item_id: str,
    reason: str,
    confidence: float = 0.0,
    conflict: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_reason = reason if reason in REVIEW_REASONS else "unknown"
    existing = conn.execute(
        """
        SELECT * FROM review_queue
        WHERE item_type = ? AND item_id = ? AND reason = ? AND status = 'pending'
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (item_type, item_id, normalized_reason),
    ).fetchone()
    if existing:
        return dict(existing)

    now = utc_now()
    review_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO review_queue (
            id, item_type, item_id, reason, status, confidence,
            conflict_json, review_payload, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?)
        """,
        (
            review_id,
            item_type,
            item_id,
            normalized_reason,
            float(confidence or 0.0),
            encode_json(conflict or {}),
            encode_json(payload or {}),
            now,
            now,
        ),
    )
    return {
        "id": review_id,
        "item_type": item_type,
        "item_id": item_id,
        "reason": normalized_reason,
        "status": "pending",
        "confidence": float(confidence or 0.0),
        "conflict_json": conflict or {},
        "review_payload": payload or {},
        "created_at": now,
        "updated_at": now,
    }


def list_review_items(
    conn,
    status: str | None = None,
    reason: str | None = None,
    item_type: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    clauses = []
    params: list[Any] = []
    for column, value in (("status", status), ("reason", reason), ("item_type", item_type)):
        if value:
            clauses.append(f"{column} = ?")
            params.append(value)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    total = conn.execute(f"SELECT COUNT(*) AS count FROM review_queue {where}", params).fetchone()["count"]
    rows = conn.execute(
        f"SELECT * FROM review_queue {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (*params, limit, offset),
    ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["conflict_json"] = decode_json(item.get("conflict_json"), {})
        item["review_payload"] = decode_json(item.get("review_payload"), {})
        item["resolution_json"] = decode_json(item.get("resolution_json"), {})
        items.append(item)
    return {"review_queue": items, "total": total, "limit": limit, "offset": offset}


def resolve_review_item(
    conn,
    *,
    review_id: str,
    resolution: dict[str, Any],
    resolved_by: str = "admin",
) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM review_queue WHERE id = ?", (review_id,)).fetchone()
    if not row:
        return {"result": "Unknown", "unknown": ["review_id"]}
    now = utc_now()
    learning_actions = apply_review_resolution(conn, dict(row), resolution, resolved_by)
    conn.execute(
        """
        UPDATE review_queue
        SET status = 'resolved',
            resolution_json = ?,
            resolved_by = ?,
            resolved_at = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (encode_json(resolution), resolved_by, now, now, review_id),
    )
    return {"status": "resolved", "review_id": review_id, "resolved_by": resolved_by, "learning_actions": learning_actions}


def apply_review_resolution(conn, review: dict[str, Any], resolution: dict[str, Any], resolved_by: str) -> dict[str, Any]:
    actions = {
        "asset_updated": False,
        "official_truth_written": False,
        "reality_truth_written": False,
        "correction_recorded": False,
        "rebuild_required": False,
    }
    item_type = review["item_type"]
    item_id = review["item_id"]
    if item_type == "vision_observation":
        observation = conn.execute("SELECT * FROM vision_observations WHERE id = ?", (item_id,)).fetchone()
        if observation:
            structured = decode_json(observation["structured_output"], {})
            changed = False
            for field, value in resolution.items():
                if field in {"resolved_by", "correction_reason"}:
                    continue
                if value in (None, ""):
                    continue
                old_value = structured.get(field)
                if old_value != value:
                    structured[field] = value
                    changed = True
                    record_human_correction(conn, "vision_observation", item_id, field, old_value, value, resolved_by)
            if changed:
                unknown_fields = [field for field in structured.get("unknown_fields", []) if field not in resolution]
                structured["unknown_fields"] = unknown_fields
                conn.execute(
                    "UPDATE vision_observations SET structured_output = ?, unknown_fields = ? WHERE id = ?",
                    (encode_json(structured), encode_json(unknown_fields), item_id),
                )
                actions["reality_truth_written"] = True
                actions["correction_recorded"] = True
                actions["rebuild_required"] = True
    if item_type == "asset":
        asset = conn.execute("SELECT * FROM assets WHERE id = ?", (item_id,)).fetchone()
        if asset:
            metadata = decode_json(asset["ingestion_metadata"], {})
            metadata.setdefault("human_review", []).append(
                {
                    "resolved_by": resolved_by,
                    "reason": resolution.get("correction_reason", ""),
                    "resolution": resolution,
                    "resolved_at": utc_now(),
                    "truth_layer": "reality_truth",
                }
            )
            assignments = []
            params: list[Any] = []
            for field in ASSET_RESOLUTION_FIELDS:
                if resolution.get(field):
                    assignments.append(f"{field} = ?")
                    params.append(str(resolution[field]))
                    record_human_correction(conn, "asset", item_id, field, asset[field], str(resolution[field]), resolved_by)
            assignments.append("ingestion_metadata = ?")
            params.append(encode_json(metadata))
            params.append(item_id)
            conn.execute(f"UPDATE assets SET {', '.join(assignments)} WHERE id = ?", params)
            actions["asset_updated"] = bool(assignments)
            actions["reality_truth_written"] = True
            actions["correction_recorded"] = True
            actions["rebuild_required"] = True
    if item_type == "official_candidate_asset":
        official_result = confirm_official_candidate_asset(conn, item_id, resolution, resolved_by)
        if official_result.get("status") == "official_truth_created":
            actions["official_truth_written"] = True
            actions["correction_recorded"] = True
            actions["rebuild_required"] = True
            actions["official_product_id"] = official_result["product_id"]
            actions["official_product_asset_id"] = official_result["official_product_asset_id"]
            actions["official_visual_reference_id"] = official_result["official_visual_reference_id"]
    return actions


def confirm_official_candidate_asset(conn, candidate_id: str, resolution: dict[str, Any], resolved_by: str) -> dict[str, Any]:
    candidate = conn.execute("SELECT * FROM official_candidate_assets WHERE id = ?", (candidate_id,)).fetchone()
    if not candidate:
        return {"result": "Unknown", "unknown": ["official_candidate_asset"]}
    asset = conn.execute("SELECT * FROM assets WHERE id = ?", (candidate["asset_id"],)).fetchone()
    if not asset:
        return {"result": "Unknown", "unknown": ["asset"]}

    brand = str(resolution.get("brand") or candidate["brand_hint"] or UNKNOWN).strip() or UNKNOWN
    product_name = str(resolution.get("product_name") or candidate["product_name_hint"] or UNKNOWN).strip() or UNKNOWN
    category = str(resolution.get("category") or "Unknown").strip() or UNKNOWN
    if brand == UNKNOWN or product_name == UNKNOWN:
        return {"result": "Unknown", "unknown": ["brand", "product_name"]}

    now = utc_now()
    existing = conn.execute(
        "SELECT * FROM official_products WHERE brand = ? AND product_name = ?",
        (brand, product_name),
    ).fetchone()
    product_id = existing["id"] if existing else str(uuid.uuid4())
    aliases = [product_name]
    official_fields = {
        "brand": brand,
        "product_name": product_name,
        "category": category,
        "source": "official_candidate_review",
        "reviewed_by": resolved_by,
        "candidate_asset_id": candidate_id,
    }
    conn.execute(
        """
        INSERT INTO official_products (
            id, brand, product_name, product_family, variant, aliases, category, description,
            colors, material, official_url, import_type, official_fields_json, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'official_candidate_review', ?, ?, ?)
        ON CONFLICT(brand, product_name) DO UPDATE SET
            category = excluded.category,
            import_type = excluded.import_type,
            official_fields_json = excluded.official_fields_json,
            updated_at = excluded.updated_at
        """,
        (
            product_id,
            brand,
            product_name,
            str(resolution.get("product_family", UNKNOWN) or UNKNOWN),
            str(resolution.get("variant", UNKNOWN) or UNKNOWN),
            encode_json(aliases),
            category,
            str(resolution.get("description", UNKNOWN) or UNKNOWN),
            encode_json([value for value in [resolution.get("color")] if value]),
            str(resolution.get("material", UNKNOWN) or UNKNOWN),
            str(resolution.get("official_url", asset["file_uri"]) or asset["file_uri"]),
            encode_json(official_fields),
            now,
            now,
        ),
    )
    asset_type = str(resolution.get("asset_type") or "official_white_bg")
    reference_id = str(uuid.uuid4())
    source_path = Path(asset["file_uri"])
    suffix = source_path.suffix or ".jpg"
    destination = DATA_DIR / "official_refs" / product_id / f"{reference_id}{suffix}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, destination)
    signature = image_signature(str(destination))
    conn.execute(
        """
        INSERT INTO official_product_assets (
            id, product_id, asset_type, uri, local_file_uri, visual_signature, import_type, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, 'official_candidate_review', ?)
        """,
        (
            reference_id,
            product_id,
            asset_type,
            asset["file_uri"],
            str(destination),
            encode_signature_for_db(signature),
            now,
        ),
    )
    visual_reference_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO official_product_visual_references (
            id, product_id, official_product_asset_id, asset_type,
            local_file_uri, visual_signature, structure_json, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, '{}', ?)
        """,
        (
            visual_reference_id,
            product_id,
            reference_id,
            asset_type,
            str(destination),
            encode_signature_for_db(signature),
            now,
        ),
    )
    conn.execute(
        """
        UPDATE official_candidate_assets
        SET status = 'confirmed',
            confirmed_product_id = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (product_id, now, candidate_id),
    )
    conn.execute(
        """
        UPDATE official_candidate_groups
        SET status = 'confirmed',
            confirmed_product_id = ?,
            updated_at = ?
        WHERE grouping_key = ?
        """,
        (product_id, now, candidate["grouping_key"]),
    )
    conn.execute(
        """
        UPDATE assets
        SET product_matching_status = 'pending'
        WHERE product_matching_status = 'blocked_missing_official_catalog'
        """
    )
    conn.execute(
        """
        UPDATE analysis_jobs
        SET status = 'queued', error_message = NULL
        WHERE status = 'blocked_missing_official_catalog'
        """
    )
    record_human_correction(conn, "official_candidate_asset", candidate_id, "official_product", UNKNOWN, f"{brand} / {product_name}", resolved_by)
    return {
        "status": "official_truth_created",
        "product_id": product_id,
        "official_product_asset_id": reference_id,
        "official_visual_reference_id": visual_reference_id,
    }


def record_human_correction(
    conn,
    target_type: str,
    target_id: str,
    field_name: str,
    old_value: Any,
    new_value: Any,
    corrected_by: str,
) -> None:
    conn.execute(
        """
        INSERT INTO human_corrections (
            id, target_type, target_id, field_name, old_value, new_value, corrected_by, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(uuid.uuid4()),
            target_type,
            target_id,
            field_name,
            None if old_value is None else str(old_value),
            str(new_value),
            corrected_by,
            utc_now(),
        ),
    )
