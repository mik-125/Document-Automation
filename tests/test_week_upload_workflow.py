from __future__ import annotations

import atexit
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_SERVER_DATA = tempfile.TemporaryDirectory()
atexit.register(_SERVER_DATA.cleanup)
os.environ.setdefault("BRIGHTLINE_DATA_DIR", _SERVER_DATA.name)

from backend import server as app_server
from backend.database import Database


class WeekUploadWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.original_database = app_server.DB
        app_server.DB = Database(Path(self.folder.name) / "brightline.db")

    def tearDown(self) -> None:
        app_server.DB = self.original_database
        self.folder.cleanup()

    @staticmethod
    def period() -> dict[str, str]:
        return {
            "id": "",
            "label": "Week 1",
            "start_date": "2026-08-17",
            "end_date": "2026-08-23",
        }

    def import_assignment(self) -> str:
        app_server.import_mapped_table(
            values=[
                ["Student ID", "Student", "Class", "Completed", "Total"],
                ["S-1", "Mark", "Economics", "8", "10"],
                ["S-2", "June", "Economics", "9", "10"],
            ],
            mapping={
                "student_id": "Student ID",
                "student_name": "Student",
                "class_name": "Class",
                "assignment_completed": "Completed",
                "assignment_total": "Total",
            },
            kind="assignment",
            period=self.period(),
            source_name="Assignment.csv",
            source_provider="manual",
        )
        return app_server.DB.state()["periods"][0]["id"]

    def combined_import(self, scores=("0", "85"), *, confirm=False) -> str:
        raw = [
            ["과목명", "학생", "학년", "학교", "WEEK 1", "", "weekly test\n점수 (%)"],
            ["", "", "", "", "7/13", "7/14", ""],
            ["(2차) 09:00 [26SU] DP1 ESS H+S", "Student One", "10", "J1MS", "A", "F", scores[0]],
            ["(2차) 09:00 [26SU] DP1 ESS H+S", "Student Two", "10", "J1MS", "B", "", scores[1]],
        ]
        values, _, preview = app_server._automatic_sheet_values(raw, "assignment")
        self.assertEqual(preview["row_count"], 2)
        self.assertEqual(preview["suggested_mapping"]["weekly_test_score"], "weekly test\n점수 (%)")
        return app_server.import_mapped_table(
            values=values, mapping=preview["suggested_mapping"],
            kind="assignment", period={**self.period(), "start_date": "2026-07-13", "end_date": "2026-07-17"}, source_name="Combined Sheet",
            source_provider="google_sheets", confirm_replace=confirm,
        )

    def test_combined_sheet_saves_homework_and_test_scores_including_zero(self) -> None:
        summary = self.combined_import()
        state = app_server.DB.state()
        self.assertEqual(len(state["students"]), 2)
        self.assertIn("2 test scores saved", summary)
        for student in state["students"]:
            self.assertTrue(student["assignment_ready"])
            self.assertTrue(student["weekly_test_ready"])
            exam = app_server.DB.get_exams(student["id"], state["periods"][0]["id"])[0]
            self.assertEqual(exam["score"], 0 if student["name"] == "Student One" else 85)
            self.assertEqual(exam["max_score"], 100)
            import json
            saved = app_server.DB.get_student_for_period(student["id"], state["periods"][0]["id"])
            daily = json.loads(saved["homework_json"])
            self.assertEqual([entry["date"] for entry in daily], ["2026-07-13", "2026-07-14"])
            self.assertEqual([entry["value"] for entry in daily], ["A", "F"] if student["name"] == "Student One" else ["B", ""])
        self.assertTrue(app_server.class_period_has_assignment_and_test(
            state["classes"][0]["id"], state["periods"][0]["id"]
        ))

    def test_combined_sheet_blank_scores_preserve_existing_tests(self) -> None:
        self.combined_import()
        summary = self.combined_import(("", ""))
        state = app_server.DB.state()
        self.assertIn("0 test scores saved", summary)
        self.assertTrue(all(student["weekly_test_ready"] for student in state["students"]))
        for student in state["students"]:
            self.assertEqual(len(app_server.DB.get_exams(student["id"], state["periods"][0]["id"])), 1)

    def test_combined_sheet_missing_score_keeps_new_student_unready(self) -> None:
        self.combined_import(("", "85"))
        students = {student["name"]: student for student in app_server.DB.state()["students"]}
        self.assertTrue(students["Student One"]["assignment_ready"])
        self.assertFalse(students["Student One"]["weekly_test_ready"])

    def test_combined_sheet_invalid_score_writes_nothing(self) -> None:
        for value in ("101", "-1", "not a score", "NaN"):
            with self.subTest(value=value):
                with self.assertRaises((ValueError, app_server.IntegrationError)):
                    self.combined_import(("50", value))
                self.assertEqual(app_server.DB.state()["students"], [])
                self.assertEqual(app_server.DB.state()["periods"], [])

    def test_combined_sheet_score_replacement_needs_confirmation(self) -> None:
        self.combined_import()
        with self.assertRaises(app_server.IntegrationError) as raised:
            self.combined_import(("20", "90"))
        self.assertEqual(raised.exception.code, "replacement_confirmation_required")
        self.assertEqual(len(app_server.DB.state()["files"]), 1)
        self.combined_import(("20", "90"), confirm=True)
        state = app_server.DB.state()
        for student in state["students"]:
            exam = app_server.DB.get_exams(student["id"], state["periods"][0]["id"])[0]
            self.assertEqual(exam["score"], 20 if student["name"] == "Student One" else 90)

    def test_combined_sheet_rolls_back_homework_if_test_write_fails(self) -> None:
        with patch.object(app_server.DB, "upsert_exam", side_effect=RuntimeError("test write failed")):
            with self.assertRaisesRegex(RuntimeError, "test write failed"):
                self.combined_import()
        state = app_server.DB.state()
        self.assertEqual(state["students"], [])
        self.assertEqual(state["periods"], [])
        self.assertEqual(state["files"], [])

    def test_comments_are_locked_until_every_roster_student_has_a_test(self) -> None:
        period_id = self.import_assignment()
        period = app_server.DB.get_period(period_id)
        app_server.import_mapped_table(
            values=[
                ["Student ID", "Student", "Class", "Score"],
                ["S-1", "Mark", "Economics", "91"],
            ],
            mapping={
                "student_id": "Student ID",
                "student_name": "Student",
                "class_name": "Class",
                "weekly_test_score": "Score",
            },
            kind="weekly_test",
            period=period,
            source_name="Tests.csv",
            source_provider="manual",
        )
        class_id = app_server.DB.state()["classes"][0]["id"]
        self.assertFalse(app_server.class_period_has_assignment_and_test(class_id, period_id))
        status = app_server.period_input_matrix()[0][0]
        self.assertFalse(status["comments_allowed"])

        app_server.import_mapped_table(
            values=[
                ["Student ID", "Student", "Class", "Score"],
                ["S-2", "June", "Economics", "88"],
            ],
            mapping={
                "student_id": "Student ID",
                "student_name": "Student",
                "class_name": "Class",
                "weekly_test_score": "Score",
            },
            kind="weekly_test",
            period=period,
            source_name="Tests-2.csv",
            source_provider="manual",
        )
        self.assertTrue(app_server.class_period_has_assignment_and_test(class_id, period_id))
        self.assertTrue(app_server.period_input_matrix()[0][0]["comments_allowed"])

    def test_all_excluded_assignment_still_defines_week_roster(self) -> None:
        app_server.import_mapped_table(
            values=[
                ["Student", "Class", "8/17", "8/18"],
                ["June", "Economics", "X", "X"],
            ],
            mapping={"student_name": "Student", "class_name": "Class"},
            kind="assignment",
            period=self.period(),
            source_name="Assignment.csv",
            source_provider="manual",
        )
        state = app_server.DB.state()
        self.assertTrue(state["students"][0]["assignment_ready"])
        status = app_server.period_input_matrix()[0][0]
        self.assertEqual(status["student_count"], 1)
        self.assertTrue(status["assignment_ready"])

    def test_comments_csv_must_represent_one_class(self) -> None:
        with self.assertRaisesRegex(ValueError, "exactly one class"):
            app_server.import_mapped_table(
                values=[
                    ["Student", "Class", "Comment"],
                    ["Mark", "Economics", "Good progress"],
                    ["June", "Mathematics", "Strong work"],
                ],
                mapping={
                    "student_name": "Student",
                    "class_name": "Class",
                    "comment": "Comment",
                },
                kind="comments",
                period=self.period(),
                source_name="Comments.csv",
                source_provider="manual",
            )
        self.assertEqual(app_server.DB.state()["files"], [])


