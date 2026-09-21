from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from backend import server as app_server


class ReportZipManifestTests(unittest.TestCase):
    def test_class_zip_records_unready_roster_students_as_skipped(self) -> None:
        class_id = "c" * 32
        ready_id = "a" * 32
        unready_id = "b" * 32
        students = [
            {
                "id": ready_id,
                "external_student_id": "S-1",
                "name": "Mark",
                "class_id": class_id,
                "class_name": "Economics",
                "period_id": "p" * 32,
                "period_label": "Week 1",
                "report_ready": True,
                "assignment_ready": True,
                "weekly_test_ready": True,
                "comments_ready": True,
                "template_ready": True,
                "google_ready": True,
            },
            {
                "id": unready_id,
                "external_student_id": "S-2",
                "name": "June",
                "class_id": class_id,
                "class_name": "Economics",
                "period_id": "p" * 32,
                "period_label": "Week 1",
                "report_ready": False,
                "assignment_ready": True,
                "weekly_test_ready": False,
                "comments_ready": False,
                "template_ready": True,
                "google_ready": True,
            },
        ]
        state = {
            "students": students,
            "classes": [{"id": class_id, "name": "Economics"}],
        }

        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            report = output / "mark.pdf"
            report.write_bytes(b"%PDF-test")
            with (
                patch.object(app_server, "app_state", return_value=state),
                patch.object(app_server, "generate_report", return_value=report) as generate,
                patch.object(app_server, "EXPORT_DIR", output / "exports"),
            ):
                archive, manifest, filename = app_server.generate_zip(class_id)

            self.assertIsNotNone(archive)
            self.assertEqual(filename, "Economics.zip")
            self.assertEqual([item["student_id"] for item in manifest["generated"]], [ready_id])
            self.assertEqual([item["student_id"] for item in manifest["skipped"]], [unready_id])
            self.assertEqual(manifest["failed"], [])
            self.assertIn("Weekly Test score", manifest["skipped"][0]["reason"])
            self.assertIn("teacher comment", manifest["skipped"][0]["reason"])
            generate.assert_called_once_with(ready_id, "p" * 32)

            with zipfile.ZipFile(archive) as bundle:
                summary = json.loads(bundle.read("generation-summary.json"))
                self.assertEqual(
                    {
                        item["student_id"]
                        for category in ("generated", "failed", "skipped")
                        for item in summary[category]
                    },
                    {ready_id, unready_id},
                )
                self.assertEqual(len([name for name in bundle.namelist() if name.endswith(".pdf")]), 1)


if __name__ == "__main__":
    unittest.main()
