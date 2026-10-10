import io
import unittest

from pypdf import PdfReader
from reportlab.pdfgen import canvas

from backend.report_charts import (draw_homework_chart, draw_homework_status_chart, draw_test_chart,
                                   draw_weekly_test_chart, homework_points, test_points)
from backend.report_engine import register_fonts


class ReportChartTests(unittest.TestCase):
    def test_homework_keeps_dates_and_exact_categories(self):
        points = homework_points([
            {"date": "2026-07-13", "value": "A", "score": 100},
            {"date": "2026-07-14", "value": "B", "score": 75},
            {"date": "2026-07-15", "value": "과제 X"},
            {"date": "2026-07-16", "value": "F", "score": 25},
            {"date": "2026-07-17", "value": ""},
            {"date": "2026-07-18", "value": "Absent"},
        ])
        self.assertEqual([p["value"] for p in points], [4, 3, None, 0, None, None])
        self.assertEqual([p["label"] for p in points], [f"7/{day}" for day in range(13, 19)])

    def test_test_scores_preserve_zero_and_reject_invalid_scores(self):
        points = test_points([
            {"label": "Week 1", "score": 0, "max_score": 20},
            {"label": "Week 2", "score": 14, "max_score": 20},
            {"label": "Week 3", "score": None, "max_score": 20},
            {"label": "Week 4", "percent": float("nan")},
            {"label": "Week 5", "score": 101, "max_score": 100},
            {"label": "Week 6", "score": 5, "max_score": 0},
        ])
        self.assertEqual([p["value"] for p in points], [0, 70, None, None, None, None])
        self.assertEqual(points[0]["display"], "0%")

    def test_drawn_pdf_has_daily_labels_and_only_actual_week(self):
        output = io.BytesIO()
        document = canvas.Canvas(output)
        draw_homework_chart(document, [{"date": "2026-07-13", "value": "A"},
                                      {"date": "2026-07-14", "value": "B"}],
                            x=30, y=400, width=260, height=150)
        draw_test_chart(document, [{"label": "Week 1", "score": 50, "max_score": 100}],
                        x=30, y=200, width=260, height=150)
        document.save()
        text = PdfReader(output).pages[0].extract_text()
        self.assertIn("7/13", text)
        self.assertIn("7/14", text)
        self.assertIn("50%", text)
        self.assertIn("Week 1", text)
        self.assertNotIn("Week 2", text)
        self.assertNotIn("75%", text)

    def test_missing_observation_breaks_line_and_single_score_is_centered(self):
        class Recorder:
            def __init__(self):
                self.lines = []
                self.dots = []
            def line(self, *args):
                self.lines.append(args)
            def circle(self, x, y, *args, **kwargs):
                self.dots.append((x, y))
            def __getattr__(self, name):
                return lambda *args, **kwargs: None
        c = Recorder()
        draw_homework_chart(c, [{"value": "A"}, {"value": ""}, {"value": "F"}],
                            x=0, y=0, width=260, height=150)
        self.assertEqual(len(c.lines), 5)  # Grid only; no bridge over the missing day.
        self.assertEqual(len(c.dots), 2)
        single = Recorder()
        draw_test_chart(single, [{"score": 50, "max_score": 100}],
                        x=0, y=0, width=260, height=150)
        self.assertEqual(len(single.lines), 5)
        self.assertEqual(single.dots[0][0], (27 + 246) / 2)


class Recorder:
    def __init__(self):
        self.lines, self.dots, self.strings = [], [], []
    def line(self, *args):
        self.lines.append(args)
    def circle(self, x, y, *args, **kwargs):
        self.dots.append((x, y))
    def drawCentredString(self, x, y, text):
        self.strings.append(text)
    drawString = drawRightString = lambda self, x, y, text: self.strings.append(text)
    def beginPath(self):
        return Recorder()
    def __getattr__(self, name):
        return lambda *args, **kwargs: None


class AcademyChartTests(unittest.TestCase):
    def test_homework_status_chart_labels_letters_and_gaps(self):
        c = Recorder()
        entries = [{"date": "2026-07-13", "value": "A"}, {"date": "2026-07-14", "value": "B"},
                   {"date": "2026-07-15", "value": "X"}, {"date": "2026-07-16", "value": ""},
                   {"date": "2026-07-17", "value": "F"}]
        draw_homework_status_chart(c, 0, 0, 220, 115, entries, "Title", "기간: a ~ b", font_name="Helvetica")
        self.assertEqual(len(c.dots), 3)
        self.assertEqual(len(c.lines), 3 + 1)  # A/B/F gridlines + A-B segment only.
        for label in ("Title", "기간: a ~ b", "2026-07-13", "2026-07-17"):
            self.assertIn(label, c.strings)
        self.assertEqual([s for s in c.strings if s in {"A", "B", "F"}].count("F"), 2)

    def test_weekly_chart_has_four_series_legend_and_skips_invalid_values(self):
        series = [{"end_date": "2026-07-10", "student": None, "average": 70, "highest": float("nan"), "lowest": 40},
                  {"end_date": "2026-07-17", "student": 66.66, "average": None, "highest": 101, "lowest": "bad"}]
        c = Recorder()
        draw_weekly_test_chart(c, 0, 0, 220, 150, series, "WT", "기간", font_name="Helvetica")
        self.assertEqual(len(c.dots), 2)  # One valid student point plus its legend marker.
        self.assertIn("66.7", c.strings)
        output = io.BytesIO()
        document = canvas.Canvas(output)
        draw_weekly_test_chart(document, 30, 400, 230, 150, series, "WT", "기간: 2026-07-06 ~ 2026-07-17",
                               font_name=register_fonts())
        draw_weekly_test_chart(document, 30, 200, 230, 150, [], "Empty", "", font_name=register_fonts())
        document.save()
        text = PdfReader(output).pages[0].extract_text()
        for legend in ("학생점수", "평균", "최고점", "최저점", "2026-07-10", "100"):
            self.assertIn(legend, text)


if __name__ == "__main__":
    unittest.main()
