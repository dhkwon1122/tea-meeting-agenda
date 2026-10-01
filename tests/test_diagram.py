import unittest

from confluence_agenda.builder import AgendaItem
from confluence_agenda.web.diagram import build_macro_diagram_html


class BuildMacroDiagramHtmlTest(unittest.TestCase):
    def test_empty_items_produces_empty_html(self):
        self.assertEqual(build_macro_diagram_html([]), "")

    def test_one_block_per_agenda_item(self):
        items = [AgendaItem(title="A"), AgendaItem(title="B"), AgendaItem(title="C")]
        result = build_macro_diagram_html(items)

        self.assertEqual(result.count('class="diagram-item"'), 3)

    def test_shows_anchors_and_macros_for_each_item(self):
        items = [AgendaItem(title="예산 승인")]
        result = build_macro_diagram_html(items)

        self.assertIn("1. 예산 승인", result)
        self.assertIn("anchor: 제목1", result)
        self.assertIn("anchor: 첨부1", result)
        self.assertIn("(첨부1)", result)
        self.assertIn("ui-expand", result)
        self.assertIn("include", result)
        self.assertIn("하위 페이지 「(첨부 1) 예산 승인」", result)
        self.assertIn("(돌아가기)", result)

    def test_numbers_and_titles_match_each_item_independently(self):
        items = [AgendaItem(title="예산 승인"), AgendaItem(title="채용 계획")]
        result = build_macro_diagram_html(items)

        self.assertIn("anchor: 제목1", result)
        self.assertIn("anchor: 제목2", result)
        self.assertIn("anchor: 첨부1", result)
        self.assertIn("anchor: 첨부2", result)
        self.assertIn("「(첨부 1) 예산 승인」", result)
        self.assertIn("「(첨부 2) 채용 계획」", result)

    def test_escapes_html_in_titles(self):
        items = [AgendaItem(title="<script>alert(1)</script>")]
        result = build_macro_diagram_html(items)

        self.assertNotIn("<script>alert(1)</script>", result)
        self.assertIn("&lt;script&gt;", result)


if __name__ == "__main__":
    unittest.main()
