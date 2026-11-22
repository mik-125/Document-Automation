"""Exercise the Sheet-to-report workflow over HTTP with synthetic files only."""

import base64
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import tempfile
import textwrap
from threading import Thread
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from pypdf import PdfReader

from tests.test_adaptive_reports import make_korean_original, squash
from tests.test_comment_pdf_import import comments_pdf
from tests.test_sample_content import make_sample
from tests.test_sheet_week_blocks import block, side_by_side
from backend import server
from backend.adaptive_reports import MISSING_TEST_FEEDBACK
from backend.database import Database


class SyntheticSheet:
    identity_fingerprint = "synthetic-sheet-account"

    @staticmethod
    def account_email():
        return "teacher@example.test"

    @staticmethod
    def read_sheet(spreadsheet, worksheet):
        weeks = []
        for week, scores in enumerate(zip((55, 80, "", 70), (92, 70, 80, 90), (84, "", 75, "")), 1):
            roster = [(name, "10", "School", "ABFAX", str(score))
                      for name, score in zip(("(ON)라마바", "사아자", "차카타"), scores)]
            weeks.append(block(week, roster, class_name="[25FA] Class A"))
        return {"spreadsheet_id": spreadsheet, "spreadsheet_name": "Synthetic academy",
                "worksheet": worksheet, "tabs": [worksheet], "values": side_by_side(*weeks, spacer=1)}


class QuietAppHandler(server.AppHandler):
    def log_message(self, format, *args):
        pass


class ReportHttpTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.dict(os.environ, {"BRIGHTLINE_DATA_DIR": str(self.root), "BRIGHTLINE_PASSWORD": ""}))
        self.enterContext(patch.multiple(
            server, DATA_DIR=self.root, DB=Database(self.root / "test.db"),
            REPORT_DIR=self.root / "reports", EXPORT_DIR=self.root / "exports",
            EXAMPLE_DIR=self.root / "examples", PDF_TEMPLATE_DIR=self.root / "templates",
        ))
        self.enterContext(patch.object(server.GoogleWorkspace, "from_env", return_value=SyntheticSheet()))
        httpd = self.enterContext(ThreadingHTTPServer(("127.0.0.1", 0), QuietAppHandler))
        self.port = httpd.server_port
        thread = Thread(target=httpd.serve_forever, kwargs={"poll_interval": .05}, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(httpd.shutdown)

    def request(self, path, body=None, *, expected=200, headers=None):
        connection = HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            if body is None:
                connection.request("GET", path, headers=headers or {})
            else:
                connection.request("POST", path, json.dumps(body), {"Content-Type": "application/json"})
            response = connection.getresponse()
            data = response.read()
            is_json = response.getheader("Content-Type", "").startswith("application/json")
            result = json.loads(data) if is_json else data
            self.assertEqual(response.status, expected, f"{path}: {result if is_json else response.reason}")
            return result
        finally:
            connection.close()

    @staticmethod
    def pdf_upload(filename, data):
        return {"filename": filename, "pdf_base64": base64.b64encode(data).decode("ascii")}

    def test_sheet_and_mixed_comment_pdf_generate_isolated_reports_and_class_zip(self):
        lookup = {"kind": "assignment", "spreadsheet": "synthetic", "worksheet": "Scores"}
        preview = self.request("/api/google/sheets/preview", lookup)
        self.assertEqual([week["week_number"] for week in preview["weeks"]], [1, 2, 3, 4])
        state = self.request("/api/google/sheets/sync", lookup)["state"]
        week_id = next(week["id"] for week in state["periods"] if week["week_number"] == 4)
        class_id = state["classes"][0]["id"]
        target = {"class_id": class_id, "period_id": week_id}
        state = self.request(f"/api/classes/{class_id}/period", {"period_id": week_id})["state"]
        student_ids = {student["name"].removeprefix("(ON)").strip(): student["id"] for student in state["students"]}

        first_comment = "라마바 학생은 잘했습니다. " + " ".join(
            f"문항{index:02d} 반응식을 자세히 설명했습니다." for index in range(85)
        )
        second_comment = "사아자 학생은 복습이 필요합니다."
        # Supply the long first comment on visual lines like a teacher PDF.
        first_lines = ["라마바", "", *textwrap.wrap(first_comment, width=38)]
        source_pages = [first_lines[index:index + 25] for index in range(0, len(first_lines), 25)]
        source = comments_pdf([*source_pages, ["총점: 18 / 21", "", second_comment]])
        self.request("/api/comments/pdf/import", {**target, **self.pdf_upload("comments.pdf", source)})
        self.request("/api/comments/shared", {**target, "comment": "___ 학생은 과제를 완성했습니다."})

        original = self.root / "original.pdf"
        make_korean_original(original)
        templates = f"/api/templates/{class_id}"
        draft = self.request(f"{templates}/pdf-upload", self.pdf_upload("original.pdf", original.read_bytes()), expected=201)
        published = self.request(f"{templates}/pdf-map", {
            "template_id": draft["template_id"], "mapping": draft["mapping"], "publish": True,
        })
        self.assertFalse(published["state"]["class_templates"][0]["ready"])
        report_url = f"/api/reports/student/{student_ids['라마바']}/download"
        missing = self.request(report_url, {"period_id": week_id}, expected=400)
        self.assertIn("Upload a completed sample report", missing["error"])

        sample = self.root / "sample.pdf"
        make_sample(sample)
        state = self.request(f"{templates}/example", self.pdf_upload("sample.pdf", sample.read_bytes()))["state"]
        by_name = {student["name"].removeprefix("(ON)").strip(): student for student in state["students"]}
        self.assertTrue(all(student["report_ready"] for student in by_name.values()))
        self.assertTrue(by_name["차카타"]["missing_test_feedback_assumption"])
        self.assertFalse(by_name["사아자"]["missing_test_feedback_assumption"])

        first = PdfReader(io.BytesIO(self.request(report_url, {"period_id": week_id})))
        first_text = "\n".join(page.extract_text() for page in first.pages)
        self.assertIn("Week 1 ~ Week 4 · 평균 68.3%", first_text)
        self.assertNotIn("Week 3", first_text)
        self.assertIn("라마바학생은과제를완성했습니다.", squash(first_text))
        self.assertNotIn("___", first_text)
        self.assertNotIn("사아자", first_text)
        self.assertNotIn("총점", first_text)
        self.assertNotIn("■", first_text)
        self.assertGreater(len(first.pages), 1)
        self.assertIn("위클리테스트", squash(first.pages[1].extract_text()))
        for index in range(85):
            self.assertEqual(squash(first_text).count(f"문항{index:02d}"), 1)

        archive = self.request(f"/api/reports/class/{class_id}/download", {})
        self.assertTrue(archive["ok"])
        self.assertEqual(archive["generated_count"], 3)
        with ZipFile(io.BytesIO(self.request(archive["download_url"]))) as bundle:
            for name, expected in [("사아자", squash(second_comment)), ("차카타", squash(MISSING_TEST_FEEDBACK))]:
                member = next(member for member in bundle.namelist() if name in member)
                report = PdfReader(io.BytesIO(bundle.read(member)))
                self.assertEqual(len(report.pages), 1)
                text = squash(report.pages[0].extract_text())
                self.assertIn(expected, text)
                if name == "차카타":
                    self.assertNotIn("학생점수", text)
                else:
                    self.assertNotIn(squash(MISSING_TEST_FEEDBACK), text)

    def test_health_and_loopback_request_guard(self):
        self.assertEqual(self.request("/api/health"), {"ok": True, "korean_font_ready": True})
        with patch.object(server, "register_fonts", side_effect=server.ValidationError("No Korean font found.")):
            self.assertEqual(self.request("/api/health", expected=503)["korean_font_ready"], False)
        self.request("/api/state", headers={"Host": "brightline.ngrok-free.app"}, expected=403)