class UploadModalFrontendContractTests(unittest.TestCase):
    """Protect the week picker workflow without coupling tests to browser tooling.

    The project deliberately has no JavaScript test dependency.  These checks keep the
    safety-critical ordering visible in the shipped source; browser QA still covers the
    actual clicks and rendered option labels.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.javascript = (Path(__file__).resolve().parent.parent / "frontend" / "app.js").read_text(
            encoding="utf-8"
        )

    @classmethod
    def section(cls, start: str, end: str) -> str:
        start_index = cls.javascript.index(f"function {start}(")
        end_index = cls.javascript.index(f"function {end}(", start_index)
        return cls.javascript[start_index:end_index]

    def test_staged_source_survives_selecting_another_week(self) -> None:
        open_modal = self.section("openUploadModal", "addUploadWeek")
        stage_manual = self.section("stageManualUpload", "beginGoogleUpload")
        change_handler = self.javascript[
            self.javascript.index('document.addEventListener("change"') :
            self.javascript.index("$$('[data-upload-file-kind]')")
        ]

        # A single session-level stage naturally follows the selected week.  A keyed
        # implementation is also valid when the change handler explicitly carries the
        # current stage to the newly selected key.
        singular_stage = bool(re.search(r"\bstage\s*:\s*null", open_modal)) and (
            "stagesByPeriod" not in open_modal
        )
        carried_keyed_stage = bool(
            re.search(
                r"(?:const|let)\s+(\w+)\s*=\s*currentUploadStage\(\)[\s\S]*"
                r"periodKey\s*=\s*event\.target\.value[\s\S]*"
                r"setUploadStage\(\1",
                change_handler,
            )
        )
        self.assertTrue(
            singular_stage or carried_keyed_stage,
            "Changing Week must keep the one staged source instead of hiding it under the old week.",
        )
        self.assertRegex(
            stage_manual,
            r"(?:session\.stage\s*=|setUploadStage\(|stagesByPeriod\[periodKey\]\s*=)",
            "Manual upload must stage the source before Save.",
        )

    def test_save_is_blocked_and_guarded_without_a_selected_week(self) -> None:
        render = self.section("renderUploadModal", "openUploadModal")
        self.assertRegex(
            render,
            r"saveDisabled\s*=\s*[^;]*!target",
            "The rendered Save button must be disabled until a week is selected.",
        )

        save = self.section("saveUpload", "fieldsForKind")
        first_request = save.index("await api(")
        guard = re.search(
            r"if\s*\(\s*!target\s*\)\s*"
            r"(?P<body>\{[^{}]*\}|return\s+[^;]+;)",
            save,
        )
        self.assertIsNotNone(guard, "saveUpload needs its own missing-week guard.")
        self.assertIn("return", guard.group("body"))
        self.assertLess(
            guard.start(),
            first_request,
            "The missing-week guard must run before any import request.",
        )

    def test_save_confirms_before_the_first_import_request(self) -> None:
        save = self.section("saveUpload", "fieldsForKind")
        first_confirmation = save.find("confirm(")
        first_request = save.find("await api(")
        self.assertGreaterEqual(first_request, 0)
        custom_confirmation = re.search(
            r"if\s*\(\s*!confirmed\s*\)\s*\{[\s\S]*?"
            r"confirming\s*=\s*true[\s\S]*?renderUploadModal\(\)[\s\S]*?return",
            save,
        )
        native_confirmation = 0 <= first_confirmation < first_request
        self.assertTrue(
            native_confirmation or bool(custom_confirmation),
            "The user must confirm the selected source and week before data is imported.",
        )
        if custom_confirmation:
            render = self.section("renderUploadModal", "openUploadModal")
            self.assertIn("data-upload-confirm", render)
            self.assertIn("Source", render)
            self.assertIn("Week", render)

    def test_added_week_options_are_owned_outside_the_modal_session(self) -> None:
        open_modal = self.section("openUploadModal", "addUploadWeek")
        add_week = self.section("addUploadWeek", "stageManualUpload")
        close_modal = self.section("closeModal", "emptyState")
        module_variables = set(
            re.findall(
                r"^(?:let|const)\s+([A-Za-z_$][\w$]*)",
                self.javascript,
                re.MULTILINE,
            )
        )
        shared = {
            name
            for name in module_variables
            if re.search(r"(?:week|draft)", name, re.IGNORECASE)
            and re.search(rf"\b{re.escape(name)}\b", open_modal)
            and re.search(rf"\b{re.escape(name)}\b", add_week)
        }
        self.assertTrue(
            shared,
            "Added week choices must live outside uploadSession so reopening a modal keeps them.",
        )
        for name in shared:
            self.assertNotRegex(
                close_modal,
                rf"\b{re.escape(name)}\s*=",
                "Closing an upload modal must not erase added week choices.",
            )

    def test_clearing_local_data_also_clears_temporary_week_choices(self) -> None:
        helper = self.section("clearUploadDraftPeriods", "normalizedPeriodLabel")
        reset_handler = self.javascript[
            self.javascript.index('$("#reset-data").onclick') :
            self.javascript.index("\n  };", self.javascript.index('$("#reset-data").onclick'))
        ]
        self.assertIn("clearUploadDraftPeriods()", reset_handler)

        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is required for the reset behavior check.")
        script = f'''
const assert = require("node:assert/strict");
const vm = require("node:vm");
const removed = [];
const context = {{sessionStorage: {{removeItem: key => removed.push(key)}}}};
vm.runInNewContext(`
const UPLOAD_DRAFT_PERIODS_KEY = "brightline.uploadDraftPeriods.v1";
const UPLOAD_LAST_PERIOD_KEY = "brightline.uploadLastPeriod.v1";
let uploadDraftPeriods = [{{label: "Week 2"}}, {{label: "Week 3"}}];
let lastUploadPeriodKey = "draft-week-3";
{helper}
clearUploadDraftPeriods();
result = {{drafts: uploadDraftPeriods, last: lastUploadPeriodKey}};
`, context);
assert.equal(context.result.drafts.length, 0);
assert.equal(context.result.last, "");
assert.deepEqual(removed, ["brightline.uploadDraftPeriods.v1", "brightline.uploadLastPeriod.v1"]);
'''
        subprocess.run(
            [node, "-e", script],
            cwd=(Path(__file__).resolve().parent.parent / "frontend"),
            check=True,
            capture_output=True,
            text=True,
        )

    def test_google_sheet_source_only_asks_for_the_sheet_url(self) -> None:
        form = self.section("sheetSourceForm", "renderUploadModal")
        self.assertIn("upload-sheet-url", form)
        self.assertNotIn("upload-sheet-tab", form)
        self.assertNotIn("upload-sheet-header", form)
        self.assertNotIn("Worksheet name", form)
        self.assertNotIn("Header row", form)

        preview = self.section("previewUploadSheet", "saveUpload")
        self.assertNotIn("upload-sheet-tab", preview)
        self.assertNotIn("upload-sheet-header", preview)
        self.assertRegex(preview, r"spreadsheet,\s*kind:\s*session\.kind")

    def test_google_sheet_preview_updates_only_the_selected_draft_week(self) -> None:
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is required for the upload UI behavior check.")
        script = r'''
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync("app.js", "utf8");
const previewSource = source.slice(source.indexOf("async function previewUploadSheet("), source.indexOf("async function saveUpload("));
const draft = {id: "draft-week-1", label: "Week 1", start_date: "2026-09-07", end_date: "2026-09-13", draft: true};
const session = {kind: "assignment", periodKey: draft.id, draftPeriods: [draft], stage: null, view: "sheet"};
let request;
let persisted = 0;
let rendered = 0;
const context = {
  uploadSession: session,
  uploadTargetPeriod: () => draft,
  $: () => ({value: "sheet-id"}),
  api: async (_url, options) => {
    request = JSON.parse(options.body);
    return {
      spreadsheet_name: "Sample 2 - Brightline",
      worksheet: "Sheet1",
      headers: ["학생", "7/13", "7/17"],
      sample_rows: [],
      assignment_period: {start_date: "2026-07-13", end_date: "2026-07-17"},
    };
  },
  persistUploadDraftPeriods: () => { persisted += 1; },
  renderUploadModal: () => { rendered += 1; },
  toast: error => { throw error; },
};
(async () => {
  await vm.runInNewContext(previewSource + "\npreviewUploadSheet()", context);
  assert.equal(request.period_start, "2026-09-07");
  assert.equal(draft.start_date, "2026-07-13");
  assert.equal(draft.end_date, "2026-07-17");
  assert.equal(persisted, 1);
  assert.equal(rendered, 1);
  assert.equal(session.stage.mode, "google_sheet");
})().catch(error => { console.error(error); process.exit(1); });
'''
        subprocess.run(
            [node, "-e", script],
            cwd=(Path(__file__).resolve().parent.parent / "frontend"),
            check=True,
            capture_output=True,
            text=True,
        )

    def test_upload_modal_does_not_offer_csv(self) -> None:
        render = self.section("renderUploadModal", "openUploadModal")
        self.assertNotIn("Upload CSV", render)
        self.assertIn("Upload comments PDF", render)

    def test_pdf_class_choices_explain_unavailable_classes_without_auto_selecting(self) -> None:
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is required for the upload UI behavior check.")
        script = r'''
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync("app.js", "utf8");
const editor = source.slice(source.indexOf("function uploadStageEditor("), source.indexOf("function stagedSourceRow("));
const stage = {mode: "comments_pdf", classId: "", label: "Week 1"};
const statuses = {
  ess: {comments_allowed: true, assignment_ready: true},
  chem: {comments_allowed: false, assignment_ready: true},
  other: {comments_allowed: false, assignment_ready: false},
};
const context = {
  currentUploadStage: () => stage,
  uploadTargetPeriod: () => ({id: "week1"}),
  state: {classes: [{id: "ess", name: "ESS"}, {id: "chem", name: "Chemistry"}, {id: "other", name: "Other"}]},
  statusForClassPeriod: id => statuses[id],
  escapeHtml: text => text,
};
let html = vm.runInNewContext(editor + "\nuploadStageEditor()", context);
assert.match(html, /value="chem"[^>]*disabled[^>]*>Chemistry — Weekly Test scores missing/);
assert.match(html, /value="other"[^>]*disabled[^>]*>Other — Homework missing for this week/);
assert.doesNotMatch(html, /<option[^>]*selected/);
assert.equal(stage.classId, "");
statuses.chem.comments_allowed = true;
stage.classId = "chem";
html = vm.runInNewContext(editor + "\nuploadStageEditor()", context);
assert.match(html, /value="chem"[^>]*selected[^>]*>Chemistry<\/option>/);
assert.doesNotMatch(html, /value="chem"[^>]*disabled/);
'''
        subprocess.run([node, "-e", script], cwd=(Path(__file__).resolve().parent.parent / "frontend"),
                       check=True, capture_output=True, text=True)

    def test_pdf_confirmation_identifies_the_selected_class(self) -> None:
        render = self.section("renderUploadModal", "openUploadModal")
        self.assertIn('stage.mode === "comments_pdf"', render)
        self.assertIn('item.id === stage.classId', render)
        self.assertIn('<span>Class</span>', render)
        self.assertIn('${classSummary}', render)

    def test_comment_matching_result_names_both_mismatch_directions(self) -> None:
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is required for the matching notification check.")
        script = r'''
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync("app.js", "utf8");
const helper = source.slice(source.indexOf("function showCommentImportResult("), source.indexOf("const FIELD_LABELS"));
const escape = source.slice(source.indexOf("function escapeHtml("), source.indexOf("function formatNumber("));
let html = "";
let notice = "";
const result = {summary: "1 matched", matching: {
  class_name: "Chemistry", period_label: "Week 1",
  missing_comments: [{name: "June", school: "School A", grade: "10"}],
  unmatched_comments: [{name: "<img src=x onerror=alert(1)>", reason: "No matching student in this class and week"}],
}};
const context = {UPLOAD_COPY: {weekly_test_comment: {title: "Weekly test comment"}}, result, openModal: value => {html = value;}, toast: value => {notice = value;}};
const run = () => vm.runInNewContext(escape + helper + "\nshowCommentImportResult(result)", context);
run();
assert.match(html, /Students without Weekly test comment \(1\)/);
assert.match(html, /June/);
assert.match(html, /School A/);
assert.match(html, /can still generate reports/);
assert.match(html, /PDF comments without a unique student match \(1\)/);
assert.match(html, /No matching student in this class and week/);
assert.match(html, /&lt;img/);
assert.doesNotMatch(html, /<img/);
assert.match(html, /data-close-modal/);
assert.equal(notice, "");
result.matching.missing_comments = [];
html = "";
run();
assert.match(html, /without a unique student match/);
assert.doesNotMatch(html, /Students without Weekly test comment/);
result.matching.unmatched_comments = [];
html = "";
run();
assert.equal(html, "");
assert.equal(notice, "1 matched");
'''
        subprocess.run([node, "-e", script], cwd=(Path(__file__).resolve().parent.parent / "frontend"),
                       check=True, capture_output=True, text=True)
        self.assertIn("showCommentImportResult(result, session.kind)", self.section("saveUpload", "showCommentImportResult"))
        self.assertIn("showCommentImportResult(result)", self.section("openCommentsPdfModal", "reviewIssue"))

    def test_single_combined_upload_replaces_weekly_test_upload(self) -> None:
        html = (Path(__file__).resolve().parent.parent / "frontend" / "index.html").read_text(encoding="utf-8")
        self.assertIn("Homework + test scores", html)
        self.assertNotIn('data-upload-kind="weekly_test"', html)
        self.assertIn('data-upload-kind="assignment"', html)
        self.assertNotIn('data-upload-kind="comments"', html)
        for kind in ('homework_comment', 'weekly_test_comment', 'general_comment'):
            self.assertIn(f'data-upload-kind="{kind}"', html)
        self.assertIn("Blank scores preserve existing results", self.javascript)

    def test_staged_google_sheet_shows_preview_without_mapping_or_date_fields(self) -> None:
        editor = self.section("uploadStageEditor", "stagedSourceRow")
        google_preview = re.search(
            r'if\s*\(stage\.mode\s*===\s*"google_sheet"\)\s*'
            r"[\s\S]*?return\s+sampleTable\(stage\.preview,\s*\{"
            r"(?P<options>[\s\S]*?)\}\s*\);",
            editor,
        )
        self.assertIsNotNone(google_preview)
        self.assertIn("progressive: true", google_preview.group("options"))
        self.assertLess(google_preview.start(), editor.index("mapping-grid"))
        self.assertLess(
            google_preview.start(), editor.index("Week name", google_preview.end())
        )

        save = self.section("saveUpload", "fieldsForKind")
        google_branch = re.search(
            r'else if\s*\(stage\.mode\s*===\s*"google_sheet"\)\s*\{'
            r"(?P<body>[\s\S]*?)\}\s*else\s*\{",
            save,
        )
        self.assertIsNotNone(google_branch)
        self.assertNotIn("mapping:", google_branch.group("body"))
        self.assertNotIn("header_row", google_branch.group("body"))

    def test_comment_uploads_keep_section_class_week_and_require_review(self) -> None:
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is required for upload behavior checks.")
        script = r'''
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync("app.js", "utf8");
const save = source.slice(source.indexOf("async function saveUpload("), source.indexOf("function showCommentImportResult("));
const sections = {homework_comment: "homework", weekly_test_comment: "weekly_test", general_comment: "general"};
(async () => {
  for (const [kind, section] of Object.entries(sections)) {
    const requests = [];
    const session = {kind, stage: {mode: "comments_pdf", name: "class.pdf", classId: "class-b", label: "Feedback", file: {arrayBuffer: async () => new ArrayBuffer(0)}}};
    const context = {
      uploadSession: session, COMMENT_SECTIONS: sections, state: {}, lastUploadPeriodKey: "", uploadDraftPeriods: [],
      captureUploadFields() {}, currentUploadStage: () => session.stage,
      uploadTargetPeriod: () => ({id: "week-2", label: "Week 2"}), uploadPrerequisite: () => ({ready: true}),
      statusForClassPeriod: (classId, periodId) => ({comments_allowed: classId === "class-b" && periodId === "week-2"}),
      renderUploadModal() {}, api: async (url, options) => {requests.push({url, payload: JSON.parse(options.body)}); return {state: {}};},
      arrayBufferToBase64: () => "pdf-data", normalizeState: value => value,
      reconcileUploadDraftPeriods() {}, closeModal() {}, render() {}, showCommentImportResult() {}, toast: message => {throw Error(message);},
    };
    vm.createContext(context); vm.runInContext(save, context);
    await context.saveUpload();
    assert.equal(requests.length, 0); assert.equal(session.confirming, true);
    await context.saveUpload(true);
    assert.deepEqual(requests, [{url: "/api/comments/pdf/import", payload: {filename: "class.pdf", class_id: "class-b", period_id: "week-2", comment_section: section, pdf_base64: "pdf-data"}}]);
  }
  const requests = [], errors = [];
  const session = {kind: "homework_comment", stage: {mode: "comments_shared", classId: "class-b", comment: "   "}};
  const context = {
    uploadSession: session, COMMENT_SECTIONS: sections, state: {}, lastUploadPeriodKey: "", uploadDraftPeriods: [],
    captureUploadFields() {}, currentUploadStage: () => session.stage,
    uploadTargetPeriod: () => ({id: "week-2", label: "Week 2"}), uploadPrerequisite: () => ({ready: true}),
    statusForClassPeriod: () => ({comments_allowed: true}), renderUploadModal() {},
    api: async (url, options) => {requests.push({url, payload: JSON.parse(options.body)}); return {state: {}};},
    normalizeState: value => value, reconcileUploadDraftPeriods() {}, closeModal() {}, render() {}, toast: message => errors.push(message),
  };
  vm.createContext(context); vm.runInContext(save, context);
  await context.saveUpload(); assert.equal(requests.length, 0); assert.match(errors.pop(), /Enter a comment/);
  session.stage.comment = "  Keep practising.  ";
  await context.saveUpload(); assert.equal(requests.length, 0); assert.equal(session.confirming, true);
  await context.saveUpload(true);
  assert.deepEqual(requests, [{url: "/api/comments/shared", payload: {class_id: "class-b", period_id: "week-2", comment: "Keep practising."}}]);
})().catch(error => {console.error(error); process.exit(1);});
'''
        subprocess.run([node, "-e", script], cwd=(Path(__file__).resolve().parent.parent / "frontend"),
                       check=True, capture_output=True, text=True)

    def test_comment_modal_offers_only_allowed_sources(self) -> None:
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is required for modal behavior checks.")
        script = r'''
const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm");
const source = fs.readFileSync("app.js", "utf8");
const constants = source.slice(source.indexOf("const COMMENT_SECTIONS"), source.indexOf("function persistUploadDraftPeriods("));
const modal = source.slice(source.indexOf("function renderUploadModal("), source.indexOf("function openUploadModal("));
let html;
const session = {kind: "homework_comment", draftPeriods: []};
const context = {uploadSession: session, state: {periods: []}, uploadTargetPeriod: () => null,
 uploadPrerequisite: () => ({ready: true}), currentUploadStage: () => null,
 sheetSourceForm: () => "", stagedSourceRow: () => "", uploadStageEditor: () => "", escapeHtml: value => value,
 openModal: value => {html = value;}, $: () => ({classList: {add() {}}}), bindSamplePreviewScroll() {}};
vm.createContext(context); vm.runInContext(constants + modal, context);
for (const kind of ["homework_comment", "weekly_test_comment", "general_comment", "assignment"]) {
 session.kind = kind; context.renderUploadModal();
 assert.equal(html.includes("data-upload-google"), kind === "assignment");
 assert.equal(html.includes("data-upload-shared"), kind === "homework_comment");
 assert.equal(html.includes("data-upload-manual"), kind !== "assignment");
}
'''
        subprocess.run([node, "-e", script], cwd=(Path(__file__).resolve().parent.parent / "frontend"),
                       check=True, capture_output=True, text=True)

    def test_report_reason_requires_homework_and_either_other_comment(self) -> None:
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is required for readiness behavior checks.")
        script = r'''
const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm");
const source = fs.readFileSync("app.js", "utf8");
const reason = source.slice(source.indexOf("function reportReason("), source.indexOf("function renderReports("));
const context = {}; vm.createContext(context); vm.runInContext(reason, context);
for (const homework of [false, true]) for (const weekly of [false, true]) for (const general of [false, true]) {
 const student = {assignment_ready: true, weekly_test_ready: true, template_ready: true, output_ready: true,
 homework_comment_ready: homework, weekly_test_comment_ready: weekly, general_comment_ready: general};
 assert.equal(context.reportReason(student) === "Ready", homework && (weekly || general));
}
'''
        subprocess.run([node, "-e", script], cwd=(Path(__file__).resolve().parent.parent / "frontend"),
                       check=True, capture_output=True, text=True)

    def test_new_week_dates_are_created_automatically(self) -> None:
        dates = self.section("nextWeekDates", "createDraftWeek")
        self.assertIn("Date.UTC", dates)
        self.assertNotIn('start_date: "", end_date: ""', dates)


if __name__ == "__main__":
    unittest.main()
