from typing import Any

import psycopg
from psycopg.rows import dict_row

from src.config import DATABASE_URL, redact


class DatabaseUnavailable(RuntimeError):
    """The database could not be reached. Message is credential-safe."""


def _query(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    """Run one read-only query.

    Connection errors from the driver can quote the connection string, which
    carries a password. They are caught and re-raised with that stripped out,
    so a failed run cannot print a credential into a terminal or a log.
    """
    try:
        with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return list(cur.fetchall())
    except psycopg.OperationalError as error:
        raise DatabaseUnavailable(
            f"Could not reach PostgreSQL. Is it running? Try: docker compose up -d\n"
            f"Driver said: {redact(error)}"
        ) from None  # from None: the original carries the unredacted conninfo


def get_demand(target_date: str, fc_id: str) -> list[dict[str, Any]]:
    return _query(
        """
        SELECT d.forecast_date, d.fc_id, d.recipe_id, r.recipe_name, d.forecast_boxes
        FROM demand_forecast d
        JOIN recipes r USING (recipe_id)
        WHERE d.forecast_date = %s AND d.fc_id = %s
        ORDER BY d.recipe_id
        """,
        (target_date, fc_id),
    )


def get_recipe_requirements(recipe_ids: list[str]) -> list[dict[str, Any]]:
    if not recipe_ids:
        return []
    return _query(
        """
        SELECT ri.recipe_id, r.recipe_name, ri.sku, i.ingredient_name, ri.qty_per_box
        FROM recipe_ingredients ri
        JOIN recipes r USING (recipe_id)
        JOIN ingredients i USING (sku)
        WHERE ri.recipe_id = ANY(%s)
        ORDER BY ri.recipe_id, ri.sku
        """,
        (recipe_ids,),
    )


def get_inventory(fc_id: str) -> list[dict[str, Any]]:
    return _query(
        """
        SELECT inv.fc_id, inv.sku, i.ingredient_name, inv.on_hand_qty
        FROM inventory inv
        JOIN ingredients i USING (sku)
        WHERE inv.fc_id = %s
        ORDER BY inv.sku
        """,
        (fc_id,),
    )


def get_confirmed_purchase_orders(fc_id: str, target_date: str) -> list[dict[str, Any]]:
    return _query(
        """
        SELECT po_id, supplier_id, fc_id, sku, quantity, expected_delivery, status
        FROM purchase_orders
        WHERE fc_id = %s
          AND expected_delivery <= %s
          AND status = 'CONFIRMED'
        ORDER BY expected_delivery
        """,
        (fc_id, target_date),
    )


def get_supplier_for_sku(sku: str) -> str | None:
    """Which supplier owns this SKU.

    Used to retrieve the right contract. Looking this up beats hard-coding a
    supplier into the retrieval node, which would quietly fetch NordFarm's
    agreement no matter which ingredient was short.
    """
    rows = _query("SELECT supplier_id FROM ingredients WHERE sku = %s", (sku,))
    return rows[0]["supplier_id"] if rows else None


def get_on_hand(fc_id: str, sku: str) -> float:
    """On-hand quantity of one SKU at one site, or 0.0 if there is no row."""
    rows = _query(
        "SELECT on_hand_qty FROM inventory WHERE fc_id = %s AND sku = %s",
        (fc_id, sku),
    )
    return float(rows[0]["on_hand_qty"]) if rows else 0.0


def get_all_purchase_orders(fc_id: str, sku: str) -> list[dict[str, Any]]:
    """Every PO for one SKU at one site, whatever its status or date.

    get_confirmed_purchase_orders() returns the ones that count toward
    availability. This returns all of them, so the summary can say which ones
    exist but do not help and why.
    """
    return _query(
        """
        SELECT po_id, supplier_id, fc_id, sku, quantity, expected_delivery, status
        FROM purchase_orders
        WHERE fc_id = %s AND sku = %s
        ORDER BY expected_delivery
        """,
        (fc_id, sku),
    )


def get_other_fc_excess(sku: str, target_fc: str, target_date: str) -> list[dict[str, Any]]:
    """Calculate other-site excess for one SKU after reserving forecast demand.

    This is deliberately SQL-heavy so you can see that exact operational facts are
    better handled deterministically than by an LLM.
    """
    return _query(
        """
        WITH demand AS (
          SELECT d.fc_id, ri.sku,
                 SUM(d.forecast_boxes * ri.qty_per_box)::numeric AS required_qty
          FROM demand_forecast d
          JOIN recipe_ingredients ri USING (recipe_id)
          WHERE d.forecast_date = %s AND ri.sku = %s
          GROUP BY d.fc_id, ri.sku
        )
        SELECT inv.fc_id,
               inv.sku,
               inv.on_hand_qty,
               COALESCE(d.required_qty, 0) AS required_qty,
               GREATEST(inv.on_hand_qty - COALESCE(d.required_qty, 0), 0) AS excess_qty
        FROM inventory inv
        LEFT JOIN demand d ON d.fc_id = inv.fc_id AND d.sku = inv.sku
        WHERE inv.sku = %s AND inv.fc_id <> %s
        ORDER BY excess_qty DESC
        """,
        (target_date, sku, sku, target_fc),
    )
