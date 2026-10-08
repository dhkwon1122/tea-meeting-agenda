import base64
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from docx import Document as DocxDocument
from docx.oxml.ns import qn
from docx.shared import Mm, Pt

from confluence_agenda.builder import AgendaItem, build_agenda_page_body
from confluence_agenda.web import confluence_credentials, docx_export
from confluence_agenda.web.docx_export import (
    DocxExportUnavailable,
    convert_confluence_url_to_docx,
    create_agenda_page,
    diagnose_connection,
    docx_bytes_to_preview_html,
    is_feature_available,
    load_agenda_page_for_editing,
    parse_agenda_page_items,
    resolve_token,
    update_agenda_page,
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

    def test_ssl_error_wrong_version_number_hints_at_scheme_port_mismatch(self):
        """[SSL: WRONG_VERSION_NUMBER]는 인증서 신뢰 문제가 아니라 https://로
        접속했는데 그 주소/포트가 실제로는 http만 쓴다는 신호다(Researcher-board의
        최종 확정 원인도 게이트웨이 URL의 스킴 오타였음) - 인증서 관련 안내가
        아니라 스킴/포트를 확인하라는 안내가 나와야 한다."""
        import requests as requests_module

        with mock.patch(
            "requests.get",
            side_effect=requests_module.exceptions.SSLError(
                "SSLError(1, '[SSL: WRONG_VERSION_NUMBER] wrong version number (_ssl.c:1016)')"
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "스킴") as ctx:
                convert_confluence_url_to_docx("https://wiki.example.com/pages/123", token="t")

        message = str(ctx.exception)
        self.assertIn("TLS가 아닌 응답", message)
        self.assertNotIn("PEM", message)  # 인증서 신뢰 문제용 안내가 섞여 나오면 안 됨

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

    def test_on_progress_reports_each_major_step_in_order(self):
        resp = _page_response("<p>본문</p>")
        seen = []
        with mock.patch("requests.get", return_value=resp):
            convert_confluence_url_to_docx(
                "https://wiki.example.com/pages/123", token="t", on_progress=seen.append
            )

        self.assertEqual(
            seen,
            [
                "컨플루언스 페이지 조회 중...",
                "본문 분석 중...",
                "이미지 가져오는 중...",
                "연결된 하위 페이지 포함 중...",
                "Word 문서 생성 중...",
                "완료",
            ],
        )

    def test_works_without_on_progress_callback(self):
        resp = _page_response("<p>본문</p>")
        with mock.patch("requests.get", return_value=resp):
            data, filename = convert_confluence_url_to_docx(
                "https://wiki.example.com/pages/123", token="t"
            )

        self.assertTrue(data)
        self.assertEqual(filename, "테스트 문서.docx")


class CreateAgendaPageTest(unittest.TestCase):
    """create_agenda_page - 안건 페이지를 API로 직접 만들고, 안건별 상세
    페이지도 그 하위 페이지로 자동 생성하는 기능(지금까지는 "템플릿에서
    페이지 만들기" 버튼을 안건 수만큼 손으로 눌러야 했다)."""

    def setUp(self):
        self._env_patch = mock.patch.dict(
            os.environ,
            {"CONFLUENCE_URL": "https://wiki.example.com", "CONFLUENCE_API_TOKEN": "t"},
            clear=True,
        )
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)

    def test_raises_unavailable_when_url_not_configured(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(DocxExportUnavailable, "CONFLUENCE_URL"):
                create_agenda_page(
                    "https://wiki.example.com/pages/100", "회의록", [AgendaItem(title="안건1")]
                )

    def test_raises_unavailable_when_no_token_available(self):
        with mock.patch.dict(os.environ, {"CONFLUENCE_URL": "https://wiki.example.com"}, clear=True):
            with self.assertRaisesRegex(DocxExportUnavailable, "PAT"):
                create_agenda_page(
                    "https://wiki.example.com/pages/100", "회의록", [AgendaItem(title="안건1")]
                )

    def test_raises_when_no_items(self):
        with self.assertRaisesRegex(ValueError, "최소 1개"):
            create_agenda_page("https://wiki.example.com/pages/100", "회의록", [])

    def test_raises_when_parent_url_has_no_page_id(self):
        with self.assertRaisesRegex(RuntimeError, "페이지 ID"):
            create_agenda_page(
                "https://wiki.example.com/not-a-page", "회의록", [AgendaItem(title="안건1")]
            )

    def test_creates_agenda_page_and_detail_pages_as_its_children(self):
        items = [AgendaItem(title="예산안 승인"), AgendaItem(title="채용 계획")]

        def fake_get(url, **kwargs):
            if url.endswith("/rest/api/content/100"):
                return _fake_response(json_data={"id": "100", "space": {"key": "TEAM"}})
            raise AssertionError(f"unexpected GET: {url}")

        created_titles: list = []

        def fake_post(url, **kwargs):
            self.assertTrue(url.endswith("/rest/api/content"))
            payload = kwargs["json"]
            created_titles.append(payload["title"])
            if payload["title"] == "회의록":
                return _fake_response(
                    json_data={
                        "id": "200",
                        "title": "회의록",
                        "_links": {"webui": "/pages/viewpage.action?pageId=200"},
                    }
                )
            page_id = str(300 + len(created_titles))
            return _fake_response(
                json_data={
                    "id": page_id,
                    "title": payload["title"],
                    "_links": {"webui": f"/pages/viewpage.action?pageId={page_id}"},
                }
            )

        progress: list = []
        with mock.patch("requests.get", side_effect=fake_get), mock.patch(
            "requests.post", side_effect=fake_post
        ) as mocked_post:
            result = create_agenda_page(
                "https://wiki.example.com/pages/100",
                "회의록",
                items,
                on_progress=progress.append,
            )

        # 첫 POST는 안건(부모) 페이지, 그 뒤 두 번은 안건별 상세 페이지.
        self.assertEqual(mocked_post.call_count, 3)
        agenda_payload = mocked_post.call_args_list[0].kwargs["json"]
        self.assertEqual(agenda_payload["title"], "회의록")
        self.assertEqual(agenda_payload["space"], {"key": "TEAM"})
        self.assertEqual(agenda_payload["ancestors"], [{"id": "100"}])
        agenda_body = agenda_payload["body"]["storage"]["value"]
        # API로 상세 페이지까지 자동으로 만드니, 수동으로 누르던 버튼 섹션은
        # 더 이상 필요 없다 - 들어가 있으면 안 된다.
        self.assertNotIn("템플릿에서 페이지 만들기", agenda_body)
        self.assertIn("(첨부 1) 예산안 승인", agenda_body)
        self.assertIn("(첨부 2) 채용 계획", agenda_body)

        detail_payload_1 = mocked_post.call_args_list[1].kwargs["json"]
        self.assertEqual(detail_payload_1["title"], "(첨부 1) 예산안 승인")
        self.assertEqual(detail_payload_1["space"], {"key": "TEAM"})
        # 상세 페이지는 상위 페이지가 아니라 "새로 만든 안건 페이지"의 하위여야 한다.
        self.assertEqual(detail_payload_1["ancestors"], [{"id": "200"}])
        self.assertEqual(
            detail_payload_1["body"]["storage"]["value"], docx_export.DEFAULT_DETAIL_PAGE_BODY_HTML
        )

        self.assertEqual(result["page_id"], "200")
        self.assertEqual(result["url"], "https://wiki.example.com/pages/viewpage.action?pageId=200")
        self.assertEqual(len(result["detail_pages"]), 2)
        self.assertTrue(all(d["ok"] for d in result["detail_pages"]))
        self.assertEqual(result["detail_pages"][0]["title"], "(첨부 1) 예산안 승인")
        self.assertEqual(
            result["detail_pages"][0]["url"],
            "https://wiki.example.com/pages/viewpage.action?pageId=302",
        )
        self.assertIsNone(result["mirror_page"])
        self.assertTrue(progress)

    def test_one_failed_detail_page_does_not_block_the_others_or_the_agenda_page(self):
        items = [AgendaItem(title="안건1"), AgendaItem(title="안건2")]

        def fake_get(url, **kwargs):
            if url.endswith("/rest/api/content/100"):
                return _fake_response(json_data={"id": "100", "space": {"key": "TEAM"}})
            raise AssertionError(f"unexpected GET: {url}")

        def fake_post(url, **kwargs):
            payload = kwargs["json"]
            if payload["title"] == "회의록":
                return _fake_response(
                    json_data={"id": "200", "title": "회의록", "_links": {"webui": "/x/200"}}
                )
            if payload["title"] == "(첨부 1) 안건1":
                return _fake_response(status_code=400, text="이미 같은 제목의 페이지가 있습니다.")
            return _fake_response(
                json_data={"id": "301", "title": payload["title"], "_links": {"webui": "/x/301"}}
            )

        with mock.patch("requests.get", side_effect=fake_get), mock.patch(
            "requests.post", side_effect=fake_post
        ):
            result = create_agenda_page("https://wiki.example.com/pages/100", "회의록", items)

        self.assertFalse(result["detail_pages"][0]["ok"])
        self.assertIn("HTTP 400", result["detail_pages"][0]["error"])
        self.assertTrue(result["detail_pages"][1]["ok"])
        # 상세 페이지 하나가 실패해도 안건 페이지 자체는 이미 만들어져 있어야 한다.
        self.assertEqual(result["page_id"], "200")

    def test_mirror_parent_url_creates_a_page_that_includes_the_original(self):
        # 또 다른 상위 페이지 밑에 "미러" 페이지를 하나 더 만들어야 한다
        # (사용자 요청). 안건 페이지 전체를 복사하지 않고, include
        # 매크로로 원본 안건 페이지를 그대로 가리키기만 하면 된다(사용자
        # 확인: "미러링 페이지는 페이지 포함 매크로로 처음 만든 안건
        # 페이지를 가리키기만 하면 돼").
        items = [AgendaItem(title="안건1")]

        def fake_get(url, **kwargs):
            if url.endswith("/rest/api/content/100"):
                return _fake_response(json_data={"id": "100", "space": {"key": "TEAM"}})
            if url.endswith("/rest/api/content/900"):
                return _fake_response(json_data={"id": "900", "space": {"key": "OTHER"}})
            raise AssertionError(f"unexpected GET: {url}")

        def fake_post(url, **kwargs):
            payload = kwargs["json"]
            if payload["title"] == "회의록" and payload["ancestors"] == [{"id": "100"}]:
                return _fake_response(
                    json_data={"id": "200", "title": "회의록", "_links": {"webui": "/x/200"}}
                )
            if payload["title"] == "회의록" and payload["ancestors"] == [{"id": "900"}]:
                return _fake_response(
                    json_data={"id": "999", "title": "회의록", "_links": {"webui": "/x/999"}}
                )
            return _fake_response(
                json_data={"id": "301", "title": payload["title"], "_links": {"webui": "/x/301"}}
            )

        with mock.patch("requests.get", side_effect=fake_get), mock.patch(
            "requests.post", side_effect=fake_post
        ) as mocked_post:
            result = create_agenda_page(
                "https://wiki.example.com/pages/100",
                "회의록",
                items,
                mirror_parent_url="https://wiki.example.com/pages/900",
            )

        mirror_payload = next(
            call.kwargs["json"]
            for call in mocked_post.call_args_list
            if call.kwargs["json"]["ancestors"] == [{"id": "900"}]
        )
        self.assertEqual(mirror_payload["title"], "회의록")
        self.assertEqual(mirror_payload["space"], {"key": "OTHER"})
        mirror_body = mirror_payload["body"]["storage"]["value"]
        # 안건 페이지 본문을 복사하지 않고, include 매크로 하나로 원본
        # 안건 페이지만 가리켜야 한다 - 상세 페이지들이 아니라 안건
        # 페이지 자체를 참조해야 한다.
        self.assertIn('ac:name="include"', mirror_body)
        self.assertEqual(mirror_body.count('ac:name="include"'), 1)
        # 미러 페이지는 안건 페이지와 다른 스페이스(OTHER)에 있으므로, 안건
        # 페이지가 실제로 있는 스페이스(TEAM)를 명시해야 올바르게 찾아간다.
        self.assertIn('ri:space-key="TEAM" ri:content-title="회의록"', mirror_body)

        self.assertEqual(result["mirror_page"], {"ok": True, "title": "회의록", "url": "https://wiki.example.com/x/999"})

    def test_mirror_page_failure_does_not_affect_agenda_or_detail_pages(self):
        items = [AgendaItem(title="안건1")]

        def fake_get(url, **kwargs):
            if url.endswith("/rest/api/content/100"):
                return _fake_response(json_data={"id": "100", "space": {"key": "TEAM"}})
            if url.endswith("/rest/api/content/900"):
                return _fake_response(status_code=404)
            raise AssertionError(f"unexpected GET: {url}")

        def fake_post(url, **kwargs):
            payload = kwargs["json"]
            if payload["title"] == "회의록":
                return _fake_response(
                    json_data={"id": "200", "title": "회의록", "_links": {"webui": "/x/200"}}
                )
            return _fake_response(
                json_data={"id": "301", "title": payload["title"], "_links": {"webui": "/x/301"}}
            )

        with mock.patch("requests.get", side_effect=fake_get), mock.patch(
            "requests.post", side_effect=fake_post
        ):
            result = create_agenda_page(
                "https://wiki.example.com/pages/100",
                "회의록",
                items,
                mirror_parent_url="https://wiki.example.com/pages/900",
            )

        self.assertEqual(result["page_id"], "200")
        self.assertTrue(result["detail_pages"][0]["ok"])
        self.assertFalse(result["mirror_page"]["ok"])
        self.assertIn("찾을 수 없습니다", result["mirror_page"]["error"])


class ParseAgendaPageItemsTest(unittest.TestCase):
    """build_agenda_page_body가 만든 본문을 다시 읽어 안건 목록으로 되돌리는
    parse_agenda_page_items - 안건 추가/수정/삭제 화면이 현재 상태를
    보여주는 데 쓴다."""

    def test_round_trips_titles_and_preserves_existing_body(self):
        items = [
            AgendaItem(title="예산안 승인"),
            AgendaItem(title="채용 계획", body="이미 쓴 본문 내용"),
        ]
        body = build_agenda_page_body(items, include_setup_section=False)

        parsed = parse_agenda_page_items(body)

        self.assertEqual(set(parsed), {1, 2})
        self.assertEqual(parsed[1].title, "예산안 승인")
        self.assertEqual(parsed[2].title, "채용 계획")
        self.assertIn("이미 쓴 본문 내용", parsed[2].body)

    def test_serialized_body_has_no_redundant_namespace_declarations(self):
        # etree.tostring()으로 트리에서 떼어낸 조각을 그대로 쓰면 각 자식마다
        # xmlns:ac/xmlns:ri가 중복으로 붙는다 - 다시 Confluence로 보낼
        # 본문에 섞이면 안 된다.
        items = [AgendaItem(title="안건1", body="본문")]
        body = build_agenda_page_body(items, include_setup_section=False)

        parsed = parse_agenda_page_items(body)

        self.assertNotIn("xmlns:ac", parsed[1].body)
        self.assertNotIn("xmlns:ri", parsed[1].body)


class LoadAgendaPageForEditingTest(unittest.TestCase):
    def setUp(self):
        self._env_patch = mock.patch.dict(
            os.environ,
            {"CONFLUENCE_URL": "https://wiki.example.com", "CONFLUENCE_API_TOKEN": "t"},
            clear=True,
        )
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)

    def test_returns_ordered_items_with_titles(self):
        items = [AgendaItem(title="안건A"), AgendaItem(title="안건B")]
        body = build_agenda_page_body(items, include_setup_section=False)
        resp = _fake_response(
            json_data={
                "id": "200",
                "title": "회의록",
                "body": {"storage": {"value": body}},
            }
        )
        with mock.patch("requests.get", return_value=resp):
            result = load_agenda_page_for_editing("https://wiki.example.com/pages/200")

        self.assertEqual(result["page_id"], "200")
        self.assertEqual(result["title"], "회의록")
        self.assertEqual(
            result["items"], [{"index": 1, "title": "안건A"}, {"index": 2, "title": "안건B"}]
        )

    def test_raises_when_no_agenda_items_found(self):
        resp = _fake_response(
            json_data={"id": "200", "title": "빈 문서", "body": {"storage": {"value": "<p>내용 없음</p>"}}}
        )
        with mock.patch("requests.get", return_value=resp):
            with self.assertRaisesRegex(RuntimeError, "안건 항목을 찾을 수 없습니다"):
                load_agenda_page_for_editing("https://wiki.example.com/pages/200")

    def test_raises_unavailable_when_no_token(self):
        with mock.patch.dict(os.environ, {"CONFLUENCE_URL": "https://wiki.example.com"}, clear=True):
            with self.assertRaisesRegex(DocxExportUnavailable, "PAT"):
                load_agenda_page_for_editing("https://wiki.example.com/pages/200")


class UpdateAgendaPageTest(unittest.TestCase):
    """update_agenda_page - 안건 추가/수정/삭제 화면이 실제로 Confluence에
    반영하는 핵심 함수."""

    def setUp(self):
        self._env_patch = mock.patch.dict(
            os.environ,
            {"CONFLUENCE_URL": "https://wiki.example.com", "CONFLUENCE_API_TOKEN": "t"},
            clear=True,
        )
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)

        existing_items = [
            AgendaItem(title="안건A"),
            AgendaItem(title="안건B"),
            AgendaItem(title="안건C"),
        ]
        self.existing_body = build_agenda_page_body(existing_items, include_setup_section=False)

    def _page_resp(self, version=3):
        return _fake_response(
            json_data={
                "id": "200",
                "title": "회의록",
                "space": {"key": "TEAM"},
                "version": {"number": version},
                "body": {"storage": {"value": self.existing_body}},
            }
        )

    def _children_resp(self):
        return _fake_response(
            json_data={
                "results": [
                    {"id": "301", "title": "(첨부 1) 안건A", "version": {"number": 1}},
                    {"id": "302", "title": "(첨부 2) 안건B", "version": {"number": 1}},
                    {"id": "303", "title": "(첨부 3) 안건C", "version": {"number": 1}},
                ]
            }
        )

    def test_renumbers_detail_pages_when_an_earlier_item_is_removed(self):
        # 안건B(2번)를 지우면, 안건C는 제목이 안 바뀌었어도 번호가
        # 3->2로 밀리므로 그 상세 페이지 제목도 "(첨부 2) 안건C"로
        # 바뀌어야 한다.
        page_resp = self._page_resp()
        children_resp = self._children_resp()

        def fake_get(url, **kwargs):
            if url.endswith("/rest/api/content/200"):
                return page_resp
            if url.endswith("/rest/api/content/200/child/page"):
                return children_resp
            raise AssertionError(f"unexpected GET: {url}")

        put_calls = []

        def fake_put(url, **kwargs):
            payload = kwargs["json"]
            put_calls.append(payload)
            return _fake_response(
                json_data={**payload, "_links": {"webui": f"/x/{payload['id']}"}}
            )

        delete_calls = []

        def fake_delete(url, **kwargs):
            delete_calls.append(url)
            return _fake_response(status_code=204)

        with mock.patch("requests.get", side_effect=fake_get), mock.patch(
            "requests.put", side_effect=fake_put
        ), mock.patch("requests.delete", side_effect=fake_delete):
            result = update_agenda_page(
                "https://wiki.example.com/pages/200",
                items=[(1, "안건A"), (3, "안건C")],
                deleted_orig_indexes=[2],
            )

        agenda_put = next(p for p in put_calls if p["id"] == "200")
        self.assertEqual(agenda_put["version"], {"number": 4})
        self.assertIn("(첨부 1) 안건A", agenda_put["body"]["storage"]["value"])
        self.assertIn("(첨부 2) 안건C", agenda_put["body"]["storage"]["value"])
        self.assertNotIn("안건B", agenda_put["body"]["storage"]["value"])

        rename_put = next(p for p in put_calls if p["id"] == "303")
        self.assertEqual(rename_put["title"], "(첨부 2) 안건C")
        self.assertEqual(rename_put["version"], {"number": 2})

        self.assertEqual(delete_calls, ["https://wiki.example.com/rest/api/content/302"])
        self.assertEqual(result["trashed_detail_pages"], [{"title": "(첨부 2) 안건B", "ok": True}])
        self.assertTrue(all(d["ok"] for d in result["detail_pages"]))

    def test_new_item_gets_a_detail_page_with_default_body(self):
        page_resp = self._page_resp()
        children_resp = self._children_resp()

        def fake_get(url, **kwargs):
            if url.endswith("/rest/api/content/200"):
                return page_resp
            if url.endswith("/rest/api/content/200/child/page"):
                return children_resp
            raise AssertionError(f"unexpected GET: {url}")

        def fake_put(url, **kwargs):
            payload = kwargs["json"]
            return _fake_response(json_data={**payload, "_links": {"webui": f"/x/{payload['id']}"}})

        created = []

        def fake_post(url, **kwargs):
            payload = kwargs["json"]
            created.append(payload)
            return _fake_response(
                json_data={"id": "304", "title": payload["title"], "_links": {"webui": "/x/304"}}
            )

        with mock.patch("requests.get", side_effect=fake_get), mock.patch(
            "requests.put", side_effect=fake_put
        ), mock.patch("requests.post", side_effect=fake_post):
            result = update_agenda_page(
                "https://wiki.example.com/pages/200",
                items=[(1, "안건A"), (2, "안건B"), (3, "안건C"), (None, "새 안건")],
            )

        self.assertEqual(len(created), 1)
        self.assertEqual(created[0]["title"], "(첨부 4) 새 안건")
        self.assertEqual(
            created[0]["body"]["storage"]["value"], docx_export.DEFAULT_DETAIL_PAGE_BODY_HTML
        )
        self.assertEqual(created[0]["ancestors"], [{"id": "200"}])
        self.assertTrue(result["detail_pages"][-1]["ok"])

    def test_renaming_a_kept_item_renames_its_detail_page(self):
        page_resp = self._page_resp()
        children_resp = self._children_resp()

        def fake_get(url, **kwargs):
            if url.endswith("/rest/api/content/200"):
                return page_resp
            if url.endswith("/rest/api/content/200/child/page"):
                return children_resp
            raise AssertionError(f"unexpected GET: {url}")

        put_calls = []

        def fake_put(url, **kwargs):
            payload = kwargs["json"]
            put_calls.append(payload)
            return _fake_response(json_data={**payload, "_links": {"webui": f"/x/{payload['id']}"}})

        with mock.patch("requests.get", side_effect=fake_get), mock.patch(
            "requests.put", side_effect=fake_put
        ):
            update_agenda_page(
                "https://wiki.example.com/pages/200",
                items=[(1, "안건A-수정됨"), (2, "안건B"), (3, "안건C")],
            )

        rename_put = next(p for p in put_calls if p["id"] == "301")
        self.assertEqual(rename_put["title"], "(첨부 1) 안건A-수정됨")

    def test_kept_item_with_unchanged_title_and_position_is_not_renamed(self):
        page_resp = self._page_resp()
        children_resp = self._children_resp()

        def fake_get(url, **kwargs):
            if url.endswith("/rest/api/content/200"):
                return page_resp
            if url.endswith("/rest/api/content/200/child/page"):
                return children_resp
            raise AssertionError(f"unexpected GET: {url}")

        put_calls = []

        def fake_put(url, **kwargs):
            payload = kwargs["json"]
            put_calls.append(payload["id"])
            return _fake_response(json_data={**payload, "_links": {"webui": f"/x/{payload['id']}"}})

        with mock.patch("requests.get", side_effect=fake_get), mock.patch(
            "requests.put", side_effect=fake_put
        ):
            update_agenda_page(
                "https://wiki.example.com/pages/200",
                items=[(1, "안건A"), (2, "안건B"), (3, "안건C")],
            )

        # 안건 페이지 본문만 갱신되고, 번호/제목이 안 바뀐 상세 페이지들은
        # 불필요하게 PUT하지 않아야 한다.
        self.assertEqual(put_calls, ["200"])

    def test_one_detail_page_rename_failure_does_not_block_the_others(self):
        page_resp = self._page_resp()
        children_resp = self._children_resp()

        def fake_get(url, **kwargs):
            if url.endswith("/rest/api/content/200"):
                return page_resp
            if url.endswith("/rest/api/content/200/child/page"):
                return children_resp
            raise AssertionError(f"unexpected GET: {url}")

        def fake_put(url, **kwargs):
            payload = kwargs["json"]
            if payload["id"] == "301":
                return _fake_response(status_code=409, text="버전 충돌")
            return _fake_response(json_data={**payload, "_links": {"webui": f"/x/{payload['id']}"}})

        with mock.patch("requests.get", side_effect=fake_get), mock.patch(
            "requests.put", side_effect=fake_put
        ):
            result = update_agenda_page(
                "https://wiki.example.com/pages/200",
                items=[(1, "안건A-수정"), (2, "안건B-수정")],
                deleted_orig_indexes=[3],
            )

        by_title = {d["title"]: d for d in result["detail_pages"]}
        self.assertFalse(by_title["(첨부 1) 안건A-수정"]["ok"])
        self.assertIn("HTTP 409", by_title["(첨부 1) 안건A-수정"]["error"])
        self.assertTrue(by_title["(첨부 2) 안건B-수정"]["ok"])

    def test_raises_when_no_items_given(self):
        with self.assertRaisesRegex(ValueError, "최소 1개"):
            update_agenda_page("https://wiki.example.com/pages/200", items=[])


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
        # 제목 글자 앞뒤에 공백이 하나씩 붙는다(밑줄이 조금 더 길어 보이게).
        data = self._convert("<p>본문</p>", title="회의록")
        self.assertIn(" 회의록 ", _docx_paragraph_texts(data))

    def test_headings_and_paragraph_are_preserved(self):
        # 구조적 변환(제목 접어넣기)으로 h2는 1단계 항목이 되어 들여쓰기가
        # 적용되지만, 제목에도 평문단에도 "1."/"□" 같은 말머리를 새로
        # 붙이지는 않는다(사용자 피드백: "그냥 안 붙여도 되겠어").
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
        # 제목 없이 바로 나오는 목록은 1단계("1.")부터 시작한다(doc2report와 동일).
        data = self._convert("<ul><li>첫째</li><li>둘째</li></ul>")
        texts = _docx_paragraph_texts(data)
        self.assertIn("1.\t첫째", texts)
        self.assertIn("2.\t둘째", texts)

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

    def test_table_cell_with_multiple_paragraphs_keeps_each_on_its_own_line(self):
        # Confluence 표 칸은 흔히 <p>가 여러 개다 - 예전에는 전부 한 문단으로
        # 몰아 넣어서 줄바꿈 없이 붙어 버렸다(실제 변환에서 발견된 버그).
        storage = "<table><tbody><tr><td><p>첫째 줄</p><p>둘째 줄</p></td></tr></tbody></table>"
        data = self._convert(storage)
        document = DocxDocument(io.BytesIO(data))
        cell = document.tables[0].cell(0, 0)
        self.assertEqual([p.text for p in cell.paragraphs], ["첫째 줄", "둘째 줄"])

    def test_table_cell_with_list_puts_each_item_on_its_own_line(self):
        storage = "<table><tbody><tr><td><ul><li>항목1</li><li>항목2</li></ul></td></tr></tbody></table>"
        data = self._convert(storage)
        document = DocxDocument(io.BytesIO(data))
        cell = document.tables[0].cell(0, 0)
        self.assertEqual([p.text for p in cell.paragraphs], ["항목1", "항목2"])

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

    def test_rowspan_merges_cells_vertically_and_keeps_columns_aligned(self):
        # 세로로 합쳐진 칸 - 합쳐진 칸 아래 행들은 그 칸이 "없는" 것처럼
        # <td>를 하나 덜 쓰는데(HTML 표의 정상적인 표현), 렌더러가 이걸
        # 추적하지 않으면 그 아래 행들의 칸이 한 칸씩 왼쪽으로 밀려 보인다.
        storage = (
            "<table><tbody>"
            '<tr><td rowspan="2">카테고리A</td><td>항목1</td></tr>'
            "<tr><td>항목2</td></tr>"
            "<tr><td>카테고리B</td><td>항목3</td></tr>"
            "</tbody></table>"
        )
        data = self._convert(storage)
        document = DocxDocument(io.BytesIO(data))
        table = document.tables[0]
        self.assertEqual(
            [[table.cell(r, c).text for c in range(2)] for r in range(3)],
            [["카테고리A", "항목1"], ["카테고리A", "항목2"], ["카테고리B", "항목3"]],
        )
        # (0,0)과 (1,0)이 같은 병합된 셀을 가리켜야 한다.
        self.assertEqual(table.cell(0, 0)._tc, table.cell(1, 0)._tc)
        # 세 번째 행의 칸은 병합과 무관한 별개의 셀이어야 한다.
        self.assertNotEqual(table.cell(0, 0)._tc, table.cell(2, 0)._tc)

    def test_rowspan_and_colspan_combine_into_one_rectangular_merge(self):
        storage = (
            "<table><tbody>"
            '<tr><td rowspan="2" colspan="2">큰 칸</td><td>c</td></tr>'
            "<tr><td>d</td></tr>"
            "</tbody></table>"
        )
        data = self._convert(storage)
        document = DocxDocument(io.BytesIO(data))
        table = document.tables[0]
        big_cell = table.cell(0, 0)._tc
        self.assertEqual(table.cell(0, 1)._tc, big_cell)
        self.assertEqual(table.cell(1, 0)._tc, big_cell)
        self.assertEqual(table.cell(1, 1)._tc, big_cell)
        self.assertEqual(table.cell(0, 2).text, "c")
        self.assertEqual(table.cell(1, 2).text, "d")

    def test_inline_macro_without_rich_text_body_does_not_leak_parameter_text(self):
        # 이 프로젝트의 builder.py가 실제로 쓰는 패턴 - 문단 중간에 anchor
        # 매크로를 끼워 넣는다. rich-text-body가 없는 인라인 매크로의
        # ac:parameter 값("제목1" 같은 북마크 이름)이 본문 텍스트로 섞여
        # 나오면 안 된다(실제로 이렇게 새던 버그).
        storage = (
            '<p>안건1 <ac:structured-macro ac:name="anchor" '
            'xmlns:ac="http://www.atlassian.com/schema/confluence/4/ac/">'
            '<ac:parameter ac:name="">제목1</ac:parameter>'
            "</ac:structured-macro> 뒷부분</p>"
        )
        data = self._convert(storage)
        texts = _docx_paragraph_texts(data)
        self.assertIn("안건1  뒷부분", texts)
        self.assertNotIn("안건1 제목1 뒷부분", texts)
        self.assertFalse(any("제목1" in t for t in texts))

    def test_block_level_anchor_macro_produces_no_placeholder(self):
        storage = (
            '<ac:structured-macro ac:name="anchor" '
            'xmlns:ac="http://www.atlassian.com/schema/confluence/4/ac/">'
            '<ac:parameter ac:name="">첨부1</ac:parameter>'
            "</ac:structured-macro>"
        )
        data = self._convert(storage)
        texts = _docx_paragraph_texts(data)
        self.assertFalse(any("지원하지 않습니다" in t for t in texts))
        self.assertFalse(any("첨부1" in t for t in texts))

    def test_macro_rich_text_body_is_unwrapped(self):
        storage = (
            '<ac:structured-macro ac:name="info" xmlns:ac="http://www.atlassian.com/schema/confluence/4/ac/">'
            "<ac:rich-text-body><p>안내 문구입니다.</p></ac:rich-text-body>"
            "</ac:structured-macro>"
        )
        data = self._convert(storage)
        self.assertIn("안내 문구입니다.", _docx_paragraph_texts(data))

    def test_unsupported_macro_without_rich_text_body_becomes_placeholder_note(self):
        # children/include/excerpt-include는 이제 실제로 펼쳐지므로(아래 LinkedPages
        # 테스트들 참고), 여기서는 그 외의(렌더링 미지원) 매크로로 확인한다.
        storage = (
            '<ac:structured-macro ac:name="jira" '
            'xmlns:ac="http://www.atlassian.com/schema/confluence/4/ac/"/>'
        )
        data = self._convert(storage)
        texts = _docx_paragraph_texts(data)
        self.assertTrue(any("jira" in t and "지원하지 않습니다" in t for t in texts))

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


