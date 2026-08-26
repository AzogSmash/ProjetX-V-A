from __future__ import annotations

import asyncio
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


DEFAULT_DATABASE_PATH = Path("/data/industrial_economy.db")
FALLBACK_DATABASE_PATH = Path("./data/industrial_economy.db")
MIGRATIONS_DIRECTORY = Path(__file__).with_name("migrations")


def get_database_path() -> Path:
    configured = os.getenv("INDUSTRIAL_DB_PATH")
    if configured:
        return Path(configured).expanduser()
    if DEFAULT_DATABASE_PATH.parent.is_dir():
        return DEFAULT_DATABASE_PATH
    return FALLBACK_DATABASE_PATH


def connect_database(path: str | Path | None = None) -> sqlite3.Connection:
    database_path = Path(path) if path is not None else get_database_path()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path, timeout=5, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute("PRAGMA synchronous = NORMAL")
    return connection


def initialize_database_sync(path: str | Path | None = None) -> Path:
    database_path = Path(path) if path is not None else get_database_path()
    connection = connect_database(database_path)
    try:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS industrial_schema_version "
            "(version INTEGER PRIMARY KEY, applied_at INTEGER NOT NULL)"
        )
        applied = {
            int(row[0])
            for row in connection.execute("SELECT version FROM industrial_schema_version")
        }
        for migration in sorted(MIGRATIONS_DIRECTORY.glob("[0-9][0-9][0-9]_*.sql")):
            version = int(migration.name[:3])
            if version in applied:
                continue
            try:
                migration_sql = migration.read_text(encoding="utf-8")
                connection.executescript(
                    "BEGIN IMMEDIATE;\n"
                    f"{migration_sql}\n"
                    "INSERT INTO industrial_schema_version(version, applied_at) "
                    f"VALUES ({version}, unixepoch());\n"
                    "COMMIT;"
                )
            except Exception:
                if connection.in_transaction:
                    connection.rollback()
                raise
    finally:
        connection.close()
    return database_path


async def initialize_database(path: str | Path | None = None) -> Path:
    return await asyncio.to_thread(initialize_database_sync, path)


@contextmanager
def immediate_transaction(path: str | Path | None = None) -> Iterator[sqlite3.Connection]:
    connection = connect_database(path)
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
