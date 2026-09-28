import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from confluence_agenda.builder import build_email_subject
from confluence_agenda.web.app import app

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
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'name="titles"', resp.data)

    def test_post_without_titles_shows_error(self):
        resp = self.client.post("/", data={"titles": "\n\n", "action": "generate"})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("안건 제목을 한 줄에 하나씩 입력해주세요".encode(), resp.data)

    def test_post_generates_source_for_multiple_newline_separated_titles(self):
        resp = self.client.post(
            "/", data={"titles": "예산안 승인\n채용 계획\n", "action": "generate"}
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.data.decode("utf-8")
        self.assertIn("1. 예산안 승인", body)
        self.assertIn("2. 채용 계획", body)
        self.assertIn("ac:name=&#34;ui-expand&#34;", body)

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
            resp = self.client.get("/")
        self.assertNotIn(b'name="preset_to"', resp.data)

    def test_mail_configured_with_contacts_file_shows_preset_chips(self):
        env = {
            "MAIL_API_TOKEN": "t",
            "MAIL_API_SYSTEM_ID": "s",
            "MAIL_API_USER_ID": "u",
            **self._contacts_env,
        }
        with mock.patch.dict("os.environ", env, clear=True):
            resp = self.client.get("/")
        body = resp.data.decode("utf-8")
        self.assertIn("테스트유저1", body)
        self.assertIn("테스트유저2", body)
        self.assertIn('value="user1@example.com"', body)

    def test_send_mail_without_config_shows_error(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            resp = self.client.post(
                "/",
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
                "/", data={"titles": "예산안 승인", "action": "send_mail", "extra_to": ""}
            )
        self.assertIn("받는 사람을 한 명 이상 선택하거나 입력해주세요".encode(), resp.data)

    def test_send_mail_to_extra_address_only(self):
        env = {"MAIL_API_TOKEN": "t", "MAIL_API_SYSTEM_ID": "s", "MAIL_API_USER_ID": "u"}
        with mock.patch.dict("os.environ", env, clear=True), mock.patch(
            "confluence_agenda.web.app.send_report_email"
        ) as fake_send:
            resp = self.client.post(
                "/",
                data={
                    "titles": "예산안 승인\n채용 계획",
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
                "/",
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
            resp = self.client.get("/")
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
                "/",
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


if __name__ == "__main__":
    unittest.main()
