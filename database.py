import os
import json
import sqlite3
from datetime import date, datetime
from typing import Dict, Any, List, Optional

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("SMART_CLOSET_DB", os.path.join(BASE_DIR, "closet.db"))


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create the clothes and outfits SQLite tables if they do not exist."""
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS clothes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                image_url TEXT NOT NULL,
                type TEXT NOT NULL,
                category TEXT NOT NULL,
                color TEXT NOT NULL,
                fit TEXT NOT NULL,
                size TEXT,
                description TEXT DEFAULT '',
                last_worn TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS outfits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                outfit_name TEXT NOT NULL,
                item_ids TEXT NOT NULL,
                styling_notes TEXT DEFAULT '',
                vibe TEXT DEFAULT 'Editorial Mix',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()


def compute_last_worn_status(last_worn: Optional[str]) -> Dict[str, Any]:
    """Compute human-friendly label and badge state from YYYY-MM-DD date string."""
    if not last_worn:
        return {"label": "Never worn", "state": "never", "days_ago": None}

    try:
        worn_date = date.fromisoformat(str(last_worn)[:10])
        delta_days = (date.today() - worn_date).days
    except ValueError:
        return {"label": f"Worn {last_worn}", "state": "normal", "days_ago": None}

    if delta_days <= 0:
        return {"label": "Worn today", "state": "today", "days_ago": 0}
    if delta_days == 1:
        return {"label": "Worn yesterday", "state": "recent", "days_ago": 1}
    if delta_days <= 7:
        return {"label": f"Worn {delta_days}d ago", "state": "recent", "days_ago": delta_days}
    if delta_days <= 30:
        return {"label": f"Worn {delta_days}d ago", "state": "normal", "days_ago": delta_days}
    return {"label": f"Worn {delta_days}d ago", "state": "stale", "days_ago": delta_days}


def serialize_cloth_row(row: sqlite3.Row) -> Dict[str, Any]:
    item = dict(row)
    item["last_worn_status"] = compute_last_worn_status(item.get("last_worn"))
    return item


def get_all_clothes(
    category: str = "all",
    search_query: str = "",
    sort_by: str = "newest",
) -> List[Dict[str, Any]]:
    query = "SELECT * FROM clothes WHERE 1=1"
    params: List[Any] = []

    if category and category.lower() != "all":
        query += " AND LOWER(category) = LOWER(?)"
        params.append(category.strip())

    if search_query and search_query.strip():
        q_like = f"%{search_query.strip().lower()}%"
        query += (
            " AND ("
            "LOWER(type) LIKE ? OR "
            "LOWER(category) LIKE ? OR "
            "LOWER(color) LIKE ? OR "
            "LOWER(fit) LIKE ? OR "
            "LOWER(COALESCE(size, '')) LIKE ? OR "
            "LOWER(COALESCE(description, '')) LIKE ?"
            ")"
        )
        params.extend([q_like] * 6)

    sort_map = {
        "newest": "id DESC",
        "never_worn": "CASE WHEN last_worn IS NULL OR last_worn = '' THEN 0 ELSE 1 END ASC, last_worn ASC, id DESC",
        "recently_worn": "CASE WHEN last_worn IS NULL OR last_worn = '' THEN 1 ELSE 0 END ASC, last_worn DESC, id DESC",
        "category": "LOWER(category) ASC, LOWER(type) ASC, id DESC",
        "color": "LOWER(color) ASC, LOWER(type) ASC, id DESC",
    }
    order_clause = sort_map.get(sort_by, "id DESC")
    query += f" ORDER BY {order_clause}"

    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
    return [serialize_cloth_row(r) for r in rows]


def get_cloth_by_id(item_id: int) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM clothes WHERE id = ?", (item_id,)).fetchone()
    return serialize_cloth_row(row) if row else None


def insert_cloth(
    image_url: str,
    garment_type: str,
    category: str,
    color: str,
    fit: str,
    size: Optional[str] = None,
    description: str = "",
    last_worn: Optional[str] = None,
) -> Dict[str, Any]:
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO clothes (image_url, type, category, color, fit, size, description, last_worn)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (image_url, garment_type, category, color, fit, size, description, last_worn),
        )
        conn.commit()
        new_id = cursor.lastrowid
    return get_cloth_by_id(int(new_id))  # type: ignore[arg-type]


def update_cloth(item_id: int, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    allowed_fields = {"type", "category", "color", "fit", "size", "description", "last_worn"}
    updates = []
    values: List[Any] = []

    for field in allowed_fields:
        if field in payload:
            updates.append(f"{field} = ?")
            values.append(payload[field])

    if updates:
        values.append(item_id)
        with get_connection() as conn:
            conn.execute(
                f"UPDATE clothes SET {', '.join(updates)} WHERE id = ?",
                values,
            )
            conn.commit()

    return get_cloth_by_id(item_id)


def mark_cloth_worn_today(item_id: int) -> Optional[Dict[str, Any]]:
    today_iso = date.today().isoformat()
    with get_connection() as conn:
        conn.execute(
            "UPDATE clothes SET last_worn = ? WHERE id = ?",
            (today_iso, item_id),
        )
        conn.commit()
    return get_cloth_by_id(item_id)


def delete_cloth(item_id: int) -> bool:
    with get_connection() as conn:
        cursor = conn.execute("DELETE FROM clothes WHERE id = ?", (item_id,))
        conn.commit()
    return cursor.rowcount > 0


def get_hydrated_outfits() -> List[Dict[str, Any]]:
    clothes_map = {item["id"]: item for item in get_all_clothes()}
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM outfits ORDER BY id DESC").fetchall()

    hydrated: List[Dict[str, Any]] = []
    for row in rows:
        record = dict(row)
        try:
            raw_ids = json.loads(record.get("item_ids") or "[]")
            item_ids = [int(i) for i in raw_ids]
        except (ValueError, TypeError, json.JSONDecodeError):
            item_ids = []

        items = [clothes_map[i] for i in item_ids if i in clothes_map]
        if items:
            hydrated.append(
                {
                    "id": record["id"],
                    "outfit_name": record["outfit_name"],
                    "item_ids": [item["id"] for item in items],
                    "items": items,
                    "styling_notes": record.get("styling_notes", ""),
                    "vibe": record.get("vibe", "Editorial Mix"),
                    "created_at": record.get("created_at"),
                }
            )
    return hydrated


def save_generated_outfits(
    generated_list: List[Dict[str, Any]],
    vibe: str = "Editorial Mix",
    replace_existing: bool = True,
) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        if replace_existing:
            conn.execute("DELETE FROM outfits")
        for outfit in generated_list:
            conn.execute(
                """
                INSERT INTO outfits (outfit_name, item_ids, styling_notes, vibe)
                VALUES (?, ?, ?, ?)
                """,
                (
                    outfit.get("outfit_name", "Curated Archival Look"),
                    json.dumps(outfit.get("item_ids", [])),
                    outfit.get("styling_notes", ""),
                    vibe,
                ),
            )
        conn.commit()
    return get_hydrated_outfits()


def delete_outfit(outfit_id: int) -> bool:
    with get_connection() as conn:
        cursor = conn.execute("DELETE FROM outfits WHERE id = ?", (outfit_id,))
        conn.commit()
    return cursor.rowcount > 0
