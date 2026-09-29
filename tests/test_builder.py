import re
import unittest
from datetime import date

from confluence_agenda.builder import (
    AgendaItem,
    build_agenda_email_html,
    build_agenda_page_body,
    build_email_subject,
)

_SECTION_RE = re.compile(
    r'<ac:layout-section ac:type="single"><ac:layout-cell>\n(.*?)\n</ac:layout-cell></ac:layout-section>',
    re.S,
)


def _sections(result: str):
    """result를 레이아웃 섹션 내용 목록(순서대로)으로 쪼갠다."""
    return _SECTION_RE.findall(result)


class BuildAgendaPageBodyTest(unittest.TestCase):
    def test_requires_at_least_one_item(self):
        with self.assertRaises(ValueError):
            build_agenda_page_body([])

    def test_wraps_whole_body_in_a_single_ac_layout(self):
        result = build_agenda_page_body([AgendaItem(title="A"), AgendaItem(title="B")])

        self.assertTrue(result.startswith("<ac:layout>"))
        self.assertTrue(result.endswith("</ac:layout>"))
        self.assertEqual(result.count("<ac:layout>"), 1)
        self.assertEqual(result.count("</ac:layout>"), 1)

    def test_section_count_is_setup_plus_one_per_agenda_plus_one_shared_attachment(self):
        # 안건 3개 -> [준비 섹션 1] + [안건별 섹션 3] + [첨부 공유 섹션 1] = 5개.
        items = [AgendaItem(title="A"), AgendaItem(title="B"), AgendaItem(title="C")]
        result = build_agenda_page_body(items)

        self.assertEqual(len(_sections(result)), 5)

    def test_first_section_is_the_page_creation_setup(self):
        items = [AgendaItem(title="예산 승인"), AgendaItem(title="채용 계획")]
        result = build_agenda_page_body(items)
        setup_section = _sections(result)[0]

        self.assertIn('ac:name="create-from-template"', setup_section)
        self.assertIn("<li>(첨부 1) 예산 승인</li>", setup_section)
        self.assertIn("<li>(첨부 2) 채용 계획</li>", setup_section)
        # 준비 섹션에는 본문/ui-expand 내용이 섞이면 안 된다.
        self.assertNotIn("ui-expand", setup_section)

    def test_setup_section_uses_given_template_id_and_button_label(self):
        items = [AgendaItem(title="예산 승인")]
        result = build_agenda_page_body(items, template_id="999", button_label="버튼")
        setup_section = _sections(result)[0]

        self.assertIn('ac:name="templateName">999<', setup_section)
        self.assertIn('ac:name="templateId">999<', setup_section)
        self.assertIn('ac:name="buttonLabel">버튼<', setup_section)

    def test_heading_and_body_share_one_section_per_agenda(self):
        items = [AgendaItem(title="예산 승인", body="본문 내용")]
        result = build_agenda_page_body(items)
        agenda_section = _sections(result)[1]

        # 제목과 본문은 같은 섹션 안에 함께 있어야 한다.
        self.assertIn("1. 예산 승인", agenda_section)
        self.assertIn("<p>본문 내용</p>", agenda_section)
        # 첨부 ui-expand는 이 섹션에는 없다.
        self.assertNotIn("ui-expand", agenda_section)

    def test_all_attachments_are_inside_a_single_shared_section(self):
        items = [AgendaItem(title="A"), AgendaItem(title="B"), AgendaItem(title="C")]
        result = build_agenda_page_body(items)
        last_section = _sections(result)[-1]

        self.assertEqual(last_section.count("ui-expand"), 3)
        for idx in (1, 2, 3):
            self.assertIn(f">첨부{idx}<", last_section)

    def test_default_body_is_three_placeholder_lines(self):
        items = [AgendaItem(title="예산 승인")]
        result = build_agenda_page_body(items)

        placeholder = "<p>&nbsp;&nbsp;&nbsp;가나다라마바사 내용을 입력해주세요</p>"
        self.assertEqual(result.count(placeholder), 3)

    def test_custom_body_overrides_placeholder(self):
        items = [AgendaItem(title="예산 승인", body="직접 작성한 본문")]
        result = build_agenda_page_body(items)

        self.assertIn("<p>직접 작성한 본문</p>", result)
        self.assertNotIn("가나다라마바사", result)

    def test_heading_link_and_expand_are_wired_to_same_anchor(self):
        items = [AgendaItem(title="A"), AgendaItem(title="B")]
        result = build_agenda_page_body(items)

        for idx in (1, 2):
            # 제목 옆 "(첨부N)" 링크는 "첨부N" 앵커를 가리킨다.
            self.assertIn(f'ac:link ac:anchor="첨부{idx}"', result)
            self.assertIn(f">첨부{idx}<", result)
            # ui-expand 안의 "(돌아가기)" 링크는 "제목N" 앵커를 가리킨다.
            self.assertIn(f'ac:link ac:anchor="제목{idx}"', result)
            self.assertIn(f">제목{idx}<", result)

    def test_heading_uses_expected_style_and_tags(self):
        items = [AgendaItem(title="스타일 테스트")]
        result = build_agenda_page_body(items)

        self.assertIn('<h3 style="text-align: left;">', result)
        self.assertIn('<strong style="letter-spacing: -0.006em;">', result)
        self.assertIn(
            '<span style="color:var(--ds-background-accent-blue-bolder,#0c66e4);">', result
        )
        self.assertNotIn("an:name", result)
        self.assertNotIn("</ac link>", result)

    def test_attachment_section_uses_ui_expand_and_include_macro(self):
        items = [AgendaItem(title="예산 승인")]
        result = build_agenda_page_body(items)

        self.assertIn('ac:name="ui-expand"', result)
        self.assertIn('ac:parameter ac:name="title">(첨부 1) 예산 승인<', result)
        self.assertIn('ac:name="include"', result)
        self.assertIn('ri:content-title="(첨부 1) 예산 승인"', result)
        self.assertIn("(돌아가기)", result)

    def test_multiple_items_preserve_order_and_numbering(self):
        items = [AgendaItem(title=f"안건{i}") for i in range(1, 4)]
        result = build_agenda_page_body(items)

        heading_order = [m.group(1) for m in re.finditer(r">(\d)\. 안건\1", result)]
        self.assertEqual(heading_order, ["1", "2", "3"])

        expand_titles = re.findall(r'ac:name="title">(\(첨부 \d\) 안건\d)<', result)
        self.assertEqual(expand_titles, ["(첨부 1) 안건1", "(첨부 2) 안건2", "(첨부 3) 안건3"])

    def test_body_escapes_html_by_default(self):
        items = [AgendaItem(title="XSS", body="<script>alert(1)</script>")]
        result = build_agenda_page_body(items)

        self.assertNotIn("<script>alert(1)</script>", result)
        self.assertIn("&lt;script&gt;", result)

    def test_cdata_terminator_in_link_text_is_escaped(self):
        items = [AgendaItem(title="테스트]]>안건")]
        result = build_agenda_page_body(items)

        self.assertNotIn("]]>안건", result)

    def test_multiline_body_creates_multiple_paragraphs(self):
        items = [AgendaItem(title="안건1", body="첫 줄\n둘째 줄")]
        result = build_agenda_page_body(items)

        self.assertIn("<p>첫 줄</p>", result)
        self.assertIn("<p>둘째 줄</p>", result)


