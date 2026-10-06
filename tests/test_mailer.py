import json
import unittest
from unittest import mock

import requests

from confluence_agenda.mailer import MailConfigError, is_mail_configured, send_report_email


class _FakeResponse:
    def __init__(self, status_code=200, json_body=None, text=""):
        self.status_code = status_code
        self._json_body = json_body if json_body is not None else {"mailId": "mail-1"}
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error", response=self)

    def json(self):
        return self._json_body


class _NonJsonResponse(_FakeResponse):
    def json(self):
        raise ValueError("not json")


FULL_CONFIG = {
    "MAIL_API_TOKEN": "test-token",
    "MAIL_API_SYSTEM_ID": "sys-123",
    "MAIL_API_USER_ID": "user-456",
}


class MailerTest(unittest.TestCase):
    def test_is_mail_configured_false_when_token_unset(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertFalse(is_mail_configured())

    def test_is_mail_configured_true_when_token_set(self):
        with mock.patch.dict("os.environ", {"MAIL_API_TOKEN": "test-token"}, clear=True):
            self.assertTrue(is_mail_configured())

    def test_raises_when_required_env_missing(self):
        for missing in ("MAIL_API_TOKEN", "MAIL_API_SYSTEM_ID", "MAIL_API_USER_ID"):
            env = {k: v for k, v in FULL_CONFIG.items() if k != missing}
            with self.subTest(missing=missing):
                with mock.patch.dict("os.environ", env, clear=True):
                    with self.assertRaisesRegex(MailConfigError, missing):
                        send_report_email("someone@example.com", subject="제목", body_html="<p>본문</p>")

    def test_posts_plain_json_body_not_multipart(self):
        env = {**FULL_CONFIG, "MAIL_API_SENDER_ADDRESS": "bot@example.com"}
        captured = {}

        def _fake_post(url, headers=None, data=None, timeout=None, proxies=None, verify=None):
            captured.update(url=url, headers=headers, data=data, proxies=proxies, verify=verify)
            return _FakeResponse()

        with mock.patch.dict("os.environ", env, clear=True), mock.patch(
            "confluence_agenda.mailer.requests.post", side_effect=_fake_post
        ):
            send_report_email("someone@example.com", subject="분석 리포트", body_html="<p>본문 내용</p>")

        self.assertEqual(
            captured["url"], "https://openapi.samsung.net/mail/api/v2.0/mails/send?userId=user-456"
        )
        self.assertEqual(captured["headers"]["Authorization"], "Bearer test-token")
        self.assertEqual(captured["headers"]["System-ID"], "sys-123")
        self.assertEqual(captured["headers"]["Content-Type"], "application/json;charset=utf-8")
        self.assertIsNone(captured["proxies"])
        self.assertTrue(captured["verify"])

        self.assertIsInstance(captured["data"], bytes)
        payload = json.loads(captured["data"].decode("utf-8"))
        self.assertEqual(payload["subject"], "분석 리포트")
        self.assertEqual(payload["contents"], "<p>본문 내용</p>")
        self.assertEqual(payload["contentType"], "html")
        self.assertEqual(payload["docSecuType"], "PERSONAL")
        self.assertEqual(payload["sender"], {"emailAddress": "bot@example.com"})
        self.assertEqual(
            payload["recipients"], [{"emailAddress": "someone@example.com", "recipientType": "TO"}]
        )

    def test_accepts_multiple_recipients_in_one_call(self):
        captured = {}

        def _fake_post(url, headers=None, data=None, timeout=None, proxies=None, verify=None):
            captured["payload"] = json.loads(data.decode("utf-8"))
            return _FakeResponse()

        with mock.patch.dict("os.environ", FULL_CONFIG, clear=True), mock.patch(
            "confluence_agenda.mailer.requests.post", side_effect=_fake_post
        ):
            send_report_email(
                ["a@example.com", "b@example.com"], subject="제목", body_html="<p>본문</p>"
            )

        self.assertEqual(
            captured["payload"]["recipients"],
            [
                {"emailAddress": "a@example.com", "recipientType": "TO"},
                {"emailAddress": "b@example.com", "recipientType": "TO"},
            ],
        )

    def test_raises_when_recipient_list_empty(self):
        with mock.patch.dict("os.environ", FULL_CONFIG, clear=True):
            with self.assertRaisesRegex(MailConfigError, "받는 사람"):
                send_report_email([], subject="제목", body_html="<p>본문</p>")

    def test_defaults_sender_to_user_id(self):
        captured = {}

        def _fake_post(url, headers=None, data=None, timeout=None, proxies=None, verify=None):
            captured["payload"] = json.loads(data.decode("utf-8"))
            return _FakeResponse()

        with mock.patch.dict("os.environ", FULL_CONFIG, clear=True), mock.patch(
            "confluence_agenda.mailer.requests.post", side_effect=_fake_post
        ):
            send_report_email("someone@example.com", subject="제목", body_html="<p>본문</p>")

        self.assertEqual(captured["payload"]["sender"], {"emailAddress": "user-456"})

    def test_url_encodes_user_id_in_query_string(self):
        env = {**FULL_CONFIG, "MAIL_API_USER_ID": "user&name=x"}
        captured = {}

        with mock.patch.dict("os.environ", env, clear=True), mock.patch(
            "confluence_agenda.mailer.requests.post",
            side_effect=lambda url, **kw: captured.update(url=url) or _FakeResponse(),
        ):
            send_report_email("someone@example.com", subject="제목", body_html="<p>본문</p>")

        self.assertTrue(captured["url"].endswith("?userId=user%26name%3Dx"))

    def test_uses_custom_base_url(self):
        env = {**FULL_CONFIG, "MAIL_API_BASE_URL": "https://mail.internal.example.com/api/v9/"}
        captured = {}

        with mock.patch.dict("os.environ", env, clear=True), mock.patch(
            "confluence_agenda.mailer.requests.post",
            side_effect=lambda url, **kw: captured.update(url=url) or _FakeResponse(),
        ):
            send_report_email("someone@example.com", subject="제목", body_html="<p>본문</p>")

        self.assertEqual(captured["url"], "https://mail.internal.example.com/api/v9/mails/send?userId=user-456")

    def test_raises_on_http_error_status_with_response_body(self):
        with mock.patch.dict("os.environ", FULL_CONFIG, clear=True), mock.patch(
            "confluence_agenda.mailer.requests.post",
            return_value=_FakeResponse(status_code=401, text='{"message":"invalid or expired token"}'),
        ):
            with self.assertRaisesRegex(MailConfigError, "invalid or expired token"):
                send_report_email("someone@example.com", subject="제목", body_html="<p>본문</p>")

    def test_strips_whitespace_from_env_values(self):
        env = {
            "MAIL_API_TOKEN": "  test-token\n",
            "MAIL_API_SYSTEM_ID": " sys-123 ",
            "MAIL_API_USER_ID": "user-456\n",
        }
        captured = {}

        def _fake_post(url, headers=None, data=None, timeout=None, proxies=None, verify=None):
            captured.update(url=url, headers=headers)
            return _FakeResponse()

        with mock.patch.dict("os.environ", env, clear=True), mock.patch(
            "confluence_agenda.mailer.requests.post", side_effect=_fake_post
        ):
            send_report_email("someone@example.com", subject="제목", body_html="<p>본문</p>")

        self.assertEqual(captured["headers"]["Authorization"], "Bearer test-token")
        self.assertEqual(captured["headers"]["System-ID"], "sys-123")
        self.assertTrue(captured["url"].endswith("?userId=user-456"))

    def test_no_proxy_forces_proxies_none(self):
        env = {**FULL_CONFIG, "MAIL_API_NO_PROXY": "true"}
        captured = {}

        with mock.patch.dict("os.environ", env, clear=True), mock.patch(
            "confluence_agenda.mailer.requests.post",
            side_effect=lambda url, **kw: captured.update(proxies=kw.get("proxies")) or _FakeResponse(),
        ):
            send_report_email("someone@example.com", subject="제목", body_html="<p>본문</p>")

        self.assertEqual(captured["proxies"], {"http": None, "https": None})

    def test_verify_ssl_can_be_disabled(self):
        env = {**FULL_CONFIG, "MAIL_API_VERIFY_SSL": "false"}
        captured = {}

        with mock.patch.dict("os.environ", env, clear=True), mock.patch(
            "confluence_agenda.mailer.requests.post",
            side_effect=lambda url, **kw: captured.update(verify=kw.get("verify")) or _FakeResponse(),
        ):
            send_report_email("someone@example.com", subject="제목", body_html="<p>본문</p>")

        self.assertFalse(captured["verify"])

    def test_raises_on_network_error(self):
        with mock.patch.dict("os.environ", FULL_CONFIG, clear=True), mock.patch(
            "confluence_agenda.mailer.requests.post",
            side_effect=requests.ConnectionError("연결 실패"),
        ):
            with self.assertRaisesRegex(MailConfigError, "연결 실패"):
                send_report_email("someone@example.com", subject="제목", body_html="<p>본문</p>")

    def test_succeeds_even_if_response_body_not_json(self):
        with mock.patch.dict("os.environ", FULL_CONFIG, clear=True), mock.patch(
            "confluence_agenda.mailer.requests.post", return_value=_NonJsonResponse()
        ):
            send_report_email("someone@example.com", subject="제목", body_html="<p>본문</p>")

    def test_without_attachments_payload_has_no_attachments_field(self):
        captured = {}

        def _fake_post(url, headers=None, data=None, **kwargs):
            captured["payload"] = json.loads(data)
            return _FakeResponse()

        with mock.patch.dict("os.environ", FULL_CONFIG, clear=True), mock.patch(
            "confluence_agenda.mailer.requests.post", side_effect=_fake_post
        ):
            send_report_email("someone@example.com", subject="제목", body_html="<p>본문</p>")

        self.assertNotIn("attachments", captured["payload"])

    def test_attachments_are_base64_encoded_in_payload(self):
        captured = {}

        def _fake_post(url, headers=None, data=None, **kwargs):
            captured["payload"] = json.loads(data)
            return _FakeResponse()

        with mock.patch.dict("os.environ", FULL_CONFIG, clear=True), mock.patch(
            "confluence_agenda.mailer.requests.post", side_effect=_fake_post
        ):
            send_report_email(
                "someone@example.com",
                subject="제목",
                body_html="<p>본문</p>",
                attachments=[
                    {
                        "filename": "회의록.docx",
                        "content": b"docx-bytes",
                        "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    }
                ],
            )

        attachments = captured["payload"]["attachments"]
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0]["fileName"], "회의록.docx")
        self.assertEqual(
            attachments[0]["contentType"],
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        import base64

        self.assertEqual(base64.b64decode(attachments[0]["data"]), b"docx-bytes")


if __name__ == "__main__":
    unittest.main()
