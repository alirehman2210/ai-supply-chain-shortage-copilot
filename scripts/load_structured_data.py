"""Loads the synthetic CSV data into PostgreSQL.

Reloads from scratch every time: each table is emptied and then filled from its
CSV. That matters for a demo project — if you edit a CSV and reload, you want
the database to match the file. An insert that skipped existing rows would
leave the old numbers in place and quietly make the documented scenario wrong.

    python -m scripts.load_structured_data
"""

import csv
from pathlib import Path

import psycopg

from src.config import DATABASE_URL

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "structured"

# Parents before children, so the foreign keys are satisfied on the way in.
LOAD_ORDER = [
    ("fulfillment_centers", "fulfillment_centers.csv", ["fc_id", "name", "city"]),
    ("suppliers", "suppliers.csv", ["supplier_id", "supplier_name"]),
    ("ingredients", "ingredients.csv", ["sku", "ingredient_name", "supplier_id", "unit"]),
    ("recipes", "recipes.csv", ["recipe_id", "recipe_name"]),
    ("recipe_ingredients", "recipe_ingredients.csv", ["recipe_id", "sku", "qty_per_box"]),
    ("inventory", "inventory.csv", ["fc_id", "sku", "on_hand_qty"]),
    (
        "demand_forecast",
        "demand_forecast.csv",
        ["forecast_date", "fc_id", "recipe_id", "forecast_boxes"],
    ),
    (
        "purchase_orders",
        "purchase_orders.csv",
        ["po_id", "supplier_id", "fc_id", "sku", "quantity", "expected_delivery", "status"],
    ),
]


def main() -> None:
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            # Children before parents on the way out.
            for table, _, _ in reversed(LOAD_ORDER):
                cur.execute(f"DELETE FROM {table}")

            for table, filename, columns in LOAD_ORDER:
                with (DATA / filename).open(encoding="utf-8") as handle:
                    rows = list(csv.DictReader(handle))

                placeholders = ", ".join(["%s"] * len(columns))
                cols = ", ".join(columns)
                cur.executemany(
                    f"INSERT INTO {table} ({cols}) VALUES ({placeholders})",
                    [[row[c] for c in columns] for row in rows],
                )
                print(f"  {table:<22} {len(rows):>3} rows")

        conn.commit()
    print("Synthetic structured data loaded.")


if __name__ == "__main__":
    main()
