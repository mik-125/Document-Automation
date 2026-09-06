import unittest
from collections import Counter

from comment_sections import split_comment_sections


class CommentSectionTests(unittest.TestCase):
    def test_bilingual_topics_preserve_original_text(self):
        text = "과제를 모두 제출했습니다. 주간 테스트에서는 80%를 받았습니다. 수업에 집중했습니다. Homework is complete. The exam needs review."
        result = split_comment_sections(text)
        self.assertEqual(result["homework"], "과제를 모두 제출했습니다. Homework is complete.")
        self.assertEqual(result["test"], " 주간 테스트에서는 80%를 받았습니다. The exam needs review.")
        self.assertEqual(result["general"], " 수업에 집중했습니다.")
        self.assertEqual(Counter("".join(result.values())), Counter(text))

    def test_explicit_test_intro_keeps_its_full_sentence(self):
        text = "주간 테스트에서는 20%의 점수를 받았으며, 미제출 과제가 한 건 확인되었습니다."
        self.assertEqual(split_comment_sections(text), {"homework": "", "test": text, "general": ""})

    def test_introductory_learning_feedback_stays_together(self):
        text = "현우는 이번 주 모든 수업 기록에서 A를 받으며 매우 꾸준한 모습을 보여주었습니다. 수업 시간에 안정적으로 참여했습니다."
        self.assertEqual(split_comment_sections(text)["homework"], text)
        self.assertEqual(split_comment_sections(text)["general"], "")

    def test_explicit_headings_keep_context_and_every_character(self):
        text = "Homework:\nAll complete.\nNeeds neater writing.\n\n주간 테스트\n개념을 잘 이해했습니다.\n총평:\nKeep it up!\n"
        result = split_comment_sections(text)
        self.assertEqual(result["homework"], "Homework:\nAll complete.\nNeeds neater writing.\n\n")
        self.assertEqual(result["test"], "주간 테스트\n개념을 잘 이해했습니다.\n")
        self.assertEqual(result["general"], "총평:\nKeep it up!\n")
        self.assertEqual("".join(result.values()), text)

    def test_decimal_scores_and_newlines_survive(self):
        text = "Exam score: 82.5%.\nHomework score: 91.25%.\n\nGood effort."
        result = split_comment_sections(text)
        self.assertIn("82.5%", result["test"])
        self.assertIn("91.25%", result["homework"])
        self.assertEqual(Counter("".join(result.values())), Counter(text))

    def test_empty_and_whitespace_only_input(self):
        self.assertEqual(split_comment_sections(""), {"homework": "", "test": "", "general": ""})
        self.assertEqual(split_comment_sections(" \n\t")["general"], " \n\t")


if __name__ == "__main__":
    unittest.main()
