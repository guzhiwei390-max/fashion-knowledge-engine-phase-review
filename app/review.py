import uuid
from typing import Any

from .database import decode_json, encode_json, utc_now


REVIEW_REASONS = {"unknown", "low_confidence", "conflict", "duplicate", "near_duplicate"}


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


def list_review_items(conn, status: str | None = None) -> list[dict[str, Any]]:
    if status:
        rows = conn.execute(
            "SELECT * FROM review_queue WHERE status = ? ORDER BY created_at DESC",
            (status,),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM review_queue ORDER BY created_at DESC").fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["conflict_json"] = decode_json(item.get("conflict_json"), {})
        item["review_payload"] = decode_json(item.get("review_payload"), {})
        item["resolution_json"] = decode_json(item.get("resolution_json"), {})
        items.append(item)
    return items


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
    return {"status": "resolved", "review_id": review_id, "resolved_by": resolved_by}
