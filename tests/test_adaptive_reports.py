from __future__ import annotations

import tempfile
import unittest
import re
from pathlib import Path

import pymupdf as fitz
from reportlab.pdfgen import canvas

from backend.adaptive_reports import (layout_comments, _flow_at_size, generate_adaptive_pdf, _wrap_feedback,
                                     MISSING_TEST_FEEDBACK, display_name, display_class)
from backend.report_engine import register_fonts
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
        result=organize_comments([{"comment":text}],guide())
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
        result = organize_comments([{"comment": text}], guide())
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

    def test_test_feedback_that_fits_stays_on_page_one_at_readable_size(self):
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
                self.assertGreaterEqual(fit['font_size'],10)
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


GREEN=(0,.69,.31)
PANELS={"과제이행도":(229,250,382),"위클리 테스트":(402,423,554),"선생님 코멘트":(575,596,727)}


def make_korean_original(path):
    """Synthetic single-page template shaped like an academy weekly report."""
    font=register_fonts()
    c=canvas.Canvas(str(path),pagesize=(540,780))
    c.setFillColorRGB(*GREEN);c.rect(520,0,20,780,stroke=0,fill=1);c.rect(0,761,540,19,stroke=0,fill=1)
    c.setFillColorRGB(0,0,0);c.setFont(font,20);c.drawString(47,700,"(1차) 09:00 DP1 Math AA HL")
    c.setFont(font,12);c.drawString(46,597,"성명");c.drawString(155,597,"김예시")
    for label,(bar,top,bottom) in PANELS.items():
        c.setFillColorRGB(*GREEN);c.rect(31,780-top-1,477,22,stroke=0,fill=1)
        c.setStrokeColorRGB(*GREEN);c.setLineWidth(1);c.rect(31,780-bottom,477,bottom-top,stroke=1,fill=0)
        c.setFillColorRGB(1,1,1);c.drawCentredString(270,780-bar-16,label)
    c.setFillColorRGB(0,0,0);c.drawCentredString(200,30,"FOOTER_LOGO")
    c.save()


def korean_guide():
    return {"version":2,"sections":[
        {"section":s,"reliable":True,"preferred_font_size":10.0,"body_lane":"full",
         "features":content_features("synthetic")} for s in ("homework","weekly_test")]}


def series(*students):
    return [{"period_label":f"Week {i+1}","start_date":f"2026-07-{6+7*i:02d}","end_date":f"2026-07-{10+7*i:02d}",
             "student":value,"average":70.0,"highest":None if i else 95.0,"lowest":40.0}
            for i,value in enumerate(students)]


def korean_student(**changes):
    student={"name":"(ON)홍길동","class_name":"(1차) 09:00 [25FA] DP2 BIO SL","school":"Synthetic School",
             "period_label":"Week 2","period_start":"2026-07-13","period_end":"2026-07-17",
             "homework":[],"test_series":[],"sample_content_guide":korean_guide(),
             "comments_by_section":{"homework":"___ 학생은 과제를 완성했습니다.","weekly_test":"짧은 WT 코멘트.","general":""}}
    student.update(changes)
    return student


def squash(text):
    return re.sub(r"\s","",text)


