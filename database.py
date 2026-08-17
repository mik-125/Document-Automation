from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
import uuid
from datetime import UTC, datetime
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

                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
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
    ) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE students SET homework_json = ?, homework_completed = ?, homework_incomplete = ?, "
                "homework_excluded = ?, homework_percent = ?, period_start = ?, period_end = ?, updated_at = ? "
                "WHERE id = ?",
                (
                    json.dumps(entries, ensure_ascii=False),
                    completed,
                    incomplete,
                    excluded,
                    percent,
                    period_start,
                    period_end,
                    now_iso(),
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

    def get_class(self, class_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM classes WHERE id = ?", (class_id,)).fetchone()
        return dict(row) if row else None

    def get_exams(self, student_id: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM exams WHERE student_id = ? ORDER BY imported_at, label", (student_id,)
            ).fetchall()
        exams: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["sections"] = json.loads(item.pop("sections_json") or "[]")
            exams.append(item)
        return exams

    def upsert_exam(self, student_id: str, class_id: str, record: dict[str, Any]) -> None:
        with self.connect() as db:
            existing = db.execute(
                "SELECT id FROM exams WHERE student_id = ? AND label = ?",
                (student_id, record["label"]),
            ).fetchone()
            exam_id = str(existing["id"]) if existing else uuid.uuid4().hex
            db.execute(
                "INSERT INTO exams(id, student_id, class_id, label, score, max_score, sections_json, comment, "
                "source_name, imported_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(student_id, label) DO UPDATE SET score = excluded.score, "
                "max_score = excluded.max_score, sections_json = excluded.sections_json, "
                "comment = excluded.comment, source_name = excluded.source_name, imported_at = excluded.imported_at",
                (
                    exam_id,
                    student_id,
                    class_id,
                    record["label"],
                    record.get("score"),
                    record.get("max_score"),
                    json.dumps(record.get("sections", []), ensure_ascii=False),
                    record.get("comment", ""),
                    record.get("source_name", ""),
                    now_iso(),
                ),
            )

    def add_import(self, name: str, kind: str, state: str, summary: str) -> str:
        import_id = uuid.uuid4().hex
        with self.connect() as db:
            db.execute(
                "INSERT INTO imports(id, name, kind, state, summary, created_at) VALUES(?, ?, ?, ?, ?, ?)",
                (import_id, name, kind, state, summary, now_iso()),
            )
        return import_id

    def add_issue(
        self,
        *,
        kind: str,
        class_id: str,
        source_name: str,
        subject_name: str,
        payload: dict[str, Any],
        candidate_ids: list[str],
    ) -> str:
        issue_id = uuid.uuid4().hex
        with self.connect() as db:
            db.execute(
                "INSERT INTO issues(id, kind, class_id, source_name, subject_name, payload_json, "
                "candidate_ids_json, created_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    issue_id,
                    kind,
                    class_id,
                    source_name,
                    subject_name,
                    json.dumps(payload, ensure_ascii=False),
                    json.dumps(candidate_ids),
                    now_iso(),
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

    def mark_report_generated(self, student_id: str) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO reports(student_id, generated_at) VALUES(?, ?) "
                "ON CONFLICT(student_id) DO UPDATE SET generated_at = excluded.generated_at",
                (student_id, now_iso()),
            )

    def clear_all(self) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM issues")
            db.execute("DELETE FROM reports")
            db.execute("DELETE FROM exams")
            db.execute("DELETE FROM students")
            db.execute("DELETE FROM classes")
            db.execute("DELETE FROM imports")
            db.execute("DELETE FROM settings WHERE key NOT LIKE 'template_%'")

    def state(self) -> dict[str, Any]:
        with self.connect() as db:
            class_rows = db.execute("SELECT * FROM classes ORDER BY name").fetchall()
            student_rows = db.execute(
                "SELECT s.*, c.name AS class_name, c.teacher, c.subject, r.generated_at "
                "FROM students s JOIN classes c ON c.id = s.class_id "
                "LEFT JOIN reports r ON r.student_id = s.id ORDER BY c.name, s.name, s.school"
            ).fetchall()
            exam_rows = db.execute("SELECT * FROM exams ORDER BY imported_at, label").fetchall()
            import_rows = db.execute("SELECT * FROM imports ORDER BY created_at DESC LIMIT 100").fetchall()
            issue_rows = db.execute(
                "SELECT * FROM issues WHERE status = 'pending' ORDER BY created_at DESC"
            ).fetchall()

        exams_by_student: dict[str, list[dict[str, Any]]] = {}
        for row in exam_rows:
            exam = dict(row)
            exam["sections"] = json.loads(exam.pop("sections_json") or "[]")
            exams_by_student.setdefault(exam["student_id"], []).append(exam)

        students: list[dict[str, Any]] = []
        student_lookup: dict[str, dict[str, Any]] = {}
        for row in student_rows:
            student = dict(row)
            student["homework"] = json.loads(student.pop("homework_json") or "[]")
            student["exams"] = exams_by_student.get(student["id"], [])
            latest_exam = student["exams"][-1] if student["exams"] else None
            student["latest_exam"] = latest_exam
            student["ready"] = bool(
                student["homework_completed"] + student["homework_incomplete"] > 0
                and latest_exam
                and latest_exam.get("comment")
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

        classes = [dict(row) for row in class_rows]
        template_path = self.get_setting("template_path")
        template_name = self.get_setting("template_name", "Royal IB report template")
        template_pages = int(self.get_setting("template_pages", "0") or 0)
        template_fields = json.loads(self.get_setting("template_fields", "[]") or "[]")
        return {
            "classes": classes,
            "students": students,
            "files": [dict(row) for row in import_rows],
            "issues": issues,
            "template": {
                "name": template_name,
                "ready": bool(template_path and Path(template_path).exists()),
                "pages": template_pages,
                "fields": template_fields,
            },
            "reporting_period": {
                "start": self.get_setting("period_start"),
                "end": self.get_setting("period_end"),
            },
        }
