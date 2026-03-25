"""
database.py — SQLite storage for NFT provenance records.

SCHEMA DESIGN:
  nft_records    — one row per minted NFT (any chain)
  image_hashes   — perceptual hashes linked to NFT records
  provenance_queries — audit log of every "who minted first?" query

WHY SQLITE?
  Zero-config, single file, fully portable. For production scale
  (millions of NFTs), swap to PostgreSQL with the same query interface.
"""

import sqlite3
import os
import json
from datetime import datetime
from typing import Optional


DB_PATH = os.path.join(os.path.dirname(__file__), "provenance.db")


def get_connection(db_path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row  # rows behave like dicts
    conn.execute("PRAGMA journal_mode=WAL")  # faster concurrent reads
    return conn


def init_db(db_path: str = DB_PATH):
    """Create all tables if they don't exist."""
    conn = get_connection(db_path)
    c = conn.cursor()

    # Core NFT record — one row per mint event
    c.execute("""
    CREATE TABLE IF NOT EXISTS nft_records (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        chain           TEXT NOT NULL,          -- 'ethereum', 'solana', 'polygon', etc.
        contract_addr   TEXT NOT NULL,          -- smart contract address
        token_id        TEXT NOT NULL,          -- token ID within contract
        minter_addr     TEXT NOT NULL,          -- wallet that called mint()
        block_number    INTEGER NOT NULL,       -- block when minted
        block_timestamp INTEGER NOT NULL,       -- unix timestamp of block (GROUND TRUTH)
        tx_hash         TEXT NOT NULL,          -- transaction hash (proof)
        metadata_uri    TEXT,                   -- IPFS/Arweave URI of metadata JSON
        image_uri       TEXT,                   -- direct image URI (from metadata)
        token_name      TEXT,
        collection_name TEXT,
        indexed_at      TEXT DEFAULT (datetime('now')),  -- when WE indexed it
        UNIQUE(chain, contract_addr, token_id)
    )
    """)

    # Perceptual hashes — linked to nft_records
    c.execute("""
    CREATE TABLE IF NOT EXISTS image_hashes (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        nft_id      INTEGER NOT NULL REFERENCES nft_records(id),
        sha256      TEXT,                  -- exact match (cryptographic)
        dhash       INTEGER,               -- difference hash (64-bit int)
        phash       INTEGER,               -- perceptual hash (64-bit int)
        dhash_hex   TEXT,
        phash_hex   TEXT,
        hashed_at   TEXT DEFAULT (datetime('now'))
    )
    """)

    # Index for fast hash lookups
    c.execute("CREATE INDEX IF NOT EXISTS idx_sha256 ON image_hashes(sha256)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_dhash  ON image_hashes(dhash)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_phash  ON image_hashes(phash)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_block_ts ON nft_records(block_timestamp)")

    # Audit log of provenance queries
    c.execute("""
    CREATE TABLE IF NOT EXISTS provenance_queries (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        query_image_uri TEXT,
        winner_nft_id   INTEGER REFERENCES nft_records(id),
        candidates_json TEXT,   -- JSON array of all matching NFT IDs
        verdict         TEXT,   -- 'UNIQUE', 'DUPLICATE_FOUND', 'NEAR_DUPLICATE_FOUND'
        queried_at      TEXT DEFAULT (datetime('now'))
    )
    """)

    conn.commit()
    conn.close()
    print(f"[DB] Initialized at: {db_path}")


# ─── WRITE ────────────────────────────────────────────────────────────────────

def insert_nft(record: dict, db_path: str = DB_PATH) -> int:
    """
    Insert an NFT record. Returns the row ID.
    record keys: chain, contract_addr, token_id, minter_addr,
                 block_number, block_timestamp, tx_hash,
                 metadata_uri, image_uri, token_name, collection_name
    """
    conn = get_connection(db_path)
    c = conn.cursor()
    c.execute("""
        INSERT OR IGNORE INTO nft_records
        (chain, contract_addr, token_id, minter_addr, block_number,
         block_timestamp, tx_hash, metadata_uri, image_uri, token_name, collection_name)
        VALUES
        (:chain, :contract_addr, :token_id, :minter_addr, :block_number,
         :block_timestamp, :tx_hash, :metadata_uri, :image_uri, :token_name, :collection_name)
    """, record)
    nft_id = c.lastrowid
    conn.commit()
    conn.close()
    return nft_id


def _to_signed64(v):
    """Convert unsigned 64-bit int to signed so SQLite doesn't overflow."""
    if v is None:
        return None
    if v >= (1 << 63):
        v -= (1 << 64)
    return v


def insert_hashes(nft_id: int, hashes: dict, db_path: str = DB_PATH):
    """
    Insert perceptual hashes for an NFT.
    hashes: {sha256, dhash, phash, dhash_hex, phash_hex}
    """
    conn = get_connection(db_path)
    conn.execute("""
        INSERT INTO image_hashes (nft_id, sha256, dhash, phash, dhash_hex, phash_hex)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (nft_id, hashes.get("sha256"),
          _to_signed64(hashes.get("dhash")),
          _to_signed64(hashes.get("phash")),
          hashes.get("dhash_hex"), hashes.get("phash_hex")))
    conn.commit()
    conn.close()


# ─── READ ─────────────────────────────────────────────────────────────────────

def get_nft_by_id(nft_id: int, db_path: str = DB_PATH) -> Optional[dict]:
    conn = get_connection(db_path)
    row = conn.execute("SELECT * FROM nft_records WHERE id=?", (nft_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_hashes(db_path: str = DB_PATH) -> list:
    """Fetch all hash records joined with their NFT metadata."""
    conn = get_connection(db_path)
    rows = conn.execute("""
        SELECT h.nft_id, h.sha256, h.dhash, h.phash,
               n.chain, n.contract_addr, n.token_id,
               n.minter_addr, n.block_timestamp, n.tx_hash,
               n.image_uri, n.token_name, n.collection_name
        FROM image_hashes h
        JOIN nft_records n ON h.nft_id = n.id
        ORDER BY n.block_timestamp ASC
    """).fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        if d["dhash"] is not None and d["dhash"] < 0:
            d["dhash"] += (1 << 64)
        if d["phash"] is not None and d["phash"] < 0:
            d["phash"] += (1 << 64)
        result.append(d)
    return result


def find_exact_match(sha256: str, db_path: str = DB_PATH) -> list:
    """Find NFTs with identical SHA256 (exact byte-for-byte copy)."""
    conn = get_connection(db_path)
    rows = conn.execute("""
        SELECT n.*, h.sha256, h.dhash_hex, h.phash_hex
        FROM image_hashes h JOIN nft_records n ON h.nft_id = n.id
        WHERE h.sha256 = ?
        ORDER BY n.block_timestamp ASC
    """, (sha256,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_stats(db_path: str = DB_PATH) -> dict:
    conn = get_connection(db_path)
    stats = {}
    stats["total_nfts"] = conn.execute("SELECT COUNT(*) FROM nft_records").fetchone()[0]
    stats["total_hashed"] = conn.execute("SELECT COUNT(*) FROM image_hashes").fetchone()[0]
    stats["chains"] = [r[0] for r in conn.execute(
        "SELECT DISTINCT chain FROM nft_records").fetchall()]
    stats["total_queries"] = conn.execute(
        "SELECT COUNT(*) FROM provenance_queries").fetchone()[0]
    conn.close()
    return stats


def log_query(query_image_uri: str, winner_id: Optional[int],
              candidates: list, verdict: str, db_path: str = DB_PATH):
    conn = get_connection(db_path)
    conn.execute("""
        INSERT INTO provenance_queries (query_image_uri, winner_nft_id, candidates_json, verdict)
        VALUES (?, ?, ?, ?)
    """, (query_image_uri, winner_id, json.dumps(candidates), verdict))
    conn.commit()
    conn.close()
