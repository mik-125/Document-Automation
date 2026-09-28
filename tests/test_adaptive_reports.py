from __future__ import annotations

import tempfile
import unittest
import re
from pathlib import Path

import pymupdf as fitz
from reportlab.pdfgen import canvas

from backend.adaptive_reports import layout_comments, _flow_at_size, generate_adaptive_pdf, _wrap_feedback, MISSING_TEST_FEEDBACK
from backend.pdf_content import analyze_content_pdf
from backend.sample_content import organize_comments, content_features, _source_units
from backend.template_mapping import default_mapping
from backend.report_engine import ValidationError, inspect_example_report


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
    def test_unheaded_passages_follow_class_sample_profiles(self):
        text=("During our lessons we studied ecosystems. Going forward, practice explanations.\n\n"
              "Alex scored 18/20 on the weekly test. The overall test result was strong.\n\n"
              "Question 1 needs reasoning. Question 2 needs units.")
        result=organize_comments([{"comment":text,"comment_section":"weekly_test"}],guide())
        self.assertIn("18/20",result["sections"]["weekly_test"])
        self.assertIn("overall test result",result["sections"]["weekly_test"])
        self.assertNotIn("Question 1",result["sections"]["weekly_test"])
        self.assertIn("Question 1",result["sections"]["general"])
        self.assertIn("During our lessons",result["sections"]["general"])
        self.assertNotIn("16/20",str(result))
        self.assertEqual("".join(_source_units(text)),text)
        self.assertFalse(any(item["requires_review"] for item in result["decisions"]))

    def test_explicit_headings_homework_and_insufficient_class_evidence_win(self):
        text="General comments:\nThe student scored 18/20. Keep here."
        self.assertEqual(organize_comments([{"comment":text}],guide())["sections"]["general"],text)
        text="On 8/3 we reviewed a test. The student scored 18/20."
        for category,g in [("homework",guide()),("weekly_test",{"sections":[]})]:
            result=organize_comments([{"comment":text,"comment_section":category}],g)
            self.assertEqual(result["sections"][category],text)

    def test_low_confidence_passage_stays_in_selected_section_for_review(self):
        text = "A uniquely ambiguous observation with no class-profile evidence."
        result = organize_comments([{"comment": text, "comment_section": "weekly_test"}], guide())
        self.assertEqual(result["sections"]["weekly_test"], text)
        self.assertTrue(result["decisions"][0]["requires_review"])
        self.assertLess(result["decisions"][0]["confidence"], .68)
        self.assertTrue(result["decisions"][0]["signals"])

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
            comments=" ".join(f"UNIQUE{i:04d} requires careful reasoning and checking units." for i in range(180))
            student={"name":"Current Student","period_label":"Week 3","sample_content_guide":guide(),
                     "comments_by_section":{"homework":"CURRENT_HOMEWORK","weekly_test":"CURRENT_TEST","general":comments},
                     "homework":[],"exams":[]}
            output=root/"result.pdf"
            self.assertTrue(generate_adaptive_pdf(student,original,output,default_mapping(600,800,2)))
            with fitz.open(output) as result:
                self.assertEqual(len(result),2)
                combined="".join(re.sub(r"\s","",p.get_text()) for p in result)
                for i in range(180):self.assertEqual(combined.count(f"UNIQUE{i:04d}"),1)
                self.assertEqual(combined.count("CURRENT_HOMEWORK"),1)
                self.assertEqual(combined.count("CURRENT_TEST"),1)
                self.assertIn("continued", result[1].get_text().casefold())
                for p in list(result)[2:]:
                    self.assertIn("ORIGINAL_CONTINUATION",p.get_text())
                    self.assertEqual(p.rect.width,500)
                    self.assertNotIn("CURRENT_TEST",p.get_text())
            self.assertEqual(original.read_bytes(),before)
            self.assertTrue(all(r["font_size"]>=7 for r in student["report_fit"]["sections"]))
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
            text=" ".join(f"TESTITEM{i:03d} needs careful reasoning." for i in range(18))
            student={"name":"Current Student","sample_content_guide":guide(),
                     "comments_by_section":{"homework":"HOMEWORK_OK","weekly_test":text,"general":"GENERAL_OK"},
                     "homework":[],"exams":[]}
            output=root/"result.pdf"
            generate_adaptive_pdf(student,original,output,default_mapping(600,800,2))
            with fitz.open(output) as doc:
                self.assertEqual(len(doc),1)
                all_text="".join(re.sub(r"\s","",p.get_text()) for p in doc)
                for i in range(18):self.assertEqual(all_text.count(f"TESTITEM{i:03d}"),1)
                fit=next(r for r in student['report_fit']['sections'] if r['section']=='weekly_test')
                self.assertGreaterEqual(fit['font_size'],7)
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

    def test_weekly_text_never_uses_a_generated_continuation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);original=root/"original.pdf"
            c=canvas.Canvas(str(original),pagesize=(600,800))
            c.setStrokeColorRGB(0,.7,.3);c.setFont("Helvetica",12)
            for label,y in [("Homework",700),("Weekly Test",485),("Teacher comments",270)]:
                c.drawString(40,y,label);c.rect(30,y-180,540,172)
            c.save()
            mapping=default_mapping(600,800,1)
            mapping["continuation_regions"]=[{
                "key":"weekly_page_2","source_key":"weekly_test_summary",
                "content_type":"text","page":2,
                "box":{"x":.08,"y":.08,"width":.84,"height":.82},
                "font_name":"auto","font_size":10,"minimum_font_size":8,
                "line_spacing":1.2,"align":"left","required":False,
            }]
            tokens=[f"TESTWORD{i:03d}" for i in range(130)]
            student={"name":"Current Student","period_label":"Week 3",
                     "sample_content_guide":guide(),
                     "comments_by_section":{"homework":"HOMEWORK_OK","weekly_test":" ".join(tokens),"general":"GENERAL_OK"},
                     "homework":[],"exams":[]}
            output=root/"result.pdf"
            with self.assertRaisesRegex(ValidationError, "weekly test comment cannot fit"):
                generate_adaptive_pdf(student,original,output,mapping)

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
        self.assertEqual(receipt[0]["font_size"],12.75)
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