class StructuralFoldTest(unittest.TestCase):
    """제목을 번호 체계(1./□/-)로 접어넣는 구조적 변환과 표 캡션/주석 자동
    첨부(_fold_headings_into_levels/_tag_table_captions_and_notes)를 확인한다."""

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

    def test_sequential_headings_show_plain_text_without_auto_markers(self):
        # 제목에도 "1."/"□" 같은 말머리를 새로 붙이지 않는다(사용자 피드백:
        # "그냥 안 붙여도 되겠어") - 제목은 그냥 제목 글자 그대로 보인다.
        data = self._convert("<h2>추진 배경</h2><h2>세부 계획</h2>")
        texts = _docx_paragraph_texts(data)
        self.assertIn("추진 배경", texts)
        self.assertIn("세부 계획", texts)

    def test_heading_levels_show_increasing_marker_depth_without_indent(self):
        # 단계가 깊어져도 왼쪽 들여쓰기는 주지 않는다(사용자 피드백:
        # "왼쪽으로 너무 많이 들여쓰기 되는 것들도 있더라") - 단계 구분은
        # 말머리 모양만으로 한다.
        data = self._convert("<h2>대분류</h2><h3>중분류</h3><h4>소분류</h4>")
        document = DocxDocument(io.BytesIO(data))
        by_text = {p.text: p for p in document.paragraphs}
        self.assertIn("대분류", by_text)
        self.assertIn("중분류", by_text)
        self.assertIn("소분류", by_text)
        for text in ("대분류", "중분류", "소분류"):
            self.assertIsNone(by_text[text].paragraph_format.left_indent)

    def test_heading_with_existing_marker_text_is_kept_verbatim_not_doubled(self):
        # 원문에 이미 "1." 같은 말머리가 타이핑돼 있으면 그대로 쓰고 새로
        # 붙이지 않는다(keep_leading_markers) - "1.\t1. 추진 배경"처럼 겹치면 안 된다.
        data = self._convert("<h2>1. 추진 배경</h2>")
        texts = _docx_paragraph_texts(data)
        self.assertIn("1.\t추진 배경", texts)
        self.assertNotIn("1.\t1. 추진 배경", texts)

    def test_hyphen_prefixed_plain_text_is_not_mistaken_for_a_marker(self):
        # "-"로 시작하는 평범한 문장(강조용 대시 등)이 목록 말머리로
        # 오인식되면 안 된다(사용자 피드백: "-나 점 문자가 말머릿기호로
        # 인식되는 거 같은데 그렇게 안 되게 할 수 있어?") - 그대로 평문단
        # 텍스트여야 한다("-\t" 같은 탭이 끼어들면 안 됨).
        data = self._convert("<p>- 전사 매출은 전년 대비 10% 증가했습니다.</p>")
        texts = _docx_paragraph_texts(data)
        self.assertIn("- 전사 매출은 전년 대비 10% 증가했습니다.", texts)

    def test_dot_prefixed_plain_text_is_not_mistaken_for_a_marker(self):
        data = self._convert("<p>· 참고 자료는 첨부 파일을 확인하세요.</p>")
        texts = _docx_paragraph_texts(data)
        self.assertIn("· 참고 자료는 첨부 파일을 확인하세요.", texts)

    def test_box_symbol_prefixed_text_is_still_recognized_as_an_existing_marker(self):
        # "□"는 평문에 흔히 나오지 않아 오탐 위험이 낮으므로, 기존 말머리
        # 인식 대상에서 빼지 않는다(회귀 방지).
        data = self._convert("<p>□ 세부 항목입니다.</p>")
        texts = _docx_paragraph_texts(data)
        self.assertIn("□\t세부 항목입니다.", texts)

    def test_nested_list_depth_follows_heading_then_restarts_sibling_counter(self):
        data = self._convert("<ul><li>A<ul><li>A-1</li></ul></li><li>B</li></ul>")
        texts = _docx_paragraph_texts(data)
        self.assertIn("1.\tA", texts)
        self.assertIn("□\tA-1", texts)
        self.assertIn("2.\tB", texts)

    def test_table_bracket_caption_is_not_a_numbered_item(self):
        storage = "<p>【사업현황】</p><table><tbody><tr><td>내용</td></tr></tbody></table>"
        data = self._convert(storage)
        texts = _docx_paragraph_texts(data)
        self.assertIn("【사업현황】", texts)
        self.assertNotIn("1.\t【사업현황】", texts)

    def test_table_note_paragraph_is_not_a_numbered_item(self):
        storage = (
            "<table><tbody><tr><td>내용</td></tr></tbody></table>"
            "<p>* 측정 기준은 내부 지표입니다.</p>"
        )
        data = self._convert(storage)
        texts = _docx_paragraph_texts(data)
        self.assertIn("* 측정 기준은 내부 지표입니다.", texts)
        self.assertNotIn("1.\t* 측정 기준은 내부 지표입니다.", texts)

    def test_table_note_blockquote_is_not_a_numbered_item(self):
        storage = (
            "<table><tbody><tr><td>내용</td></tr></tbody></table>"
            "<blockquote><p>측정 기준은 내부 지표입니다.</p></blockquote>"
        )
        data = self._convert(storage)
        texts = _docx_paragraph_texts(data)
        self.assertIn("측정 기준은 내부 지표입니다.", texts)
        self.assertNotIn("1.\t측정 기준은 내부 지표입니다.", texts)

    def test_paragraph_under_heading_without_marker_stays_without_indent(self):
        # 말머리("□")는 새로 만들어 붙이지 않고(auto_markers=false와 같은
        # 취지), 왼쪽 들여쓰기도 주지 않는다(사용자 피드백: "왼쪽으로 너무
        # 많이 들여쓰기 되는 것들도 있더라").
        data = self._convert("<h2>소제목</h2><p>본문</p><p>본문2</p>")
        document = DocxDocument(io.BytesIO(data))
        body_paragraphs = [p for p in document.paragraphs if p.text in ("본문", "본문2")]
        self.assertEqual(len(body_paragraphs), 2)
        for paragraph in body_paragraphs:
            self.assertIsNone(paragraph.paragraph_format.left_indent)

    def test_paragraph_before_any_heading_with_no_marker_stays_plain(self):
        data = self._convert("<p>제목도 말머리도 없는 문단</p>")
        texts = _docx_paragraph_texts(data)
        self.assertIn("제목도 말머리도 없는 문단", texts)

    def test_blank_spacer_paragraph_under_heading_does_not_become_empty_bullet(self):
        # builder.py가 안건 사이 여백으로 실제로 쓰는 "<p><br/></p>" - 제목
        # 아래라고 해서 "-\t"처럼 빈 말머리가 붙은 항목이 되면 안 된다.
        data = self._convert("<h2>소제목</h2><p><br/></p><p>본문</p>")
        texts = _docx_paragraph_texts(data)
        self.assertFalse(any(t.strip() in ("-", "□", "·") for t in texts))
        self.assertIn("본문", texts)

    def test_h3_only_document_normalizes_marker_depth_to_match_h2_baseline(self):
        # h2 없이 h3부터 시작하는 문서(normalize_levels) - h3는 원래 depth1
        # ("□" 단계)이지만 문서 전체의 최저 단계가 h3뿐이면 그 최저 단계를
        # 0("1." 단계)으로 민다. 들여쓰기는 이제 안 쓰지만, 그 아래 목록이
        # 받는 말머리 단계는 여전히 이 정규화를 따라야 한다("□" 대신 "-"가
        # 되면 h2부터 시작하는 문서와 체계가 어긋난다).
        data = self._convert("<h3>첫 항목</h3><ul><li>하위 항목</li></ul>")
        texts = _docx_paragraph_texts(data)
        self.assertIn("□\t하위 항목", texts)
        self.assertNotIn("-\t하위 항목", texts)

    def test_heading_with_marker_nested_inside_formatting_tags_is_detected(self):
        # 이 프로젝트의 builder.py가 실제로 만드는 안건 제목 구조 -
        # "<h3><strong><span>1. 안건1 …" - 말머리 "1."이 h3.text가 아니라
        # 서식 태그 두 겹 안의 span.text에 있다. normalize_levels로 h3가
        # depth0이 되면서 원문의 "1."과 들여쓰기가 맞아떨어져야 하고, 말머리가
        # 겹쳐 "1.\t1. 안건1"처럼 두 번 나오면 안 된다.
        storage = (
            '<h3 style="text-align: left;"><strong><span>'
            "1. 안건1 "
            "</span></strong></h3>"
        )
        data = self._convert(storage)
        texts = _docx_paragraph_texts(data)
        self.assertIn("1.\t안건1 ", texts)
        self.assertNotIn("1.\t1. 안건1 ", texts)


