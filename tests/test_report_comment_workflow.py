"""Required section combinations and section-scoped imports use a real database."""
import base64
import itertools
import unittest
from unittest.mock import patch
from tests import test_comment_pdf_import as fixtures
from backend.comment_sections import report_comment_sections
from backend.integrations import IntegrationError
from backend.report_engine import ValidationError

app = fixtures.app_server


class ReportCommentWorkflowTests(unittest.TestCase):
    setUp = fixtures.CommentPdfImportTests.setUp
    tearDown = fixtures.CommentPdfImportTests.tearDown

    def record(self, student, section, text):
        app.DB.upsert_exam(student['id'], self.class_id, {
            'label': app._comment_label(section), 'comment_section': section, 'comment': text,
        }, self.period_id)

    def invoke(self, method, **payload):
        class Handler:
            def _send_json(self, response):
                self.response = response
        handler = Handler()
        with patch.object(app, 'app_state', return_value={}):
            getattr(app.AppHandler, method)(handler, {
                'class_id': self.class_id, 'period_id': self.period_id, **payload,
            })
        return handler.response

    def pdf(self, section, lines, **options):
        return self.invoke('_import_comments_pdf', comment_section=section,
            filename='all-comments.pdf', pdf_base64=base64.b64encode(fixtures.comments_pdf([lines])).decode(), **options)

    def test_no_comment_combination_blocks_a_student_with_sheet_data(self):
        student = app.DB.state()['students'][0]
        for homework, weekly, general in itertools.product((False, True), repeat=3):
            with self.subTest(homework=homework, weekly=weekly, general=general):
                with app.DB.connect() as db:
                    db.execute("UPDATE period_exams SET comment = ''")
                for section, enabled in zip(('homework', 'weekly_test', 'general'), (homework, weekly, general)):
                    if enabled:
                        self.record(student, section, section + ' full feedback')
                actual = next(s for s in app.DB.state()['students'] if s['id'] == student['id'])
                self.assertTrue(actual['ready'])
                payload = app.get_ready_student(student['id'], self.period_id)
                for section, enabled in zip(('homework', 'weekly_test', 'general'), (homework, weekly, general)):
                    self.assertEqual(bool(payload['comments_by_section'][section]), enabled)

    def test_shared_homework_and_one_matched_optional_pdf_allow_partial_class(self):
        self.invoke('_import_shared_comment', comment='Everyone: complete all homework.')
        self.pdf('general', ['Mark', '', 'General advice mentioning a test remains general.'])
        students = app.DB.state()['students']
        self.assertEqual(sum(s['ready'] for s in students), 5)
        self.assertEqual(sum(s['homework_comment_ready'] for s in students), 5)
        matrix, _ = app.period_input_matrix()
        self.assertEqual(matrix[0]['homework_comment_count'], 5)
        self.assertEqual(matrix[0]['general_comment_count'], 1)
        self.assertEqual(matrix[0]['comment_count'], 1)
        self.assertFalse(matrix[0]['comments_ready'])

    def test_homework_pdf_applies_one_comment_to_every_student(self):
        result = self.pdf('homework', ['Review ionic bonding and Lewis structures.'])
        self.assertEqual(result['summary'], 'Homework comment applied to 5 students.')
        students = app.DB.state()['students']
        self.assertEqual(sum(student['homework_comment_ready'] for student in students), 5)
        for student in students:
            self.assertEqual(
                report_comment_sections(app.DB.get_exams(student['id'], self.period_id))['homework'],
                'Review ionic bonding and Lewis structures.',
            )
        upload = next(file for file in app.DB.state()['files'] if file['kind'] == 'comments_shared_pdf')
        self.assertEqual(upload['name'], 'all-comments.pdf')

    def test_three_pdf_sections_stay_separate_and_replacement_preserves_scores(self):
        self.pdf('homework', ['homework feedback.'])
        for section in ('weekly_test', 'general'):
            self.pdf(section, ['Mark', '', section + ' feedback.'])
        mark = app.DB.find_students_by_name(self.class_id, 'Mark')[0]
        sections = report_comment_sections(app.DB.get_exams(mark['id'], self.period_id))
        self.assertEqual(sections, {s: s + ' feedback.' for s in sections})
        with self.assertRaises(IntegrationError):
            self.pdf('weekly_test', ['Mark', '', 'New weekly feedback.'])
        self.pdf('weekly_test', ['Mark', '', 'New weekly feedback.'], confirm_replace=True)
        exams = app.DB.get_exams(mark['id'], self.period_id)
        self.assertIn(80, [e['score'] for e in exams])
        self.assertEqual(report_comment_sections(exams)['weekly_test'], 'New weekly feedback.')
        self.assertEqual(report_comment_sections(exams)['homework'], 'homework feedback.')

    def test_shared_scope_and_deleting_import_removes_only_that_comment(self):
        self.invoke('_import_shared_comment', comment='Class homework.')
        self.pdf('weekly_test', ['Mark', '', 'Weekly feedback.'])
        self.assertEqual(sum(s['homework_comment_ready'] for s in app.DB.state()['students']), 5)
        upload = next(f for f in app.DB.state()['files'] if f['kind'] == 'comments_shared')
        app.DB.delete_imports([upload['id']])
        self.assertFalse(any(s['homework_comment_ready'] for s in app.DB.state()['students']))
        self.assertEqual(sum(s['weekly_test_comment_ready'] for s in app.DB.state()['students']), 1)
        # Sheet data keeps students ready; missing comments never block generation.
        self.assertTrue(all(s['ready'] for s in app.DB.state()['students']))

    def test_shared_comment_does_not_touch_another_class_or_week(self):
        app.import_mapped_table(
            values=[["ID", "Student", "Class", "Completed", "Total", "Score"],
                    ["S1", "(ON) Mark", "Class A", "4", "5", "80"],
                    ["B1", "Other", "Class B", "4", "5", "70"]],
            mapping={"student_id": "ID", "student_name": "Student", "class_name": "Class",
                     "assignment_completed": "Completed", "assignment_total": "Total", "weekly_test_score": "Score"},
            kind="assignment", period={"id": "", "label": "Week 2", "start_date": "2026-07-20", "end_date": "2026-07-24"},
            source_name="Second week", source_provider="manual",
        )
        self.invoke('_import_shared_comment', comment='Week one class A only.')
        with app.DB.connect() as db:
            rows = db.execute("SELECT class_id, period_id, comment FROM period_exams WHERE comment_section = 'homework'").fetchall()
        self.assertEqual(len(rows), 5)
        self.assertTrue(all(row['class_id'] == self.class_id and row['period_id'] == self.period_id for row in rows))

    def test_review_resolution_keeps_section_and_import_ownership(self):
        self.pdf('general', ['Unknown', '', 'General feedback.'])
        issue = app.DB.state()['issues'][0]
        mark = app.DB.find_students_by_name(self.class_id, 'Mark')[0]
        class Handler:
            def _send_json(self, response):
                self.response = response
        with patch.object(app, 'app_state', return_value={}):
            app.AppHandler._resolve_issue(Handler(), issue['id'], {'student_id': mark['id']})
        exams = app.DB.get_exams(mark['id'], self.period_id)
        self.assertEqual(report_comment_sections(exams)['general'], 'General feedback.')
        self.assertFalse(report_comment_sections(exams)['weekly_test'])
        upload = next(f for f in app.DB.state()['files'] if f['kind'] == 'comments_pdf')
        app.DB.delete_imports([upload['id']])
        self.assertFalse(report_comment_sections(app.DB.get_exams(mark['id'], self.period_id))['general'])

    def test_score_reimport_keeps_explicit_comment_section(self):
        student = app.DB.state()['students'][0]
        self.record(student, 'homework', 'Kept homework feedback.')
        app.DB.upsert_exam(student['id'], self.class_id, {'label': app._comment_label('homework'), 'score': 95}, self.period_id)
        self.assertEqual(report_comment_sections(app.DB.get_exams(student['id'], self.period_id))['homework'], 'Kept homework feedback.')

    def test_empty_and_invalid_sections_rejected_without_writes(self):
        for section in ('invalid', 'general'):
            with self.assertRaises(ValidationError):
                self.invoke('_import_shared_comment', comment='text', comment_section=section)
        with self.assertRaises(ValidationError):
            self.invoke('_import_shared_comment', comment=' \n\t')
        with self.assertRaises(ValidationError):
            self.pdf('invalid', ['Mark', '', 'feedback'])
        self.assertFalse(any(s['comments_ready'] for s in app.DB.state()['students']))

    def test_legacy_weekly_is_preserved_until_explicit_replacement(self):
        mark = app.DB.find_students_by_name(self.class_id, 'Mark')[0]
        app.DB.upsert_exam(mark['id'], self.class_id, {'label': 'Old comment label', 'comment': 'Legacy text.'}, self.period_id)
        self.assertEqual(report_comment_sections(app.DB.get_exams(mark['id'], self.period_id))['weekly_test'], 'Legacy text.')
        with self.assertRaises(IntegrationError):
            self.pdf('weekly_test', ['Mark', '', 'Replacement text.'])
        self.pdf('weekly_test', ['Mark', '', 'Replacement text.'], confirm_replace=True)
        self.assertEqual(report_comment_sections(app.DB.get_exams(mark['id'], self.period_id))['weekly_test'], 'Replacement text.')


if __name__ == '__main__':
    unittest.main()
