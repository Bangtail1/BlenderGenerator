"""Thread-safe SQLite database manager for the Roblox Asset Library."""

from __future__ import annotations

import logging
import shutil
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

from .migrations.migrator import MigrationSpec, SchemaMigrator

logger = logging.getLogger("blender_vault.database")


class RobloxDatabaseManager:
    """Manages SQLite connections, FK enforcement, schema migrations, backups, and integrity checks."""

    def __init__(
        self,
        db_path: Path,
        *,
        backups_dir: Optional[Path] = None,
        auto_migrate: bool = True,
    ) -> None:
        self.db_path = Path(db_path)
        self.backups_dir = Path(backups_dir) if backups_dir else (self.db_path.parent / "backups")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.migrator = SchemaMigrator(self.db_path, backup_fn=self.create_backup)
        self.last_migration_result: dict[str, Any] = {}
        if auto_migrate:
            self.initialize()

    @contextmanager
    def connect(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        """Yield a SQLite connection with WAL mode, FK enforcement, and safe commit/rollback."""
        with self._lock:
            conn = sqlite3.connect(
                str(self.db_path),
                timeout=30.0,
                isolation_level=None if immediate else "",
                check_same_thread=False,
            )
            conn.row_factory = sqlite3.Row
            try:
                conn.execute("PRAGMA foreign_keys = ON;")
                conn.execute("PRAGMA journal_mode = WAL;")
                conn.execute("PRAGMA synchronous = NORMAL;")
                if immediate:
                    conn.execute("BEGIN IMMEDIATE;")
                yield conn
                if conn.in_transaction:
                    conn.commit()
            except Exception:
                if conn.in_transaction:
                    conn.rollback()
                raise
            finally:
                conn.close()

    def initialize(self, *, extra_migrations_dir: Optional[Path] = None) -> dict[str, Any]:
        """Initialize the SQLite file and apply any pending schema migrations."""
        with self.connect() as conn:
            res = self.migrator.run_migrations(conn, extra_migrations_dir=extra_migrations_dir)
        self.last_migration_result = res
        return res

    def apply_migrations(self, migrations: list[MigrationSpec]) -> dict[str, Any]:
        """Apply explicit migration specifications (with automatic pre-migration backup)."""
        with self.connect() as conn:
            res = self.migrator.run_migrations(conn, migrations=migrations)
        self.last_migration_result = res
        return res

    def get_schema_version(self) -> int:
        with self.connect() as conn:
            applied = self.migrator.get_applied_versions(conn)
            return max(applied.keys(), default=0)

    def create_backup(self, reason: str = "manual") -> Path:
        """Create a consistent online SQLite backup using sqlite3.Connection.backup()."""
        self.backups_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        safe_reason = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in reason).strip("_") or "backup"
        dest = self.backups_dir / f"{self.db_path.stem}_{safe_reason}_{stamp}.db"
        # Ensure unique filename if multiple backups occur within the same second
        counter = 1
        while dest.exists():
            dest = self.backups_dir / f"{self.db_path.stem}_{safe_reason}_{stamp}_{counter}.db"
            counter += 1

        with self._lock:
            src_conn = sqlite3.connect(str(self.db_path), timeout=30.0)
            dst_conn = sqlite3.connect(str(dest), timeout=30.0)
            try:
                src_conn.backup(dst_conn)
                dst_conn.commit()
            finally:
                dst_conn.close()
                src_conn.close()
        logger.info("SQLite backup written to %s", dest)
        return dest

    def restore_from_backup(self, backup_path: Path) -> Path:
        """Safely restore database from a verified backup after creating a pre-restore safety snapshot."""
        src = Path(backup_path)
        if not src.is_file():
            raise FileNotFoundError(f"Backup file not found: {src}")

        # Verify backup integrity before overwriting live DB
        check_conn = sqlite3.connect(str(src), timeout=30.0)
        try:
            res = check_conn.execute("PRAGMA integrity_check;").fetchone()
            if not res or res[0] != "ok":
                raise RuntimeError(f"Backup file failed integrity check: {res}")
        finally:
            check_conn.close()

        safety_copy = self.create_backup(reason="pre_restore") if self.db_path.is_file() else None
        with self._lock:
            tmp_target = self.db_path.with_suffix(".restore.tmp")
            shutil.copy2(src, tmp_target)
            tmp_target.replace(self.db_path)
            # Remove stale WAL/SHM sidecars if present after file replacement
            for suffix in ("-wal", "-shm"):
                sidecar = Path(str(self.db_path) + suffix)
                if sidecar.exists():
                    sidecar.unlink()
        logger.info("Database restored from %s (pre-restore backup: %s)", src, safety_copy)
        return self.db_path

    def verify_integrity(self) -> dict[str, Any]:
        """Run SQLite PRAGMA integrity_check and foreign_key_check."""
        with self.connect() as conn:
            fk_enabled = bool(conn.execute("PRAGMA foreign_keys;").fetchone()[0])
            integrity_rows = [r[0] for r in conn.execute("PRAGMA integrity_check;").fetchall()]
            fk_violations = [dict(zip(("table", "rowid", "parent", "fkid"), r)) for r in conn.execute("PRAGMA foreign_key_check;").fetchall()]
            table_counts: dict[str, int] = {}
            tables = [
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                ).fetchall()
            ]
            for tbl in tables:
                table_counts[tbl] = int(conn.execute(f"SELECT COUNT(*) FROM [{tbl}]").fetchone()[0])

        ok = fk_enabled and integrity_rows == ["ok"] and len(fk_violations) == 0
        return {
            "ok": ok,
            "foreign_keys_enabled": fk_enabled,
            "integrity_check": integrity_rows,
            "foreign_key_violations": fk_violations,
            "schema_version": self.get_schema_version(),
            "table_counts": table_counts,
        }