class LinkedPagesExpansionTest(unittest.TestCase):
    """include/excerpt-include/children 매크로가 실제로 다른 페이지를 더 불러와
    그 자리에 펼쳐지는지 확인한다(_resolve_linked_pages)."""

    def setUp(self):
        self._env_patch = mock.patch.dict(
            os.environ, {"CONFLUENCE_URL": "https://wiki.example.com"}, clear=True
        )
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)

    def test_include_macro_expands_into_subpage_with_title_prefix_stripped(self):
        root_storage = (
            '<ac:structured-macro ac:name="include" '
            'xmlns:ac="http://www.atlassian.com/schema/confluence/4/ac/" '
            'xmlns:ri="http://www.atlassian.com/schema/confluence/4/ri/">'
            '<ac:parameter ac:name=""><ac:link>'
            '<ri:page ri:content-title="(첨부 1) 안건1"/>'
            "</ac:link></ac:parameter>"
            "</ac:structured-macro>"
        )
        root_resp = _page_response(root_storage, title="회의록")
        sub_resp = _fake_response(
            json_data={
                "results": [
                    {
                        "id": "999",
                        "title": "(첨부 1) 안건1",
                        "body": {"storage": {"value": "<p>첨부 내용입니다.</p>"}},
                    }
                ]
            }
        )

        def fake_get(url, **kwargs):
            if url.endswith("/rest/api/content/123"):
                return root_resp
            if url.endswith("/rest/api/content"):
                return sub_resp
            raise AssertionError(f"unexpected url: {url}")

        with mock.patch("requests.get", side_effect=fake_get):
            data, _ = convert_confluence_url_to_docx("https://wiki.example.com/pages/123", token="t")

        texts = _docx_paragraph_texts(data)
        # "(첨부 1)" 접두어가 제거된 제목(쪽 제목이라 앞뒤에 공백이 하나씩 붙음).
        self.assertIn(" 안건1 ", texts)
        self.assertNotIn(" (첨부 1) 안건1 ", texts)
        self.assertIn("첨부 내용입니다.", texts)

    def test_include_macro_wrapped_in_lone_p_still_renders_table_and_line_breaks(self):
        # builder.py의 실제 패턴 - include 매크로가 "<p>{macro}</p>"처럼 <p>
        # 안에 홀로 들어있다. 매크로 바로 옆에만 펼친 내용을 끼워 넣으면 그
        # 내용이 <p> "안에" 들어가 버려서, 렌더링할 때 그 <p> 전체가 한
        # 문단으로 뭉개진다(표가 안 그려지고 줄바꿈도 사라짐 - 실사용에서
        # 발견된 버그). <p> 전체를 대신해야 바깥 블록 내용으로 제대로 펼쳐진다.
        root_storage = (
            "<h2>원본 섹션</h2>"
            '<p><ac:structured-macro ac:name="include" '
            'xmlns:ac="http://www.atlassian.com/schema/confluence/4/ac/" '
            'xmlns:ri="http://www.atlassian.com/schema/confluence/4/ri/">'
            '<ac:parameter ac:name=""><ac:link>'
            '<ri:page ri:content-title="첨부 페이지"/>'
            "</ac:link></ac:parameter>"
            "</ac:structured-macro></p>"
        )
        sub_storage = (
            "<table><tbody>"
            '<tr><td rowspan="2">카테고리A</td><td><p>첫째 줄</p><p>둘째 줄</p></td></tr>'
            "<tr><td>항목2</td></tr>"
            "</tbody></table>"
        )
        root_resp = _page_response(root_storage, title="회의록")
        sub_resp = _fake_response(
            json_data={
                "results": [
                    {"id": "999", "title": "첨부 페이지", "body": {"storage": {"value": sub_storage}}}
                ]
            }
        )

        def fake_get(url, **kwargs):
            if url.endswith("/rest/api/content/123"):
                return root_resp
            if url.endswith("/rest/api/content"):
                return sub_resp
            raise AssertionError(f"unexpected url: {url}")

        with mock.patch("requests.get", side_effect=fake_get):
            data, _ = convert_confluence_url_to_docx("https://wiki.example.com/pages/123", token="t")

        document = DocxDocument(io.BytesIO(data))
        self.assertEqual(len(document.tables), 1)
        table = document.tables[0]
        self.assertEqual(
            [[table.cell(r, c).text for c in range(2)] for r in range(2)],
            [["카테고리A", "첫째 줄\n둘째 줄"], ["카테고리A", "항목2"]],
        )
        self.assertEqual([p.text for p in table.cell(0, 1).paragraphs], ["첫째 줄", "둘째 줄"])

    def test_numbering_restarts_at_each_linked_page(self):
        # doc2report의 render/docx_writer.py가 제목(여기서는 연결된 페이지의 제목)을
        # 만나면 번호 카운터를 다시 1부터 센다 - 원본 페이지의 목록 항목에서
        # "1."까지 쓴 뒤 연결된 페이지로 넘어가면 그 페이지의 첫 목록 항목도
        # "1."부터 다시 시작해야 한다(제목 자신은 말머리를 안 보여주므로
        # 목록 항목으로 확인한다).
        root_storage = (
            "<ul><li>원본 항목</li></ul>"
            '<ac:structured-macro ac:name="include" '
            'xmlns:ac="http://www.atlassian.com/schema/confluence/4/ac/" '
            'xmlns:ri="http://www.atlassian.com/schema/confluence/4/ri/">'
            '<ac:parameter ac:name=""><ac:link>'
            '<ri:page ri:content-title="첨부 페이지"/>'
            "</ac:link></ac:parameter>"
            "</ac:structured-macro>"
        )
        root_resp = _page_response(root_storage, title="회의록")
        sub_resp = _fake_response(
            json_data={
                "results": [
                    {
                        "id": "999",
                        "title": "첨부 페이지",
                        "body": {"storage": {"value": "<ul><li>첨부 항목</li></ul>"}},
                    }
                ]
            }
        )

        def fake_get(url, **kwargs):
            if url.endswith("/rest/api/content/123"):
                return root_resp
            if url.endswith("/rest/api/content"):
                return sub_resp
            raise AssertionError(f"unexpected url: {url}")

        with mock.patch("requests.get", side_effect=fake_get):
            data, _ = convert_confluence_url_to_docx("https://wiki.example.com/pages/123", token="t")

        texts = _docx_paragraph_texts(data)
        self.assertIn("1.\t원본 항목", texts)
        self.assertIn("1.\t첨부 항목", texts)  # "2."가 아니라 새 쪽에서 다시 "1."

    def test_children_macro_expands_each_child_page(self):
        root_storage = (
            '<ac:structured-macro ac:name="children" '
            'xmlns:ac="http://www.atlassian.com/schema/confluence/4/ac/"/>'
        )
        root_resp = _page_response(root_storage, title="회의록")
        children_resp = _fake_response(
            json_data={
                "results": [
                    {"id": "201", "title": "안건A", "body": {"storage": {"value": "<p>A 내용</p>"}}},
                    {"id": "202", "title": "안건B", "body": {"storage": {"value": "<p>B 내용</p>"}}},
                ]
            }
        )

        def fake_get(url, **kwargs):
            if url.endswith("/rest/api/content/123"):
                return root_resp
            if url.endswith("/rest/api/content/123/child/page"):
                return children_resp
            raise AssertionError(f"unexpected url: {url}")

        with mock.patch("requests.get", side_effect=fake_get):
            data, _ = convert_confluence_url_to_docx("https://wiki.example.com/pages/123", token="t")

        texts = _docx_paragraph_texts(data)
        # 쪽 제목이라 앞뒤에 공백이 하나씩 붙음.
        self.assertIn(" 안건A ", texts)
        self.assertIn("A 내용", texts)
        self.assertIn(" 안건B ", texts)
        self.assertIn("B 내용", texts)

    def test_include_macro_pointing_back_to_root_page_is_skipped_as_cycle(self):
        root_storage = (
            '<ac:structured-macro ac:name="include" '
            'xmlns:ac="http://www.atlassian.com/schema/confluence/4/ac/" '
            'xmlns:ri="http://www.atlassian.com/schema/confluence/4/ri/">'
            '<ac:parameter ac:name=""><ac:link>'
            '<ri:page ri:content-title="회의록"/>'
            "</ac:link></ac:parameter>"
            "</ac:structured-macro>"
        )
        root_resp = _page_response(root_storage, title="회의록")
        self_resp = _fake_response(
            json_data={
                "results": [
                    {"id": "123", "title": "회의록", "body": {"storage": {"value": "<p>본문</p>"}}}
                ]
            }
        )

        def fake_get(url, **kwargs):
            if url.endswith("/rest/api/content/123"):
                return root_resp
            if url.endswith("/rest/api/content"):
                return self_resp
            raise AssertionError(f"unexpected url: {url}")

        with mock.patch("requests.get", side_effect=fake_get):
            data, _ = convert_confluence_url_to_docx("https://wiki.example.com/pages/123", token="t")

        texts = _docx_paragraph_texts(data)
        self.assertTrue(any("순환" in t for t in texts))

    def test_include_macro_target_not_found_becomes_placeholder(self):
        root_storage = (
            '<ac:structured-macro ac:name="include" '
            'xmlns:ac="http://www.atlassian.com/schema/confluence/4/ac/" '
            'xmlns:ri="http://www.atlassian.com/schema/confluence/4/ri/">'
            '<ac:parameter ac:name=""><ac:link>'
            '<ri:page ri:content-title="존재하지 않는 페이지"/>'
            "</ac:link></ac:parameter>"
            "</ac:structured-macro>"
        )
        root_resp = _page_response(root_storage, title="회의록")
        empty_resp = _fake_response(json_data={"results": []})

        def fake_get(url, **kwargs):
            if url.endswith("/rest/api/content/123"):
                return root_resp
            if url.endswith("/rest/api/content"):
                return empty_resp
            raise AssertionError(f"unexpected url: {url}")

        with mock.patch("requests.get", side_effect=fake_get):
            data, _ = convert_confluence_url_to_docx("https://wiki.example.com/pages/123", token="t")

        texts = _docx_paragraph_texts(data)
        self.assertTrue(any("찾을 수 없습니다" in t for t in texts))


