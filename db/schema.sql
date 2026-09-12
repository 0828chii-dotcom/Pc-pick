CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY, canonical_model TEXT UNIQUE, category TEXT, manufacturer TEXT, model_name TEXT, specs_json TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS price_snapshots(id INTEGER PRIMARY KEY, listing_url TEXT, price_type TEXT, price_krw INTEGER, captured_at TEXT);
CREATE TABLE IF NOT EXISTS evidence(id INTEGER PRIMARY KEY, canonical_model TEXT, source TEXT, source_url TEXT, evidence_type TEXT, status TEXT, reliability REAL, severity REAL, summary TEXT, observed_at TEXT);
CREATE TABLE IF NOT EXISTS component_scores(id INTEGER PRIMARY KEY, canonical_model TEXT UNIQUE, category TEXT, manufacturer TEXT, model_name TEXT, score REAL, tier TEXT, breakdown_json TEXT, evidence_json TEXT, methodology_version TEXT, scored_at TEXT);
CREATE TABLE IF NOT EXISTS shopping_listings(id INTEGER PRIMARY KEY, url TEXT, shop TEXT, product_type TEXT, raw_title TEXT, captured_at TEXT);
