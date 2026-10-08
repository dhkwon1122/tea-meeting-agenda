import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import unquote

from confluence_agenda.builder import build_email_subject
from confluence_agenda.mailer import MailConfigError
from confluence_agenda.web import auth, confluence_credentials
from confluence_agenda.web.app import app, main
from confluence_agenda.web.docx_export import DocxExportUnavailable

_TEST_CONTACTS = [
    {"name": "테스트유저1", "email": "user1@example.com"},
    {"name": "테스트유저2", "email": "user2@example.com"},
]


class WebAppTest(unittest.TestCase):
    def setUp(self):
        app.testing = True
        self.client = app.test_client()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        contacts_path = Path(self._tmpdir.name) / "contacts.json"
        contacts_path.write_text(json.dumps(_TEST_CONTACTS, ensure_ascii=False), encoding="utf-8")
        self._contacts_env = {"CONTACTS_FILE": str(contacts_path)}

    def test_get_index_renders_form(self):
        resp = self.client.get("/agenda")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'name="titles"', resp.data)

    def test_post_without_titles_shows_error(self):
        resp = self.client.post("/agenda", data={"titles": "\n\n", "action": "generate"})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("안건 제목을 한 줄에 하나씩 입력해주세요".encode(), resp.data)

    def test_post_generates_source_for_multiple_blank_line_separated_titles(self):
        resp = self.client.post(
            "/agenda", data={"titles": "예산안 승인\n\n채용 계획\n", "action": "generate"}
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.data.decode("utf-8")
        self.assertIn("1. 예산안 승인", body)
        self.assertIn("2. 채용 계획", body)
        self.assertIn("ac:name=&#34;ui-expand&#34;", body)

    def test_post_includes_already_written_body_instead_of_placeholder(self):
        # 예산안 승인은 본문을 이미 써서 붙여넣었고, 채용 계획은 본문 없이
        # 제목만 입력했다 - 전자는 그 내용이 그대로 들어가고, 후자만 여전히
        # 자리표시자로 채워져야 한다.
        resp = self.client.post(
            "/agenda",
            data={
                "titles": "예산안 승인\n부서별 예산안을 검토하고 승인합니다.\n\n채용 계획",
                "action": "generate",
            },
        )
        body = resp.data.decode("utf-8")
        self.assertIn("부서별 예산안을 검토하고 승인합니다.", body)
        self.assertEqual(body.count("가나다라마바사"), 3)

    def test_post_shows_macro_connection_diagram(self):
        resp = self.client.post(
            "/agenda", data={"titles": "예산안 승인\n\n채용 계획", "action": "generate"}
        )
        body = resp.data.decode("utf-8")
        self.assertIn("매크로 연결 구조", body)
        self.assertEqual(body.count('class="diagram-item"'), 2)
        self.assertIn("anchor: 제목1", body)
        self.assertIn("anchor: 첨부2", body)

    def test_no_contacts_file_means_no_preset_chips(self):
        # 실제 워크스테이션에 contacts.json이 있어도 이 테스트가 영향받지
        # 않도록, 존재하지 않는 경로를 명시적으로 가리킨다.
        missing_path = Path(self._tmpdir.name) / "does-not-exist.json"
        env = {
            "MAIL_API_TOKEN": "t",
            "MAIL_API_SYSTEM_ID": "s",
            "MAIL_API_USER_ID": "u",
            "CONTACTS_FILE": str(missing_path),
        }
        with mock.patch.dict("os.environ", env, clear=True):
            resp = self.client.get("/agenda")
        self.assertNotIn(b'name="preset_to"', resp.data)

    def test_mail_configured_with_contacts_file_shows_preset_chips(self):
        env = {
            "MAIL_API_TOKEN": "t",
            "MAIL_API_SYSTEM_ID": "s",
            "MAIL_API_USER_ID": "u",
            **self._contacts_env,
        }
        with mock.patch.dict("os.environ", env, clear=True):
            resp = self.client.get("/agenda")
        body = resp.data.decode("utf-8")
        self.assertIn("테스트유저1", body)
        self.assertIn("테스트유저2", body)
        self.assertIn('value="user1@example.com"', body)

    def test_send_mail_without_config_shows_error(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            resp = self.client.post(
                "/agenda",
                data={
                    "titles": "예산안 승인",
                    "action": "send_mail",
                    "extra_to": "someone@example.com",
                },
            )
        self.assertIn("메일 API 환경변수가 설정되지 않아".encode(), resp.data)

    def test_send_mail_without_recipient_shows_error(self):
        env = {"MAIL_API_TOKEN": "t", "MAIL_API_SYSTEM_ID": "s", "MAIL_API_USER_ID": "u"}
        with mock.patch.dict("os.environ", env, clear=True):
            resp = self.client.post(
                "/agenda", data={"titles": "예산안 승인", "action": "send_mail", "extra_to": ""}
            )
        self.assertIn("받는 사람을 한 명 이상 선택하거나 입력해주세요".encode(), resp.data)

    def test_send_mail_to_extra_address_only(self):
        env = {"MAIL_API_TOKEN": "t", "MAIL_API_SYSTEM_ID": "s", "MAIL_API_USER_ID": "u"}
        with mock.patch.dict("os.environ", env, clear=True), mock.patch(
            "confluence_agenda.web.app.send_report_email"
        ) as fake_send:
            resp = self.client.post(
                "/agenda",
                data={
                    "titles": "예산안 승인\n\n채용 계획",
                    "action": "send_mail",
                    "extra_to": "someone@example.com",
                },
            )

        fake_send.assert_called_once()
        args, kwargs = fake_send.call_args
        self.assertEqual(args[0], ["someone@example.com"])
        # 제목은 안건 내용과 무관하게 고정 날짜 템플릿을 쓴다.
        self.assertEqual(kwargs["subject"], build_email_subject())
        # 메일 본문에는 (제목만이 아니라) 생성된 전체 소스가 그대로 들어가야 한다.
        self.assertIn("1. 예산안 승인", kwargs["body_html"])
        self.assertIn("2. 채용 계획", kwargs["body_html"])
        self.assertIn("ui-expand", kwargs["body_html"])
        self.assertIn("메일을 보냈습니다: someone@example.com".encode(), resp.data)

    def test_send_mail_uses_custom_subject_when_provided(self):
        env = {"MAIL_API_TOKEN": "t", "MAIL_API_SYSTEM_ID": "s", "MAIL_API_USER_ID": "u"}
        with mock.patch.dict("os.environ", env, clear=True), mock.patch(
            "confluence_agenda.web.app.send_report_email"
        ) as fake_send:
            resp = self.client.post(
                "/agenda",
                data={
                    "titles": "예산안 승인",
                    "action": "send_mail",
                    "extra_to": "someone@example.com",
                    "subject": "직접 수정한 제목",
                },
            )

        self.assertEqual(resp.status_code, 200)
        _, kwargs = fake_send.call_args
        self.assertEqual(kwargs["subject"], "직접 수정한 제목")

    def test_get_index_prefills_fixed_default_subject(self):
        env = {"MAIL_API_TOKEN": "t", "MAIL_API_SYSTEM_ID": "s", "MAIL_API_USER_ID": "u"}
        with mock.patch.dict("os.environ", env, clear=True):
            resp = self.client.get("/agenda")
        self.assertIn(f'value="{build_email_subject()}"'.encode(), resp.data)

    def test_send_mail_to_preset_chips_and_extra_address_combined(self):
        env = {
            "MAIL_API_TOKEN": "t",
            "MAIL_API_SYSTEM_ID": "s",
            "MAIL_API_USER_ID": "u",
            **self._contacts_env,
        }
        with mock.patch.dict("os.environ", env, clear=True), mock.patch(
            "confluence_agenda.web.app.send_report_email"
        ) as fake_send:
            resp = self.client.post(
                "/agenda",
                data={
                    "titles": "예산안 승인",
                    "action": "send_mail",
                    "preset_to": ["user1@example.com", "user2@example.com"],
                    "extra_to": "other@example.com, other@example.com",
                },
            )

        fake_send.assert_called_once()
        args, _ = fake_send.call_args
        # 프리셋 두 명 + 그 외 주소(중복 제거)가 순서대로 전달돼야 한다.
        self.assertEqual(
            args[0], ["user1@example.com", "user2@example.com", "other@example.com"]
        )
        # 확인 메시지는 프리셋 대상은 이름으로, 그 외는 이메일 그대로 보여준다.
        self.assertIn(
            "메일을 보냈습니다: 테스트유저1, 테스트유저2, other@example.com".encode(), resp.data
        )

    def test_publish_confluence_card_hidden_when_feature_not_available(self):
        with mock.patch("confluence_agenda.web.app.docx_export_configured", return_value=False):
            resp = self.client.get("/agenda")

        self.assertNotIn(b'name="parent_url"', resp.data)
        self.assertNotIn(b'value="publish_confluence"', resp.data)

    def test_publish_confluence_card_shown_when_feature_available(self):
        with mock.patch("confluence_agenda.web.app.docx_export_configured", return_value=True):
            resp = self.client.get("/agenda")

        self.assertIn(b'name="parent_url"', resp.data)
        self.assertIn(b'value="publish_confluence"', resp.data)

    def test_publish_confluence_card_appears_above_mail_card(self):
        env = {"MAIL_API_TOKEN": "t", "MAIL_API_SYSTEM_ID": "s", "MAIL_API_USER_ID": "u"}
        with mock.patch.dict("os.environ", env, clear=True), mock.patch(
            "confluence_agenda.web.app.docx_export_configured", return_value=True
        ):
            resp = self.client.get("/agenda")

        body = resp.data.decode("utf-8")
        self.assertLess(body.index("Confluence에 자동 생성"), body.index("메일 발송"))

    def test_publish_confluence_button_is_leftmost_and_blue(self):
        env = {"MAIL_API_TOKEN": "t", "MAIL_API_SYSTEM_ID": "s", "MAIL_API_USER_ID": "u"}
        with mock.patch.dict("os.environ", env, clear=True), mock.patch(
            "confluence_agenda.web.app.docx_export_configured", return_value=True
        ):
            resp = self.client.get("/agenda")

        body = resp.data.decode("utf-8")
        self.assertIn('class="primary" type="submit" name="action" value="publish_confluence"', body)
        self.assertIn('class="secondary" type="submit" name="action" value="generate"', body)
        self.assertIn('class="secondary" type="submit" name="action" value="send_mail"', body)
        self.assertLess(body.index('value="publish_confluence"'), body.index('value="generate"'))
        self.assertLess(body.index('value="generate"'), body.index('value="send_mail"'))

    def test_publish_confluence_without_parent_url_shows_error(self):
        with mock.patch("confluence_agenda.web.app.docx_export_configured", return_value=True):
            resp = self.client.post(
                "/agenda",
                data={"titles": "안건1", "action": "publish_confluence", "page_title": "회의록"},
            )

        self.assertIn("상위 페이지 URL을 입력해주세요".encode(), resp.data)

    def test_publish_confluence_without_page_title_shows_error(self):
        with mock.patch("confluence_agenda.web.app.docx_export_configured", return_value=True):
            resp = self.client.post(
                "/agenda",
                data={
                    "titles": "안건1",
                    "action": "publish_confluence",
                    "parent_url": "https://wiki.example.com/pages/100",
                },
            )

        self.assertIn("새 안건 페이지 제목을 입력해주세요".encode(), resp.data)

    def test_publish_confluence_without_mirror_parent_url_shows_error(self):
        with mock.patch("confluence_agenda.web.app.docx_export_configured", return_value=True):
            resp = self.client.post(
                "/agenda",
                data={
                    "titles": "안건1",
                    "action": "publish_confluence",
                    "parent_url": "https://wiki.example.com/pages/100",
                    "page_title": "회의록",
                },
            )

        self.assertIn("미러링할 페이지의 상위 페이지 URL을 입력해주세요".encode(), resp.data)

    def test_publish_confluence_without_token_shows_error(self):
        with mock.patch(
            "confluence_agenda.web.app.docx_export_configured", return_value=True
        ), mock.patch("confluence_agenda.web.app.resolve_confluence_token", return_value=None):
            resp = self.client.post(
                "/agenda",
                data={
                    "titles": "안건1",
                    "action": "publish_confluence",
                    "parent_url": "https://wiki.example.com/pages/100",
                    "page_title": "회의록",
                    "mirror_parent_url": "https://wiki.example.com/pages/900",
                },
            )

        self.assertIn("PAT".encode(), resp.data)

    def test_publish_confluence_success_shows_created_page_links(self):
        fake_result = {
            "page_id": "200",
            "url": "https://wiki.example.com/x/200",
            "title": "회의록",
            "detail_pages": [
                {"title": "(첨부 1) 안건1", "ok": True, "url": "https://wiki.example.com/x/300"},
                {"title": "(첨부 2) 안건2", "ok": False, "error": "이미 같은 제목의 페이지가 있습니다."},
            ],
            "mirror_page": {"ok": True, "title": "회의록", "url": "https://wiki.example.com/x/999"},
        }
        with mock.patch(
            "confluence_agenda.web.app.docx_export_configured", return_value=True
        ), mock.patch(
            "confluence_agenda.web.app.resolve_confluence_token", return_value="my-pat"
        ), mock.patch(
            "confluence_agenda.web.app.create_agenda_page", return_value=fake_result
        ) as fake_create, mock.patch(
            "confluence_agenda.web.app._save_last_publish_parents"
        ) as fake_save:
            resp = self.client.post(
                "/agenda",
                data={
                    "titles": "안건1\n\n안건2",
                    "action": "publish_confluence",
                    "parent_url": "https://wiki.example.com/pages/100",
                    "page_title": "회의록",
                    "mirror_parent_url": "https://wiki.example.com/pages/900",
                },
            )

        fake_create.assert_called_once()
        args, kwargs = fake_create.call_args
        self.assertEqual(args[0], "https://wiki.example.com/pages/100")
        self.assertEqual(args[1], "회의록")
        self.assertEqual(kwargs["token"], "my-pat")
        self.assertEqual(kwargs["mirror_parent_url"], "https://wiki.example.com/pages/900")
        # 다음에 또 쓸 수 있게 이번에 쓴 상위 페이지 URL들을 기본값으로 저장해야 한다.
        fake_save.assert_called_once_with(
            "https://wiki.example.com/pages/100", "https://wiki.example.com/pages/900"
        )

        body = resp.data.decode("utf-8")
        self.assertIn("회의록", body)
        self.assertIn("https://wiki.example.com/x/200", body)
        self.assertIn("https://wiki.example.com/x/300", body)
        self.assertIn("이미 같은 제목의 페이지가 있습니다", body)
        self.assertIn("https://wiki.example.com/x/999", body)

    def test_publish_confluence_failure_shows_error_message(self):
        with mock.patch(
            "confluence_agenda.web.app.docx_export_configured", return_value=True
        ), mock.patch(
            "confluence_agenda.web.app.resolve_confluence_token", return_value="my-pat"
        ), mock.patch(
            "confluence_agenda.web.app.create_agenda_page",
            side_effect=RuntimeError("상위 페이지 1을 찾을 수 없습니다(404)."),
        ):
            resp = self.client.post(
                "/agenda",
                data={
                    "titles": "안건1",
                    "action": "publish_confluence",
                    "parent_url": "https://wiki.example.com/pages/1",
                    "page_title": "회의록",
                    "mirror_parent_url": "https://wiki.example.com/pages/900",
                },
            )

        self.assertIn("안건 페이지 생성 실패".encode(), resp.data)
        self.assertIn("찾을 수 없습니다".encode(), resp.data)

    def test_agenda_page_prefills_parent_urls_from_last_successful_publish(self):
        # 상위 페이지는 거의 고정이라, 지난번에 쓴 URL이 기본값으로 미리
        # 채워져 있어야 한다(사용자 요청).
        saved = {
            "parent_url": "https://wiki.example.com/pages/100",
            "mirror_parent_url": "https://wiki.example.com/pages/900",
        }
        with mock.patch(
            "confluence_agenda.web.app.docx_export_configured", return_value=True
        ), mock.patch("confluence_agenda.web.app._load_last_publish_parents", return_value=saved):
            resp = self.client.get("/agenda")

        body = resp.data.decode("utf-8")
        self.assertIn('value="https://wiki.example.com/pages/100"', body)
        self.assertIn('value="https://wiki.example.com/pages/900"', body)

    def test_publish_confluence_does_not_show_manual_copy_paste_source_card(self):
        # API로 이미 다 만들었는데 "소스 생성" 카드가 같이 뜨면, 템플릿
        # 버튼을 또 누르라는 안내처럼 보여 혼란스럽다 - publish_confluence일
        # 때는 그 카드가 아예 없어야 한다.
        fake_result = {
            "page_id": "200",
            "url": "https://wiki.example.com/x/200",
            "title": "회의록",
            "detail_pages": [],
            "mirror_page": None,
        }
        with mock.patch(
            "confluence_agenda.web.app.docx_export_configured", return_value=True
        ), mock.patch(
            "confluence_agenda.web.app.resolve_confluence_token", return_value="my-pat"
        ), mock.patch(
            "confluence_agenda.web.app.create_agenda_page", return_value=fake_result
        ), mock.patch("confluence_agenda.web.app._save_last_publish_parents"):
            resp = self.client.post(
                "/agenda",
                data={
                    "titles": "안건1",
                    "action": "publish_confluence",
                    "parent_url": "https://wiki.example.com/pages/100",
                    "page_title": "회의록",
                    "mirror_parent_url": "https://wiki.example.com/pages/900",
                },
            )

        self.assertNotIn("생성된 소스".encode(), resp.data)
        self.assertNotIn(b'ac:name=&#34;create-from-template&#34;', resp.data)


class LoginFlowTest(unittest.TestCase):
    """auth.is_configured()가 True일 때(= DATABASE_URL로 로그인이 켜졌을 때)
    before_request 보호/로그인/로그아웃 흐름을 확인한다. 실제 DB는 쓰지 않고
    confluence_agenda.web.auth의 함수를 직접 모킹한다(DB 자체 동작은
    test_auth.py에서 sqlite로 이미 검증)."""

    def setUp(self):
        app.testing = True
        app.secret_key = "test-secret"
        self.client = app.test_client()

    def test_root_redirects_to_login_when_not_authenticated(self):
        with mock.patch.object(auth, "is_configured", return_value=True), mock.patch.object(
            auth, "get_current_user", return_value=None
        ):
            resp = self.client.get("/", follow_redirects=False)

        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp.headers["Location"].startswith("/login"))

    def test_root_accessible_when_authenticated(self):
        with mock.patch.object(auth, "is_configured", return_value=True), mock.patch.object(
            auth, "get_current_user", return_value={"user_id": "dh.kwon", "display_name": "권동혁"}
        ):
            resp = self.client.get("/")

        self.assertEqual(resp.status_code, 200)
        self.assertIn("권동혁님".encode(), resp.data)

    def test_login_page_shows_invalid_credentials_error(self):
        resp = self.client.get("/login?error=invalid")
        self.assertIn("아이디 또는 비밀번호가 올바르지 않습니다".encode(), resp.data)

    def test_auth_login_success_sets_session_and_redirects_to_next(self):
        with mock.patch.object(
            auth, "authenticate", return_value={"user_id": "dh.kwon", "display_name": "권동혁"}
        ) as fake_auth, mock.patch.object(auth, "set_session") as fake_set_session:
            resp = self.client.post(
                "/auth/login",
                data={"user_id": "dh.kwon", "password": "pw", "next": "/"},
                follow_redirects=False,
            )

        fake_auth.assert_called_once_with("dh.kwon", "pw")
        fake_set_session.assert_called_once_with({"user_id": "dh.kwon", "display_name": "권동혁"})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.headers["Location"], "/")

    def test_auth_login_invalid_credentials_redirects_with_error(self):
        with mock.patch.object(auth, "authenticate", return_value=None):
            resp = self.client.post(
                "/auth/login", data={"user_id": "dh.kwon", "password": "wrong"}, follow_redirects=False
            )

        self.assertEqual(resp.status_code, 302)
        self.assertIn("error=invalid", resp.headers["Location"])

    def test_auth_login_must_change_password_redirects_with_specific_error(self):
        with mock.patch.object(auth, "authenticate", side_effect=auth.PasswordChangeRequired()):
            resp = self.client.post(
                "/auth/login", data={"user_id": "newbie", "password": "12345678"}, follow_redirects=False
            )

        self.assertEqual(resp.status_code, 302)
        self.assertIn("error=must_change_password", resp.headers["Location"])

    def test_logout_clears_session_and_redirects_to_login(self):
        with mock.patch.object(auth, "clear_session") as fake_clear:
            resp = self.client.get("/logout", follow_redirects=False)

        fake_clear.assert_called_once()
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.headers["Location"], "/login")


