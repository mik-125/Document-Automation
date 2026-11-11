"""Week numbers identify reporting weeks; dates are descriptive (synthetic data)."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from backend.database import Database, week_number_from_label

OLD_PERIODS = """
CREATE TABLE periods (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(start_date, end_date)
);
"""
COUNTED = ("periods", "period_exams", "homework_periods", "class_periods")


class WeekNumberTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "brightline.db"
        self.db = Database(self.path)

    def test_label_parsing(self):
        for label, expected in [("Week 2", 2), ("WEEK 04", 4), ("week1", 1), ("3주차", 3),
                                ("2026-07-13 to 2026-07-17", None), ("Week 0", None), ("", None)]:
            self.assertEqual(week_number_from_label(label), expected, label)

    def test_same_dates_different_numbers_are_separate_weeks(self):
        a = self.db.ensure_period("2026-07-13", "2026-07-17", "Week 1")
        b = self.db.ensure_period("2026-07-13", "2026-07-17", "Week 2")
        self.assertNotEqual(a["id"], b["id"])
        self.assertEqual((a["week_number"], b["week_number"]), (1, 2))

    def test_number_wins_over_dates(self):
        a = self.db.ensure_period("2026-07-13", "2026-07-17", "Week 2")
        b = self.db.ensure_period("2026-08-03", "2026-08-07", "", week_number=2)
        self.assertEqual(a["id"], b["id"])
        self.assertEqual(b["start_date"], "2026-07-13")

    def test_blank_label_with_number_is_named_week_n(self):
        self.assertEqual(self.db.ensure_period("2026-07-13", "2026-07-17", "", week_number=4)["label"], "Week 4")

    def test_unnumbered_weeks_still_match_by_dates(self):
        a = self.db.ensure_period("2026-07-13", "2026-07-17", "Summer")
        self.assertIsNone(a["week_number"])
        self.assertEqual(self.db.ensure_period("2026-07-13", "2026-07-17")["id"], a["id"])

    def test_dates_only_lookup_finds_the_single_numbered_week(self):
        a = self.db.ensure_period("2026-07-13", "2026-07-17", "Week 2")
        self.assertEqual(self.db.ensure_period("2026-07-13", "2026-07-17")["id"], a["id"])

    def test_dates_only_lookup_is_refused_when_ambiguous(self):
        self.db.ensure_period("2026-07-13", "2026-07-17", "Week 1")
        self.db.ensure_period("2026-07-13", "2026-07-17", "Week 2")
        with self.assertRaisesRegex(ValueError, "Several weeks share these dates"):
            self.db.ensure_period("2026-07-13", "2026-07-17")

    def test_state_orders_numbered_weeks_newest_first_then_unnumbered(self):
        self.db.ensure_period("2026-07-13", "2026-07-17", "Week 1")
        self.db.ensure_period("2026-07-13", "2026-07-17", "Week 4")
        self.db.ensure_period("2026-07-13", "2026-07-17", "Week 2")
        self.db.ensure_period("2026-09-01", "2026-09-05", "Summer")
        self.assertEqual([p["label"] for p in self.db.state()["periods"]],
                         ["Week 4", "Week 2", "Week 1", "Summer"])

    def _downgrade(self, periods):
        """Rebuild ``periods`` with the pre-week-number schema and dependent rows."""
        school_class = self.db.create_class(name="Synthetic Class")
        student = self.db.create_student(class_id=school_class["id"], name="Synthetic Student")
        raw = sqlite3.connect(self.path)
        raw.execute("PRAGMA foreign_keys = OFF")
        raw.execute("DROP INDEX IF EXISTS idx_periods_week_number")
        raw.execute("DROP TABLE periods")
        raw.executescript(OLD_PERIODS)
        for index, (label, start, end) in enumerate(periods):
            period_id = f"{index:032x}"
            raw.execute("INSERT INTO periods VALUES(?, ?, ?, ?, 't', 't')", (period_id, label, start, end))
            raw.execute(
                "INSERT INTO period_exams(id, student_id, class_id, period_id, label, score, max_score, "
                "sections_json, comment, source_name, imported_at) VALUES(?, ?, ?, ?, 'Test', 80, 100, '[]', '', 's', 't')",
                (f"e{index:031x}", student["id"], school_class["id"], period_id))
            raw.execute(
                "INSERT INTO homework_periods(student_id, period_id, updated_at) VALUES(?, ?, 't')",
                (student["id"], period_id))
        raw.execute("DELETE FROM class_periods")
        raw.execute("INSERT INTO class_periods VALUES(?, ?, 't')", (school_class["id"], f"{0:032x}"))
        raw.commit()
        counts = {table: raw.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in COUNTED}
        raw.close()
        return counts

    def _backups(self):
        return sorted((self.path.parent / "backups").glob("brightline-before-week-number-*.db"))

    def test_fresh_database_writes_no_backup(self):
        self.assertEqual(self._backups(), [])

    def test_upgrade_from_old_schema_preserves_rows(self):
        before = self._downgrade([("Week 2", "2026-07-13", "2026-07-17"),
                                  ("2026-07-20 to 2026-07-24", "2026-07-20", "2026-07-24")])
        upgraded = Database(self.path)
        raw = sqlite3.connect(self.path)
        after = {table: raw.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in COUNTED}
        self.assertEqual(after, before)
        self.assertEqual(raw.execute("PRAGMA foreign_key_check").fetchall(), [])
        raw.close()
        numbers = {p["label"]: p["week_number"] for p in upgraded.state()["periods"]}
        self.assertEqual(numbers, {"Week 2": 2, "2026-07-20 to 2026-07-24": None})
        self.assertEqual(len(self._backups()), 1)
        Database(self.path)
        self.assertEqual(len(self._backups()), 1)
        # The old date rule is gone: a second week may reuse Week 2's dates.
        other = upgraded.ensure_period("2026-07-13", "2026-07-17", "Week 5")
        self.assertEqual(other["week_number"], 5)

    def test_upgrade_tolerates_existing_orphan_rows(self):
        before = self._downgrade([("Week 2", "2026-07-13", "2026-07-17")])
        raw = sqlite3.connect(self.path)
        raw.execute("PRAGMA foreign_keys = OFF")
        raw.execute("INSERT INTO class_periods VALUES('ghost-class', 'missing-period', 't')")
        raw.commit()
        raw.close()
        upgraded = Database(self.path)
        self.assertEqual([p["week_number"] for p in upgraded.state()["periods"]], [2])
        raw = sqlite3.connect(self.path)
        self.assertEqual(raw.execute("SELECT COUNT(*) FROM class_periods").fetchone()[0],
                         before["class_periods"] + 1)
        raw.close()
        self.assertEqual(len(self._backups()), 1)

    def test_upgrade_with_duplicate_label_numbers(self):
        self._downgrade([("Week 2", "2026-07-20", "2026-07-24"), ("Week 2", "2026-07-13", "2026-07-17")])
        upgraded = Database(self.path)
        numbers = {p["start_date"]: p["week_number"] for p in upgraded.state()["periods"]}
        self.assertEqual(numbers, {"2026-07-13": 2, "2026-07-20": None})


if __name__ == "__main__":
    unittest.main()
