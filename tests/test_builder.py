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

        # 안건 제목에 번호와 본문이 포함된다.
        self.assertIn("1. 예산 승인", result)
        self.assertIn("<p>올해 예산안입니다.</p>", result)

        # Expand 제목 형식 확인.
        self.assertIn('ac:parameter ac:name="title">(첨부 1) 예산 승인<', result)

        # anchor-1(제목)과 attachment-1(첨부) 두 앵커가 모두 존재해야 한다.
        self.assertIn('>agenda-1<', result)
        self.assertIn('>attachment-1<', result)

    def test_heading_link_and_expand_are_wired_to_same_anchor(self):
        items = [AgendaItem(title="A"), AgendaItem(title="B")]
        result = build_agenda_page_body(items)

        for idx in (1, 2):
            # 제목 옆 "(첨부 N)" 링크는 attachment-N 앵커를 가리킨다.
            self.assertIn(f'ac:link ac:anchor="attachment-{idx}"', result)
            # attachment-N 앵커 자체도 존재한다.
            self.assertIn(f'>attachment-{idx}<', result)
            # Expand 안의 되돌아가기 링크는 agenda-N 앵커를 가리킨다.
            self.assertIn(f'ac:link ac:anchor="agenda-{idx}"', result)
            self.assertIn(f'>agenda-{idx}<', result)

    def test_multiple_items_preserve_order_and_numbering(self):
        items = [AgendaItem(title=f"안건{i}") for i in range(1, 4)]
        result = build_agenda_page_body(items)

        heading_order = [m.group(1) for m in re.finditer(r">(\d)\. 안건\1", result)]
        self.assertEqual(heading_order, ["1", "2", "3"])

        expand_titles = re.findall(r'ac:name="title">(\(첨부 \d\) 안건\d)<', result)
        self.assertEqual(expand_titles, ["(첨부 1) 안건1", "(첨부 2) 안건2", "(첨부 3) 안건3"])

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

    def test_custom_attachment_label_and_back_link_text(self):
        items = [
            AgendaItem(
                title="특이 안건",
                attachment_label="[별첨1]",
                back_link_text="상단으로",
            )
        ]
        result = build_agenda_page_body(items)

        self.assertIn("[별첨1]", result)
        self.assertIn("상단으로", result)


if __name__ == "__main__":
    unittest.main()
