"""Persistent submission sessions: a SQLite registry + per-session files.

A *session* groups everything about one ENA submission so it can be reopened
later with all UI state restored. Two storage layers:

  * SQLite (``sessions.db``) — the session registry and the per-run reads
    submission ledger (the source of truth for reads resumability).
  * Per-session directory (``<id>/``) — the full UI snapshot (``state.json``),
    the DataHarmonizer export (``dh_export.json``) and the streamed reads log
    (``logs/reads.log``).

Credentials are never stored here — they stay in server memory only.

All file writes use the atomic temp-then-rename pattern; SQLite access uses a
fresh connection per call so it is safe to use from the request threadpool and
from the reads-submission executor thread.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any

_SESSIONS_DIR = pathlib.Path(os.environ.get("SESSIONS_CONTAINER_DIR", "/sessions"))
_DB_PATH = _SESSIONS_DIR / "sessions.db"

# reads_runs.status values.
STATUS_PENDING = "pending"
STATUS_DONE = "done"
STATUS_ALREADY_IN_ENA = "already_in_ena"
STATUS_FAILED = "failed"


def _now() -> str:
    return datetime.now(tz=UTC).isoformat()


def _connect() -> sqlite3.Connection:
    _SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    # NB: keep the default rollback journal — WAL needs a shared-memory (-shm)
    # mmap that fails ("disk I/O error") on Docker Desktop bind mounts
    # (virtiofs/gRPC-FUSE). A busy timeout covers the rare concurrent write
    # (reads-submission thread vs request threads); access is low-volume.
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            id          TEXT PRIMARY KEY,
            name        TEXT UNIQUE NOT NULL,
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL,
            test_env    INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS reads_runs (
            session_id           TEXT NOT NULL,
            run_name             TEXT NOT NULL,
            stable_alias         TEXT NOT NULL,
            status               TEXT NOT NULL,
            experiment_accession TEXT,
            run_accession        TEXT,
            submitted_alias      TEXT,
            submitted_at         TEXT,
            PRIMARY KEY (session_id, run_name)
        );
        """
    )
    return conn


# ---------------------------------------------------------------------------
# Per-session paths + atomic file IO
# ---------------------------------------------------------------------------


def session_dir(session_id: str) -> pathlib.Path:
    return _SESSIONS_DIR / session_id


def state_path(session_id: str) -> pathlib.Path:
    return session_dir(session_id) / "state.json"


def dh_export_path(session_id: str) -> pathlib.Path:
    return session_dir(session_id) / "dh_export.json"


def reads_log_path(session_id: str) -> pathlib.Path:
    return session_dir(session_id) / "logs" / "reads.log"


def _atomic_write_json(path: pathlib.Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj))
    tmp.replace(path)  # atomic on POSIX


def _iso_mtime(path: pathlib.Path) -> str | None:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat() if path.is_file() else None


def save_state(session_id: str, state: Any) -> str:
    _atomic_write_json(state_path(session_id), state)
    touch_session(session_id)
    return _iso_mtime(state_path(session_id)) or _now()


def load_state(session_id: str) -> Any | None:
    p = state_path(session_id)
    return json.loads(p.read_text()) if p.is_file() else None


def save_dh_export(session_id: str, export: Any) -> str:
    _atomic_write_json(dh_export_path(session_id), export)
    touch_session(session_id)
    return _iso_mtime(dh_export_path(session_id)) or _now()


def load_dh_export(session_id: str) -> tuple[Any | None, str | None]:
    p = dh_export_path(session_id)
    if not p.is_file():
        return None, None
    return json.loads(p.read_text()), _iso_mtime(p)


def append_reads_log(session_id: str, text: str) -> None:
    p = reads_log_path(session_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a") as fh:
        fh.write(text + "\n")


def read_reads_log(session_id: str) -> str:
    p = reads_log_path(session_id)
    return p.read_text() if p.is_file() else ""


# ---------------------------------------------------------------------------
# Stable, account-unique alias for a run within a session
# ---------------------------------------------------------------------------


def _slug(text: str) -> str:
    """Filesystem/alias-safe slug: keep word chars, collapse the rest to '-'."""
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", str(text).strip()).strip("-")
    return s or "x"


def session_run_alias(session_name: str, run_name: str) -> str:
    """Stable per-run alias. Session names are unique, so this is unique per
    account and identical across re-submits — which is what lets us detect a
    run that is already in ENA."""
    return f"{_slug(session_name)}_{_slug(run_name)}"


# ---------------------------------------------------------------------------
# Session registry
# ---------------------------------------------------------------------------


def _session_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "test_env": bool(row["test_env"]),
    }


def create_session(name: str, *, test_env: bool = True) -> dict[str, Any]:
    name = name.strip()
    if not name:
        raise ValueError("Session name is required")
    session_id = uuid.uuid4().hex[:12]
    now = _now()
    with _connect() as conn:
        try:
            conn.execute(
                "INSERT INTO sessions (id, name, created_at, updated_at, test_env) VALUES (?, ?, ?, ?, ?)",
                (session_id, name, now, now, int(test_env)),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"A session named {name!r} already exists") from exc
    session_dir(session_id).mkdir(parents=True, exist_ok=True)
    return {"id": session_id, "name": name, "created_at": now, "updated_at": now, "test_env": test_env}


def list_sessions() -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM sessions ORDER BY updated_at DESC").fetchall()
    return [_session_row_to_dict(r) for r in rows]


def get_session(session_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    return _session_row_to_dict(row) if row else None


def touch_session(session_id: str) -> None:
    with _connect() as conn:
        conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (_now(), session_id))


def set_test_env(session_id: str, test_env: bool) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE sessions SET test_env = ?, updated_at = ? WHERE id = ?",
            (int(test_env), _now(), session_id),
        )


def delete_session(session_id: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM reads_runs WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    d = session_dir(session_id)
    if d.is_dir():
        import shutil

        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
# reads_runs ledger (resumability)
# ---------------------------------------------------------------------------


def _reads_run_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "run_name": row["run_name"],
        "stable_alias": row["stable_alias"],
        "status": row["status"],
        "experiment_accession": row["experiment_accession"],
        "run_accession": row["run_accession"],
        "submitted_alias": row["submitted_alias"],
        "submitted_at": row["submitted_at"],
    }


def upsert_reads_run(
    session_id: str,
    run_name: str,
    stable_alias: str,
    status: str,
    *,
    experiment_accession: str | None = None,
    run_accession: str | None = None,
    submitted_alias: str | None = None,
) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO reads_runs (
                session_id, run_name, stable_alias, status,
                experiment_accession, run_accession, submitted_alias, submitted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id, run_name) DO UPDATE SET
                stable_alias = excluded.stable_alias,
                status = excluded.status,
                experiment_accession = excluded.experiment_accession,
                run_accession = excluded.run_accession,
                submitted_alias = excluded.submitted_alias,
                submitted_at = excluded.submitted_at
            """,
            (
                session_id,
                run_name,
                stable_alias,
                status,
                experiment_accession,
                run_accession,
                submitted_alias,
                _now(),
            ),
        )


def get_reads_run(session_id: str, run_name: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM reads_runs WHERE session_id = ? AND run_name = ?",
            (session_id, run_name),
        ).fetchone()
    return _reads_run_to_dict(row) if row else None


def list_reads_runs(session_id: str) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM reads_runs WHERE session_id = ? ORDER BY run_name",
            (session_id,),
        ).fetchall()
    return [_reads_run_to_dict(r) for r in rows]