class DocumentStyleConfigurationTest(unittest.TestCase):
    """doc2report의 profiles/confluence.yaml(+default.yaml)에서 옮긴 글꼴/크기/여백
    값이 실제로 적용되는지 확인한다(_configure_document_styles)."""

    def setUp(self):
        self._env_patch = mock.patch.dict(
            os.environ, {"CONFLUENCE_URL": "https://wiki.example.com"}, clear=True
        )
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)

    def _convert_document(self) -> DocxDocument:
        resp = _page_response("<h1>소제목</h1><p>본문</p>", title="회의록")
        with mock.patch("requests.get", return_value=resp):
            data, _ = convert_confluence_url_to_docx("https://wiki.example.com/pages/123", token="t")
        return DocxDocument(io.BytesIO(data))

    def test_normal_style_matches_confluence_profile(self):
        document = self._convert_document()
        normal = document.styles["Normal"]
        self.assertEqual(normal.font.name, "맑은 고딕")
        self.assertEqual(normal.font.size, Pt(12))
        east_asia = normal.font._element.get_or_add_rPr().get_or_add_rFonts().get(qn("w:eastAsia"))
        self.assertEqual(east_asia, "맑은 고딕")

    def test_title_style_is_centered_bold_underlined_18pt(self):
        document = self._convert_document()
        title = document.styles["Title"]
        self.assertEqual(title.font.size, Pt(18))
        self.assertTrue(title.font.bold)
        self.assertTrue(title.font.underline)

    def test_title_style_has_no_bottom_border_and_no_theme_font_override(self):
        # python-docx 기본 템플릿의 Title 스타일은 (1) 문단 밑에 가로줄
        # 테두리가 있어서 우리가 지정한 글자 밑줄과 겹쳐 "긴 밑줄"처럼
        # 보였고, (2) 글꼴을 테마 참조로도 갖고 있어서 우리가 지정한
        # 맑은 고딕이 무시되고 테마 기본 글꼴로 보였다(사용자 보고 두 건).
        document = self._convert_document()
        title_element = document.styles["Title"].element
        self.assertIsNone(title_element.find(qn("w:pPr") + "/" + qn("w:pBdr")))
        r_fonts = title_element.find(qn("w:rPr") + "/" + qn("w:rFonts"))
        self.assertIsNotNone(r_fonts)
        self.assertIsNone(r_fonts.get(qn("w:asciiTheme")))
        self.assertIsNone(r_fonts.get(qn("w:eastAsiaTheme")))
        self.assertEqual(r_fonts.get(qn("w:ascii")), "맑은 고딕")
        self.assertEqual(r_fonts.get(qn("w:eastAsia")), "맑은 고딕")

    def test_document_title_text_is_padded_with_spaces(self):
        # 제목 글자 앞뒤에 공백을 하나씩 줘서 글자 밑줄이 조금 더 길어
        # 보이게 한다(사용자 요청).
        document = self._convert_document()
        self.assertEqual(document.paragraphs[0].text, " 회의록 ")

    def test_heading_styles_use_profile_sizes(self):
        document = self._convert_document()
        self.assertEqual(document.styles["Heading 1"].font.size, Pt(15))
        self.assertEqual(document.styles["Heading 2"].font.size, Pt(14))
        self.assertEqual(document.styles["Heading 3"].font.size, Pt(14))
        self.assertTrue(document.styles["Heading 1"].font.bold)

    def test_margins_are_20mm(self):
        document = self._convert_document()
        section = document.sections[0]
        # 여백은 OOXML에 1/20pt(dxa) 단위로 저장돼 Mm 왕복에 아주 작은 양자화 오차가
        # 생길 수 있어(20mm == 56.69...pt), 정확한 EMU 일치가 아니라 근사치로 확인한다.
        self.assertAlmostEqual(section.top_margin, Mm(20), delta=200)
        self.assertAlmostEqual(section.left_margin, Mm(20), delta=200)

    def test_compatibility_mode_is_bumped_to_modern_word(self):
        # python-docx 기본 템플릿은 "호환 모드"(워드 2010, compatibilityMode=14)
        # 상태인데, 이 호환 모드에서는 워드 데스크톱이 표 열 폭 같은 레이아웃을
        # 옛 버전 방식으로 다시 계산해 버리는 경우가 있다(실사용 보고: 표마다
        # 글자 양이 달라도 폭이 항상 똑같이 나옴 - 표 하나의 문제가 아니라
        # 문서 전체에 걸친 호환성 문제로 보임).
        document = self._convert_document()
        settings = document.settings.element
        compat_values = {
            s.get(qn("w:name")): s.get(qn("w:val")) for s in settings.iter(qn("w:compatSetting"))
        }
        self.assertEqual(compat_values.get("compatibilityMode"), "15")

    def test_table_header_row_is_bold_shaded_and_10pt(self):
        storage = (
            "<table><tbody>"
            "<tr><th>헤더1</th><th>헤더2</th></tr>"
            "<tr><td>값1</td><td>값2</td></tr>"
            "</tbody></table>"
        )
        resp = _page_response(storage, title="회의록")
        with mock.patch("requests.get", return_value=resp):
            data, _ = convert_confluence_url_to_docx("https://wiki.example.com/pages/123", token="t")
        document = DocxDocument(io.BytesIO(data))
        table = document.tables[0]
        header_run = table.cell(0, 0).paragraphs[0].runs[0]
        self.assertEqual(header_run.font.size, Pt(10))
        self.assertTrue(header_run.bold)
        shading = table.cell(0, 0)._tc.get_or_add_tcPr().find(qn("w:shd"))
        self.assertIsNotNone(shading)
        self.assertEqual(shading.get(qn("w:fill")), "F2F2F2")

        data_run = table.cell(1, 0).paragraphs[0].runs[0]
        self.assertEqual(data_run.font.size, Pt(10))
        self.assertFalse(data_run.bold)

    def test_table_column_widths_are_proportional_to_content_not_equal(self):
        # 전에는 python-docx 기본값대로 모든 열이 똑같은 폭이었다 - 열마다
        # 내용 길이가 크게 다르면 어색해 보인다는 요청으로, 글자 양에
        # 비례해서 폭을 다르게 준다.
        storage = (
            "<table><tbody>"
            "<tr><th>번호</th><th>담당자 이름 및 소속 부서 설명이 긴 칸</th><th>비고</th></tr>"
            "<tr><td>1</td><td>김철수(기획전략팀, 사내 인프라 담당)</td><td>-</td></tr>"
            "</tbody></table>"
        )
        resp = _page_response(storage, title="회의록")
        with mock.patch("requests.get", return_value=resp):
            data, _ = convert_confluence_url_to_docx("https://wiki.example.com/pages/123", token="t")
        document = DocxDocument(io.BytesIO(data))
        table = document.tables[0]
        widths = [col.width for col in table.columns]
        self.assertFalse(table.autofit)
        # 표 전체 폭(w:tblW)도 열 폭 합과 같은 "고정값"으로 맞춰져 있어야
        # 한다 - 이게 "auto"로 남아 있으면 워드가 열 폭을 다시 균등하게
        # 그려 버렸다(실사용에서 발견된 버그: 미리보기는 맞는데 실제 워드
        # 파일만 전부 같은 폭으로 나옴).
        tbl_w = table._tbl.tblPr.find(qn("w:tblW"))
        self.assertEqual(tbl_w.get(qn("w:type")), "dxa")
        self.assertAlmostEqual(int(tbl_w.get(qn("w:w"))), sum(widths) / 635, delta=5)
        # 가운데 열(내용이 훨씨 길다)이 양쪽보다 뚜렷하게 넓어야 한다.
        self.assertGreater(widths[1], widths[0] * 2)
        self.assertGreater(widths[1], widths[2] * 2)
        # 짧은 열도 거의 0으로 눌리지는 않아야 한다(최소 가중치 바닥값).
        self.assertGreater(widths[0], Mm(5))
        self.assertGreater(widths[2], Mm(5))

    def test_table_colgroup_widths_take_priority_over_text_length(self):
        # 사용자가 Confluence에서 표 열 폭을 직접 조정하면 <colgroup><col
        # style="width: Npx"/></colgroup>에 그 값이 저장된다 - 글자 양으로
        # 추정하는 것보다 이 값을 그대로 쓰는 게 더 정확하다는 요청
        # ("원본 표에 설정된 폭이 있다면 그걸 비율로 먼저 치환"). 아래 표는
        # 글자 수로 보면 가운데 열이 훨씬 넓어야 하지만, colgroup은 정반대
        # 비율(1:4:1)로 지정해 뒀으므로 colgroup 쪽이 이겨야 한다.
        storage = (
            "<table>"
            "<colgroup>"
            '<col style="width: 60.0px;" />'
            '<col style="width: 240.0px;" />'
            '<col style="width: 60.0px;" />'
            "</colgroup>"
            "<tbody>"
            "<tr><th>번호</th><th>담당자 이름 및 소속 부서 설명이 긴 칸</th><th>비고</th></tr>"
            "<tr><td>1</td><td>짧음</td><td>-</td></tr>"
            "</tbody></table>"
        )
        resp = _page_response(storage, title="회의록")
        with mock.patch("requests.get", return_value=resp):
            data, _ = convert_confluence_url_to_docx("https://wiki.example.com/pages/123", token="t")
        document = DocxDocument(io.BytesIO(data))
        table = document.tables[0]
        widths = [col.width for col in table.columns]
        # colgroup 비율(1:4:1)대로 가운데 열이 양쪽 각각의 약 4배여야 한다.
        self.assertAlmostEqual(widths[1] / widths[0], 4.0, delta=0.3)
        self.assertAlmostEqual(widths[1] / widths[2], 4.0, delta=0.3)
        self.assertAlmostEqual(widths[0], widths[2], delta=Mm(1))

    def test_table_colgroup_widths_ignored_when_incomplete(self):
        # colgroup이 일부 열만 폭을 주거나 아예 없으면(흔히 있는 일) 기존
        # 글자 수 기반 계산으로 넘어가야 한다 - 섣불리 일부 정보만으로
        # 비율을 정하면 더 틀어질 수 있다.
        storage = (
            "<table>"
            "<colgroup>"
            '<col style="width: 60.0px;" />'
            "<col />"
            "<col />"
            "</colgroup>"
            "<tbody>"
            "<tr><th>번호</th><th>담당자 이름 및 소속 부서 설명이 긴 칸</th><th>비고</th></tr>"
            "<tr><td>1</td><td>김철수(기획전략팀, 사내 인프라 담당)</td><td>-</td></tr>"
            "</tbody></table>"
        )
        resp = _page_response(storage, title="회의록")
        with mock.patch("requests.get", return_value=resp):
            data, _ = convert_confluence_url_to_docx("https://wiki.example.com/pages/123", token="t")
        document = DocxDocument(io.BytesIO(data))
        table = document.tables[0]
        widths = [col.width for col in table.columns]
        self.assertGreater(widths[1], widths[0] * 2)
        self.assertGreater(widths[1], widths[2] * 2)

    def test_table_column_width_is_applied_to_every_row_cell_not_just_grid(self):
        # python-docx의 Column.width setter는 w:tblGrid/w:gridCol만 바꾸고
        # 각 행 칸의 w:tcW는 그대로 둔다 - 워드는 gridCol보다 각 칸의 w:tcW를
        # 우선해서 렌더링하는 것으로 보여서, gridCol만 바꾸면 미리보기는
        # 맞는데 실제 워드 파일은 여전히 모든 열이 같은 폭으로 나왔다(실사용
        # 보고). 모든 행의 모든 칸에도 같은 폭이 직접 들어가 있어야 한다.
        storage = (
            "<table><tbody>"
            "<tr><th>번호</th><th>담당자 이름 및 소속 부서 설명이 긴 칸</th><th>비고</th></tr>"
            "<tr><td>1</td><td>김철수(기획전략팀, 사내 인프라 담당)</td><td>-</td></tr>"
            "<tr><td>2</td><td>이영희(개발팀, 백엔드 담당)</td><td>-</td></tr>"
            "</tbody></table>"
        )
        resp = _page_response(storage, title="회의록")
        with mock.patch("requests.get", return_value=resp):
            data, _ = convert_confluence_url_to_docx("https://wiki.example.com/pages/123", token="t")
        document = DocxDocument(io.BytesIO(data))
        table = document.tables[0]
        grid_widths = [col.width for col in table.columns]
        for row in table.rows:
            for col_index, cell in enumerate(row.cells):
                tc_w = cell._tc.tcPr.find(qn("w:tcW"))
                self.assertIsNotNone(tc_w)
                self.assertEqual(tc_w.get(qn("w:type")), "dxa")
                self.assertAlmostEqual(
                    int(tc_w.get(qn("w:w"))), int(grid_widths[col_index] / 635), delta=2
                )

    def test_table_column_with_rowspan_cell_still_gets_measured_from_other_rows(self):
        # colspan>1인 칸은 폭 계산에서 건너뛰지만, 그 열의 "다른" 행에 있는
        # (colspan==1) 칸으로는 여전히 폭을 가늠할 수 있어야 한다.
        storage = (
            "<table><tbody>"
            '<tr><td colspan="2">합쳐진 칸(짧음)</td></tr>'
            "<tr><td>짧음</td><td>이 열은 내용이 훨씬 더 길게 들어 있는 칸입니다</td></tr>"
            "</tbody></table>"
        )
        resp = _page_response(storage, title="회의록")
        with mock.patch("requests.get", return_value=resp):
            data, _ = convert_confluence_url_to_docx("https://wiki.example.com/pages/123", token="t")
        document = DocxDocument(io.BytesIO(data))
        table = document.tables[0]
        widths = [col.width for col in table.columns]
        self.assertGreater(widths[1], widths[0] * 2)


_PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


class ImageEmbeddingTest(unittest.TestCase):
    """ac:image가 실제 첨부파일 내용으로 그려지는지 확인한다
    (_fetch_images_for_page/_render_image) - 전에는 파일명만 보여주는
    자리표시자였다."""

    def setUp(self):
        self._env_patch = mock.patch.dict(
            os.environ, {"CONFLUENCE_URL": "https://wiki.example.com"}, clear=True
        )
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)

    def test_image_with_matching_attachment_is_embedded_not_placeholder(self):
        root_storage = (
            "<h2>스크린샷</h2>"
            '<ac:image xmlns:ac="http://www.atlassian.com/schema/confluence/4/ac/" '
            'xmlns:ri="http://www.atlassian.com/schema/confluence/4/ri/">'
            '<ri:attachment ri:filename="shot.png"/>'
            "</ac:image>"
        )
        root_resp = _page_response(root_storage, title="회의록")
        attachments_resp = _fake_response(
            json_data={
                "results": [
                    {"title": "shot.png", "_links": {"download": "/download/attachments/123/shot.png"}}
                ]
            }
        )
        download_resp = _fake_response(text="")
        download_resp.content = _PNG_1PX

        def fake_get(url, **kwargs):
            if url.endswith("/rest/api/content/123"):
                return root_resp
            if url.endswith("/rest/api/content/123/child/attachment"):
                return attachments_resp
            if url.endswith("/download/attachments/123/shot.png"):
                return download_resp
            raise AssertionError(f"unexpected url: {url}")

        with mock.patch("requests.get", side_effect=fake_get):
            data, _ = convert_confluence_url_to_docx("https://wiki.example.com/pages/123", token="t")

        document = DocxDocument(io.BytesIO(data))
        self.assertEqual(len(document.inline_shapes), 1)
        self.assertFalse(any("shot.png" in t for t in _docx_paragraph_texts(data)))

    def test_image_wrapped_in_p_tag_is_still_embedded_not_silently_dropped(self):
        # Confluence는 이미지를 흔히 <p><ac:image>...</ac:image></p>처럼 문단
        # "안"에 끼워 넣는다 - _add_inline_runs가 ac:image를 모르는 태그로
        # 보고 건너뛰면 자리표시자조차 없이 통째로 사라졌다(실사용 보고:
        # "이미지 파일명 그런 자리표시자도 안 보여").
        root_storage = (
            "<h2>스크린샷</h2>"
            '<p><ac:image xmlns:ac="http://www.atlassian.com/schema/confluence/4/ac/" '
            'xmlns:ri="http://www.atlassian.com/schema/confluence/4/ri/">'
            '<ri:attachment ri:filename="shot.png"/>'
            "</ac:image></p>"
        )
        root_resp = _page_response(root_storage, title="회의록")
        attachments_resp = _fake_response(
            json_data={
                "results": [
                    {"title": "shot.png", "_links": {"download": "/download/attachments/123/shot.png"}}
                ]
            }
        )
        download_resp = _fake_response(text="")
        download_resp.content = _PNG_1PX

        def fake_get(url, **kwargs):
            if url.endswith("/rest/api/content/123"):
                return root_resp
            if url.endswith("/rest/api/content/123/child/attachment"):
                return attachments_resp
            if url.endswith("/download/attachments/123/shot.png"):
                return download_resp
            raise AssertionError(f"unexpected url: {url}")

        with mock.patch("requests.get", side_effect=fake_get):
            data, _ = convert_confluence_url_to_docx("https://wiki.example.com/pages/123", token="t")

        document = DocxDocument(io.BytesIO(data))
        self.assertEqual(len(document.inline_shapes), 1)

    def test_image_download_tries_direct_attachments_url_before_listing_attachments(self):
        # 사내망에서 "GET /download/attachments/{id}/{filename}"로 직접
        # 받으면 된다고 확인됨 - 첨부파일 목록 API를 거치지 않고도 이 주소로
        # 바로 받아야 한다(목록 API 쪽은 게이트웨이에서 막혀 404가 나는
        # 경우가 있었음).
        root_storage = (
            '<ac:image xmlns:ac="http://www.atlassian.com/schema/confluence/4/ac/" '
            'xmlns:ri="http://www.atlassian.com/schema/confluence/4/ri/">'
            '<ri:attachment ri:filename="shot.png"/>'
            "</ac:image>"
        )
        root_resp = _page_response(root_storage, title="회의록")
        download_resp = _fake_response(text="")
        download_resp.content = _PNG_1PX

        def fake_get(url, **kwargs):
            if url.endswith("/rest/api/content/123"):
                return root_resp
            if url.endswith("/download/attachments/123/shot.png"):
                return download_resp
            raise AssertionError(f"unexpected url: {url}")

        with mock.patch("requests.get", side_effect=fake_get):
            data, _ = convert_confluence_url_to_docx("https://wiki.example.com/pages/123", token="t")

        document = DocxDocument(io.BytesIO(data))
        self.assertEqual(len(document.inline_shapes), 1)

    def test_image_without_matching_attachment_falls_back_to_placeholder(self):
        root_storage = (
            '<ac:image xmlns:ac="http://www.atlassian.com/schema/confluence/4/ac/" '
            'xmlns:ri="http://www.atlassian.com/schema/confluence/4/ri/">'
            '<ri:attachment ri:filename="missing.png"/>'
            "</ac:image>"
        )
        root_resp = _page_response(root_storage, title="회의록")
        attachments_resp = _fake_response(json_data={"results": []})

        def fake_get(url, **kwargs):
            if url.endswith("/rest/api/content/123"):
                return root_resp
            if url.endswith("/download/attachments/123/missing.png"):
                return _fake_response(status_code=404, text="")
            if url.endswith("/rest/api/content/123/child/attachment"):
                return attachments_resp
            raise AssertionError(f"unexpected url: {url}")

        with mock.patch("requests.get", side_effect=fake_get):
            data, _ = convert_confluence_url_to_docx("https://wiki.example.com/pages/123", token="t")

        document = DocxDocument(io.BytesIO(data))
        self.assertEqual(len(document.inline_shapes), 0)
        self.assertTrue(any("missing.png" in t for t in _docx_paragraph_texts(data)))

    def test_placeholder_explains_why_when_attachment_not_in_list(self):
        # docx 파일을 직접 못 보내주는 환경에서도 변환된 문서 자체에서 원인을
        # 바로 읽을 수 있어야 한다 - 자리표시자에 실패 이유를 적는다.
        root_storage = (
            '<ac:image xmlns:ac="http://www.atlassian.com/schema/confluence/4/ac/" '
            'xmlns:ri="http://www.atlassian.com/schema/confluence/4/ri/">'
            '<ri:attachment ri:filename="missing.png"/>'
            "</ac:image>"
        )
        root_resp = _page_response(root_storage, title="회의록")
        attachments_resp = _fake_response(
            json_data={"results": [{"title": "other.png", "_links": {"download": "/x"}}]}
        )

        def fake_get(url, **kwargs):
            if url.endswith("/rest/api/content/123"):
                return root_resp
            if url.endswith("/download/attachments/123/missing.png"):
                return _fake_response(status_code=404, text="")
            if url.endswith("/rest/api/content/123/child/attachment"):
                return attachments_resp
            raise AssertionError(f"unexpected url: {url}")

        with mock.patch("requests.get", side_effect=fake_get):
            data, _ = convert_confluence_url_to_docx("https://wiki.example.com/pages/123", token="t")

        texts = _docx_paragraph_texts(data)
        self.assertTrue(any("missing.png" in t and "목록에 없음" in t and "other.png" in t for t in texts))

    def test_placeholder_explains_attachment_list_fetch_failure(self):
        root_storage = (
            '<ac:image xmlns:ac="http://www.atlassian.com/schema/confluence/4/ac/" '
            'xmlns:ri="http://www.atlassian.com/schema/confluence/4/ri/">'
            '<ri:attachment ri:filename="shot.png"/>'
            "</ac:image>"
        )
        root_resp = _page_response(root_storage, title="회의록")

        def fake_get(url, **kwargs):
            if url.endswith("/rest/api/content/123"):
                return root_resp
            if url.endswith("/download/attachments/123/shot.png"):
                return _fake_response(status_code=404, text="")
            if url.endswith("/rest/api/content/123/child/attachment"):
                return _fake_response(status_code=403, text="forbidden")
            raise AssertionError(f"unexpected url: {url}")

        with mock.patch("requests.get", side_effect=fake_get):
            data, _ = convert_confluence_url_to_docx("https://wiki.example.com/pages/123", token="t")

        texts = _docx_paragraph_texts(data)
        self.assertTrue(any("shot.png" in t and "HTTP 403" in t for t in texts))

    def test_image_inside_linked_page_is_fetched_from_that_pages_own_attachments(self):
        # 연결된 페이지의 이미지는 그 페이지 "자신"의 첨부파일 목록에서
        # 찾아야 한다(원본 페이지의 첨부파일 목록과 혼동하면 안 됨).
        root_storage = (
            '<p><ac:structured-macro ac:name="include" '
            'xmlns:ac="http://www.atlassian.com/schema/confluence/4/ac/" '
            'xmlns:ri="http://www.atlassian.com/schema/confluence/4/ri/">'
            '<ac:parameter ac:name=""><ac:link>'
            '<ri:page ri:content-title="첨부 페이지"/>'
            "</ac:link></ac:parameter>"
            "</ac:structured-macro></p>"
        )
        sub_storage = (
            '<ac:image xmlns:ac="http://www.atlassian.com/schema/confluence/4/ac/" '
            'xmlns:ri="http://www.atlassian.com/schema/confluence/4/ri/">'
            '<ri:attachment ri:filename="sub.png"/>'
            "</ac:image>"
        )
        root_resp = _page_response(root_storage, title="회의록")
        sub_resp = _fake_response(
            json_data={
                "results": [
                    {"id": "999", "title": "첨부 페이지", "body": {"storage": {"value": sub_storage}}}
                ]
            }
        )
        root_attachments_resp = _fake_response(json_data={"results": []})
        sub_attachments_resp = _fake_response(
            json_data={
                "results": [
                    {"title": "sub.png", "_links": {"download": "/download/attachments/999/sub.png"}}
                ]
            }
        )
        download_resp = _fake_response(text="")
        download_resp.content = _PNG_1PX

        def fake_get(url, **kwargs):
            if url.endswith("/rest/api/content/123"):
                return root_resp
            if url.endswith("/rest/api/content/123/child/attachment"):
                return root_attachments_resp
            if url.endswith("/rest/api/content"):
                return sub_resp
            if url.endswith("/rest/api/content/999/child/attachment"):
                return sub_attachments_resp
            if url.endswith("/download/attachments/999/sub.png"):
                return download_resp
            raise AssertionError(f"unexpected url: {url}")

        with mock.patch("requests.get", side_effect=fake_get):
            data, _ = convert_confluence_url_to_docx("https://wiki.example.com/pages/123", token="t")

        document = DocxDocument(io.BytesIO(data))
        self.assertEqual(len(document.inline_shapes), 1)


