-- ============================================================================
-- ContextPulse Knowledge Store — schema v1 (PRAGMA user_version = 1)
-- Semantic contract: cp_core.py (the referee). Target: cp-design-FINAL.md §1.3.
--
-- DIVERGENCES FROM TARGET DDL (binding notes for the future Rust port):
--  D1: no vec_idx/vec0/sqlite-vec. Vectors = plain table + brute-force cosine
--      (numpy fast path, pure-python reference). Rust port may reintroduce
--      sqlite-vec behind the storage trait; semantics must match rank_hybrid.
--  D2: external-content FTS5 sync triggers written explicitly (target omits).
--  D3: obs_fts indexes (content, window_title, app) — parity with live events_fts.
--  D4 (REVISED): observations.source_event_id UNIQUE ACROSS ALL SOURCES via
--      ux_obs_event. source is a provenance annotation, NOT part of identity.
--      Synthetic sources mint globally-unique ids. Bridge-era; review at P4.
--  D5: this schema lives in its own knowledge.db, NOT activity.db (capture-path
--      freeze; WAL writer isolation). Co-location decision deferred to P3/P5.
--  D6: all timestamps INTEGER epoch MILLISECONDS UTC (events.timestamp is REAL
--      seconds; bridge converts round(ts*1000), HALF-EVEN rounding — Rust port
--      must use round_ties_even, never half-up).
--  D7: corrections concretized; vec_meta folded into vectors.model_id.
--  D8: no chunks table — chunks derive from observations via cp_core.chunk_text.
--  BD-1: project aliases of length <= 3 match window titles on word boundaries
--      (behavioral divergence from live ActiveProjectDetector containment).
-- Append-only with audited exceptions. The ONLY permitted UPDATEs:
--   facts.valid_to      NULL -> t   (world changed)
--   facts.retracted_at  NULL -> t   (belief revision)  + superseded_by NULL -> id
--   facts.confidence    monotone non-decreasing via fusion (fuse is identity at cap)
-- The ONLY permitted DELETEs: the purge path (writes purge_log).
-- ============================================================================

PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA busy_timeout = 5000;
PRAGMA foreign_keys = ON;

-- L0: OBSERVATIONS ----------------------------------------------------------
CREATE TABLE observations (
  id              INTEGER PRIMARY KEY,
  source          TEXT NOT NULL,              -- annotation: 'bridge:events'|'live:eventbus'|'bridge:vocab_file'|test
  source_event_id TEXT NOT NULL,              -- globally unique (D4 revised)
  kind            TEXT NOT NULL,              -- events.event_type passthrough
  observed_at     INTEGER NOT NULL,           -- epoch ms UTC (D6)
  session_id      TEXT,                       -- entity id 'session:<start_ms>'; NULL for late/lock-only
  app             TEXT NOT NULL DEFAULT '',
  window_title    TEXT NOT NULL DEFAULT '',
  url             TEXT,                       -- RESERVED (M7): no Phase-1 writer populates this
  content         TEXT,                       -- ContextEvent.text_content(), 5-key join
  content_hash    BLOB,                       -- sha256(content) when content <> ''
  media_ref       TEXT,
  meta            TEXT NOT NULL DEFAULT '{}'  -- JSON
);
CREATE UNIQUE INDEX ux_obs_event  ON observations(source_event_id);   -- D4/M5: THE idempotency constraint
CREATE INDEX idx_obs_observed ON observations(observed_at);
CREATE INDEX idx_obs_session  ON observations(session_id, observed_at);
CREATE INDEX idx_obs_kind     ON observations(kind, observed_at);

-- FTS (D2, D3): external-content, explicit sync triggers -----------------
CREATE VIRTUAL TABLE obs_fts USING fts5(
  content, window_title, app,
  content='observations', content_rowid='id',
  tokenize='porter unicode61'
);
CREATE TRIGGER obs_fts_ai AFTER INSERT ON observations BEGIN
  INSERT INTO obs_fts(rowid, content, window_title, app)
  VALUES (new.id, COALESCE(new.content,''), new.window_title, new.app);
END;
CREATE TRIGGER obs_fts_ad AFTER DELETE ON observations BEGIN
  INSERT INTO obs_fts(obs_fts, rowid, content, window_title, app)
  VALUES ('delete', old.id, COALESCE(old.content,''), old.window_title, old.app);
END;
CREATE TRIGGER obs_fts_au AFTER UPDATE ON observations BEGIN
  INSERT INTO obs_fts(obs_fts, rowid, content, window_title, app)
  VALUES ('delete', old.id, COALESCE(old.content,''), old.window_title, old.app);
  INSERT INTO obs_fts(rowid, content, window_title, app)
  VALUES (new.id, COALESCE(new.content,''), new.window_title, new.app);
