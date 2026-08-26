"""Single-process, cancellable SQLite backup scheduler for Railway volumes."""
import asyncio
import logging
import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from economy_v2.database import connect_database, get_database_path

_task = None
logger = logging.getLogger(__name__)

def _enabled(): return os.getenv("INDUSTRIAL_BACKUP_ENABLED", "false").casefold() in {"1","true","yes","on"}

def backup_once(source=None, directory=None, retention=None, now=None):
    source=Path(source or get_database_path());directory=Path(directory or os.getenv("INDUSTRIAL_BACKUP_DIR","/data/backups"));retention=max(2,int(retention or os.getenv("INDUSTRIAL_BACKUP_RETENTION","12")));directory.mkdir(parents=True,exist_ok=True)
    destination=directory/f"industrial_economy-{int(now or time.time())}.db";temporary=directory/f".{destination.name}.tmp"
    with closing(connect_database(source)) as src,closing(sqlite3.connect(temporary)) as dst:src.backup(dst)
    with closing(sqlite3.connect(temporary)) as check:
        if check.execute("PRAGMA integrity_check").fetchone()[0]!="ok":temporary.unlink(missing_ok=True);raise RuntimeError("invalid SQLite backup")
    temporary.replace(destination)
    for old in sorted(directory.glob("industrial_economy-*.db"),key=lambda p:p.stat().st_mtime,reverse=True)[retention:]:old.unlink()
    return destination

async def _loop():
    interval=max(300,int(os.getenv("INDUSTRIAL_BACKUP_INTERVAL","21600")))
    while True:
        try:
            await asyncio.to_thread(backup_once)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("[ECONOMY BACKUP] SQLite backup failed; next attempt remains scheduled")
        await asyncio.sleep(interval)

def start_backup_scheduler():
    global _task
    if not _enabled() or (_task and not _task.done()):return _task
    _task=asyncio.create_task(_loop(),name="industrial-sqlite-backups");return _task

async def stop_backup_scheduler():
    global _task
    if _task:
        _task.cancel()
        try:await _task
        except asyncio.CancelledError:pass
        _task=None
