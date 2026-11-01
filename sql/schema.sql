CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS fulfillment_centers (
  fc_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  city TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS suppliers (
  supplier_id TEXT PRIMARY KEY,
  supplier_name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ingredients (
  sku TEXT PRIMARY KEY,
  ingredient_name TEXT NOT NULL,
  supplier_id TEXT REFERENCES suppliers(supplier_id),
  unit TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS recipes (
  recipe_id TEXT PRIMARY KEY,
  recipe_name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS recipe_ingredients (
  recipe_id TEXT REFERENCES recipes(recipe_id),
  sku TEXT REFERENCES ingredients(sku),
  qty_per_box NUMERIC NOT NULL,
  PRIMARY KEY (recipe_id, sku)
);

CREATE TABLE IF NOT EXISTS inventory (
  fc_id TEXT REFERENCES fulfillment_centers(fc_id),
  sku TEXT REFERENCES ingredients(sku),
  on_hand_qty INTEGER NOT NULL,
  PRIMARY KEY (fc_id, sku)
);

CREATE TABLE IF NOT EXISTS demand_forecast (
  forecast_date DATE NOT NULL,
  fc_id TEXT REFERENCES fulfillment_centers(fc_id),
  recipe_id TEXT REFERENCES recipes(recipe_id),
  forecast_boxes INTEGER NOT NULL,
  PRIMARY KEY (forecast_date, fc_id, recipe_id)
);

CREATE TABLE IF NOT EXISTS purchase_orders (
  po_id TEXT PRIMARY KEY,
  supplier_id TEXT REFERENCES suppliers(supplier_id),
  fc_id TEXT REFERENCES fulfillment_centers(fc_id),
  sku TEXT REFERENCES ingredients(sku),
  quantity INTEGER NOT NULL,
  expected_delivery DATE NOT NULL,
  status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS document_chunks (
  chunk_id BIGSERIAL PRIMARY KEY,
  document_name TEXT NOT NULL,
  document_type TEXT NOT NULL,
  supplier_id TEXT,
  sku TEXT,
  chunk_index INTEGER NOT NULL,
  content TEXT NOT NULL,
  embedding vector(1536),
  -- Every ingredient the document covers. A supplier contract can cover more
  -- than one, so a single column cannot hold the answer. Empty or NULL means
  -- the document is general and applies to any ingredient.
  skus TEXT[]
);

-- Idempotent, so re-running this file on a database created before the column
-- existed adds it rather than failing.
ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS skus TEXT[];

CREATE INDEX IF NOT EXISTS idx_document_chunks_document_type ON document_chunks(document_type);
CREATE INDEX IF NOT EXISTS idx_document_chunks_supplier ON document_chunks(supplier_id);
CREATE INDEX IF NOT EXISTS idx_document_chunks_sku ON document_chunks(sku);
CREATE INDEX IF NOT EXISTS idx_document_chunks_skus ON document_chunks USING GIN(skus);