class ConfluenceDocxAndPatTest(unittest.TestCase):
    """컨플루언스 -> Word 변환 라우트와, 사용자별 PAT 등록/삭제 라우트."""

    def setUp(self):
        app.testing = True
        self.client = app.test_client()

    def _poll_job_until_finished(self, job_id, *, timeout=2.0):
        """변환이 백그라운드 스레드에서 돌므로, 끝날 때까지 /status를 짧게 반복
        확인한다(실제 화면의 JS 폴링과 같은 방식) - 테스트의 경합 상태를 피하려고."""
        deadline = time.monotonic() + timeout
        status = None
        while time.monotonic() < deadline:
            status = self.client.get(f"/confluence-to-docx/status/{job_id}").get_json()
            if status["status"] != "running":
                return status
            time.sleep(0.02)
        self.fail(f"작업이 {timeout}초 안에 끝나지 않음: {status}")

    def test_confluence_to_docx_start_without_url_returns_error(self):
        resp = self.client.post("/confluence-to-docx/start", data={"confluence_url": ""})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("URL", resp.get_json()["error"])

    def test_confluence_to_docx_start_without_token_returns_error(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            resp = self.client.post(
                "/confluence-to-docx/start",
                data={"confluence_url": "https://wiki.example.com/pages/123"},
            )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("PAT", resp.get_json()["error"])

    def test_confluence_to_docx_job_flow_reports_progress_then_downloads_file(self):
        env = {"CONFLUENCE_API_TOKEN": "global-token"}

        def fake_convert(url, *, token, on_progress=None):
            if on_progress:
                on_progress("테스트 진행 중...")
            return b"docx-bytes", "report.docx"

        with mock.patch.dict("os.environ", env, clear=True), mock.patch(
            "confluence_agenda.web.app.convert_confluence_url_to_docx", side_effect=fake_convert
        ) as fake:
            start_resp = self.client.post(
                "/confluence-to-docx/start",
                data={"confluence_url": "https://wiki.example.com/pages/123"},
            )
            self.assertEqual(start_resp.status_code, 200)
            job_id = start_resp.get_json()["job_id"]

            status = self._poll_job_until_finished(job_id)
            self.assertEqual(status["status"], "done")

            download_resp = self.client.get(f"/confluence-to-docx/download/{job_id}")

        fake.assert_called_once()
        self.assertEqual(fake.call_args.kwargs["token"], "global-token")
        self.assertEqual(download_resp.status_code, 200)
        self.assertEqual(download_resp.data, b"docx-bytes")
        self.assertIn("report.docx", download_resp.headers["Content-Disposition"])

    def test_confluence_to_docx_job_reports_conversion_error(self):
        env = {"CONFLUENCE_API_TOKEN": "global-token"}
        with mock.patch.dict("os.environ", env, clear=True), mock.patch(
            "confluence_agenda.web.app.convert_confluence_url_to_docx",
            side_effect=DocxExportUnavailable("python-docx/lxml이 설치되지 않았습니다."),
        ):
            start_resp = self.client.post(
                "/confluence-to-docx/start",
                data={"confluence_url": "https://wiki.example.com/pages/123"},
            )
            job_id = start_resp.get_json()["job_id"]
            status = self._poll_job_until_finished(job_id)

        self.assertEqual(status["status"], "error")
        self.assertIn("python-docx/lxml", status["message"])

    def test_confluence_to_docx_download_of_unknown_job_redirects_with_error(self):
        resp = self.client.get("/confluence-to-docx/download/not-a-real-job", follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("docx_error=", resp.headers["Location"])

    def test_confluence_to_docx_status_of_unknown_job_returns_404(self):
        resp = self.client.get("/confluence-to-docx/status/not-a-real-job")
        self.assertEqual(resp.status_code, 404)

    def _finish_job(self, storage_html="<h2>소제목</h2><p>본문</p>", title="회의록"):
        """변환이 끝난(job["status"]=="done") job_id를 돌려준다 - 미리보기/메일
        테스트가 공통으로 쓰는 준비 단계."""
        env = {"CONFLUENCE_API_TOKEN": "global-token"}

        def fake_convert(url, *, token, on_progress=None):
            from confluence_agenda.web.docx_export import _render_document, _parse_storage

            data = _render_document(title, _parse_storage(storage_html))
            return data, f"{title}.docx"

        with mock.patch.dict("os.environ", env, clear=True), mock.patch(
            "confluence_agenda.web.app.convert_confluence_url_to_docx", side_effect=fake_convert
        ):
            start_resp = self.client.post(
                "/confluence-to-docx/start",
                data={"confluence_url": "https://wiki.example.com/pages/123"},
            )
            job_id = start_resp.get_json()["job_id"]
            self._poll_job_until_finished(job_id)
        return job_id

    def test_confluence_to_docx_preview_shows_content_without_downloading(self):
        job_id = self._finish_job()
        resp = self.client.get(f"/confluence-to-docx/preview/{job_id}")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn("소제목", body)
        self.assertIn("본문", body)
        self.assertIn(f"/confluence-to-docx/download/{job_id}", body)

    def test_confluence_to_docx_preview_of_unknown_job_redirects_with_error(self):
        resp = self.client.get("/confluence-to-docx/preview/not-a-real-job", follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("docx_error=", resp.headers["Location"])

    def test_confluence_to_docx_download_can_be_repeated_after_preview(self):
        # 미리보기/다운로드/메일 중 하나를 했다고 나머지가 못 쓰게 되면 안 된다
        # (예전에는 다운로드 한 번으로 job을 지워버렸음).
        job_id = self._finish_job()
        self.client.get(f"/confluence-to-docx/preview/{job_id}")
        first = self.client.get(f"/confluence-to-docx/download/{job_id}")
        second = self.client.get(f"/confluence-to-docx/download/{job_id}")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.data, second.data)

    def test_confluence_to_docx_email_without_address_returns_error(self):
        job_id = self._finish_job()
        with mock.patch.dict("os.environ", {"MAIL_API_TOKEN": "t"}, clear=False):
            resp = self.client.post(f"/confluence-to-docx/email/{job_id}", data={"email_to": ""})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("이메일", resp.get_json()["error"])

    def test_confluence_to_docx_email_when_mail_not_configured_returns_error(self):
        job_id = self._finish_job()
        with mock.patch.dict("os.environ", {}, clear=True):
            resp = self.client.post(
                f"/confluence-to-docx/email/{job_id}", data={"email_to": "a@example.com"}
            )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("MAIL_API_TOKEN", resp.get_json()["error"])

    def test_confluence_to_docx_email_sends_docx_bytes_as_attachment(self):
        job_id = self._finish_job(title="회의록")
        with mock.patch.dict("os.environ", {"MAIL_API_TOKEN": "t"}, clear=False), mock.patch(
            "confluence_agenda.web.app.send_report_email"
        ) as fake_send:
            resp = self.client.post(
                f"/confluence-to-docx/email/{job_id}", data={"email_to": "a@example.com"}
            )

        self.assertEqual(resp.status_code, 200)
        self.assertIn("a@example.com", resp.get_json()["message"])
        fake_send.assert_called_once()
        args, kwargs = fake_send.call_args
        self.assertEqual(args[0], "a@example.com")
        attachments = kwargs["attachments"]
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0]["filename"], "회의록.docx")
        self.assertTrue(attachments[0]["content"])

    def test_confluence_to_docx_email_failure_is_reported(self):
        job_id = self._finish_job()
        with mock.patch.dict("os.environ", {"MAIL_API_TOKEN": "t"}, clear=False), mock.patch(
            "confluence_agenda.web.app.send_report_email",
            side_effect=MailConfigError("메일 API 호출 실패"),
        ):
            resp = self.client.post(
                f"/confluence-to-docx/email/{job_id}", data={"email_to": "a@example.com"}
            )

        self.assertEqual(resp.status_code, 502)
        self.assertIn("메일 API 호출 실패", resp.get_json()["error"])

    def test_confluence_to_docx_email_of_unknown_job_returns_404(self):
        resp = self.client.post(
            "/confluence-to-docx/email/not-a-real-job", data={"email_to": "a@example.com"}
        )
        self.assertEqual(resp.status_code, 404)

    def test_save_confluence_pat_requires_login(self):
        with mock.patch.object(auth, "get_current_user", return_value=None):
            resp = self.client.post(
                "/confluence-pat", data={"confluence_pat": "x"}, follow_redirects=False
            )
        self.assertEqual(resp.status_code, 302)
        self.assertIn("docx_error=", resp.headers["Location"])

    def test_save_confluence_pat_requires_nonempty_value(self):
        user = {"user_id": "dh.kwon", "display_name": "권동혁"}
        with mock.patch.object(auth, "get_current_user", return_value=user):
            resp = self.client.post(
                "/confluence-pat", data={"confluence_pat": "   "}, follow_redirects=False
            )
        self.assertEqual(resp.status_code, 302)
        self.assertIn("docx_error=", resp.headers["Location"])

    def test_save_confluence_pat_success_verifies_then_calls_set_pat(self):
        user = {"user_id": "dh.kwon", "display_name": "권동혁"}
        with mock.patch.object(auth, "get_current_user", return_value=user), mock.patch(
            "confluence_agenda.web.app.verify_confluence_token", return_value="권동혁 (dh.kwon)"
        ) as fake_verify, mock.patch.object(confluence_credentials, "set_pat") as fake_set:
            resp = self.client.post(
                "/confluence-pat", data={"confluence_pat": "my-new-pat"}, follow_redirects=False
            )

        fake_verify.assert_called_once_with("my-new-pat")
        fake_set.assert_called_once_with("dh.kwon", "my-new-pat")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("docx_message=", resp.headers["Location"])
        self.assertIn("권동혁", unquote(resp.headers["Location"]))

    def test_save_confluence_pat_verification_failure_does_not_save(self):
        user = {"user_id": "dh.kwon", "display_name": "권동혁"}
        with mock.patch.object(auth, "get_current_user", return_value=user), mock.patch(
            "confluence_agenda.web.app.verify_confluence_token",
            side_effect=RuntimeError("PAT이 인정되지 않았습니다(익명 사용자로 응답받음)"),
        ), mock.patch.object(confluence_credentials, "set_pat") as fake_set:
            resp = self.client.post(
                "/confluence-pat", data={"confluence_pat": "wrong-pat"}, follow_redirects=False
            )

        fake_set.assert_not_called()
        self.assertEqual(resp.status_code, 302)
        self.assertIn("docx_error=", resp.headers["Location"])

    def test_save_confluence_pat_verification_failure_shows_diagnostic_detail(self):
        """화면 에러 메시지가 그냥 "PAT 확인 실패: ..." 한 줄이 아니라,
        diagnose_connection()의 단계별 결과(요청 URL/DEP_TICKET 상태 등)를
        그대로 담아서 터미널 없이도 원인을 좁힐 수 있게 해야 한다."""
        user = {"user_id": "dh.kwon", "display_name": "권동혁"}
        diagnostic_lines = [
            "✓ CONFLUENCE_URL 설정됨 → 실제 요청 기준 주소: https://wiki.example.com",
            "→ 실제 요청 URL: GET https://wiki.example.com/rest/api/user/current",
            "✗ 요청 실패: RuntimeError: Confluence 인증 실패(401)",
        ]
        with mock.patch("confluence_agenda.web.app.docx_export_configured", return_value=True), \
             mock.patch.object(auth, "get_current_user", return_value=user), \
             mock.patch(
                 "confluence_agenda.web.app.verify_confluence_token",
                 side_effect=RuntimeError("Confluence 인증 실패(401)"),
             ), \
             mock.patch(
                 "confluence_agenda.web.app.diagnose_confluence_connection",
                 return_value=diagnostic_lines,
             ) as fake_diagnose, \
             mock.patch.object(confluence_credentials, "set_pat") as fake_set:
            post_resp = self.client.post(
                "/confluence-pat", data={"confluence_pat": "wrong-pat"}, follow_redirects=False
            )
            resp = self.client.get(post_resp.headers["Location"])

        fake_diagnose.assert_called_once_with("wrong-pat")
        fake_set.assert_not_called()
        body = resp.data.decode("utf-8")
        self.assertIn("실제 요청 URL", body)
        self.assertIn("rest/api/user/current", body)

    def test_save_confluence_pat_storage_unavailable_shows_error(self):
        user = {"user_id": "dh.kwon", "display_name": "권동혁"}
        with mock.patch.object(auth, "get_current_user", return_value=user), mock.patch(
            "confluence_agenda.web.app.verify_confluence_token", return_value="권동혁 (dh.kwon)"
        ), mock.patch.object(
            confluence_credentials,
            "set_pat",
            side_effect=confluence_credentials.CredentialStorageUnavailable("설정이 없습니다"),
        ):
            resp = self.client.post(
                "/confluence-pat", data={"confluence_pat": "my-new-pat"}, follow_redirects=False
            )
        self.assertEqual(resp.status_code, 302)
        self.assertIn("docx_error=", resp.headers["Location"])

    def test_delete_confluence_pat_requires_login(self):
        with mock.patch.object(auth, "get_current_user", return_value=None):
            resp = self.client.post("/confluence-pat/delete", follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("docx_error=", resp.headers["Location"])

    def test_delete_confluence_pat_calls_delete_pat(self):
        user = {"user_id": "dh.kwon", "display_name": "권동혁"}
        with mock.patch.object(auth, "get_current_user", return_value=user), mock.patch.object(
            confluence_credentials, "delete_pat"
        ) as fake_delete:
            resp = self.client.post("/confluence-pat/delete", follow_redirects=False)

        fake_delete.assert_called_once_with("dh.kwon")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("docx_message=", resp.headers["Location"])

    def test_docx_page_shows_pat_registered_status_for_logged_in_user(self):
        user = {"user_id": "dh.kwon", "display_name": "권동혁"}
        with mock.patch("confluence_agenda.web.app.docx_export_configured", return_value=True), \
             mock.patch.object(auth, "get_current_user", return_value=user), \
             mock.patch.object(confluence_credentials, "is_configured", return_value=True), \
             mock.patch.object(confluence_credentials, "status", return_value=object()):
            resp = self.client.get("/confluence-to-docx")

        self.assertIn("등록되어 있습니다".encode(), resp.data)

    def test_docx_page_shows_not_registered_message_when_no_pat_yet(self):
        user = {"user_id": "dh.kwon", "display_name": "권동혁"}
        with mock.patch("confluence_agenda.web.app.docx_export_configured", return_value=True), \
             mock.patch.object(auth, "get_current_user", return_value=user), \
             mock.patch.object(confluence_credentials, "is_configured", return_value=True), \
             mock.patch.object(confluence_credentials, "status", return_value=None):
            resp = self.client.get("/confluence-to-docx")

        self.assertIn("아직 등록되지 않았습니다".encode(), resp.data)

    def test_docx_page_redirects_to_index_when_feature_not_available(self):
        with mock.patch("confluence_agenda.web.app.docx_export_configured", return_value=False):
            resp = self.client.get("/confluence-to-docx", follow_redirects=False)

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.headers["Location"], "/")

    def test_index_shows_nav_link_to_docx_page_when_available(self):
        with mock.patch("confluence_agenda.web.app.docx_export_configured", return_value=True):
            resp = self.client.get("/")

        self.assertIn(b'href="/confluence-to-docx"', resp.data)

    def test_index_hides_nav_link_when_docx_feature_not_available(self):
        with mock.patch("confluence_agenda.web.app.docx_export_configured", return_value=False):
            resp = self.client.get("/")

        self.assertNotIn(b'href="/confluence-to-docx"', resp.data)

    def test_home_page_always_shows_agenda_choice_link(self):
        # 워드 변환 기능이 꺼져 있어도 안건 페이지 생성 선택지는 항상 보여야 한다.
        with mock.patch("confluence_agenda.web.app.docx_export_configured", return_value=False):
            resp = self.client.get("/")

        self.assertIn(b'href="/agenda"', resp.data)

    def test_persistent_top_nav_shows_both_feature_links_on_agenda_page(self):
        # 다른 화면으로 가더라도 맨 위에서 두 기능을 바로 전환할 수 있어야
        # 한다 - 메인(선택) 화면으로 먼저 돌아갈 필요가 없게.
        with mock.patch("confluence_agenda.web.app.docx_export_configured", return_value=True):
            resp = self.client.get("/agenda")

        body = resp.data.decode("utf-8")
        self.assertIn('class="nav-link active" href="/agenda"', body)
        self.assertIn('class="nav-link " href="/confluence-to-docx"', body)

    def test_persistent_top_nav_hides_docx_link_when_feature_not_available(self):
        with mock.patch("confluence_agenda.web.app.docx_export_configured", return_value=False):
            resp = self.client.get("/agenda")

        self.assertNotIn(b'href="/confluence-to-docx"', resp.data)

    def test_persistent_top_nav_shows_both_feature_links_on_docx_page(self):
        with mock.patch("confluence_agenda.web.app.docx_export_configured", return_value=True):
            resp = self.client.get("/confluence-to-docx")

        body = resp.data.decode("utf-8")
        self.assertIn('class="nav-link " href="/agenda"', body)
        self.assertIn('class="nav-link active" href="/confluence-to-docx"', body)

    def test_persistent_top_nav_includes_agenda_edit_link_when_available(self):
        with mock.patch("confluence_agenda.web.app.docx_export_configured", return_value=True):
            resp = self.client.get("/agenda")

        self.assertIn(b'href="/agenda/edit"', resp.data)

    def test_persistent_top_nav_hides_agenda_edit_link_when_not_available(self):
        with mock.patch("confluence_agenda.web.app.docx_export_configured", return_value=False):
            resp = self.client.get("/agenda")

        self.assertNotIn(b'href="/agenda/edit"', resp.data)


