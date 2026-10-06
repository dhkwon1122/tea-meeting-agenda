import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from docx import Document as DocxDocument

from confluence_agenda.web import confluence_credentials, docx_export
from confluence_agenda.web.docx_export import (
    DocxExportUnavailable,
    convert_confluence_url_to_docx,
    diagnose_connection,
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

    def test_no_proxy_true_forces_direct_connection(self):
        resp = _fake_response(json_data={"type": "known", "displayName": "권동혁"})
        with mock.patch.dict(os.environ, {"CONFLUENCE_NO_PROXY": "true"}):
            with mock.patch("requests.get", return_value=resp) as fake_get:
                verify_token("my-pat")

        self.assertEqual(fake_get.call_args.kwargs["proxies"], {"http": None, "https": None})


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

    def test_ca_bundle_path_that_does_not_exist_falls_back_to_default_verify(self):
        """Docker 등에서 호스트 절대경로를 그대로 넣어 컨테이너 안에 그 파일이
        없는 흔한 실수를 흉내낸다 - requests에 존재하지 않는 경로를 그대로
        넘기면 OSError로 깨지므로, 조용히 기본 인증서(verify=True)로 넘어가야 한다."""
        env = {"CONFLUENCE_CA_BUNDLE": "/host/only/path/does-not-exist-in-container.crt"}
        with mock.patch.dict(os.environ, env):
            resp = _page_response("<p>본문</p>")
            with mock.patch("requests.get", return_value=resp) as fake_get:
                convert_confluence_url_to_docx("https://wiki.example.com/pages/123", token="t")

        self.assertTrue(fake_get.call_args.kwargs["verify"])

    def test_ca_bundle_path_that_exists_is_used(self):
        with tempfile.TemporaryDirectory() as tmp:
            ca_path = Path(tmp) / "corp-ca.crt"
            ca_path.write_text("fake cert contents")

            with mock.patch.dict(os.environ, {"CONFLUENCE_CA_BUNDLE": str(ca_path)}):
                resp = _page_response("<p>본문</p>")
                with mock.patch("requests.get", return_value=resp) as fake_get:
                    convert_confluence_url_to_docx("https://wiki.example.com/pages/123", token="t")

            self.assertEqual(fake_get.call_args.kwargs["verify"], str(ca_path))

    def test_oserror_from_bad_verify_path_is_wrapped_as_runtime_error(self):
        """_ssl_verify()가 못 거른 다른 OSError(권한 문제 등)도 requests.RequestException이
        아니라서 그냥 두면 밖으로 새어나간다 - 사용자에게 보이는 에러가
        "변환 실패: Could not find a suitable TLS..." 처럼 혼란스럽지 않도록
        우리 쪽 "Confluence 서버 연결 실패" 메시지로 감싸야 한다."""
        with mock.patch(
            "requests.get",
            side_effect=OSError("Could not find a suitable TLS CA certificate bundle"),
        ):
            with self.assertRaisesRegex(RuntimeError, "Confluence 서버 연결 실패"):
                convert_confluence_url_to_docx("https://wiki.example.com/pages/123", token="t")

    def test_ssl_error_with_default_verify_hints_at_untrusted_corp_ca(self):
        """CONFLUENCE_CA_BUNDLE 없이(= 기본 인증서로) SSLError가 나면, 사내 루트 CA가
        신뢰 저장소에 없다는 것과 certs/의 PEM 형식·재빌드 여부를 확인하라는
        구체적인 안내가 나와야 한다 - 그냥 "SSL 문제일 수 있다"보다 실행 가능해야 함."""
        import requests as requests_module

        with mock.patch(
            "requests.get",
            side_effect=requests_module.exceptions.SSLError(
                "certificate verify failed: unable to get local issuer certificate"
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "PEM 형식") as ctx:
                convert_confluence_url_to_docx("https://wiki.example.com/pages/123", token="t")

        self.assertIn("기본", str(ctx.exception))
        self.assertIn("재빌드", str(ctx.exception))

    def test_ssl_error_with_custom_ca_bundle_hints_at_bad_file(self):
        """CONFLUENCE_CA_BUNDLE을 지정했는데도 SSLError가 나면, 그 파일 자체(진짜
        루트 CA인지/PEM인지/전체 체인인지)를 확인하라는 안내가 나와야 한다."""
        import requests as requests_module

        with tempfile.TemporaryDirectory() as tmp:
            ca_path = Path(tmp) / "corp-ca.crt"
            ca_path.write_text("-----BEGIN CERTIFICATE-----\nfake\n-----END CERTIFICATE-----")

            with mock.patch.dict(os.environ, {"CONFLUENCE_CA_BUNDLE": str(ca_path)}), mock.patch(
                "requests.get",
                side_effect=requests_module.exceptions.SSLError("self signed certificate in certificate chain"),
            ):
                with self.assertRaisesRegex(RuntimeError, str(ca_path)) as ctx:
                    convert_confluence_url_to_docx("https://wiki.example.com/pages/123", token="t")

        self.assertIn("전체", str(ctx.exception))

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

    def test_default_headers_include_accept_json_and_curl_style_user_agent(self):
        """사내 API 게이트웨이가 requests의 기본 User-Agent("python-requests/x.y.z")를
        스크립트로 식별해 연결을 끊는 경우가 실측됐다(dhkwon1122/Researcher-board의
        pipeline/confluence_client.py) - curl 스타일 UA를 기본값으로 보내야 한다."""
        resp = _page_response("<p>본문</p>")
        with mock.patch("requests.get", return_value=resp) as fake_get:
            convert_confluence_url_to_docx("https://wiki.example.com/pages/123", token="t")

        headers = fake_get.call_args.kwargs["headers"]
        self.assertEqual(headers["Accept"], "application/json")
        self.assertEqual(headers["User-Agent"], "curl/8.0.0")

    def test_user_agent_overridable_via_env(self):
        with mock.patch.dict(os.environ, {"CONFLUENCE_USER_AGENT": "내사내봇/1.0"}):
            resp = _page_response("<p>본문</p>")
            with mock.patch("requests.get", return_value=resp) as fake_get:
                convert_confluence_url_to_docx("https://wiki.example.com/pages/123", token="t")

        self.assertEqual(fake_get.call_args.kwargs["headers"]["User-Agent"], "내사내봇/1.0")

    def test_auth_header_name_and_scheme_overridable_via_env(self):
        """일부 사내 게이트웨이는 표준 Authorization: Bearer가 아니라 다른 헤더명/스킴을
        요구할 수 있다(Researcher-board에서 실제로 겪은 사례) - .env로 재배포 없이 바꾼다."""
        env = {"CONFLUENCE_AUTH_HEADER": "X-Auth-Token", "CONFLUENCE_AUTH_SCHEME": ""}
        with mock.patch.dict(os.environ, env):
            resp = _page_response("<p>본문</p>")
            with mock.patch("requests.get", return_value=resp) as fake_get:
                convert_confluence_url_to_docx("https://wiki.example.com/pages/123", token="my-pat")

        headers = fake_get.call_args.kwargs["headers"]
        self.assertEqual(headers["X-Auth-Token"], "my-pat")
        self.assertNotIn("Authorization", headers)

    def test_gateway_dep_ticket_and_data_classification_headers_sent_when_configured(self):
        env = {
            "CONFLUENCE_DEP_TICKET": "DEP-1234",
            "CONFLUENCE_DATA_CLASSIFICATION": "internal",
        }
        with mock.patch.dict(os.environ, env):
            resp = _page_response("<p>본문</p>")
            with mock.patch("requests.get", return_value=resp) as fake_get:
                convert_confluence_url_to_docx("https://wiki.example.com/pages/123", token="t")

        headers = fake_get.call_args.kwargs["headers"]
        self.assertEqual(headers["X-Dep-Ticket"], "DEP-1234")
        self.assertEqual(headers["X-Data-Classification"], "internal")

    def test_gateway_headers_not_sent_when_not_configured(self):
        resp = _page_response("<p>본문</p>")
        with mock.patch("requests.get", return_value=resp) as fake_get:
            convert_confluence_url_to_docx("https://wiki.example.com/pages/123", token="t")

        headers = fake_get.call_args.kwargs["headers"]
        self.assertNotIn("X-Dep-Ticket", headers)
        self.assertNotIn("X-Data-Classification", headers)

    def test_gateway_header_names_overridable_via_env(self):
        env = {
            "CONFLUENCE_DEP_TICKET": "DEP-1234",
            "CONFLUENCE_DEP_TICKET_HEADER": "X-My-Dep-Ticket",
        }
        with mock.patch.dict(os.environ, env):
            resp = _page_response("<p>본문</p>")
            with mock.patch("requests.get", return_value=resp) as fake_get:
                convert_confluence_url_to_docx("https://wiki.example.com/pages/123", token="t")

        headers = fake_get.call_args.kwargs["headers"]
        self.assertEqual(headers["X-My-Dep-Ticket"], "DEP-1234")
        self.assertNotIn("X-Dep-Ticket", headers)

    def test_no_proxy_defaults_to_using_environment_proxy(self):
        resp = _page_response("<p>본문</p>")
        with mock.patch("requests.get", return_value=resp) as fake_get:
            convert_confluence_url_to_docx("https://wiki.example.com/pages/123", token="t")

        self.assertIsNone(fake_get.call_args.kwargs["proxies"])

    def test_no_proxy_true_forces_direct_connection(self):
        """사내 프록시가 Confluence API 호출을 제대로 못 넘겨서 실패하면
        CONFLUENCE_NO_PROXY=true로 우회한다(mailer.py의 MAIL_API_NO_PROXY와 같은 패턴)."""
        with mock.patch.dict(os.environ, {"CONFLUENCE_NO_PROXY": "true"}):
            resp = _page_response("<p>본문</p>")
            with mock.patch("requests.get", return_value=resp) as fake_get:
                convert_confluence_url_to_docx("https://wiki.example.com/pages/123", token="t")

        self.assertEqual(fake_get.call_args.kwargs["proxies"], {"http": None, "https": None})

    def test_no_proxy_false_explicitly_uses_environment_proxy(self):
        with mock.patch.dict(os.environ, {"CONFLUENCE_NO_PROXY": "false"}):
            resp = _page_response("<p>본문</p>")
            with mock.patch("requests.get", return_value=resp) as fake_get:
                convert_confluence_url_to_docx("https://wiki.example.com/pages/123", token="t")

        self.assertIsNone(fake_get.call_args.kwargs["proxies"])

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


class DiagnoseConnectionTest(unittest.TestCase):
    """실제 연결 문제를 추적하는 진단 함수(python -m confluence_agenda.web.confluence_check)."""

    def test_reports_missing_confluence_url(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            lines = diagnose_connection()

        self.assertTrue(any("CONFLUENCE_URL 환경변수가 비어있습니다" in l for l in lines))

    def test_reports_missing_token(self):
        with mock.patch.dict(os.environ, {"CONFLUENCE_URL": "https://wiki.example.com"}, clear=True):
            lines = diagnose_connection()

        self.assertTrue(any("쓸 수 있는 토큰(PAT)이 없습니다" in l for l in lines))

    def test_reports_ca_bundle_path_that_does_not_exist(self):
        env = {
            "CONFLUENCE_URL": "https://wiki.example.com",
            "CONFLUENCE_CA_BUNDLE": "/host/only/missing.crt",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            lines = diagnose_connection("my-token")

        self.assertTrue(any("이 경로에 파일이 없음" in l for l in lines))

    def test_reports_success_with_resolved_owner(self):
        env = {"CONFLUENCE_URL": "https://wiki.example.com"}
        resp = _fake_response(json_data={"type": "known", "displayName": "권동혁", "username": "dh.kwon"})
        with mock.patch.dict(os.environ, env, clear=True), mock.patch("requests.get", return_value=resp):
            lines = diagnose_connection("my-token")

        self.assertTrue(any("연결 성공" in l and "권동혁" in l for l in lines))

    def test_reports_failure_with_exception_detail(self):
        env = {"CONFLUENCE_URL": "https://wiki.example.com"}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch(
            "requests.get", side_effect=OSError("boom")
        ):
            lines = diagnose_connection("my-token")

        self.assertTrue(any("요청 실패" in l for l in lines))

    def test_reports_exact_request_url(self):
        env = {"CONFLUENCE_URL": "https://wiki.example.com"}
        resp = _fake_response(json_data={"type": "known", "displayName": "권동혁"})
        with mock.patch.dict(os.environ, env, clear=True), mock.patch("requests.get", return_value=resp):
            lines = diagnose_connection("my-token")

        self.assertTrue(
            any("https://wiki.example.com/rest/api/user/current" in l for l in lines)
        )

    def test_reports_dep_ticket_unset_as_possible_cause_on_failure(self):
        env = {"CONFLUENCE_URL": "https://wiki.example.com"}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch(
            "requests.get", return_value=_fake_response(status_code=403)
        ):
            lines = diagnose_connection("my-token")

        self.assertTrue(any("✗ CONFLUENCE_DEP_TICKET 미설정" in l for l in lines))
        self.assertTrue(any("이게 원인일 가능성이 높다" in l for l in lines))

    def test_reports_dep_ticket_and_data_classification_when_set(self):
        env = {
            "CONFLUENCE_URL": "https://wiki.example.com",
            "CONFLUENCE_DEP_TICKET": "DEP-1234",
            "CONFLUENCE_DATA_CLASSIFICATION": "internal",
        }
        resp = _fake_response(json_data={"type": "known", "displayName": "권동혁"})
        with mock.patch.dict(os.environ, env, clear=True), mock.patch("requests.get", return_value=resp):
            lines = diagnose_connection("my-token")

        self.assertTrue(any("✓ CONFLUENCE_DEP_TICKET 설정됨" in l for l in lines))
        self.assertTrue(any("✓ CONFLUENCE_DATA_CLASSIFICATION 설정됨" in l for l in lines))


if __name__ == "__main__":
    unittest.main()
