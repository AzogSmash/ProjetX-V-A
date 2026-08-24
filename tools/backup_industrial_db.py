from __future__ import annotations

import argparse
import sqlite3
from contextlib import closing
from pathlib import Path

from economy_v2.database import connect_database, get_database_path


def backup_database(destination: Path, source: Path | None = None) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with closing(connect_database(source or get_database_path())) as source_connection:
        with closing(sqlite3.connect(destination)) as destination_connection:
            source_connection.backup(destination_connection)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sauvegarde cohérente de la base industrielle SQLite")
    parser.add_argument("destination", type=Path)
    parser.add_argument("--source", type=Path)
    arguments = parser.parse_args()
    backup_database(arguments.destination, arguments.source)
