"""vendor/document-parsing에 복사해 둔 doc2report가 실제로 동작하는지 확인한다.

doc2report는 profiles/*.yaml, rules/*.yaml을 "그 저장소 루트 기준 상대 경로"로
찾는다(vendor/document-parsing/README.md 참고) - src/doc2report만 떼어
옮기면 이 경로 계산이 깨진다. 이 테스트는 그 경로 계산이 vendor 디렉터리
구조에서도 올바르게 맞아떨어지는지, 그리고 실제로 .docx까지 끝까지
만들어지는지를 doc2report 자신의 의존성(pydantic/lxml/python-docx/httpx 등)이
설치된 환경에서만 확인한다 - 안 설치된 환경(예: 이 기능을 안 쓰는 배포)에서는
건너뛴다.
"""

import importlib
import sys
import tempfile
import unittest
from pathlib import Path

_VENDOR_SRC = str(Path(__file__).resolve().parents[1] / "vendor" / "document-parsing" / "src")
if _VENDOR_SRC not in sys.path:
    sys.path.insert(0, _VENDOR_SRC)

try:
    import doc2report  # noqa: F401

    _IMPORT_ERROR = None
except ImportError as exc:  # doc2report 자신의 의존성이 설치되지 않은 환경
    _IMPORT_ERROR = exc


@unittest.skipIf(_IMPORT_ERROR is not None, f"doc2report 의존성이 설치되지 않음: {_IMPORT_ERROR}")
class VendoredDoc2ReportTest(unittest.TestCase):
    def test_profile_and_rules_dirs_resolve_inside_vendor_directory(self):
        from doc2report.profile import PROFILE_DIR
        from doc2report.transform.stylize_ko import RULES_DIR

        self.assertTrue(PROFILE_DIR.is_dir(), f"PROFILE_DIR이 없음: {PROFILE_DIR}")
        self.assertTrue(RULES_DIR.is_dir(), f"RULES_DIR이 없음: {RULES_DIR}")
        self.assertTrue((PROFILE_DIR / "confluence.yaml").is_file())
        self.assertTrue((RULES_DIR / "endings.yaml").is_file())

    def test_confluence_profile_loads(self):
        from doc2report.profile import load_profile

        profile = load_profile("confluence")
        self.assertEqual(profile.name, "confluence")

    def test_confluence_storage_converts_to_a_real_docx_file(self):
        # importlib.reload로 모듈 캐시 영향 없이 매번 깨끗하게 - 테스트 간 공유되는
        # 모듈 전역 상태는 없지만, 혹시 다른 테스트가 sys.modules를 스텁해 둔 채로
        # 끝났을 가능성에 대비해 명시적으로 실제 모듈을 다시 가져온다.
        pipeline = importlib.import_module("doc2report.pipeline")
        sources = importlib.import_module("doc2report.sources")
        profile_mod = importlib.import_module("doc2report.profile")

        storage = "<p>테스트 문단입니다.</p>"
        loaded = sources.LoadedSource(
            text=storage, name="test", base_dir=None, notes=[],
            format="confluence_storage", title="테스트 문서", linked=None,
        )
        doc, _notes = pipeline.load_document(loaded, linked=False)
        prof = profile_mod.load_profile("confluence")

        with tempfile.TemporaryDirectory(prefix="doc2report-vendor-test-") as tmp:
            out_path = Path(tmp) / "out.docx"
            result = pipeline.convert_document(doc, out_path, prof)
            self.assertTrue(out_path.is_file())
            self.assertGreater(out_path.stat().st_size, 0)
            self.assertEqual(result.output, out_path)


if __name__ == "__main__":
    unittest.main()
