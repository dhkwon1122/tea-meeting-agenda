import re
import unittest

from confluence_agenda.builder import (
    AgendaItem,
    attachment_image_macro,
    build_agenda_page_body,
)


class BuildAgendaPageBodyTest(unittest.TestCase):
    def test_requires_at_least_one_item(self):
        with self.assertRaises(ValueError):
            build_agenda_page_body([])

    def test_single_item_structure(self):
        items = [AgendaItem(title="예산 승인", body="올해 예산안입니다.", attachment_body="상세 내역")]
        result = build_agenda_page_body(items)

        self.assertIn("1. 예산 승인", result)
        self.assertIn("<p>올해 예산안입니다.</p>", result)
        self.assertIn('ac:parameter ac:name="title">(첨부1) 예산 승인<', result)

        # "제목1" 앵커(안건 제목 줄)와 "첨부1" 앵커(Expand 앞) 둘 다 존재해야 한다.
        self.assertIn(">제목1<", result)
        self.assertIn(">첨부1<", result)

    def test_heading_link_and_expand_are_wired_to_same_anchor(self):
        items = [AgendaItem(title="A"), AgendaItem(title="B")]
        result = build_agenda_page_body(items)

        for idx in (1, 2):
            # 제목 옆 "(첨부N)" 링크는 "첨부N" 앵커를 가리킨다.
            self.assertIn(f'ac:link ac:anchor="첨부{idx}"', result)
            self.assertIn(f">첨부{idx}<", result)
            # Expand 안의 되돌아가기 링크는 "제목N" 앵커를 가리킨다.
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

    def test_multiple_items_preserve_order_and_numbering(self):
        items = [AgendaItem(title=f"안건{i}") for i in range(1, 4)]
        result = build_agenda_page_body(items)

        heading_order = [m.group(1) for m in re.finditer(r">(\d)\. 안건\1", result)]
        self.assertEqual(heading_order, ["1", "2", "3"])

        expand_titles = re.findall(r'ac:name="title">(\(첨부\d\) 안건\d)<', result)
        self.assertEqual(expand_titles, ["(첨부1) 안건1", "(첨부2) 안건2", "(첨부3) 안건3"])

    def test_raw_body_bypasses_escaping(self):
        raw_html = attachment_image_macro("chart.png", width=400)
        items = [AgendaItem(title="차트", attachment_body=raw_html, raw_attachment_body=True)]
        result = build_agenda_page_body(items)

        self.assertIn(raw_html, result)

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

    def test_custom_back_link_text(self):
        items = [AgendaItem(title="특이 안건", back_link_text="상단으로")]
        result = build_agenda_page_body(items)

        self.assertIn("상단으로", result)


if __name__ == "__main__":
    unittest.main()