END;

-- L1: ENTITIES + FACTS -------------------------------------------------------
CREATE TABLE entities (
  id             TEXT PRIMARY KEY,            -- '<type>:<slug>'
  type           TEXT NOT NULL,
  canonical_name TEXT NOT NULL,
  created_at     INTEGER NOT NULL,
  meta           TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX idx_entities_type ON entities(type);

CREATE TABLE entity_aliases (
  entity_id  TEXT NOT NULL REFERENCES entities(id),
  alias      TEXT NOT NULL,                   -- stored casefolded
  source     TEXT NOT NULL,                   -- 'registry' | 'deterministic' | 'user' | 'llm:<model>'
  created_at INTEGER NOT NULL,
  PRIMARY KEY (alias, entity_id)
);
CREATE INDEX idx_aliases_entity ON entity_aliases(entity_id);

CREATE TABLE facts (
  id               TEXT PRIMARY KEY,          -- deterministic (cp_core.fact_id)
  subject_id       TEXT NOT NULL REFERENCES entities(id),
  predicate        TEXT NOT NULL,
  object_entity_id TEXT REFERENCES entities(id),
  object_value     TEXT,
  valid_from       INTEGER NOT NULL,          -- VALID TIME  [valid_from, valid_to)
  valid_to         INTEGER,                   -- NULL = open
  asserted_at      INTEGER NOT NULL,          -- ASSERTION TIME
  retracted_at     INTEGER,                   -- NULL = currently believed
  superseded_by    TEXT REFERENCES facts(id),
  confidence       REAL NOT NULL,
  extraction       TEXT NOT NULL,             -- 'deterministic'|'llm:<model>'|'user' (partition key)
  meta             TEXT NOT NULL DEFAULT '{}',
  CHECK (valid_to IS NULL OR valid_to > valid_from),
  CHECK (confidence > 0.0 AND confidence <= 1.0)
);
CREATE INDEX idx_facts_subject ON facts(subject_id, predicate, valid_from);
CREATE INDEX idx_facts_valid   ON facts(valid_from, valid_to);
CREATE INDEX idx_facts_object  ON facts(object_entity_id) WHERE object_entity_id IS NOT NULL;
CREATE INDEX idx_facts_partition ON facts(extraction);

CREATE TABLE fact_provenance (
  fact_id        TEXT NOT NULL REFERENCES facts(id),
  observation_id INTEGER NOT NULL REFERENCES observations(id),
  PRIMARY KEY (fact_id, observation_id)
);
CREATE INDEX idx_prov_obs ON fact_provenance(observation_id);

-- L2: RETRIEVAL ACCELERATORS (derivable, rebuildable) -------------------------
CREATE TABLE vectors (                        -- D1, D7, D8
  id         INTEGER PRIMARY KEY,
  item_kind  TEXT NOT NULL CHECK (item_kind IN ('observation','obs_chunk','fact')),
             -- 'fact' reserved for P2 (M6): NO Phase-1 writer produces fact vectors
  item_id    TEXT NOT NULL,                   -- obs id | '<obs_id>#<n>' | fact id
  model_id   TEXT NOT NULL,                   -- 'all-MiniLM-L6-v2@10244843' (decision #12)
  dim        INTEGER NOT NULL,
  embedding  BLOB NOT NULL,                   -- float32 little-endian, L2-normalized
  created_at INTEGER NOT NULL,
  UNIQUE (item_kind, item_id, model_id)
);

CREATE TABLE corrections (                    -- D7
  id             INTEGER PRIMARY KEY,
  original       TEXT NOT NULL,
  corrected      TEXT NOT NULL,
  detected_at    INTEGER NOT NULL,
  source         TEXT NOT NULL,               -- 'voice_vocab' | 'correction_event' | 'user'
  observation_id INTEGER REFERENCES observations(id),
  applied        INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE purge_log (
  tombstone_seq INTEGER PRIMARY KEY AUTOINCREMENT,   -- future replication tombstone order
  item_kind     TEXT NOT NULL,                -- 'observation' | 'fact' | 'vector'
  item_id       TEXT NOT NULL,                -- id only; NEVER content (audit *that*, not *what*)
  purged_at     INTEGER NOT NULL
);

CREATE TABLE ingest_state (                   -- bridge watermark + sessionizer state
  key   TEXT PRIMARY KEY,                     -- 'sessionizer' | 'bridge_watermark' | 'schema_notes'
  value TEXT NOT NULL                         -- JSON
);