class BuildAgendaEmailHtmlTest(unittest.TestCase):
    def test_wraps_full_source_verbatim_for_copy_paste(self):
        source = build_agenda_page_body([AgendaItem(title="예산 승인")])
        result = build_agenda_email_html(source)

        # 이스케이프된 형태로라도 전체 소스가 그대로 담겨 있어야 한다
        # (메일에는 제목만 오고 본문 소스가 빠지는 문제의 회귀 테스트).
        self.assertIn("<pre", result)
        self.assertIn("(첨부 1) 예산 승인", result)
        self.assertIn("ui-expand", result)
        self.assertIn("include", result)

    def test_escapes_html_in_source(self):
        result = build_agenda_email_html("<script>alert(1)</script>")

        self.assertNotIn("<script>alert(1)</script>", result)
        self.assertIn("&lt;script&gt;", result)


class BuildEmailSubjectTest(unittest.TestCase):
    def test_fixed_template_with_date_and_korean_weekday(self):
        # 2026-09-21은 월요일.
        self.assertEqual(date(2026, 9, 21).weekday(), 0)
        self.assertEqual(
            build_email_subject(on=date(2026, 9, 21)),
            "[보고] 9.21(월) 스탭팀장 미팅 피플팀 안건",
        )

    def test_does_not_zero_pad_month_or_day(self):
        self.assertEqual(
            build_email_subject(on=date(2026, 3, 5)),
            "[보고] 3.5(목) 스탭팀장 미팅 피플팀 안건",
        )

    def test_subject_is_independent_of_agenda_content(self):
        # 안건 제목이 몇 개든, 무엇이든 제목 문구에 전혀 영향을 주지 않는다.
        same_day = date(2026, 9, 21)
        self.assertEqual(build_email_subject(on=same_day), build_email_subject(on=same_day))

    def test_defaults_to_today_when_no_date_given(self):
        self.assertEqual(build_email_subject(), build_email_subject(on=date.today()))


if __name__ == "__main__":
    unittest.main()
