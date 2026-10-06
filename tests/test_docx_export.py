import io
import os
import unittest
from unittest import mock

from docx import Document as DocxDocument

from confluence_agenda.web import confluence_credentials, docx_export
from confluence_agenda.web.docx_export import (
    DocxExportUnavailable,
    convert_confluence_url_to_docx,
    is_feature_available,
    resolve_token,
    verify_token,
)


def _fake_response(*, status_code=200, json_data=None, text=""):
    resp = mock.Mock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.text = text
    return resp


def _page_response(storage_html: str, *, title: str = "테스트 문서"):
    return _fake_response(
        json_data={"title": title, "body": {"storage": {"value": storage_html}}}
    )


def _docx_paragraph_texts(data: bytes):
    document = DocxDocument(io.BytesIO(data))
    return [p.text for p in document.paragraphs]


class IsFeatureAvailableTest(unittest.TestCase):
    def test_false_when_confluence_url_not_set(self):
        with mock.patch.dict(os.environ, {"CONFLUENCE_API_TOKEN": "t"}, clear=True):
            self.assertFalse(is_feature_available())

    def test_true_when_url_and_global_token_both_set(self):
        env = {"CONFLUENCE_URL": "https://wiki.example.com", "CONFLUENCE_API_TOKEN": "t"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertTrue(is_feature_available())

    def test_true_when_no_global_token_but_per_user_storage_available(self):
        env = {"CONFLUENCE_URL": "https://wiki.example.com"}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(
            confluence_credentials, "is_configured", return_value=True
        ):
            self.assertTrue(is_feature_available())

    def test_false_when_no_global_token_and_no_per_user_storage(self):
        env = {"CONFLUENCE_URL": "https://wiki.example.com"}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(
            confluence_credentials, "is_configured", return_value=False
        ):
            self.assertFalse(is_feature_available())

    def test_false_when_rendering_dependencies_missing(self):
        env = {"CONFLUENCE_URL": "https://wiki.example.com", "CONFLUENCE_API_TOKEN": "t"}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(
            docx_export, "DocxDocument", None
        ), mock.patch.object(docx_export, "etree", None):
            self.assertFalse(is_feature_available())


class ResolveTokenTest(unittest.TestCase):
    def test_prefers_personal_token_over_global(self):
        with mock.patch.dict(os.environ, {"CONFLUENCE_API_TOKEN": "global-token"}, clear=True), \
             mock.patch.object(confluence_credentials, "get_pat", return_value="personal-token"):
            self.assertEqual(resolve_token("dh.kwon"), "personal-token")

    def test_falls_back_to_global_token_when_no_personal_token(self):
        with mock.patch.dict(os.environ, {"CONFLUENCE_API_TOKEN": "global-token"}, clear=True), \
             mock.patch.object(confluence_credentials, "get_pat", return_value=None):
            self.assertEqual(resolve_token("dh.kwon"), "global-token")

    def test_returns_global_token_when_no_user_id(self):
        with mock.patch.dict(os.environ, {"CONFLUENCE_API_TOKEN": "global-token"}, clear=True):
            self.assertEqual(resolve_token(None), "global-token")

    def test_returns_none_when_nothing_configured(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(resolve_token(None))
            self.assertIsNone(resolve_token("dh.kwon"))


class VerifyTokenTest(unittest.TestCase):
    """PAT 등록 시 Confluence에 직접 물어 유효성/소유자를 확인한다 - Server/DC는
    틀린 토큰에 401 대신 익명 사용자로 응답하는 경우가 있어서 상태 코드만으로는
    못 걸러내는 걸 doc2report의 confluence_whoami()에서 참고했다."""

    def setUp(self):
        self._env_patch = mock.patch.dict(
            os.environ, {"CONFLUENCE_URL": "https://wiki.example.com"}, clear=True
        )
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)

    def test_raises_unavailable_when_url_not_configured(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(DocxExportUnavailable):
                verify_token("some-pat")

    def test_uses_bearer_auth_by_default(self):
        resp = _fake_response(json_data={"type": "known", "displayName": "권동혁", "username": "dh.kwon"})
        with mock.patch("requests.get", return_value=resp) as fake_get:
            verify_token("my-pat")

        self.assertEqual(fake_get.call_args.kwargs["headers"]["Authorization"], "Bearer my-pat")

    def test_401_raises(self):
        with mock.patch("requests.get", return_value=_fake_response(status_code=401)):
            with self.assertRaisesRegex(RuntimeError, "올바르지 않습니다"):
                verify_token("wrong-pat")

    def test_anonymous_response_raises_even_with_200(self):
        resp = _fake_response(json_data={"type": "anonymous"})
        with mock.patch("requests.get", return_value=resp):
            with self.assertRaisesRegex(RuntimeError, "익명 사용자"):
                verify_token("wrong-pat")

    def test_missing_display_name_and_username_raises(self):
        resp = _fake_response(json_data={"type": "known"})
        with mock.patch("requests.get", return_value=resp):
            with self.assertRaisesRegex(RuntimeError, "익명 사용자"):
                verify_token("wrong-pat")

    def test_returns_display_name_with_login_when_both_present(self):
        resp = _fake_response(json_data={"type": "known", "displayName": "권동혁", "username": "dh.kwon"})
        with mock.patch("requests.get", return_value=resp):
            self.assertEqual(verify_token("my-pat"), "권동혁 (dh.kwon)")

    def test_returns_just_name_when_no_separate_login(self):
        resp = _fake_response(json_data={"type": "known", "displayName": "권동혁"})
        with mock.patch("requests.get", return_value=resp):
            self.assertEqual(verify_token("my-pat"), "권동혁")


class ConvertConfluenceUrlToDocxGuardsTest(unittest.TestCase):
    def test_raises_unavailable_when_rendering_dependencies_missing(self):
        env = {"CONFLUENCE_URL": "https://wiki.example.com"}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(
            docx_export, "DocxDocument", None
        ), mock.patch.object(docx_export, "etree", None):
            with self.assertRaisesRegex(DocxExportUnavailable, "python-docx/lxml"):
                convert_confluence_url_to_docx("https://wiki.example.com/pages/123456", token="t")

    def test_raises_unavailable_when_url_not_configured(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(DocxExportUnavailable, "CONFLUENCE_URL"):
                convert_confluence_url_to_docx("https://wiki.example.com/pages/123456", token="t")

    def test_raises_unavailable_when_no_token_available(self):
        env = {"CONFLUENCE_URL": "https://wiki.example.com"}
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(DocxExportUnavailable, "PAT"):
                convert_confluence_url_to_docx("https://wiki.example.com/pages/123456")

    def test_raises_when_page_id_cannot_be_found_in_url(self):
        env = {"CONFLUENCE_URL": "https://wiki.example.com"}
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(RuntimeError, "페이지 ID"):
                convert_confluence_url_to_docx("https://wiki.example.com/not-a-page-url", token="t")


class ConvertConfluenceUrlToDocxHttpTest(unittest.TestCase):
    def setUp(self):
        self._env_patch = mock.patch.dict(
            os.environ, {"CONFLUENCE_URL": "https://wiki.example.com"}, clear=True
        )
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)

    def test_401_raises_auth_error(self):
        with mock.patch("requests.get", return_value=_fake_response(status_code=401)):
            with self.assertRaisesRegex(RuntimeError, "인증 실패"):
                convert_confluence_url_to_docx("https://wiki.example.com/pages/123", token="t")

    def test_403_raises_permission_error(self):
        with mock.patch("requests.get", return_value=_fake_response(status_code=403)):
            with self.assertRaisesRegex(RuntimeError, "접근 권한"):
                convert_confluence_url_to_docx("https://wiki.example.com/pages/123", token="t")

    def test_404_raises_not_found_error(self):
        with mock.patch("requests.get", return_value=_fake_response(status_code=404)):
            with self.assertRaisesRegex(RuntimeError, "찾을 수 없습니다"):
                convert_confluence_url_to_docx("https://wiki.example.com/pages/123", token="t")

    def test_missing_storage_body_raises(self):
        resp = _fake_response(json_data={"title": "제목", "body": {}})
        with mock.patch("requests.get", return_value=resp):
            with self.assertRaisesRegex(RuntimeError, "storage 본문"):
                convert_confluence_url_to_docx("https://wiki.example.com/pages/123", token="t")

    def test_sends_bearer_auth_header_when_no_username_configured(self):
        resp = _page_response("<p>본문</p>")
        with mock.patch("requests.get", return_value=resp) as fake_get:
            convert_confluence_url_to_docx("https://wiki.example.com/pages/123", token="my-pat")

        headers = fake_get.call_args.kwargs["headers"]
        self.assertEqual(headers["Authorization"], "Bearer my-pat")

    def test_sends_basic_auth_header_when_username_configured(self):
        with mock.patch.dict(os.environ, {"CONFLUENCE_USERNAME": "someone@example.com"}):
            resp = _page_response("<p>본문</p>")
            with mock.patch("requests.get", return_value=resp) as fake_get:
                convert_confluence_url_to_docx("https://wiki.example.com/pages/123", token="my-pat")

        headers = fake_get.call_args.kwargs["headers"]
        self.assertTrue(headers["Authorization"].startswith("Basic "))

    def test_filename_strips_unsafe_characters_and_adds_docx_extension(self):
        resp = _page_response("<p>본문</p>", title="예산안 승인 / 2026")
        with mock.patch("requests.get", return_value=resp):
            _, filename = convert_confluence_url_to_docx("https://wiki.example.com/pages/123", token="t")

        self.assertEqual(filename, "예산안 승인  2026.docx")

    def test_accepts_page_id_query_string_url_form(self):
        resp = _page_response("<p>본문</p>")
        with mock.patch("requests.get", return_value=resp) as fake_get:
            convert_confluence_url_to_docx(
                "https://wiki.example.com/pages/viewpage.action?pageId=99887", token="t"
            )

        called_url = fake_get.call_args.args[0]
        self.assertIn("99887", called_url)


class RenderedDocxContentTest(unittest.TestCase):
    """실제로 storage XHTML을 넣었을 때 .docx 본문에 올바른 내용이 들어가는지 확인한다."""

    def setUp(self):
        self._env_patch = mock.patch.dict(
            os.environ, {"CONFLUENCE_URL": "https://wiki.example.com"}, clear=True
        )
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)

    def _convert(self, storage_html: str, *, title: str = "테스트 문서") -> bytes:
        resp = _page_response(storage_html, title=title)
        with mock.patch("requests.get", return_value=resp):
            data, _ = convert_confluence_url_to_docx("https://wiki.example.com/pages/123", token="t")
        return data

    def test_title_becomes_first_heading(self):
        data = self._convert("<p>본문</p>", title="회의록")
        self.assertIn("회의록", _docx_paragraph_texts(data))

    def test_headings_and_paragraph_are_preserved(self):
        data = self._convert("<h2>소제목</h2><p>본문 내용입니다.</p>")
        texts = _docx_paragraph_texts(data)
        self.assertIn("소제목", texts)
        self.assertIn("본문 내용입니다.", texts)

    def test_bold_and_italic_runs_are_preserved(self):
        data = self._convert("<p>일반 <strong>굵게</strong>와 <em>기울임</em> 텍스트</p>")
        document = DocxDocument(io.BytesIO(data))
        paragraph = document.paragraphs[-1]
        bold_runs = [r for r in paragraph.runs if r.text == "굵게"]
        italic_runs = [r for r in paragraph.runs if r.text == "기울임"]
        self.assertTrue(bold_runs and bold_runs[0].bold)
        self.assertTrue(italic_runs and italic_runs[0].italic)

    def test_bullet_list_items_become_separate_paragraphs(self):
        data = self._convert("<ul><li>첫째</li><li>둘째</li></ul>")
        texts = _docx_paragraph_texts(data)
        self.assertIn("첫째", texts)
        self.assertIn("둘째", texts)

    def test_table_cells_are_preserved(self):
        storage = (
            "<table><tbody>"
            "<tr><th>이름</th><th>역할</th></tr>"
            "<tr><td>권동혁</td><td>스탭팀장</td></tr>"
            "</tbody></table>"
        )
        data = self._convert(storage)
        document = DocxDocument(io.BytesIO(data))
        self.assertEqual(len(document.tables), 1)
        table = document.tables[0]
        self.assertEqual(table.cell(0, 0).text, "이름")
        self.assertEqual(table.cell(1, 0).text, "권동혁")
        self.assertEqual(table.cell(1, 1).text, "스탭팀장")

    def test_colspan_merges_cells(self):
        storage = (
            '<table><tbody><tr><td colspan="2">합쳐진 칸</td></tr>'
            "<tr><td>a</td><td>b</td></tr></tbody></table>"
        )
        data = self._convert(storage)
        document = DocxDocument(io.BytesIO(data))
        table = document.tables[0]
        self.assertEqual(table.cell(0, 0).text, "합쳐진 칸")
        # 병합된 칸이라 (0,0)과 (0,1)이 같은 셀을 가리켜야 한다.
        self.assertEqual(table.cell(0, 0)._tc, table.cell(0, 1)._tc)

    def test_macro_rich_text_body_is_unwrapped(self):
        storage = (
            '<ac:structured-macro ac:name="info" xmlns:ac="http://www.atlassian.com/schema/confluence/4/ac/">'
            "<ac:rich-text-body><p>안내 문구입니다.</p></ac:rich-text-body>"
            "</ac:structured-macro>"
        )
        data = self._convert(storage)
        self.assertIn("안내 문구입니다.", _docx_paragraph_texts(data))

    def test_unsupported_macro_without_rich_text_body_becomes_placeholder_note(self):
        storage = (
            '<ac:structured-macro ac:name="children" '
            'xmlns:ac="http://www.atlassian.com/schema/confluence/4/ac/"/>'
        )
        data = self._convert(storage)
        texts = _docx_paragraph_texts(data)
        self.assertTrue(any("children" in t and "지원하지 않습니다" in t for t in texts))

    def test_image_becomes_placeholder_note(self):
        storage = (
            '<ac:image xmlns:ac="http://www.atlassian.com/schema/confluence/4/ac/" '
            'xmlns:ri="http://www.atlassian.com/schema/confluence/4/ri/">'
            '<ri:attachment ri:filename="diagram.png"/>'
            "</ac:image>"
        )
        data = self._convert(storage)
        texts = _docx_paragraph_texts(data)
        self.assertTrue(any("diagram.png" in t for t in texts))

    def test_layout_wrapper_is_flattened(self):
        storage = (
            '<ac:layout xmlns:ac="http://www.atlassian.com/schema/confluence/4/ac/">'
            '<ac:layout-section ac:type="single"><ac:layout-cell><p>레이아웃 안 내용</p>'
            "</ac:layout-cell></ac:layout-section></ac:layout>"
        )
        data = self._convert(storage)
        self.assertIn("레이아웃 안 내용", _docx_paragraph_texts(data))


if __name__ == "__main__":
    unittest.main()
