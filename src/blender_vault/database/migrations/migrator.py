"""Versioned SQL schema migration engine with automatic pre-migration backups."""

from __future__ import annotations

import hashlib
import logging
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from ..models import utc_now_iso

logger = logging.getLogger("blender_vault.database.migrator")

MIGRATIONS_DIR = Path(__file__).resolve().parent
_MIGRATION_FILE_RE = re.compile(r"^(\d+)_(.+)\.sql$")


@dataclass(frozen=True)
class MigrationSpec:
    version: int
    name: str
    sql: str
    checksum: str


def load_sql_migrations(directory: Path = MIGRATIONS_DIR) -> list[MigrationSpec]:
    """Discover and parse numbered *.sql migration files in ascending version order."""
    specs: list[MigrationSpec] = []
    if not directory.is_dir():
        return specs
    for sql_file in sorted(directory.glob("*.sql")):
        match = _MIGRATION_FILE_RE.match(sql_file.name)
        if not match:
            continue
        ver = int(match.group(1))
        name = match.group(2)
        sql = sql_file.read_text(encoding="utf-8")
        checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
        specs.append(MigrationSpec(version=ver, name=name, sql=sql, checksum=checksum))
    specs.sort(key=lambda s: s.version)
    return specs


class SchemaMigrator:
    """Applies forward SQLite migrations atomically with automatic pre-migration backups."""

    def __init__(self, db_path: Path, backup_fn: Optional[Callable[[str], Path]] = None) -> None:
        self.db_path = db_path
        self.backup_fn = backup_fn

    def ensure_migrations_table(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                checksum TEXT NOT NULL,
                applied_at TEXT NOT NULL
            )
            """
        )

    def get_applied_versions(self, conn: sqlite3.Connection) -> dict[int, str]:
        self.ensure_migrations_table(conn)
        rows = conn.execute("SELECT version, checksum FROM schema_migrations ORDER BY version").fetchall()
        return {int(r[0]): str(r[1]) for r in rows}

    def run_migrations(
        self,
        conn: sqlite3.Connection,
        *,
        migrations: Optional[list[MigrationSpec]] = None,
        extra_migrations_dir: Optional[Path] = None,
    ) -> dict[str, object]:
        """Apply any pending migrations in order, backing up first if DB already has state."""
        all_specs = list(migrations) if migrations is not None else load_sql_migrations()
        if extra_migrations_dir is not None:
            all_specs.extend(load_sql_migrations(extra_migrations_dir))
            all_specs.sort(key=lambda s: s.version)

        applied = self.get_applied_versions(conn)
        pending = [m for m in all_specs if m.version not in applied]

        # Validate checksums of previously applied migrations
        for m in all_specs:
            if m.version in applied and applied[m.version] != m.checksum:
                logger.warning(
                    "Migration %03d (%s) checksum differs from recorded value.",
                    m.version,
                    m.name,
                )

        backup_path: Optional[Path] = None
        if pending and self.backup_fn is not None:
            # Back up whenever existing tables/data or previous migrations exist
            existing_tables = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name != 'schema_migrations'"
            ).fetchone()[0]
            if applied or existing_tables > 0:
                reason = f"pre_migration_v{pending[0].version:03d}"
                conn.commit()
                backup_path = self.backup_fn(reason)
                logger.info("Created automatic pre-migration backup: %s", backup_path)

        applied_now: list[int] = []
        for spec in pending:
            logger.info("Applying schema migration %03d_%s", spec.version, spec.name)
            conn.executescript(spec.sql)
            conn.execute("PRAGMA foreign_keys = ON;")
            conn.execute(
                """
                INSERT INTO schema_migrations (version, name, checksum, applied_at)
                VALUES (?, ?, ?, ?)
                """,
                (spec.version, spec.name, spec.checksum, utc_now_iso()),
            )
            conn.commit()
            applied_now.append(spec.version)

        return {
            "applied_versions": applied_now,
            "current_version": max(list(applied.keys()) + applied_now, default=0),
            "backup_path": str(backup_path) if backup_path else None,
        }
