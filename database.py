from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def normalize_value(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value or "").strip().lower()
    return re.sub(r"\s+", " ", normalized)


def normalize_student_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value or "").strip()
    normalized = re.sub(r"^\s*\(\s*ON\s*\)\s*", "", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.casefold()


def make_identity_key(name: str, school: str, grade: str, class_name: str) -> str:
    return "|".join(
        (
            normalize_student_name(name),
            normalize_value(school),
            normalize_value(grade),
            normalize_value(class_name),
        )
    )


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    @staticmethod
    def _ensure_column(db: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        columns = {row["name"] for row in db.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in columns:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def _initialize(self) -> None:
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS classes (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    normalized_name TEXT NOT NULL UNIQUE,
                    subject TEXT NOT NULL DEFAULT '',
                    teacher TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS students (
                    id TEXT PRIMARY KEY,
                    class_id TEXT NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
                    identity_key TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    normalized_name TEXT NOT NULL,
                    school TEXT NOT NULL DEFAULT '',
                    grade TEXT NOT NULL DEFAULT '',
                    attendance_mode TEXT NOT NULL DEFAULT '',
                    homework_json TEXT NOT NULL DEFAULT '[]',
                    homework_completed INTEGER NOT NULL DEFAULT 0,
                    homework_incomplete INTEGER NOT NULL DEFAULT 0,
                    homework_excluded INTEGER NOT NULL DEFAULT 0,
                    homework_percent REAL,
                    period_start TEXT,
                    period_end TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_students_class_name
                ON students(class_id, normalized_name);

                CREATE TABLE IF NOT EXISTS periods (
                    id TEXT PRIMARY KEY,
                    label TEXT NOT NULL,
                    start_date TEXT NOT NULL,
                    end_date TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(start_date, end_date)
                );

                CREATE TABLE IF NOT EXISTS class_periods (
                    class_id TEXT PRIMARY KEY REFERENCES classes(id) ON DELETE CASCADE,
                    period_id TEXT NOT NULL REFERENCES periods(id) ON DELETE CASCADE,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS homework_periods (
                    student_id TEXT NOT NULL REFERENCES students(id) ON DELETE CASCADE,
                    period_id TEXT NOT NULL REFERENCES periods(id) ON DELETE CASCADE,
                    homework_json TEXT NOT NULL DEFAULT '[]',
                    homework_completed INTEGER NOT NULL DEFAULT 0,
                    homework_incomplete INTEGER NOT NULL DEFAULT 0,
                    homework_excluded INTEGER NOT NULL DEFAULT 0,
                    homework_percent REAL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(student_id, period_id)
                );

                CREATE TABLE IF NOT EXISTS exams (
                    id TEXT PRIMARY KEY,
                    student_id TEXT NOT NULL REFERENCES students(id) ON DELETE CASCADE,
                    class_id TEXT NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
                    label TEXT NOT NULL,
                    score REAL,
                    max_score REAL,
                    sections_json TEXT NOT NULL DEFAULT '[]',
                    comment TEXT NOT NULL DEFAULT '',
                    source_name TEXT NOT NULL,
                    imported_at TEXT NOT NULL,
                    UNIQUE(student_id, label)
                );

                CREATE TABLE IF NOT EXISTS period_exams (
                    id TEXT PRIMARY KEY,
                    student_id TEXT NOT NULL REFERENCES students(id) ON DELETE CASCADE,
                    class_id TEXT NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
                    period_id TEXT NOT NULL REFERENCES periods(id) ON DELETE CASCADE,
                    label TEXT NOT NULL,
                    score REAL,
                    max_score REAL,
                    sections_json TEXT NOT NULL DEFAULT '[]',
                    comment TEXT NOT NULL DEFAULT '',
                    source_name TEXT NOT NULL,
                    imported_at TEXT NOT NULL,
                    UNIQUE(student_id, period_id, label)
                );

                CREATE TABLE IF NOT EXISTS imports (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    state TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS issues (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    class_id TEXT REFERENCES classes(id) ON DELETE CASCADE,
                    source_name TEXT NOT NULL,
                    subject_name TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    candidate_ids_json TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    resolved_at TEXT
                );

                CREATE TABLE IF NOT EXISTS reports (
                    student_id TEXT PRIMARY KEY REFERENCES students(id) ON DELETE CASCADE,
                    generated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS report_archive (
                    id TEXT PRIMARY KEY,
                    student_id TEXT REFERENCES students(id) ON DELETE SET NULL,
                    class_id TEXT REFERENCES classes(id) ON DELETE SET NULL,
                    period_id TEXT REFERENCES periods(id) ON DELETE SET NULL,
                    student_name TEXT NOT NULL,
                    class_name TEXT NOT NULL,
                    period_label TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    pdf_path TEXT NOT NULL,
                    generated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_report_archive_student_period
                ON report_archive(student_id, period_id, revision);

                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            self._ensure_column(db, "imports", "period_id", "TEXT")
            self._ensure_column(db, "imports", "stored_path", "TEXT")
            self._ensure_column(db, "issues", "period_id", "TEXT")
            self._migrate_legacy_period_data(db)

    def _migrate_legacy_period_data(self, db: sqlite3.Connection) -> None:
        settings = {
            row["key"]: row["value"]
            for row in db.execute(
                "SELECT key, value FROM settings WHERE key IN ('period_start', 'period_end', 'selected_period_id')"
            ).fetchall()
        }
        start = settings.get("period_start", "")
        end = settings.get("period_end", "")
        selected = settings.get("selected_period_id", "")
        if selected and db.execute("SELECT 1 FROM periods WHERE id = ?", (selected,)).fetchone():
            return
        has_legacy = db.execute(
            "SELECT 1 FROM students WHERE homework_completed + homework_incomplete > 0 LIMIT 1"
        ).fetchone() or db.execute("SELECT 1 FROM exams LIMIT 1").fetchone()
        if not has_legacy or not start or not end:
            return
        period_id = uuid.uuid4().hex
        timestamp = now_iso()
        db.execute(
            "INSERT OR IGNORE INTO periods(id, label, start_date, end_date, created_at, updated_at) "
            "VALUES(?, ?, ?, ?, ?, ?)",
            (period_id, f"{start} to {end}", start, end, timestamp, timestamp),
        )
        row = db.execute(
            "SELECT id FROM periods WHERE start_date = ? AND end_date = ?", (start, end)
        ).fetchone()
        if not row:
            return
        period_id = str(row["id"])
        db.execute(
            "INSERT OR IGNORE INTO homework_periods(student_id, period_id, homework_json, "
            "homework_completed, homework_incomplete, homework_excluded, homework_percent, updated_at) "
            "SELECT id, ?, homework_json, homework_completed, homework_incomplete, homework_excluded, "
            "homework_percent, updated_at FROM students WHERE homework_completed + homework_incomplete > 0",
            (period_id,),
        )
        db.execute(
            "INSERT OR IGNORE INTO period_exams(id, student_id, class_id, period_id, label, score, max_score, "
            "sections_json, comment, source_name, imported_at) SELECT id, student_id, class_id, ?, label, score, "
            "max_score, sections_json, comment, source_name, imported_at FROM exams",
            (period_id,),
        )
        db.execute(
            "INSERT OR IGNORE INTO class_periods(class_id, period_id, updated_at) "
            "SELECT id, ?, ? FROM classes",
            (period_id, timestamp),
        )
        db.execute("UPDATE imports SET period_id = ? WHERE period_id IS NULL", (period_id,))
        db.execute("UPDATE issues SET period_id = ? WHERE period_id IS NULL", (period_id,))
        db.execute(
            "INSERT INTO settings(key, value) VALUES('selected_period_id', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (period_id,),
        )

    def get_setting(self, key: str, default: str = "") -> str:
        with self.connect() as db:
            row = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def ensure_period(self, start_date: str, end_date: str, label: str = "") -> dict[str, Any]:
        try:
            start = date.fromisoformat(start_date)
            end = date.fromisoformat(end_date)
        except ValueError as exc:
            raise ValueError("Reporting period dates are invalid.") from exc
        if start > end:
            raise ValueError("The reporting period start must be before its end.")
        label = label.strip() or f"{start_date} to {end_date}"
        timestamp = now_iso()
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM periods WHERE start_date = ? AND end_date = ?", (start_date, end_date)
            ).fetchone()
            if row:
                db.execute(
                    "UPDATE periods SET label = ?, updated_at = ? WHERE id = ?",
                    (label, timestamp, row["id"]),
                )
                period_id = str(row["id"])
            else:
                period_id = uuid.uuid4().hex
                db.execute(
                    "INSERT INTO periods(id, label, start_date, end_date, created_at, updated_at) "
                    "VALUES(?, ?, ?, ?, ?, ?)",
                    (period_id, label, start_date, end_date, timestamp, timestamp),
                )
            result = db.execute("SELECT * FROM periods WHERE id = ?", (period_id,)).fetchone()
        return dict(result)

    def get_period(self, period_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM periods WHERE id = ?", (period_id,)).fetchone()
        return dict(row) if row else None

    def selected_period_id(self) -> str:
        return self.get_setting("selected_period_id")

    def select_period(self, period_id: str) -> None:
        period = self.get_period(period_id)
        if not period:
            raise ValueError("Reporting period not found.")
        self.set_setting("selected_period_id", period_id)
        self.set_setting("period_start", period["start_date"])
        self.set_setting("period_end", period["end_date"])

    def select_class_period(self, class_id: str, period_id: str) -> None:
        timestamp = now_iso()
        with self.connect() as db:
            if not db.execute("SELECT 1 FROM classes WHERE id = ?", (class_id,)).fetchone():
                raise ValueError("Class not found.")
            if not db.execute("SELECT 1 FROM periods WHERE id = ?", (period_id,)).fetchone():
                raise ValueError("Reporting period not found.")
            db.execute(
                "INSERT INTO class_periods(class_id, period_id, updated_at) VALUES(?, ?, ?) "
                "ON CONFLICT(class_id) DO UPDATE SET period_id = excluded.period_id, "
                "updated_at = excluded.updated_at",
                (class_id, period_id, timestamp),
            )

    def period_id_for_class(self, class_id: str) -> str:
        with self.connect() as db:
            row = db.execute(
                "SELECT period_id FROM class_periods WHERE class_id = ?", (class_id,)
            ).fetchone()
        return str(row["period_id"]) if row else self.selected_period_id()

    def upsert_class(self, name: str, teacher: str = "") -> dict[str, Any]:
        normalized = normalize_value(name)
        timestamp = now_iso()
        subject = re.sub(r"^\([^)]*\)\s*\d{1,2}:\d{2}\s*\[[^]]+\]\s*", "", name).strip()
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM classes WHERE normalized_name = ?", (normalized,)
            ).fetchone()
            if row:
                db.execute(
                    "UPDATE classes SET teacher = CASE WHEN ? <> '' THEN ? ELSE teacher END, "
                    "subject = CASE WHEN ? <> '' THEN ? ELSE subject END, updated_at = ? WHERE id = ?",
                    (teacher, teacher, subject, subject, timestamp, row["id"]),
                )
                class_id = str(row["id"])
            else:
                class_id = uuid.uuid4().hex
                db.execute(
                    "INSERT INTO classes(id, name, normalized_name, subject, teacher, created_at, updated_at) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?)",
                    (class_id, name.strip(), normalized, subject, teacher.strip(), timestamp, timestamp),
                )
            result = db.execute("SELECT * FROM classes WHERE id = ?", (class_id,)).fetchone()
        return dict(result)

    def upsert_student(
        self,
        *,
        class_id: str,
        class_name: str,
        name: str,
        school: str,
        grade: str,
        attendance_mode: str,
    ) -> tuple[dict[str, Any], bool]:
        identity_key = make_identity_key(name, school, grade, class_name)
        timestamp = now_iso()
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM students WHERE identity_key = ?", (identity_key,)
            ).fetchone()
            created = row is None
            if created:
                student_id = uuid.uuid4().hex
                db.execute(
                    "INSERT INTO students(id, class_id, identity_key, name, normalized_name, school, grade, "
                    "attendance_mode, created_at, updated_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        student_id,
                        class_id,
                        identity_key,
                        name.strip(),
                        normalize_student_name(name),
                        school.strip(),
                        grade.strip(),
                        attendance_mode,
                        timestamp,
                        timestamp,
                    ),
                )
            else:
                student_id = str(row["id"])
                db.execute(
                    "UPDATE students SET class_id = ?, name = ?, normalized_name = ?, school = ?, grade = ?, "
                    "attendance_mode = ?, updated_at = ? WHERE id = ?",
                    (
                        class_id,
                        name.strip(),
                        normalize_student_name(name),
                        school.strip(),
                        grade.strip(),
                        attendance_mode,
                        timestamp,
                        student_id,
                    ),
                )
            result = db.execute("SELECT * FROM students WHERE id = ?", (student_id,)).fetchone()
        return dict(result), created

    def create_student(
        self, *, class_id: str, name: str, school: str = "", grade: str = ""
    ) -> dict[str, Any]:
        name = name.strip()
        if not name:
            raise ValueError("Student name is required.")
        timestamp = now_iso()
        with self.connect() as db:
            class_row = db.execute("SELECT * FROM classes WHERE id = ?", (class_id,)).fetchone()
            if not class_row:
                raise ValueError("Choose a valid class.")
            identity_key = make_identity_key(name, school, grade, str(class_row["name"]))
            if db.execute("SELECT 1 FROM students WHERE identity_key = ?", (identity_key,)).fetchone():
                raise ValueError("That student already exists in this class.")
            student_id = uuid.uuid4().hex
            db.execute(
                "INSERT INTO students(id, class_id, identity_key, name, normalized_name, school, grade, "
                "attendance_mode, created_at, updated_at) VALUES(?, ?, ?, ?, ?, ?, ?, '', ?, ?)",
                (
                    student_id,
                    class_id,
                    identity_key,
                    name,
                    normalize_student_name(name),
                    school.strip(),
                    grade.strip(),
                    timestamp,
                    timestamp,
                ),
            )
            result = db.execute("SELECT * FROM students WHERE id = ?", (student_id,)).fetchone()
        return dict(result)

    def update_student_profile(
        self, student_id: str, *, class_id: str, name: str, school: str = "", grade: str = ""
    ) -> dict[str, Any]:
        name = name.strip()
        if not name:
            raise ValueError("Student name is required.")
        timestamp = now_iso()
        with self.connect() as db:
            if not db.execute("SELECT 1 FROM students WHERE id = ?", (student_id,)).fetchone():
                raise ValueError("Student not found.")
            class_row = db.execute("SELECT * FROM classes WHERE id = ?", (class_id,)).fetchone()
            if not class_row:
                raise ValueError("Choose a valid class.")
            identity_key = make_identity_key(name, school, grade, str(class_row["name"]))
            duplicate = db.execute(
                "SELECT 1 FROM students WHERE identity_key = ? AND id <> ?",
                (identity_key, student_id),
            ).fetchone()
            if duplicate:
                raise ValueError("That student already exists in this class.")
            db.execute(
                "UPDATE students SET class_id = ?, identity_key = ?, name = ?, normalized_name = ?, "
                "school = ?, grade = ?, updated_at = ? WHERE id = ?",
                (
                    class_id,
                    identity_key,
                    name,
                    normalize_student_name(name),
                    school.strip(),
                    grade.strip(),
                    timestamp,
                    student_id,
                ),
            )
            db.execute("UPDATE exams SET class_id = ? WHERE student_id = ?", (class_id, student_id))
            db.execute("UPDATE period_exams SET class_id = ? WHERE student_id = ?", (class_id, student_id))
            result = db.execute("SELECT * FROM students WHERE id = ?", (student_id,)).fetchone()
        return dict(result)

    def delete_students(self, student_ids: list[str]) -> int:
        unique_ids = list(dict.fromkeys(student_id for student_id in student_ids if student_id))
        if not unique_ids:
            return 0
        placeholders = ",".join("?" for _ in unique_ids)
        with self.connect() as db:
            count = int(
                db.execute(
                    f"SELECT COUNT(*) FROM students WHERE id IN ({placeholders})", unique_ids
                ).fetchone()[0]
            )
            db.execute(f"DELETE FROM students WHERE id IN ({placeholders})", unique_ids)
        return count

    def update_homework(
        self,
        student_id: str,
        entries: list[dict[str, Any]],
        completed: int,
        incomplete: int,
        excluded: int,
        percent: float | None,
        period_start: str,
        period_end: str,
        period_id: str | None = None,
    ) -> None:
        period_id = period_id or self.ensure_period(period_start, period_end)["id"]
        timestamp = now_iso()
        encoded = json.dumps(entries, ensure_ascii=False)
        with self.connect() as db:
            db.execute(
                "INSERT INTO homework_periods(student_id, period_id, homework_json, homework_completed, "
                "homework_incomplete, homework_excluded, homework_percent, updated_at) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(student_id, period_id) DO UPDATE SET "
                "homework_json = excluded.homework_json, homework_completed = excluded.homework_completed, "
                "homework_incomplete = excluded.homework_incomplete, homework_excluded = excluded.homework_excluded, "
                "homework_percent = excluded.homework_percent, updated_at = excluded.updated_at",
                (student_id, period_id, encoded, completed, incomplete, excluded, percent, timestamp),
            )
            db.execute(
                "UPDATE students SET homework_json = ?, homework_completed = ?, homework_incomplete = ?, "
                "homework_excluded = ?, homework_percent = ?, period_start = ?, period_end = ?, updated_at = ? "
                "WHERE id = ?",
                (
                    encoded,
                    completed,
                    incomplete,
                    excluded,
                    percent,
                    period_start,
                    period_end,
                    timestamp,
                    student_id,
                ),
            )

    def find_students_by_name(self, class_id: str, name: str) -> list[dict[str, Any]]:
        normalized = normalize_student_name(name)
        with self.connect() as db:
            rows = db.execute(
                "SELECT s.*, c.name AS class_name FROM students s JOIN classes c ON c.id = s.class_id "
                "WHERE s.class_id = ? AND s.normalized_name = ? ORDER BY s.school, s.grade",
                (class_id, normalized),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_student(self, student_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT s.*, c.name AS class_name, c.teacher, c.subject "
                "FROM students s JOIN classes c ON c.id = s.class_id WHERE s.id = ?",
                (student_id,),
            ).fetchone()
        return dict(row) if row else None

    def get_student_for_period(self, student_id: str, period_id: str | None = None) -> dict[str, Any] | None:
        student = self.get_student(student_id)
        if not student:
            return None
        period_id = period_id or self.period_id_for_class(student["class_id"])
        period = self.get_period(period_id) if period_id else None
        with self.connect() as db:
            homework = db.execute(
                "SELECT * FROM homework_periods WHERE student_id = ? AND period_id = ?",
                (student_id, period_id),
            ).fetchone() if period_id else None
        student.update(
            {
                "period_id": period_id,
                "period_label": period["label"] if period else "",
                "period_start": period["start_date"] if period else None,
                "period_end": period["end_date"] if period else None,
                "homework_json": homework["homework_json"] if homework else "[]",
                "homework_completed": int(homework["homework_completed"]) if homework else 0,
                "homework_incomplete": int(homework["homework_incomplete"]) if homework else 0,
                "homework_excluded": int(homework["homework_excluded"]) if homework else 0,
                "homework_percent": homework["homework_percent"] if homework else None,
            }
        )
        return student

    def get_class(self, class_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM classes WHERE id = ?", (class_id,)).fetchone()
        return dict(row) if row else None

    def get_exams(self, student_id: str, period_id: str | None = None) -> list[dict[str, Any]]:
        student = self.get_student(student_id)
        if not student:
            return []
        period_id = period_id or self.period_id_for_class(student["class_id"])
        if not period_id:
            return []
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM period_exams WHERE student_id = ? AND period_id = ? "
                "ORDER BY imported_at, label",
                (student_id, period_id),
            ).fetchall()
        exams: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["sections"] = json.loads(item.pop("sections_json") or "[]")
            exams.append(item)
        return exams

    def upsert_exam(
        self, student_id: str, class_id: str, record: dict[str, Any], period_id: str | None = None
    ) -> None:
        period_id = period_id or self.period_id_for_class(class_id)
        if not period_id or not self.get_period(period_id):
            raise ValueError("Choose a reporting period before importing comments.")
        with self.connect() as db:
            existing = db.execute(
                "SELECT id FROM period_exams WHERE student_id = ? AND period_id = ? AND label = ?",
                (student_id, period_id, record["label"]),
            ).fetchone()
            exam_id = str(existing["id"]) if existing else uuid.uuid4().hex
            db.execute(
                "INSERT INTO period_exams(id, student_id, class_id, period_id, label, score, max_score, "
                "sections_json, comment, source_name, imported_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(student_id, period_id, label) DO UPDATE SET score = excluded.score, "
                "max_score = excluded.max_score, sections_json = excluded.sections_json, "
                "comment = excluded.comment, source_name = excluded.source_name, imported_at = excluded.imported_at",
                (
                    exam_id,
                    student_id,
                    class_id,
                    period_id,
                    record["label"],
                    record.get("score"),
                    record.get("max_score"),
                    json.dumps(record.get("sections", []), ensure_ascii=False),
                    record.get("comment", ""),
                    record.get("source_name", ""),
                    now_iso(),
                ),
            )

    def add_import(
        self,
        name: str,
        kind: str,
        state: str,
        summary: str,
        period_id: str | None = None,
        stored_path: str | None = None,
    ) -> str:
        import_id = uuid.uuid4().hex
        with self.connect() as db:
            db.execute(
                "INSERT INTO imports(id, name, kind, state, summary, created_at, period_id, stored_path) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                (import_id, name, kind, state, summary, now_iso(), period_id, stored_path),
            )
        return import_id

    def get_import(self, import_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM imports WHERE id = ?", (import_id,)).fetchone()
        return dict(row) if row else None

    def add_issue(
        self,
        *,
        kind: str,
        class_id: str,
        source_name: str,
        subject_name: str,
        payload: dict[str, Any],
        candidate_ids: list[str],
        period_id: str | None = None,
    ) -> str:
        issue_id = uuid.uuid4().hex
        with self.connect() as db:
            db.execute(
                "INSERT INTO issues(id, kind, class_id, source_name, subject_name, payload_json, "
                "candidate_ids_json, created_at, period_id) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    issue_id,
                    kind,
                    class_id,
                    source_name,
                    subject_name,
                    json.dumps(payload, ensure_ascii=False),
                    json.dumps(candidate_ids),
                    now_iso(),
                    period_id,
                ),
            )
        return issue_id

    def get_issue(self, issue_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM issues WHERE id = ?", (issue_id,)).fetchone()
        if not row:
            return None
        issue = dict(row)
        issue["payload"] = json.loads(issue.pop("payload_json"))
        issue["candidate_ids"] = json.loads(issue.pop("candidate_ids_json"))
        return issue

    def resolve_issue(self, issue_id: str) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE issues SET status = 'resolved', resolved_at = ? WHERE id = ?",
                (now_iso(), issue_id),
            )

    def archive_report(self, student: dict[str, Any], period_id: str, pdf_path: Path) -> dict[str, Any]:
        period = self.get_period(period_id)
        if not period:
            raise ValueError("Reporting period not found.")
        report_id = uuid.uuid4().hex
        with self.connect() as db:
            row = db.execute(
                "SELECT COALESCE(MAX(revision), 0) AS revision FROM report_archive "
                "WHERE student_id = ? AND period_id = ?",
                (student["id"], period_id),
            ).fetchone()
            revision = int(row["revision"]) + 1
            db.execute(
                "INSERT INTO report_archive(id, student_id, class_id, period_id, student_name, class_name, "
                "period_label, revision, snapshot_json, pdf_path, generated_at) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    report_id,
                    student["id"],
                    student["class_id"],
                    period_id,
                    student["name"],
                    student["class_name"],
                    period["label"],
                    revision,
                    json.dumps(student, ensure_ascii=False),
                    str(pdf_path),
                    now_iso(),
                ),
            )
        return self.get_archived_report(report_id) or {}

    def get_archived_report(self, report_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM report_archive WHERE id = ?", (report_id,)).fetchone()
        return dict(row) if row else None

    def clear_all(self) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM issues")
            db.execute("DELETE FROM report_archive")
            db.execute("DELETE FROM reports")
            db.execute("DELETE FROM period_exams")
            db.execute("DELETE FROM exams")
            db.execute("DELETE FROM homework_periods")
            db.execute("DELETE FROM class_periods")
            db.execute("DELETE FROM students")
            db.execute("DELETE FROM classes")
            db.execute("DELETE FROM imports")
            db.execute("DELETE FROM periods")
            db.execute("DELETE FROM settings WHERE key NOT LIKE 'template_%'")

    def state(self) -> dict[str, Any]:
        with self.connect() as db:
            period_rows = db.execute(
                "SELECT * FROM periods ORDER BY start_date DESC, end_date DESC"
            ).fetchall()
            class_rows = db.execute(
                "SELECT c.*, cp.period_id AS selected_period_id FROM classes c "
                "LEFT JOIN class_periods cp ON cp.class_id = c.id ORDER BY c.name"
            ).fetchall()
            student_rows = db.execute(
                "SELECT s.*, c.name AS class_name, c.teacher, c.subject "
                "FROM students s JOIN classes c ON c.id = s.class_id ORDER BY c.name, s.name, s.school"
            ).fetchall()
            homework_rows = db.execute("SELECT * FROM homework_periods").fetchall()
            exam_rows = db.execute("SELECT * FROM period_exams ORDER BY imported_at, label").fetchall()
            import_rows = db.execute(
                "SELECT i.id, i.name, i.kind, i.state, i.summary, i.created_at, i.period_id, "
                "p.label AS period_label FROM imports i "
                "LEFT JOIN periods p ON p.id = i.period_id ORDER BY i.created_at DESC LIMIT 100"
            ).fetchall()
            issue_rows = db.execute(
                "SELECT i.*, p.label AS period_label FROM issues i "
                "LEFT JOIN periods p ON p.id = i.period_id "
                "WHERE i.status = 'pending' ORDER BY i.created_at DESC"
            ).fetchall()
            report_rows = db.execute(
                "SELECT * FROM report_archive ORDER BY generated_at DESC LIMIT 250"
            ).fetchall()

        periods = [dict(row) for row in period_rows]
        period_lookup = {item["id"]: item for item in periods}
        selected_period_id = self.selected_period_id()
        classes: list[dict[str, Any]] = []
        class_period_lookup: dict[str, str] = {}
        for row in class_rows:
            item = dict(row)
            item["selected_period_id"] = item.get("selected_period_id") or selected_period_id
            selected = period_lookup.get(item["selected_period_id"])
            item["selected_period_label"] = selected["label"] if selected else "No period selected"
            class_period_lookup[item["id"]] = item["selected_period_id"]
            classes.append(item)

        homework_lookup = {
            (row["student_id"], row["period_id"]): dict(row) for row in homework_rows
        }
        exams_lookup: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for row in exam_rows:
            exam = dict(row)
            exam["sections"] = json.loads(exam.pop("sections_json") or "[]")
            exams_lookup.setdefault((exam["student_id"], exam["period_id"]), []).append(exam)

        students: list[dict[str, Any]] = []
        student_lookup: dict[str, dict[str, Any]] = {}
        for row in student_rows:
            student = dict(row)
            period_id = class_period_lookup.get(student["class_id"], selected_period_id)
            period = period_lookup.get(period_id)
            homework = homework_lookup.get((student["id"], period_id))
            student.update(
                {
                    "period_id": period_id,
                    "period_label": period["label"] if period else "",
                    "period_start": period["start_date"] if period else None,
                    "period_end": period["end_date"] if period else None,
                    "homework": json.loads(homework["homework_json"] or "[]") if homework else [],
                    "homework_completed": int(homework["homework_completed"]) if homework else 0,
                    "homework_incomplete": int(homework["homework_incomplete"]) if homework else 0,
                    "homework_excluded": int(homework["homework_excluded"]) if homework else 0,
                    "homework_percent": homework["homework_percent"] if homework else None,
                    "exams": exams_lookup.get((student["id"], period_id), []),
                }
            )
            student["latest_exam"] = student["exams"][-1] if student["exams"] else None
            student["ready"] = bool(
                student["homework_completed"] + student["homework_incomplete"] > 0
                and student["latest_exam"]
                and student["latest_exam"].get("comment")
            )
            students.append(student)
            student_lookup[student["id"]] = student

        issues: list[dict[str, Any]] = []
        for row in issue_rows:
            issue = dict(row)
            issue["payload"] = json.loads(issue.pop("payload_json") or "{}")
            candidate_ids = json.loads(issue.pop("candidate_ids_json") or "[]")
            issue["candidates"] = [
                {
                    "id": student_lookup[candidate_id]["id"],
                    "name": student_lookup[candidate_id]["name"],
                    "school": student_lookup[candidate_id]["school"],
                    "grade": student_lookup[candidate_id]["grade"],
                    "class_name": student_lookup[candidate_id]["class_name"],
                }
                for candidate_id in candidate_ids
                if candidate_id in student_lookup
            ]
            issues.append(issue)

        selected_period = period_lookup.get(selected_period_id)
        template_path = self.get_setting("template_path")
        return {
            "classes": classes,
            "students": students,
            "files": [dict(row) for row in import_rows],
            "issues": issues,
            "periods": periods,
            "selected_period_id": selected_period_id,
            "reports": [dict(row) for row in report_rows],
            "template": {
                "name": self.get_setting("template_name", "Royal IB report template"),
                "ready": bool(template_path and Path(template_path).exists()),
                "pages": int(self.get_setting("template_pages", "0") or 0),
                "fields": json.loads(self.get_setting("template_fields", "[]") or "[]"),
            },
            "reporting_period": {
                "id": selected_period_id,
                "label": selected_period["label"] if selected_period else "",
                "start": selected_period["start_date"] if selected_period else "",
                "end": selected_period["end_date"] if selected_period else "",
            },
        }
