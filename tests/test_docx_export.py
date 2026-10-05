import sys
import types
import unittest
from pathlib import Path
from unittest import mock

from confluence_agenda.web.docx_export import (
    DocxExportUnavailable,
    convert_confluence_url_to_docx,
    is_configured,
)


def _fake_doc2report_modules(*, configured: bool, convert_fn=None):
    """실제 doc2report 패키지를 설치하지 않고도 docx_export.py의 지연 import가
    받아들일 수 있는 가짜 모듈들을 만든다(sys.modules 스텁)."""
    pkg = types.ModuleType("doc2report")
    sources_pkg = types.ModuleType("doc2report.sources")
    confluence_mod = types.ModuleType("doc2report.sources.confluence")
    confluence_mod.confluence_status = lambda: {"configured": configured}
    pipeline_mod = types.ModuleType("doc2report.pipeline")
    pipeline_mod.convert = convert_fn or (lambda *a, **k: None)
    return {
        "doc2report": pkg,
        "doc2report.sources": sources_pkg,
        "doc2report.sources.confluence": confluence_mod,
        "doc2report.pipeline": pipeline_mod,
    }


class IsConfiguredTest(unittest.TestCase):
    def test_false_when_doc2report_not_installed(self):
        # 이 테스트 환경에는 실제로 doc2report가 설치돼 있지 않다 - 그 상태를 그대로 검증.
        modules_without_doc2report = {
            k: v for k, v in sys.modules.items() if not k.startswith("doc2report")
        }
        with mock.patch.dict(sys.modules, modules_without_doc2report, clear=True):
            self.assertFalse(is_configured())

    def test_true_when_stubbed_module_reports_configured(self):
        with mock.patch.dict(sys.modules, _fake_doc2report_modules(configured=True)):
            self.assertTrue(is_configured())

    def test_false_when_stubbed_module_reports_not_configured(self):
        with mock.patch.dict(sys.modules, _fake_doc2report_modules(configured=False)):
            self.assertFalse(is_configured())


class ConvertConfluenceUrlToDocxTest(unittest.TestCase):
    def test_raises_unavailable_when_doc2report_not_installed(self):
        modules_without_doc2report = {
            k: v for k, v in sys.modules.items() if not k.startswith("doc2report")
        }
        with mock.patch.dict(sys.modules, modules_without_doc2report, clear=True):
            with self.assertRaisesRegex(DocxExportUnavailable, "설치되지 않았습니다"):
                convert_confluence_url_to_docx("https://wiki.example.com/pages/123456")

    def test_raises_unavailable_when_not_configured(self):
        fakes = _fake_doc2report_modules(configured=False)
        with mock.patch.dict(sys.modules, fakes):
            with self.assertRaisesRegex(DocxExportUnavailable, "CONFLUENCE_URL"):
                convert_confluence_url_to_docx("https://wiki.example.com/pages/123456")

    def test_returns_bytes_and_safe_filename_on_success(self):
        class _FakeDocument:
            title = "예산안 승인 / 2026"

        class _FakeResult:
            document = _FakeDocument()

        def fake_convert(source, output=None, **kwargs):
            Path(output).write_bytes(b"PK\x03\x04-fake-docx-bytes")
            return _FakeResult()

        fakes = _fake_doc2report_modules(configured=True, convert_fn=fake_convert)
        with mock.patch.dict(sys.modules, fakes):
            data, filename = convert_confluence_url_to_docx("https://wiki.example.com/pages/123456")

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

        fakes = _fake_doc2report_modules(configured=True, convert_fn=fake_convert)
        with mock.patch.dict(sys.modules, fakes):
            _, filename = convert_confluence_url_to_docx("https://wiki.example.com/pages/123456")

        self.assertEqual(filename, "report.docx")


if __name__ == "__main__":
    unittest.main()