class AgendaEditPageTest(unittest.TestCase):
    """/agenda/edit - 이미 만든 안건 페이지를 불러와 안건 제목을
    추가/수정/삭제하는 화면."""

    def setUp(self):
        app.testing = True
        self.client = app.test_client()

    def test_redirects_to_home_when_feature_not_available(self):
        with mock.patch("confluence_agenda.web.app.docx_export_configured", return_value=False):
            resp = self.client.get("/agenda/edit", follow_redirects=False)

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.headers["Location"], "/")

    def test_get_shows_load_form(self):
        with mock.patch("confluence_agenda.web.app.docx_export_configured", return_value=True):
            resp = self.client.get("/agenda/edit")

        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'name="page_url"', resp.data)
        self.assertIn(b'value="load"', resp.data)

    def test_load_without_url_shows_error(self):
        with mock.patch("confluence_agenda.web.app.docx_export_configured", return_value=True):
            resp = self.client.post("/agenda/edit", data={"action": "load", "page_url": ""})

        self.assertIn("안건 페이지 URL을 입력해주세요".encode(), resp.data)

    def test_load_without_token_shows_error(self):
        with mock.patch(
            "confluence_agenda.web.app.docx_export_configured", return_value=True
        ), mock.patch("confluence_agenda.web.app.resolve_confluence_token", return_value=None):
            resp = self.client.post(
                "/agenda/edit",
                data={"action": "load", "page_url": "https://wiki.example.com/pages/200"},
            )

        self.assertIn("PAT".encode(), resp.data)

    def test_load_failure_shows_error_message(self):
        with mock.patch(
            "confluence_agenda.web.app.docx_export_configured", return_value=True
        ), mock.patch(
            "confluence_agenda.web.app.resolve_confluence_token", return_value="my-pat"
        ), mock.patch(
            "confluence_agenda.web.app.load_agenda_page_for_editing",
            side_effect=RuntimeError("페이지 200을 찾을 수 없습니다(404)."),
        ):
            resp = self.client.post(
                "/agenda/edit",
                data={"action": "load", "page_url": "https://wiki.example.com/pages/200"},
            )

        self.assertIn("안건 페이지를 불러오지 못했습니다".encode(), resp.data)
        self.assertIn("찾을 수 없습니다".encode(), resp.data)

    def test_load_success_shows_editable_rows_and_blank_new_rows(self):
        fake_loaded = {
            "page_id": "200",
            "url": "https://wiki.example.com/pages/200",
            "title": "회의록",
            "items": [{"index": 1, "title": "안건A"}, {"index": 2, "title": "안건B"}],
        }
        with mock.patch(
            "confluence_agenda.web.app.docx_export_configured", return_value=True
        ), mock.patch(
            "confluence_agenda.web.app.resolve_confluence_token", return_value="my-pat"
        ), mock.patch(
            "confluence_agenda.web.app.load_agenda_page_for_editing", return_value=fake_loaded
        ):
            resp = self.client.post(
                "/agenda/edit",
                data={"action": "load", "page_url": "https://wiki.example.com/pages/200"},
            )

        body = resp.data.decode("utf-8")
        self.assertIn('value="안건A"', body)
        self.assertIn('value="안건B"', body)
        self.assertEqual(body.count('name="orig_index" value="1"'), 1)
        self.assertEqual(body.count('name="orig_index" value="2"'), 1)
        self.assertIn('placeholder="새 안건 제목"', body)
        self.assertIn(b'value="save"', resp.data)

    def test_save_without_any_items_shows_error(self):
        with mock.patch("confluence_agenda.web.app.docx_export_configured", return_value=True):
            resp = self.client.post(
                "/agenda/edit",
                data={
                    "action": "save",
                    "page_url": "https://wiki.example.com/pages/200",
                    "orig_index": ["1", ""],
                    "title": ["", ""],
                },
            )

        self.assertIn("최소 1개 이상의 안건이 필요합니다".encode(), resp.data)

    def test_save_sends_kept_renamed_new_items_and_excludes_deleted(self):
        fake_result = {
            "page_id": "200",
            "url": "https://wiki.example.com/x/200",
            "title": "회의록",
            "detail_pages": [],
            "trashed_detail_pages": [],
        }
        with mock.patch(
            "confluence_agenda.web.app.docx_export_configured", return_value=True
        ), mock.patch(
            "confluence_agenda.web.app.resolve_confluence_token", return_value="my-pat"
        ), mock.patch(
            "confluence_agenda.web.app.update_agenda_page", return_value=fake_result
        ) as fake_update, mock.patch(
            "confluence_agenda.web.app.load_agenda_page_for_editing",
            return_value={
                "page_id": "200",
                "url": "https://wiki.example.com/pages/200",
                "title": "회의록",
                "items": [{"index": 1, "title": "안건A-수정"}, {"index": 2, "title": "새 안건"}],
            },
        ):
            resp = self.client.post(
                "/agenda/edit",
                data={
                    "action": "save",
                    "page_url": "https://wiki.example.com/pages/200",
                    "orig_index": ["1", "2", ""],
                    "title": ["안건A-수정", "안건B", "새 안건"],
                    "delete_orig_index": ["2"],
                },
            )

        self.assertEqual(resp.status_code, 200)
        fake_update.assert_called_once()
        args, kwargs = fake_update.call_args
        self.assertEqual(args[0], "https://wiki.example.com/pages/200")
        # 2번은 삭제 체크됐으니 최종 목록에서 빠지고, 새 항목(원래 번호
        # 없음)은 포함돼야 한다.
        self.assertEqual(args[1], [(1, "안건A-수정"), (None, "새 안건")])
        self.assertEqual(kwargs["deleted_orig_indexes"], [2])
        self.assertEqual(kwargs["token"], "my-pat")

    def test_save_failure_shows_error_message(self):
        with mock.patch(
            "confluence_agenda.web.app.docx_export_configured", return_value=True
        ), mock.patch(
            "confluence_agenda.web.app.resolve_confluence_token", return_value="my-pat"
        ), mock.patch(
            "confluence_agenda.web.app.update_agenda_page",
            side_effect=RuntimeError("안건 페이지 200의 버전 정보를 확인할 수 없습니다."),
        ):
            resp = self.client.post(
                "/agenda/edit",
                data={
                    "action": "save",
                    "page_url": "https://wiki.example.com/pages/200",
                    "orig_index": ["1"],
                    "title": ["안건A"],
                },
            )

        self.assertIn("안건 페이지 수정 실패".encode(), resp.data)
        self.assertIn("버전 정보를 확인할 수 없습니다".encode(), resp.data)

    def test_save_success_shows_detail_and_trashed_results(self):
        fake_result = {
            "page_id": "200",
            "url": "https://wiki.example.com/x/200",
            "title": "회의록",
            "detail_pages": [
                {"title": "(첨부 1) 안건A-수정", "ok": True, "url": "https://wiki.example.com/x/301"}
            ],
            "trashed_detail_pages": [{"title": "(첨부 2) 안건B", "ok": True}],
        }
        fake_loaded_after = {
            "page_id": "200",
            "url": "https://wiki.example.com/pages/200",
            "title": "회의록",
            "items": [{"index": 1, "title": "안건A-수정"}],
        }
        with mock.patch(
            "confluence_agenda.web.app.docx_export_configured", return_value=True
        ), mock.patch(
            "confluence_agenda.web.app.resolve_confluence_token", return_value="my-pat"
        ), mock.patch(
            "confluence_agenda.web.app.update_agenda_page", return_value=fake_result
        ), mock.patch(
            "confluence_agenda.web.app.load_agenda_page_for_editing", return_value=fake_loaded_after
        ):
            resp = self.client.post(
                "/agenda/edit",
                data={
                    "action": "save",
                    "page_url": "https://wiki.example.com/pages/200",
                    "orig_index": ["1", "2"],
                    "title": ["안건A-수정", "안건B"],
                    "delete_orig_index": ["2"],
                },
            )

        body = resp.data.decode("utf-8")
        self.assertIn("안건 페이지를 수정했습니다", body)
        self.assertIn("https://wiki.example.com/x/301", body)
        self.assertIn("휴지통으로 이동했습니다", body)
        # 저장 후에는 최신 상태(새 번호/제목)를 다시 보여줘야 한다.
        self.assertIn('value="안건A-수정"', body)

    def test_save_success_is_not_masked_by_a_failed_post_save_reload(self):
        # 저장(update_agenda_page) 자체는 성공했는데, 그 직후 "최신 상태
        # 다시 불러오기"(load_agenda_page_for_editing)만 실패하는 경우 -
        # 저장은 이미 끝났으니 성공 메시지가 "실패"로 뒤바뀌면 안 된다.
        fake_result = {
            "page_id": "200",
            "url": "https://wiki.example.com/x/200",
            "title": "회의록",
            "detail_pages": [],
            "trashed_detail_pages": [],
        }
        with mock.patch(
            "confluence_agenda.web.app.docx_export_configured", return_value=True
        ), mock.patch(
            "confluence_agenda.web.app.resolve_confluence_token", return_value="my-pat"
        ), mock.patch(
            "confluence_agenda.web.app.update_agenda_page", return_value=fake_result
        ), mock.patch(
            "confluence_agenda.web.app.load_agenda_page_for_editing",
            side_effect=RuntimeError("방금 바뀐 내용이 아직 조회에 반영되지 않았습니다."),
        ):
            resp = self.client.post(
                "/agenda/edit",
                data={
                    "action": "save",
                    "page_url": "https://wiki.example.com/pages/200",
                    "orig_index": ["1"],
                    "title": ["안건A"],
                },
            )

        body = resp.data.decode("utf-8")
        self.assertIn("안건 페이지를 수정했습니다", body)
        self.assertNotIn("안건 페이지 수정 실패", body)


class MainStartupGuardTest(unittest.TestCase):
    """importing confluence_agenda.web.* (진단 스크립트 등)은 SESSION_SECRET이
    없어도 절대 실패하면 안 되고, 실제 서버 기동(main())에서만 막혀야 한다."""

    def setUp(self):
        self._orig_secret_key = app.secret_key

    def tearDown(self):
        app.secret_key = self._orig_secret_key

    def test_main_raises_when_db_configured_without_session_secret(self):
        app.secret_key = None
        with mock.patch.object(auth, "is_configured", return_value=True):
            with self.assertRaisesRegex(RuntimeError, "SESSION_SECRET"):
                main()

    def test_main_does_not_raise_when_secret_key_already_set(self):
        app.secret_key = "already-set"
        with mock.patch.object(auth, "is_configured", return_value=True), mock.patch.object(
            app, "run"
        ) as fake_run:
            main()
        fake_run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
