from __future__ import annotations

import tempfile
import unittest
import re
from pathlib import Path

import pymupdf as fitz
from reportlab.pdfgen import canvas

from adaptive_reports import layout_comments, _flow_at_size, generate_adaptive_pdf, _wrap_feedback, MISSING_TEST_FEEDBACK
from pdf_content import analyze_content_pdf
from sample_content import organize_comments, content_features, _source_units
from template_mapping import default_mapping
from report_engine import ValidationError, inspect_example_report


def guide():
    return {"version":2,"sections":[
        {"section":"weekly_test","reliable":True,"body_lane":"right","preferred_font_size":6,
         "features":content_features("The student scored 16/20 on the weekly test. The overall test result was strong.")},
        {"section":"general","reliable":True,"preferred_font_size":6,
         "features":content_features("During our lessons we studied ecosystems. Question 1 needs reasoning. Question 2 needs units. Going forward, practice explanations.")},
        {"section":"homework","reliable":True,"body_lane":"right","preferred_font_size":7,
         "features":content_features("All assignments were completed.")},
    ]}


def make_original(path):
    c=canvas.Canvas(str(path),pagesize=(600,800))
    c.setStrokeColorRGB(0,.5,.2)
    c.setFont("Helvetica",12)
    c.drawString(30,770,"ORIGINAL_PAGE_ONE")
    for label,y in [("Homework",700),("Weekly Test",485),("Teacher comments",270)]:
        c.drawString(40,y,label)
        c.rect(30,y-180,540,172)
    c.showPage()
    c.setPageSize((500,700))
    c.setFont("Helvetica",12)
    c.drawString(30,675,"ORIGINAL_CONTINUATION")
    c.drawString(40,640,"Teacher comments")
    c.rect(30,40,440,590)
    c.save()


