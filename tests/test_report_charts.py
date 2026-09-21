import io
import unittest

from pypdf import PdfReader
from reportlab.pdfgen import canvas

from backend.report_charts import draw_homework_chart, draw_test_chart, homework_points, test_points


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


if __name__ == "__main__":
    unittest.main()
