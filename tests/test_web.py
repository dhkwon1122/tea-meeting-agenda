import unittest
from unittest import mock

from confluence_agenda.web.app import app


class WebAppTest(unittest.TestCase):
    def setUp(self):
        app.testing = True
        self.client = app.test_client()

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

    def test_send_mail_without_config_shows_error(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            resp = self.client.post(
                "/",
                data={"titles": "예산안 승인", "action": "send_mail", "to": "someone@example.com"},
            )
        self.assertIn("메일 API 환경변수가 설정되지 않아".encode(), resp.data)

    def test_send_mail_without_recipient_shows_error(self):
        env = {"MAIL_API_TOKEN": "t", "MAIL_API_SYSTEM_ID": "s", "MAIL_API_USER_ID": "u"}
        with mock.patch.dict("os.environ", env, clear=True):
            resp = self.client.post("/", data={"titles": "예산안 승인", "action": "send_mail", "to": ""})
        self.assertIn("받는 사람 이메일을 입력해주세요".encode(), resp.data)

    def test_send_mail_success_calls_mailer_with_generated_content(self):
        env = {"MAIL_API_TOKEN": "t", "MAIL_API_SYSTEM_ID": "s", "MAIL_API_USER_ID": "u"}
        with mock.patch.dict("os.environ", env, clear=True), mock.patch(
            "confluence_agenda.web.app.send_report_email"
        ) as fake_send:
            resp = self.client.post(
                "/",
                data={
                    "titles": "예산안 승인\n채용 계획",
                    "action": "send_mail",
                    "to": "someone@example.com",
                },
            )

        fake_send.assert_called_once()
        _, kwargs = fake_send.call_args
        self.assertEqual(fake_send.call_args.args[0], "someone@example.com")
        self.assertEqual(kwargs["subject"], "[안건 보고] 예산안 승인 외 1건")
        self.assertIn("<li>예산안 승인</li>", kwargs["body_html"])
        self.assertIn("메일을 보냈습니다: someone@example.com".encode(), resp.data)


if __name__ == "__main__":
    unittest.main()
