import re
import unittest

from confluence_agenda.builder import (
    AgendaItem,
    build_agenda_email_html,
    build_agenda_page_body,
    build_email_subject,
)


class BuildAgendaPageBodyTest(unittest.TestCase):
    def test_requires_at_least_one_item(self):
        with self.assertRaises(ValueError):
            build_agenda_page_body([])

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
    def test_requires_at_least_one_item(self):
        with self.assertRaises(ValueError):
            build_agenda_email_html([])

    def test_lists_titles_as_ordered_list(self):
        items = [AgendaItem(title="예산 승인"), AgendaItem(title="채용 계획")]
        result = build_agenda_email_html(items)

        self.assertIn("<ol>", result)
        self.assertIn("<li>예산 승인</li>", result)
        self.assertIn("<li>채용 계획</li>", result)
        # ac:* Confluence 매크로는 메일 클라이언트가 렌더링 못하므로 섞이면 안 된다.
        self.assertNotIn("ac:structured-macro", result)

    def test_escapes_html_in_titles(self):
        items = [AgendaItem(title="<script>alert(1)</script>")]
        result = build_agenda_email_html(items)

        self.assertNotIn("<script>alert(1)</script>", result)
        self.assertIn("&lt;script&gt;", result)


class BuildEmailSubjectTest(unittest.TestCase):
    def test_single_item(self):
        self.assertEqual(build_email_subject([AgendaItem(title="예산 승인")]), "[안건 보고] 예산 승인")

    def test_multiple_items_add_count_suffix(self):
        items = [AgendaItem(title="예산 승인"), AgendaItem(title="채용 계획")]
        self.assertEqual(build_email_subject(items), "[안건 보고] 예산 승인 외 1건")


if __name__ == "__main__":
    unittest.main()
