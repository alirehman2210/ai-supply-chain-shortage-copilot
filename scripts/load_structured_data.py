from pathlib import Path
import csv
import psycopg

from src.config import DATABASE_URL

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "structured"

LOAD_ORDER = [
    ("fulfillment_centers", "fulfillment_centers.csv", ["fc_id", "name", "city"]),
    ("suppliers", "suppliers.csv", ["supplier_id", "supplier_name"]),
    ("ingredients", "ingredients.csv", ["sku", "ingredient_name", "supplier_id", "unit"]),
    ("recipes", "recipes.csv", ["recipe_id", "recipe_name"]),
    ("recipe_ingredients", "recipe_ingredients.csv", ["recipe_id", "sku", "qty_per_box"]),
    ("inventory", "inventory.csv", ["fc_id", "sku", "on_hand_qty"]),
    ("demand_forecast", "demand_forecast.csv", ["forecast_date", "fc_id", "recipe_id", "forecast_boxes"]),
    ("purchase_orders", "purchase_orders.csv", ["po_id", "supplier_id", "fc_id", "sku", "quantity", "expected_delivery", "status"]),
]


def main():
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            for table, filename, columns in LOAD_ORDER:
                path = DATA / filename
                with path.open(encoding="utf-8") as f:
                    rows = list(csv.DictReader(f))
                placeholders = ", ".join(["%s"] * len(columns))
                cols = ", ".join(columns)
                update_cols = [c for c in columns if c not in columns[:1]]
                for row in rows:
                    vals = [row[c] for c in columns]
                    # Synthetic demo loader: delete matching primary-key row then reinsert.
                    # Keeps the script understandable instead of hiding schema-specific UPSERT logic.
                    try:
                        cur.execute(f"INSERT INTO {table} ({cols}) VALUES ({placeholders}) ON CONFLICT DO NOTHING", vals)
                    except Exception:
                        raise
            conn.commit()
    print("Structured synthetic data loaded.")


if __name__ == "__main__":
    main()