class AdaptiveReportTests(unittest.TestCase):
    def test_unheaded_overview_is_separated_from_lesson_and_question_detail(self):
        text=("During our lessons we studied ecosystems. "
              "Alex scored 18/20 on the weekly test. The answer structure was strong. "
              "Question 1 needs more units. The student scored 2/3 for this answer. "
              "Question 2 needs an explanation. Going forward, practice daily.")
        result=organize_comments([{"comment":text,"comment_section":"weekly_test"}],guide())
        self.assertIn("18/20",result["sections"]["weekly_test"])
        self.assertIn("answer structure was strong",result["sections"]["weekly_test"])
        self.assertNotIn("Question 1",result["sections"]["weekly_test"])
        self.assertIn("Question 1",result["sections"]["general"])
        self.assertIn("2/3",result["sections"]["general"])
        self.assertIn("During our lessons",result["sections"]["general"])
        self.assertNotIn("16/20",str(result))
        self.assertEqual("".join(_source_units(text)),text)
        for sentence in _source_units(text):
            self.assertEqual(sum(v.count(sentence.strip()) for v in result["sections"].values()),1)

    def test_explicit_headings_homework_and_insufficient_class_evidence_win(self):
        text="General comments:\nThe student scored 18/20. Keep here."
        self.assertEqual(organize_comments([{"comment":text}],guide())["sections"]["general"],text)
        text="On 8/3 we reviewed a test. The student scored 18/20."
        for category,g in [("homework",guide()),("weekly_test",{"sections":[]})]:
            result=organize_comments([{"comment":text,"comment_section":category}],g)
            self.assertEqual(result["sections"][category],text)

    def test_overview_keeps_advice_question_references_and_test_context_together(self):
        intro = "첫 주 수업에서는 개념을 학습하였습니다. 복습하는 것이 중요합니다 "
        overview = ("민수는 Week 1 Test에서 23/42, Grade 5를 받았습니다. "
                    "정의형 문항에서는 안정적이었습니다. 다만 Q4 문항에서는 설명이 부족했습니다. "
                    "이번 주는 SFC/SLC 없이 Weekly Test만 진행되었습니다. "
                    "앞으로는 정확한 용어를 사용하면 좋겠습니다. ")
        detail = "Q1(a) 그래프 계산 문제는 잘 풀었습니다. Q1(b)에서는 2/3을 받았습니다."
        result = organize_comments([{"comment":intro+overview+detail}],guide())
        self.assertEqual(result["sections"]["weekly_test"],overview)
        self.assertEqual(result["sections"]["general"],intro+"\n\n"+detail)
        self.assertEqual("".join(_source_units(intro+overview+detail)),intro+overview+detail)

    def test_score_free_overview_and_bare_numbered_subquestion(self):
        intro = "수업에서는 기본 개념을 다루었습니다. "
        overview = ("지수는 이번 Week 2 Test에서 성실하게 시도해 주었습니다. "
                    "앞으로는 답안을 더 자세히 설명하면 좋겠습니다. ")
        detail = "1(a) 그래프의 값을 읽는 문제에서는 단위가 필요합니다. 1(b) 해석도 연습하세요."
        result=organize_comments([{"comment":intro+overview+detail}],guide())
        self.assertEqual(result["sections"]["weekly_test"],overview)
        self.assertEqual(result["sections"]["general"],intro+"\n\n"+detail)

    def test_dates_are_not_question_blocks_and_later_scores_stay_in_detail(self):
        overview=("Alex scored 18/20 on the weekly test. "
                  "8/3 was the test date. However, Question 4 needs attention. "
                  "Going forward, check units. ")
        detail="Question 1 needs reasoning. Alex scored 2/3 on this test question."
        result=organize_comments([{"comment":overview+detail}],guide())
        self.assertEqual(result["sections"]["weekly_test"],overview)
        self.assertEqual(result["sections"]["general"],detail)

    def test_shared_lesson_only_does_not_fabricate_individual_overview(self):
        text="During our lessons we studied ecosystems. Going forward, practice definitions."
        result=organize_comments([{"comment":text}],guide())
        self.assertEqual(result["sections"]["weekly_test"],"")
        self.assertEqual(result["sections"]["general"],text)

    def test_other_subject_sample_can_keep_detailed_questions_in_test_section(self):
        different={"sections":[
            {"section":"weekly_test","features":content_features(
                "Alex scored 18/20. Question 1: reasoning. Question 2: units. Question 3: algebra.")},
            {"section":"general","features":content_features("During our lessons we studied algebra.")},
        ]}
        text="Alex scored 18/20 on the weekly test. Question 1 needs reasoning."
        result=organize_comments([{"comment":text}],different)
        self.assertEqual(result["sections"]["weekly_test"],text)
        self.assertEqual(result["sections"]["general"],"")

    def test_real_font_size_includes_scaled_text_matrix(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/"sample.pdf"
            c=canvas.Canvas(str(p),pagesize=(600,800))
            c.setFont("Helvetica",12);c.drawString(30,740,"Teacher comments")
            c.saveState();c.translate(30,700);c.scale(10,10)
            c.setFont("Helvetica",1);c.drawString(0,0,"Detailed feedback.");c.restoreState();c.save()
            a=inspect_example_report(p)
            section=a["content_guide"]["sections"][0]
            self.assertAlmostEqual(section["preferred_font_size"],10,places=1)
            self.assertNotIn("Detailed feedback",str(a))

    def test_original_pages_then_safe_readable_continuation_no_lost_or_duplicate_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);original=root/"original.pdf";make_original(original)
            before=original.read_bytes()
            comments=" ".join(f"UNIQUE{i:04d} requires careful reasoning and checking units." for i in range(500))
            student={"name":"Current Student","period_label":"Week 3","sample_content_guide":guide(),
                     "comments_by_section":{"homework":"CURRENT_HOMEWORK","weekly_test":"CURRENT_TEST","general":comments},
                     "homework":[],"exams":[]}
            output=root/"result.pdf"
            self.assertTrue(generate_adaptive_pdf(student,original,output,default_mapping(600,800,2)))
            with fitz.open(output) as result:
                self.assertGreater(len(result),2)
                combined="".join(re.sub(r"\s","",p.get_text()) for p in result)
                for i in range(500):self.assertEqual(combined.count(f"UNIQUE{i:04d}"),1)
                self.assertEqual(combined.count("CURRENT_HOMEWORK"),1)
                self.assertEqual(combined.count("CURRENT_TEST"),1)
                for p in list(result)[2:]:
                    self.assertIn("ORIGINAL_CONTINUATION",p.get_text())
                    self.assertEqual(p.rect.width,500)
                    self.assertNotIn("CURRENT_TEST",p.get_text())
            self.assertEqual(original.read_bytes(),before)
            self.assertTrue(all(r["font_size"]>=5 for r in student["report_fit"]["sections"]))
            self.assertGreaterEqual(student["report_fit"]["sections"][-1]["last_page_fill"],.5)
            self.assertEqual(student["report_fit"]["sections"][-1]["pages"][:2],[1,2])

    def test_too_small_box_never_eats_source_character(self):
        frame={"page":0,"section":"general","box":[0,0,50,1],"font":"Helvetica","preferred":9,"minimum":8}
        draws,remaining=_flow_at_size("KEEP EVERY CHARACTER",[frame],8)
        self.assertEqual(remaining,"KEEP EVERY CHARACTER")
        with self.assertRaises(ValidationError):layout_comments({"teacher_comment":remaining},[frame],1)

    def test_test_feedback_shrinks_in_original_box_without_continuation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);original=root/"original.pdf";make_original(original)
            text=" ".join(f"TESTITEM{i:03d} needs careful reasoning." for i in range(40))
            student={"name":"Current Student","sample_content_guide":guide(),
                     "comments_by_section":{"homework":"HOMEWORK_OK","weekly_test":text,"general":"GENERAL_OK"},
                     "homework":[],"exams":[]}
            output=root/"result.pdf"
            generate_adaptive_pdf(student,original,output,default_mapping(600,800,2))
            with fitz.open(output) as doc:
                self.assertEqual(len(doc),1)
                all_text="".join(re.sub(r"\s","",p.get_text()) for p in doc)
                for i in range(40):self.assertEqual(all_text.count(f"TESTITEM{i:03d}"),1)
                fit=next(r for r in student['report_fit']['sections'] if r['section']=='weekly_test')
                self.assertLess(fit['font_size'],8)
                self.assertEqual(fit['pages'],[1])

    def test_missing_test_feedback_uses_requested_message_without_mutating_scores(self):
        import copy
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);original=root/"original.pdf";make_original(original)
            student={"name":"Current Student","sample_content_guide":guide(),
                     "comments_by_section":{"homework":"HOMEWORK_OK","weekly_test":"","general":"Course notes. "*75},
                     "homework":[],"exams":[{"label":"Week 1","score":90,"max_score":100}]}
            before=copy.deepcopy(student)
            output=root/"result.pdf"
            generate_adaptive_pdf(student,original,output,default_mapping(600,800,2))
            self.assertEqual(student['exams'],before['exams'])
            self.assertEqual(student['comments_by_section'],before['comments_by_section'])
            self.assertTrue(student['report_fit']['missing_test_feedback_assumption'])
            with fitz.open(output) as doc:
                self.assertEqual(len(doc),1)
                text=re.sub(r'\s','',doc[0].get_text())
                self.assertIn(re.sub(r'\s','',MISSING_TEST_FEEDBACK),text)
                self.assertEqual(text.count('Coursenotes.'),75)
                self.assertNotIn('90/100',text)

    def test_weekly_text_too_long_does_not_silently_overflow_or_drop_words(self):
        frame={"page":0,"section":"weekly_test","box":[0,0,100,60],"font":"Helvetica","preferred":9.5,"minimum":8}
        with self.assertRaisesRegex(ValidationError,'no text was omitted'):
            layout_comments({'weekly_test_summary':'Long feedback. '*500},[frame],1)

    def test_short_second_teacher_page_is_compacted_into_first(self):
        frames=[{"page":page,"section":"general","box":box,"font":"Helvetica","preferred":9.5,"minimum":8}
                for page,box in [(0,[0,0,300,90]),(1,[0,0,300,500])]]
        text=' '.join(f'WORD{i:03d}' for i in range(80))
        draws,clones,receipt=layout_comments({'teacher_comment':text},frames,2)
        self.assertFalse(clones)
        self.assertEqual(receipt[0]['pages'],[1])
        self.assertLess(receipt[0]['font_size'],9.5)
        self.assertEqual(re.sub(r'\s','', ''.join(''.join(d['lines']) for d in draws)),re.sub(r'\s','',text))

    def test_fits_largest_readable_font_and_reflows_soft_newlines(self):
        frame={"page":0,"section":"general","box":[0,0,300,300],"font":"Helvetica","preferred":12,"minimum":8}
        draws,clones,receipt=layout_comments({"teacher_comment":"Keep every\nword intact."},[frame],1)
        self.assertEqual(receipt[0]["font_size"],12)
        self.assertFalse(clones)
        self.assertEqual(" ".join(draws[0]["lines"]),"Keep every word intact.")

    def test_english_words_do_not_split_at_line_or_page_edges(self):
        text="Feedback requires careful reasoning and checking every calculation."
        lines=_wrap_feedback(text,"Helvetica",10,110)
        self.assertEqual(" ".join(lines),text)
        frame={"page":0,"section":"general","box":[0,0,110,30],"font":"Helvetica","preferred":10,"minimum":8}
        draws,remaining=_flow_at_size(text,[frame],10)
        self.assertEqual(" ".join(draws[0]["lines"])+" "+remaining,text)


if __name__=="__main__":unittest.main()