class DocxBytesToPreviewHtmlTest(unittest.TestCase):
    """다운로드 없이 보는 미리보기(docx_bytes_to_preview_html)를 확인한다 -
    이미 만들어진 .docx를 다시 읽어서 HTML로 바꾸므로, 다운로드할 파일과
    내용이 같은지를 검증하는 셈이다."""

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

    def test_title_and_heading_and_text_all_appear(self):
        data = self._convert("<h2>소제목</h2><p>본문</p>", title="회의록")
        preview = docx_bytes_to_preview_html(data)
        self.assertIn("회의록", preview)
        self.assertIn("소제목", preview)
        self.assertIn("본문", preview)

    def test_bold_run_becomes_strong_tag(self):
        data = self._convert("<p>일반 <strong>굵게</strong> 텍스트</p>")
        preview = docx_bytes_to_preview_html(data)
        self.assertIn("<strong>굵게</strong>", preview)

    def test_table_cells_become_html_table(self):
        storage = "<table><tbody><tr><th>이름</th></tr><tr><td>권동혁</td></tr></tbody></table>"
        data = self._convert(storage)
        preview = docx_bytes_to_preview_html(data)
        self.assertIn("<table", preview)
        self.assertIn("권동혁", preview)

    def test_html_special_characters_are_escaped(self):
        data = self._convert("<p>&lt;script&gt;가 아니라 글자 그대로</p>")
        preview = docx_bytes_to_preview_html(data)
        self.assertNotIn("<script>", preview)
        self.assertIn("&lt;script&gt;", preview)


if __name__ == "__main__":
    unittest.main()
