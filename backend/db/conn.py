"""SQLite connection helpers. Stdlib only.

One file-backed database at backend/db/paygent.db. No ORM, no migrations —
`scripts/demo_reset.sh` drops the file and reseeds.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "paygent.db"
SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Open a connection with row access by column name."""
    conn = sqlite3.connect(str(db_path or DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Apply schema.sql. Idempotent — every statement is CREATE ... IF NOT EXISTS."""
    conn.executescript(SCHEMA_PATH.read_text())
    conn.commit()


def reset(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Delete the database and recreate it empty. Used by seed and demo_reset."""
    path = Path(db_path or DB_PATH)
    for suffix in ("", "-wal", "-shm"):
        p = path.with_name(path.name + suffix)
        if p.exists():
            p.unlink()
    conn = connect(path)
    init_schema(conn)
    return conn


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, str(value)))


def get_meta(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def reference_now(conn: sqlite3.Connection) -> datetime:
    """The instant the current dataset is anchored to.

    Every dwell calculation measures against this, not the wall clock. Falls back
    to real time only for a database that was never seeded (a live-only run).
    """
    raw = get_meta(conn, "reference_now")
    if raw:
        return datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


def clear_signals(conn: sqlite3.Connection, source: str) -> None:
    """Delete one source's signals, plus any cases derived from them.

    Cases are downstream of signals. Re-running a detector makes the cases built from
    its old signals stale, and `case_signals` holds foreign keys onto both — so the
    delete has to walk the chain in order rather than leaving dangling references
    (or, as it did before this existed, failing outright on the FK).
    """
    conn.execute(
        """DELETE FROM case_signals WHERE signal_id IN
           (SELECT signal_id FROM signals WHERE source = ?)""", (source,))
    # A case left holding no signals at all no longer describes anything.
    conn.execute(
        "DELETE FROM cases WHERE case_id NOT IN (SELECT case_id FROM case_signals)")
    conn.execute("DELETE FROM signals WHERE source = ?", (source,))
    conn.commit()


def table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    """Row count per table — used by the seed summary and demo checks."""
    tables = [
        r["name"]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    return {t: conn.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()["n"] for t in tables}
