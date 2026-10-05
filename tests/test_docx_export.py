import os
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

from confluence_agenda.web import confluence_credentials
from confluence_agenda.web.docx_export import (
    DocxExportUnavailable,
    convert_confluence_url_to_docx,
    is_feature_available,
    resolve_token,
)


def _fake_doc2report_modules(convert_fn=None):
    """실제 doc2report 패키지를 설치하지 않고도 docx_export.py의 지연 import가
    받아들일 수 있는 가짜 모듈들을 만든다(sys.modules 스텁)."""
    pkg = types.ModuleType("doc2report")
    pipeline_mod = types.ModuleType("doc2report.pipeline")
    pipeline_mod.convert = convert_fn or (lambda *a, **k: None)
    return {"doc2report": pkg, "doc2report.pipeline": pipeline_mod}


def _without_doc2report():
    return {k: v for k, v in sys.modules.items() if not k.startswith("doc2report")}


class IsFeatureAvailableTest(unittest.TestCase):
    def test_false_when_doc2report_not_installed(self):
        with mock.patch.dict(sys.modules, _without_doc2report(), clear=True), mock.patch.dict(
            os.environ, {"CONFLUENCE_URL": "https://wiki.example.com", "CONFLUENCE_API_TOKEN": "t"}, clear=True
        ):
            self.assertFalse(is_feature_available())

    def test_false_when_confluence_url_not_set(self):
        with mock.patch.dict(sys.modules, _fake_doc2report_modules()), mock.patch.dict(
            os.environ, {"CONFLUENCE_API_TOKEN": "t"}, clear=True
        ):
            self.assertFalse(is_feature_available())

    def test_true_when_url_and_global_token_both_set(self):
        env = {"CONFLUENCE_URL": "https://wiki.example.com", "CONFLUENCE_API_TOKEN": "t"}
        with mock.patch.dict(sys.modules, _fake_doc2report_modules()), mock.patch.dict(
            os.environ, env, clear=True
        ):
            self.assertTrue(is_feature_available())

    def test_true_when_no_global_token_but_per_user_storage_available(self):
        env = {"CONFLUENCE_URL": "https://wiki.example.com"}
        with mock.patch.dict(sys.modules, _fake_doc2report_modules()), mock.patch.dict(
            os.environ, env, clear=True
        ), mock.patch.object(confluence_credentials, "is_configured", return_value=True):
            self.assertTrue(is_feature_available())

    def test_false_when_no_global_token_and_no_per_user_storage(self):
        env = {"CONFLUENCE_URL": "https://wiki.example.com"}
        with mock.patch.dict(sys.modules, _fake_doc2report_modules()), mock.patch.dict(
            os.environ, env, clear=True
        ), mock.patch.object(confluence_credentials, "is_configured", return_value=False):
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


class ConvertConfluenceUrlToDocxTest(unittest.TestCase):
    def test_raises_unavailable_when_doc2report_not_installed(self):
        with mock.patch.dict(sys.modules, _without_doc2report(), clear=True):
            with self.assertRaisesRegex(DocxExportUnavailable, "설치되지 않았습니다"):
                convert_confluence_url_to_docx("https://wiki.example.com/pages/123456", token="t")

    def test_raises_unavailable_when_url_not_configured(self):
        with mock.patch.dict(sys.modules, _fake_doc2report_modules()), mock.patch.dict(
            os.environ, {}, clear=True
        ):
            with self.assertRaisesRegex(DocxExportUnavailable, "CONFLUENCE_URL"):
                convert_confluence_url_to_docx("https://wiki.example.com/pages/123456", token="t")

    def test_raises_unavailable_when_no_token_available(self):
        env = {"CONFLUENCE_URL": "https://wiki.example.com"}
        with mock.patch.dict(sys.modules, _fake_doc2report_modules()), mock.patch.dict(
            os.environ, env, clear=True
        ):
            with self.assertRaisesRegex(DocxExportUnavailable, "PAT"):
                convert_confluence_url_to_docx("https://wiki.example.com/pages/123456")

    def test_returns_bytes_and_safe_filename_on_success(self):
        class _FakeDocument:
            title = "예산안 승인 / 2026"

        class _FakeResult:
            document = _FakeDocument()

        def fake_convert(source, output=None, **kwargs):
            Path(output).write_bytes(b"PK\x03\x04-fake-docx-bytes")
            return _FakeResult()

        env = {"CONFLUENCE_URL": "https://wiki.example.com"}
        with mock.patch.dict(sys.modules, _fake_doc2report_modules(fake_convert)), mock.patch.dict(
            os.environ, env, clear=True
        ):
            data, filename = convert_confluence_url_to_docx(
                "https://wiki.example.com/pages/123456", token="personal-pat"
            )
            # 변환이 끝나면 요청 전 상태(토큰 없음)로 깨끗이 되돌아가 있어야 한다.
            self.assertNotIn("CONFLUENCE_API_TOKEN", os.environ)

        self.assertEqual(data, b"PK\x03\x04-fake-docx-bytes")
        # 파일명으로 쓸 수 없는 문자(/)는 빠지고, 확장자는 .docx로 붙는다.
        self.assertEqual(filename, "예산안 승인  2026.docx")

    def test_falls_back_to_generic_filename_when_title_missing(self):
        class _FakeDocument:
            title = None

        class _FakeResult:
            document = _FakeDocument()

        def fake_convert(source, output=None, **kwargs):
            Path(output).write_bytes(b"data")
            return _FakeResult()

        env = {"CONFLUENCE_URL": "https://wiki.example.com"}
        with mock.patch.dict(sys.modules, _fake_doc2report_modules(fake_convert)), mock.patch.dict(
            os.environ, env, clear=True
        ):
            _, filename = convert_confluence_url_to_docx(
                "https://wiki.example.com/pages/123456", token="t"
            )

        self.assertEqual(filename, "report.docx")

    def test_uses_explicit_token_over_global_env_token_during_call(self):
        seen_tokens = []

        def fake_convert(source, output=None, **kwargs):
            seen_tokens.append(os.environ.get("CONFLUENCE_API_TOKEN"))
            Path(output).write_bytes(b"data")

            class _R:
                class document:
                    title = "t"

            return _R()

        env = {"CONFLUENCE_URL": "https://wiki.example.com", "CONFLUENCE_API_TOKEN": "global-token"}
        with mock.patch.dict(sys.modules, _fake_doc2report_modules(fake_convert)), mock.patch.dict(
            os.environ, env, clear=True
        ):
            convert_confluence_url_to_docx("https://wiki.example.com/pages/123456", token="personal-pat")
            # 호출 동안에는 전달받은 토큰으로 바뀌어 있었고, 끝난 뒤에는 원래 전역 토큰으로 복원된다.
            self.assertEqual(os.environ["CONFLUENCE_API_TOKEN"], "global-token")

        self.assertEqual(seen_tokens, ["personal-pat"])


if __name__ == "__main__":
    unittest.main()
