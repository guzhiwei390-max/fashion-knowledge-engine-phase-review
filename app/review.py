import uuid
from typing import Any

from .database import decode_json, encode_json, utc_now


REVIEW_REASONS = {"unknown", "low_confidence", "conflict", "duplicate", "near_duplicate"}
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
    return actions


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
