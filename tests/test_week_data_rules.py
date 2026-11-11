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
        self.assertEqual(shared["summary"], "과제이행도 comment applied to 2 students.")

    def test_added_week_with_reused_dates_is_its_own_week(self):
        # Week 1 already uses 2026-07-13..17; week identity is the number, not the dates.
        reused = {"period_label": "Week 5", "period_start": "2026-07-13", "period_end": "2026-07-17"}
        self.pdf(["엄현우", "", "다섯째 주 코멘트입니다."], **reused)
        periods = {p["week_number"]: p for p in app.DB.state()["periods"]}
        self.assertEqual(sorted(periods), [1, 5])
        hyunwoo = self.student("엄현우")
        self.assertEqual([e["comment"] for e in app.DB.get_exams(hyunwoo["id"], periods[5]["id"])],
                         ["다섯째 주 코멘트입니다."])
        self.assertEqual([e["comment"] for e in app.DB.get_exams(hyunwoo["id"], periods[1]["id"])], [""])

    def test_added_week_with_existing_number_lands_in_that_week(self):
        same_number = {"period_label": "Week 1", "period_start": "2026-08-03", "period_end": "2026-08-07"}
        self.pdf(["엄현우", "", "첫 코멘트입니다."], **same_number)
        self.assertEqual([p["week_number"] for p in app.DB.state()["periods"]], [1])
        with self.assertRaises(IntegrationError) as raised:
            self.pdf(["엄현우", "", "바뀐 코멘트입니다."], **same_number)
        self.assertEqual(raised.exception.code, "replacement_confirmation_required")

    def test_week_number_in_payload_must_be_positive_whole_number(self):
        for bad in (0, -1, "two", 1.5):
            with self.assertRaisesRegex(ValidationError, "Week number must be a whole number of 1 or more"):
                app.period_from_payload({"week_number": bad, "period_start": "2026-08-03", "period_end": "2026-08-07"})
        self.assertEqual(app.period_from_payload(
            {"week_number": 3, "period_start": "2026-08-03", "period_end": "2026-08-07"})["week_number"], 3)
        self.assertEqual(app.period_from_payload(
            {"period_label": "Week 6", "period_start": "2026-08-03", "period_end": "2026-08-07"})["week_number"], 6)

    def same_dates(self, number):
        return {"period_label": f"Week {number}", "period_start": "2026-07-13", "period_end": "2026-07-17"}

    def cumulative_weeks(self):
        """Week 1 (fixture: 엄현우 75), then Weeks 2 and 4 on Week 1's dates."""
        scores = {2: {"엄현우": 90, "June": 70}, 4: {"엄현우": 75, "엄하진": 50}}
        for number, by_name in scores.items():
            period = app.DB.ensure_period("2026-07-13", "2026-07-17", f"Week {number}")
            for name, score in by_name.items():
                app.DB.upsert_exam(self.student(name)["id"], self.class_id, {
                    "label": f"Week {number}", "score": score, "max_score": 100,
                    "source_name": "synthetic"}, period["id"])
        return self.student("엄현우")["id"], self.period_named("Week 2"), self.period_named("Week 4")

    def test_history_includes_only_weeks_up_to_chosen_number(self):
        student_id, week_2, week_4 = self.cumulative_weeks()
        _, series = app.weekly_test_history(self.class_id, student_id, week_2)
        self.assertEqual([item["week_number"] for item in series], [1, 2])
        _, series = app.weekly_test_history(self.class_id, student_id, week_4)
        self.assertEqual([item["week_number"] for item in series], [1, 2, 4])
        self.assertEqual([item["student"] for item in series], [75.0, 90.0, 75.0])
        _, series = app.weekly_test_history(self.class_id, student_id, self.period_named("Week 1"))
        self.assertEqual([item["week_number"] for item in series], [1])

    def test_average_matches_hand_computed_value(self):
        student_id, week_2, week_4 = self.cumulative_weeks()
        payload = app.get_ready_student(student_id, week_4)
        self.assertEqual(payload["test_average"], 80.0)
        self.assertEqual(payload["test_weeks"], ["Week 1", "Week 2", "Week 4"])
        self.assertEqual(app.get_ready_student(student_id, week_2)["test_average"], 82.5)

    def test_week_without_student_score_is_left_out(self):
        _, _, week_4 = self.cumulative_weeks()
        payload = app.get_ready_student(self.student("엄하진")["id"], week_4)
        self.assertEqual([item["student"] for item in payload["test_series"]], [None, None, 50.0])
        self.assertEqual(payload["test_average"], 50.0)
        self.assertEqual(payload["test_weeks"], ["Week 4"])

    def test_no_scores_gives_no_average(self):
        self.assertIsNone(app.cumulative_test_average([]))
        self.assertIsNone(app.cumulative_test_average([{"student": None}]))
        self.pdf(["엄하진", "", "점수 없는 코멘트입니다."], section="general", **self.same_dates(6))
        payload = app.get_ready_student(self.student("엄하진")["id"], self.period_named("Week 6"))
        self.assertIsNone(payload["test_average"])
        self.assertEqual(payload["test_weeks"], [])

    def test_unnumbered_week_keeps_date_rule(self):
        student = self.student("엄현우")
        for label, start, end, score in [("Spring A", "2026-06-01", "2026-06-05", 40),
                                         ("Spring B", "2026-06-08", "2026-06-12", 60)]:
            period = app.DB.ensure_period(start, end, label)
            app.DB.upsert_exam(student["id"], self.class_id, {
                "label": label, "score": score, "max_score": 100, "source_name": "synthetic"}, period["id"])
        _, series = app.weekly_test_history(self.class_id, student["id"], self.period_named("Spring B"))
        self.assertEqual([item["period_label"] for item in series], ["Spring A", "Spring B"])
        self.assertEqual([item["week_number"] for item in series], [None, None])

    def test_missed_test_notice_needs_neither_score_nor_comment(self):
        guide = {"sections": [{"section": "weekly_test", "reliable": True}]}
        rule = lambda **changes: app.missing_test_assumption(**{
            "pdf_template_ready": True, "guide": guide, "weekly_test_text": "", "exams": [], **changes})
        self.assertTrue(rule())
        self.assertTrue(rule(exams=[{"score": None, "comment": "과제 코멘트"}]))
        self.assertFalse(rule(exams=[{"score": 73, "max_score": 100}]))
        self.assertFalse(rule(exams=[{"score": 0, "max_score": 100}]))
        self.assertFalse(rule(weekly_test_text="피드백"))
        self.assertFalse(rule(pdf_template_ready=False))
        self.assertFalse(rule(guide={"sections": [{"section": "weekly_test", "reliable": False}]}))
        self.assertFalse(rule(guide={}))

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
