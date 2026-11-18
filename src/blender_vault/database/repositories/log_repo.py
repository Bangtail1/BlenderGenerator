"""Production logs and Drive upload retry queue repository."""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Optional

from ..db_manager import RobloxDatabaseManager
from ..models import DriveUploadQueueRecord, ProductionLogRecord, utc_now_iso


class LogRepository:
    """Manages structured production_logs and persistent drive_upload_queue rows."""

    def __init__(self, db: RobloxDatabaseManager) -> None:
        self.db = db

    @staticmethod
    def _row_to_log(row: sqlite3.Row) -> ProductionLogRecord:
        return ProductionLogRecord(
            log_id=int(row["log_id"]),
            asset_id=row["asset_id"],
            process_name=row["process_name"],
            status=row["status"],
            message=row["message"],
            error_details=row["error_details"],
            timestamp=row["timestamp"],
        )

    @staticmethod
    def _row_to_queue(row: sqlite3.Row) -> DriveUploadQueueRecord:
        return DriveUploadQueueRecord(
            queue_id=int(row["queue_id"]),
            asset_id=row["asset_id"],
            version_id=int(row["version_id"]),
            payload=json.loads(row["payload_json"] or "{}"),
            status=row["status"],
            attempts=int(row["attempts"]),
            max_attempts=int(row["max_attempts"]),
            last_error=row["last_error"],
            next_retry_at=row["next_retry_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def log_event(
        self,
        *,
        process_name: str,
        status: str,
        message: str,
        asset_id: Optional[str] = None,
        error_details: Optional[str] = None,
        conn: Optional[sqlite3.Connection] = None,
    ) -> ProductionLogRecord:
        now = utc_now_iso()

        def _insert(c: sqlite3.Connection) -> ProductionLogRecord:
            cur = c.execute(
                """
                INSERT INTO production_logs (asset_id, process_name, status, message, error_details, timestamp)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (asset_id, process_name, status, message, error_details, now),
            )
            row = c.execute("SELECT * FROM production_logs WHERE log_id = ?", (cur.lastrowid,)).fetchone()
            return self._row_to_log(row)

        if conn is not None:
            return _insert(conn)
        with self.db.connect() as c:
            return _insert(c)

    def list_logs(
        self,
        *,
        asset_id: Optional[str] = None,
        process_name: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> list[ProductionLogRecord]:
        clauses: list[str] = []
        params: list[Any] = []
        if asset_id is not None:
            clauses.append("asset_id = ?")
            params.append(asset_id)
        if process_name is not None:
            clauses.append("process_name = ?")
            params.append(process_name)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        with self.db.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM production_logs {where_sql} ORDER BY log_id DESC LIMIT ?",
                tuple(params),
            ).fetchall()
            return [self._row_to_log(r) for r in rows]

    # ------------------------------------------------------------------
    # Persistent Drive Upload Retry Queue
    # ------------------------------------------------------------------
    def enqueue_drive_upload(
        self,
        *,
        asset_id: str,
        version_id: int,
        payload: dict[str, Any],
        last_error: Optional[str] = None,
        max_attempts: int = 5,
    ) -> DriveUploadQueueRecord:
        now = utc_now_iso()
        with self.db.connect() as conn:
            existing = conn.execute(
                """
                SELECT * FROM drive_upload_queue
                WHERE asset_id = ? AND version_id = ? AND status IN ('PENDING', 'UPLOADING')
                ORDER BY queue_id DESC LIMIT 1
                """,
                (asset_id, version_id),
            ).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE drive_upload_queue
                    SET payload_json = ?, last_error = COALESCE(?, last_error), updated_at = ?
                    WHERE queue_id = ?
                    """,
                    (json.dumps(payload), last_error, now, existing["queue_id"]),
                )
                row = conn.execute("SELECT * FROM drive_upload_queue WHERE queue_id = ?", (existing["queue_id"],)).fetchone()
                return self._row_to_queue(row)

            cur = conn.execute(
                """
                INSERT INTO drive_upload_queue (
                    asset_id, version_id, payload_json, status, attempts, max_attempts,
                    last_error, next_retry_at, created_at, updated_at
                ) VALUES (?, ?, ?, 'PENDING', 0, ?, ?, ?, ?, ?)
                """,
                (asset_id, version_id, json.dumps(payload), max_attempts, last_error, now, now, now),
            )
            row = conn.execute("SELECT * FROM drive_upload_queue WHERE queue_id = ?", (cur.lastrowid,)).fetchone()
            return self._row_to_queue(row)

    def list_pending_drive_uploads(self, *, include_failed: bool = False) -> list[DriveUploadQueueRecord]:
        statuses = ("PENDING", "FAILED") if include_failed else ("PENDING",)
        placeholders = ",".join("?" for _ in statuses)
        with self.db.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM drive_upload_queue WHERE status IN ({placeholders}) ORDER BY queue_id ASC",
                statuses,
            ).fetchall()
            return [self._row_to_queue(r) for r in rows]

    def mark_drive_upload_attempt(
        self,
        queue_id: int,
        *,
        error: Optional[str] = None,
        next_retry_at: Optional[str] = None,
    ) -> DriveUploadQueueRecord:
        now = utc_now_iso()
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM drive_upload_queue WHERE queue_id = ?", (queue_id,)).fetchone()
            if not row:
                raise KeyError(f"Drive queue item {queue_id} not found.")
            attempts = int(row["attempts"]) + 1
            max_attempts = int(row["max_attempts"])
            new_status = "FAILED" if attempts >= max_attempts else "PENDING"
            conn.execute(
                """
                UPDATE drive_upload_queue
                SET attempts = ?, status = ?, last_error = ?, next_retry_at = ?, updated_at = ?
                WHERE queue_id = ?
                """,
                (attempts, new_status, error, next_retry_at or now, now, queue_id),
            )
            updated = conn.execute("SELECT * FROM drive_upload_queue WHERE queue_id = ?", (queue_id,)).fetchone()
            return self._row_to_queue(updated)

    def mark_drive_upload_completed(self, queue_id: int) -> DriveUploadQueueRecord:
        now = utc_now_iso()
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE drive_upload_queue
                SET status = 'COMPLETED', last_error = NULL, updated_at = ?
                WHERE queue_id = ?
                """,
                (now, queue_id),
            )
            row = conn.execute("SELECT * FROM drive_upload_queue WHERE queue_id = ?", (queue_id,)).fetchone()
            if not row:
                raise KeyError(f"Drive queue item {queue_id} not found.")
            return self._row_to_queue(row)
