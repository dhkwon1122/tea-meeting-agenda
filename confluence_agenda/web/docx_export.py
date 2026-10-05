"""Confluence 페이지를 조회해 Word(.docx)로 변환하는 기능.

wcoffee77/document-parsing(doc2report)을 그대로 가져와 쓴다. 그 저장소의
pipeline.convert()가 "URL -> REST API로 storage XHTML 조회 -> 레이아웃 계산
-> .docx 저장"을 전부 처리하므로, 여기서는 얇게 감싸서 Flask 라우트에서 쓰기
좋은 형태(바이트 + 파일명)로 바꾸는 역할만 한다. 계정 등록 화면이나 토큰
암호화 저장(doc2report의 account.py) 같은 그쪽 전용 UI는 가져오지 않는다 -
이 앱은 다른 통합(mailer.py의 MAIL_API_*, auth.py의 DATABASE_URL)과 똑같이
환경변수(.env)로 자격증명을 받는다.

CONFLUENCE_URL / CONFLUENCE_API_TOKEN(및 Cloud면 CONFLUENCE_USERNAME)이
없으면 이 기능 자체가 꺼진다(is_configured() False) - doc2report가 설치되지
않은 환경에서도 나머지 기능은 그대로 쓸 수 있다.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Tuple


class DocxExportUnavailable(RuntimeError):
    """doc2report가 설치되지 않았거나 Confluence 연동 환경변수가 없을 때."""


def is_configured() -> bool:
    try:
        from doc2report.sources.confluence import confluence_status
    except ImportError:
        return False
    return bool(confluence_status().get("configured"))


_UNSAFE_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|]')


def _safe_filename(title: str) -> str:
    cleaned = _UNSAFE_FILENAME_CHARS.sub("", title).strip()
    return (cleaned or "report") + ".docx"


def convert_confluence_url_to_docx(url: str) -> Tuple[bytes, str]:
    """Confluence 페이지 URL을 받아 (.docx 바이트, 파일명)을 돌려준다.

    doc2report 미설치/미설정이면 DocxExportUnavailable. 그 외 실패(페이지를
    못 찾음, 권한 없음, 네트워크 오류 등)는 doc2report가 던지는 RuntimeError가
    그대로 올라온다 - 호출부에서 str(exc)로 사용자에게 보여주면 된다.
    """
    try:
        from doc2report.pipeline import convert
    except ImportError as exc:
        raise DocxExportUnavailable(
            "doc2report가 설치되지 않았습니다. requirements.txt를 다시 설치해주세요."
        ) from exc

    if not is_configured():
        raise DocxExportUnavailable(
            "CONFLUENCE_URL / CONFLUENCE_API_TOKEN 환경변수가 설정되지 않았습니다. "
            ".env.example을 참고해 설정해주세요."
        )

    with tempfile.TemporaryDirectory(prefix="docx-export-") as tmp:
        out_path = Path(tmp) / "report.docx"
        result = convert(url, output=out_path)
        data = out_path.read_bytes()

    title = (result.document.title or "").strip() or "report"
    return data, _safe_filename(title)