class AcademyLayoutTests(unittest.TestCase):
    def render(self, student):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        original=Path(tmp.name)/"original.pdf";make_korean_original(original)
        output=Path(tmp.name)/"result.pdf"
        self.assertTrue(generate_adaptive_pdf(student,original,output,default_mapping(540,780,1)))
        doc=fitz.open(output);self.addCleanup(doc.close)
        return doc

    def test_display_transforms_are_render_only(self):
        self.assertEqual(display_name("(ON)홍길동"),"홍길동")
        self.assertEqual(display_name("(on) Alex"),"Alex")
        self.assertEqual(display_class("(1차) 09:00 [25FA] DP2 BIO SL"),"(1차) 09:00 DP2 BIO SL")
        student=korean_student()
        doc=self.render(student)
        text=squash(doc[0].get_text())
        self.assertIn("(1차)09:00DP2BIOSL",text)
        self.assertNotIn("[25FA]",text)
        self.assertNotIn("(ON)",text)
        self.assertIn("홍길동학생은과제를완성했습니다.",text)
        self.assertNotIn("___",text)
        self.assertNotIn("김예시",text)
        self.assertEqual(student["name"],"(ON)홍길동")
        self.assertEqual(student["comments_by_section"]["homework"],"___ 학생은 과제를 완성했습니다.")

    def test_missing_homework_comment_stays_blank_without_generated_summary(self):
        student=korean_student(comments_by_section={"homework":"","weekly_test":"짧은 WT 코멘트.","general":""})
        text=squash(self.render(student)[0].get_text())
        self.assertNotIn("Homeworkcompletion",text)
        self.assertNotIn("Completed:",text)
        self.assertIn("짧은WT코멘트.",text)

    def test_minus_sign_never_renders_blank(self):
        student=korean_student(comments_by_section={
            "homework":"과제 완료.","weekly_test":"Na+와 O2−의 크기를 비교했습니다.","general":""})
        text=squash(self.render(student)[0].get_text())
        self.assertTrue("O2−의" in text or "O2–의" in text, text)
        self.assertEqual(student["comments_by_section"]["weekly_test"],"Na+와 O2−의 크기를 비교했습니다.")

    def test_empty_general_hides_teacher_panel_and_omits_charts_without_data(self):
        student=korean_student()
        doc=self.render(student)
        self.assertEqual(len(doc),1)
        text=doc[0].get_text()
        self.assertNotIn("선생님 코멘트",text)
        self.assertNotIn("과제 -",text)
        self.assertNotIn("학생점수",text)
        self.assertTrue(student["report_fit"]["general_panel_hidden"])
        self.assertIn("FOOTER_LOGO",text)

    def test_general_comment_keeps_teacher_panel(self):
        doc=self.render(korean_student(comments_by_section={
            "homework":"과제 완료.","weekly_test":"짧은 WT 코멘트.","general":"GENERAL_PRESENT"}))
        text=doc[0].get_text()
        self.assertIn("선생님 코멘트",text)
        self.assertIn("GENERAL_PRESENT",text)

    def test_long_weekly_text_continues_on_headed_pages_at_10pt_without_loss(self):
        paragraphs=[f"Q{q}: " + " ".join(f"문항{q}설명{i:02d} reaction stoichiometry." for i in range(45))
                    for q in range(1,6)]
        text="\n\n".join(paragraphs)
        student=korean_student(comments_by_section={"homework":"과제 완료.","weekly_test":text,"general":""})
        doc=self.render(student)
        self.assertGreaterEqual(len(doc),2)
        fit=next(r for r in student["report_fit"]["sections"] if r["section"]=="weekly_test")
        self.assertGreaterEqual(fit["font_size"],10)
        self.assertGreater(max(fit["pages"]),1)
        # Page one: the extended Weekly Test panel; later pages: below the heading bar.
        body=doc[0].get_text(clip=fitz.Rect(31,424,509,728))
        for page in list(doc)[1:]:
            page_text=page.get_text()
            self.assertIn("위클리 테스트",page_text)
            self.assertNotIn("홍길동",page_text)
            self.assertNotIn("과제이행도",page_text)
            self.assertIn("FOOTER_LOGO",page_text)
            self.assertIn(f"/{len(doc)}",page_text)
            body+=page.get_text(clip=fitz.Rect(31,70,509,728))
        self.assertEqual(squash(body),squash(text))

    def test_charts_draw_series_legend_and_tolerate_missing_values(self):
        homework=[{"date":f"2026-07-1{d}","label":f"7/1{d}","value":v} for d,v in zip(range(3,8),"ABXA ")]
        doc=self.render(korean_student(homework=homework,test_series=series(None,66.7)))
        text=doc[0].get_text()
        for legend in ("학생점수","평균","최고점","최저점"):self.assertIn(legend,text)
        self.assertIn("(ON)홍길동 (과제 - (1차) 09:00 [25FA] DP2 BIO SL)",text)
        self.assertIn("기간: 2026-07-13 ~ 2026-07-17",text)
        self.assertIn("기간: 2026-07-06 ~ 2026-07-17",text)
        self.assertIn("2026-07-15",text)
        self.assertIn("66.7",text)

    def test_missing_test_feedback_has_no_weekly_chart(self):
        student=korean_student(test_series=series(80.0,90.0),
                               comments_by_section={"homework":"과제 완료.","weekly_test":"","general":""})
        doc=self.render(student)
        self.assertEqual(len(doc),1)
        text=doc[0].get_text()
        self.assertNotIn("학생점수",text)
        self.assertIn(squash(MISSING_TEST_FEEDBACK),squash(text))


if __name__=="__main__":unittest.main()
