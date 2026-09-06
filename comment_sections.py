"""Conservatively arrange source comments without rewriting teacher feedback."""

import re


_HEADINGS = {
    "homework": {"homework", "assignments", "assignment", "과제", "숙제", "과제이행도", "과제 코멘트", "숙제 코멘트"},
    "test": {"test", "tests", "weekly test", "weekly tests", "test comments", "exam", "주간 테스트", "위클리 테스트", "시험", "시험 코멘트", "테스트 코멘트"},
    "general": {"general", "general comments", "teacher comments", "overall", "종합", "종합 의견", "종합 코멘트", "총평", "선생님 코멘트"},
}
_HOMEWORK = re.compile(r"과제|숙제|\b(?:homework|assignments?)\b", re.IGNORECASE)
_TEST = re.compile(r"테스트|시험|퀴즈|\b(?:tests?|exams?|examinations?|quizzes|quiz)\b", re.IGNORECASE)


def split_comment_sections(text: str) -> dict[str, str]:
    """Return homework, test, and general text, retaining each source character.

    Explicit standalone headings determine the following section. Otherwise,
    only sentences with an unambiguous homework or test reference are assigned.
    Recognized weekly narratives retain their topic context; otherwise mixed
    references and general classroom feedback remain in ``general``.
    Whitespace and headings are retained so this operation never edits content.
    Introductory classroom-grade feedback is displayed as learning feedback
    alongside homework; this function never derives or changes a grade.
    """
    sections = {"homework": [], "test": [], "general": []}
    explicit_section = None
    narrative_section = None
    intro = text.split(".", 1)[0]
    narrative = bool("수업" in intro and re.search(r"(?<![A-Za-z])[ABF](?![A-Za-z])", intro))
    test_narrative = bool(re.search(r"주간\s*테스트", intro))
    for line in text.splitlines(keepends=True):
        heading = line.strip().strip("#* :：").strip().casefold()
        matching_heading = next(
            (section for section, names in _HEADINGS.items() if heading in names),
            None,
        )
        if matching_heading:
            explicit_section = matching_heading
            sections[matching_heading].append(line)
            continue
        # Only split at sentence punctuation followed by whitespace; decimal
        # scores, URLs, and punctuation within a token remain intact.
        for sentence in re.split(r"(?<=[.!?。！？])(?=\s)", line):
            if not sentence:
                continue
            if explicit_section:
                section = explicit_section
            else:
                clean = sentence.strip()
                # The imported report narrative commonly follows learning
                # feedback -> weekly test -> forward-looking general advice.
                # Keep following sentences with that topic, including pronouns.
                test_intro = bool(re.match(r"(?:주간\s*테스트|위클리\s*테스트|Weekly\s+test|Test\s+score)", clean, re.I))
                if (narrative or test_narrative) and test_intro:
                    narrative_section = "test"
                elif (narrative or test_narrative) and re.match(r"(?:앞으로|Going forward|In future)", clean, re.I):
                    narrative_section = "general"
                elif narrative and narrative_section is None:
                    narrative_section = "homework"
                elif test_narrative and narrative_section is None:
                    narrative_section = "test"
                homework = bool(_HOMEWORK.search(sentence))
                test = bool(_TEST.search(sentence))
                if narrative_section == "general":
                    section = "general"
                elif homework and not test:
                    section = "homework"
                else:
                    section = narrative_section or ("test" if test_intro or test and not homework else "general")
            sections[section].append(sentence)
    return {section: "".join(parts) for section, parts in sections.items()}
