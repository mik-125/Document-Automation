"""Week rosters, PDF 총점 fallback scores, relaxed readiness, and test series (synthetic data)."""
import base64
import unittest
from unittest.mock import patch

from tests import test_comment_pdf_import as fixtures
from backend.comment_sections import report_comment_sections
from backend.integrations import IntegrationError
from backend.report_engine import ValidationError
from backend.sample_content import content_features, organize_comments

app = fixtures.app_server
WEEK_2 = {"period_label": "Week 2", "period_start": "2026-07-20", "period_end": "2026-07-24"}
WEEK_3 = {"period_label": "Week 3", "period_start": "2026-07-27", "period_end": "2026-07-31"}


class Handler:
    def _send_json(self, response):
        self.response = response


class WeekDataRuleTests(unittest.TestCase):
    def setUp(self):
        fixtures.CommentPdfImportTests.setUp(self)
        # A class student with no Week 1 data; Korean narrative names are required by the parser.
        app.DB.create_student(class_id=self.class_id, name="엄하진")

    tearDown = fixtures.CommentPdfImportTests.tearDown

    def invoke(self, method, payload):
        handler = Handler()
        with patch.object(app, "app_state", return_value={}):
            getattr(app.AppHandler, method)(handler, {"class_id": self.class_id, **payload})
        return handler.response

    def pdf(self, lines, section="weekly_test", **period):
        return self.invoke("_import_comments_pdf", {
            "comment_section": section, "filename": "exam.pdf",
            "pdf_base64": base64.b64encode(fixtures.comments_pdf([lines])).decode(), **period,
        })

    def student(self, name):
        return app.DB.find_students_by_name(self.class_id, name)[0]

    def period_named(self, label):
        return next(p["id"] for p in app.DB.state()["periods"] if p["label"] == label)

    def scored_exam(self, *blocks):
        lines = []
        for total, text in blocks:
            lines += [total, "", text, ""]
        return lines

    def test_new_week_without_sheet_data_accepts_exam_pdf_and_uses_its_total_score(self):
        result = self.pdf(self.scored_exam(("총점: 18 / 21", "엄현우 학생은 계산을 잘했습니다."),
                                           ("총점: 12 / 20", "엄하진 학생은 복습이 필요합니다."),
                                           ("총점: 9 / 21", "김지우 학생은 처음 보는 이름입니다.")), **WEEK_3)
        self.assertEqual(result["summary"], "Weekly Test: 2 comments matched · 1 need review")
        self.assertEqual(result["matching"]["period_label"], "Week 3")
        week_3 = self.period_named("Week 3")
        hyunwoo = self.student("엄현우")
        exams = app.DB.get_exams(hyunwoo["id"], week_3)
        self.assertEqual([(e["score"], e["max_score"]) for e in exams], [(18, 21)])
        _, rosters = app.period_input_matrix()
        self.assertEqual(rosters[(self.class_id, week_3)], {hyunwoo["id"], self.student("엄하진")["id"]})
        issue = next(i for i in app.DB.state()["issues"] if i["subject_name"] == "김지우")
        self.assertEqual(issue["kind"], "unmatched_comment")

        payload = app.get_ready_student(hyunwoo["id"], week_3)
        self.assertEqual(payload["homework"], [])
        self.assertEqual(payload["comments_by_section"]["weekly_test"], "엄현우 학생은 계산을 잘했습니다.")
        self.assertEqual([s["period_label"] for s in payload["test_series"]], ["Week 1", "Week 3"])
        week_1, week_3_point = payload["test_series"]
        self.assertEqual(week_1["student"], 75)
        self.assertEqual((week_1["average"], week_1["highest"], week_1["lowest"]), (70, 80, 60))
        self.assertAlmostEqual(week_3_point["student"], 18 / 21 * 100)
        self.assertAlmostEqual(week_3_point["average"], (18 / 21 * 100 + 60) / 2)
        self.assertEqual(week_3_point["lowest"], 60)
        self.assertEqual((week_3_point["start_date"], week_3_point["end_date"]), ("2026-07-27", "2026-07-31"))

        google_state = {"google_docs": {"status": "disconnected", "account_name": ""},
                        "google_sheets": {"status": "disconnected", "account_name": ""}}
        app.DB.select_class_period(self.class_id, week_3)
        with patch.object(app, "google_connection_state", return_value=google_state), \
                patch.object(app.DB, "get_class_template_version", return_value={"ready": True}):
            state = app.app_state()
        self.assertEqual({s["name"] for s in state["students"]}, {"엄현우", "엄하진"})
        self.assertTrue(all(s["report_ready"] for s in state["students"]))
        self.assertTrue(all(c["student_count"] == 6 for c in state["classes"]))

        shared = self.invoke("_import_shared_comment", {"period_id": week_3, "comment": "Week 3 homework."})
        self.assertEqual(shared["summary"], "Homework comment applied to 2 students.")

    def test_resolving_review_does_not_need_week_data(self):
        self.pdf(self.scored_exam(("총점: 9 / 21", "김지우 학생은 처음 보는 이름입니다."),
                                  ("총점: 8 / 21", "엄현우 학생은 성실합니다.")), **WEEK_3)
        issue = next(i for i in app.DB.state()["issues"] if i["subject_name"] == "김지우")
        mark = self.student("Mark")
        with patch.object(app, "app_state", return_value={}):
            app.AppHandler._resolve_issue(Handler(), issue["id"], {"student_id": mark["id"]})
        week_3 = self.period_named("Week 3")
        exams = app.DB.get_exams(mark["id"], week_3)
        self.assertEqual(report_comment_sections(exams)["weekly_test"], "김지우 학생은 처음 보는 이름입니다.")
        self.assertEqual(exams[0]["score"], 9)
        self.assertIn(mark["id"], app.period_input_matrix()[1][(self.class_id, week_3)])

    def test_sheet_score_wins_over_pdf_total_for_the_same_week(self):
        self.pdf(self.scored_exam(("총점: 18 / 21", "엄현우 학생은 계산을 잘했습니다."),
                                  ("총점: 3 / 20", "엄하진 학생은 복습이 필요합니다.")),
                 period_id=self.period_id)
        hyunwoo = self.student("엄현우")
        stored = app.DB.get_exams(hyunwoo["id"], self.period_id)
        self.assertEqual(sorted(e["score"] for e in stored), [18, 75])
        payload = app.get_ready_student(hyunwoo["id"], self.period_id)
        self.assertEqual([e["score"] for e in payload["exams"] if e["score"] is not None], [75])
        self.assertEqual(payload["test_series"][0]["student"], 75)
        # 엄하진 has no Sheet score this week, so her PDF 총점 (3/20) is the fallback.
        self.assertEqual(payload["test_series"][0]["lowest"], 15)
        hajin = app.get_ready_student(self.student("엄하진")["id"], self.period_id)
        self.assertEqual(hajin["test_series"][0]["student"], 15)
        self.assertEqual([h["score"] for h in payload["test_history"]], [75])

    def test_general_pdf_never_stores_a_score(self):
        self.pdf(self.scored_exam(("총점: 18 / 21", "엄현우 학생은 계산을 잘했습니다."),
                                  ("총점: 3 / 20", "엄하진 학생은 복습이 필요합니다.")),
                 section="general", **WEEK_3)
        week_3 = self.period_named("Week 3")
        exams = app.DB.get_exams(self.student("엄현우")["id"], week_3)
        self.assertEqual([e["score"] for e in exams], [None])
        self.assertEqual(app.get_ready_student(self.student("엄현우")["id"], week_3)["test_series"][-1]["period_label"], "Week 1")

    def test_week_2_and_week_3_imports_stay_independent(self):
        self.pdf(self.scored_exam(("총점: 10 / 20", "엄현우 학생은 2주차입니다."),
                                  ("총점: 11 / 20", "엄하진 학생은 2주차입니다.")), **WEEK_2)
        self.pdf(self.scored_exam(("총점: 15 / 20", "엄현우 학생은 3주차입니다."),
                                  ("총점: 16 / 20", "엄하진 학생은 3주차입니다.")), **WEEK_3)
        hyunwoo = self.student("엄현우")
        week_2, week_3 = self.period_named("Week 2"), self.period_named("Week 3")
        for period, text, score in [(week_2, "엄현우 학생은 2주차입니다.", 10), (week_3, "엄현우 학생은 3주차입니다.", 15)]:
            exams = app.DB.get_exams(hyunwoo["id"], period)
            self.assertEqual(report_comment_sections(exams)["weekly_test"], text)
            self.assertEqual([e["score"] for e in exams], [score])
        series = app.get_ready_student(hyunwoo["id"], week_2)["test_series"]
        self.assertEqual([s["period_label"] for s in series], ["Week 1", "Week 2"])
        with self.assertRaises(IntegrationError):
            self.pdf(["총점: 1 / 20", "", "엄현우 학생은 바뀐 내용입니다.", "", "총점: 2 / 20", "", "엄하진 학생은 바뀐 내용입니다."],
                     period_id=week_3)
        self.pdf(["총점: 1 / 20", "", "엄현우 학생은 바뀐 내용입니다.", "", "총점: 2 / 20", "", "엄하진 학생은 바뀐 내용입니다."],
                 period_id=week_3, confirm_replace=True)
        self.assertEqual([e["score"] for e in app.DB.get_exams(hyunwoo["id"], week_3)], [1])
        self.assertEqual(report_comment_sections(app.DB.get_exams(hyunwoo["id"], week_2))["weekly_test"],
                         "엄현우 학생은 2주차입니다.")

    def test_any_single_piece_of_week_data_is_enough_and_none_is_rejected(self):
        week_3 = app.DB.ensure_period(WEEK_3["period_start"], WEEK_3["period_end"], "Week 3")["id"]
        mark = self.student("Mark")
        with self.assertRaisesRegex(ValidationError, "no data"):
            app.get_ready_student(mark["id"], week_3)
        app.DB.upsert_exam(mark["id"], self.class_id, {
            "label": app._comment_label("general"), "comment_section": "general", "comment": "Only general.",
        }, week_3)
        payload = app.get_ready_student(mark["id"], week_3)
        self.assertEqual(payload["comments_by_section"]["general"], "Only general.")
        # Week 3 has no scores, so the series stops at Week 1 rather than inventing a point.
        self.assertEqual([point["period_label"] for point in payload["test_series"]], ["Week 1"])
        self.assertEqual(payload["test_series"][0]["student"], 80)

    def test_shared_comment_and_pdf_need_students(self):
        with self.assertRaisesRegex(ValidationError, "before a shared 과제이행도 comment"):
            self.pdf(["Homework for everyone."], section="homework", **WEEK_3)
        empty = app.DB.upsert_class("Empty Class")
        with self.assertRaisesRegex(ValidationError, "no students"):
            self.invoke("_import_comments_pdf", {
                "class_id": empty["id"], "comment_section": "weekly_test", "filename": "x.pdf",
                "pdf_base64": base64.b64encode(fixtures.comments_pdf([["엄현우 학생은 잘했습니다."]])).decode(),
                **WEEK_3,
            })
        self.assertEqual([p["label"] for p in app.DB.state()["periods"]], ["Week 1"])

    def test_explicit_sections_are_never_rerouted_or_reviewed(self):
        guide = {"sections": [
            {"section": "weekly_test", "features": content_features("Alex scored 18/20 on the weekly test.")},
            {"section": "general", "features": content_features("During our lessons we studied ecosystems.")},
        ]}
        text = "During our lessons we studied ecosystems.\n\nA uniquely ambiguous observation.\nGeneral comments:\nStill weekly."
        for section in ("homework", "weekly_test", "general"):
            result = organize_comments([{"comment": text, "comment_section": section}], guide)
            self.assertEqual(result["sections"][section], text)
            self.assertEqual(sum(bool(v) for v in result["sections"].values()), 1)
            self.assertEqual([(d["confidence"], d["requires_review"]) for d in result["decisions"]], [(1.0, False)])


if __name__ == "__main__":
    unittest.main()
