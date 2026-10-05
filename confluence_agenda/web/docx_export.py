"""Confluence 페이지를 조회해 Word(.docx)로 변환하는 기능.

wcoffee77/document-parsing(doc2report)을 그대로 가져와 쓴다. 그 저장소의
pipeline.convert()가 "URL -> REST API로 storage XHTML 조회 -> 레이아웃 계산
-> .docx 저장"을 전부 처리하므로, 여기서는 얇게 감싸서 Flask 라우트에서 쓰기
좋은 형태(바이트 + 파일명)로 바꾸는 역할만 한다. 계정 등록 화면이나 토큰
암호화 저장 같은 그쪽 전용 UI(web/server.py, account.py)는 가져오지 않았다 -
대신 이 앱 자신의 로그인(auth.py)에 묶어 confluence_credentials.py가 사용자별
PAT을 저장한다.

CONFLUENCE_URL이 없으면 이 기능 자체가 꺼진다(is_feature_available() False).
토큰(PAT)은 두 가지 경로를 지원한다:
  1. 사용자별 등록 (confluence_credentials.py, 로그인 DB에 암호화 저장) - 우선.
  2. 전역 CONFLUENCE_API_TOKEN 환경변수 - 사용자별 등록이 없을 때 fallback
     (로그인 기능을 안 쓰는 배포, 또는 CLI 전용 사용 등).
doc2report가 설치되지 않은 환경에서도 나머지 기능은 그대로 쓸 수 있다.
"""

from __future__ import annotations

import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Optional, Tuple

from . import confluence_credentials


class DocxExportUnavailable(RuntimeError):
    """doc2report가 설치되지 않았거나, Confluence 연동에 필요한 설정(URL/토큰)이 없을 때."""


def _doc2report_installed() -> bool:
    try:
        import doc2report  # noqa: F401
    except ImportError:
        return False
    return True


def is_url_configured() -> bool:
    return bool(os.environ.get("CONFLUENCE_URL", "").strip())


def is_feature_available() -> bool:
    """화면에 "컨플루언스 → Word" 카드를 보여줄지.

    URL은 전역 설정이 필수고, 토큰은 전역(CONFLUENCE_API_TOKEN) 또는 사용자별
    DB 등록(confluence_credentials)이 가능한 상태면 된다 - 아직 아무도 등록
    전이어도, 등록할 수 있는 상태라면 카드는 보여주고 등록 폼을 그 안에 띄운다.
    실제로 쓸 토큰이 없으면 변환 시점에 안내한다(resolve_token 참고).
    """
    if not _doc2report_installed() or not is_url_configured():
        return False
    if os.environ.get("CONFLUENCE_API_TOKEN", "").strip():
        return True
    return confluence_credentials.is_configured()


def resolve_token(user_id: Optional[str]) -> Optional[str]:
    """이 요청에 쓸 토큰을 고른다 - 로그인한 사용자의 개인 등록 PAT을 우선 쓰고,
    없으면 전역 CONFLUENCE_API_TOKEN(관리자가 .env에 넣어둔 공용 토큰)으로 넘어간다."""
    if user_id:
        personal = confluence_credentials.get_pat(user_id)
        if personal:
            return personal
    env_token = os.environ.get("CONFLUENCE_API_TOKEN", "").strip()
    return env_token or None


_UNSAFE_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|]')


def _safe_filename(title: str) -> str:
    cleaned = _UNSAFE_FILENAME_CHARS.sub("", title).strip()
    return (cleaned or "report") + ".docx"


# doc2report(sources/confluence.py)는 토큰을 os.environ["CONFLUENCE_API_TOKEN"]에서만
# 읽는다(인자로 넘길 방법이 없다) - 그래서 변환 한 번 동안만 전역 환경변수를 이 요청의
# 토큰으로 바꿔 쓰고 끝나면 되돌린다. app.py가 threaded=True로 띄우므로, 동시에 다른
# 사용자가 변환을 요청하면 서로의 토큰을 덮어쓸 수 있어 락으로 직렬화한다 - 사내
# 소규모 팀 도구라 변환 요청이 실제로 겹칠 일은 드물고, 겹쳐도 뒤 요청이 잠깐
# 기다리는 것뿐이라 안전한 쪽을 택했다.
_env_token_lock = threading.Lock()


def convert_confluence_url_to_docx(url: str, *, token: Optional[str] = None) -> Tuple[bytes, str]:
    """Confluence 페이지 URL을 받아 (.docx 바이트, 파일명)을 돌려준다.

    token을 안 주면 전역 CONFLUENCE_API_TOKEN을 쓴다 - 호출부(app.py)는 보통
    resolve_token()으로 사용자별 토큰을 먼저 구해서 넘긴다.

    doc2report 미설치/URL·토큰 미설정이면 DocxExportUnavailable. 그 외 실패
    (페이지를 못 찾음, 권한 없음, 네트워크 오류 등)는 doc2report가 던지는
    RuntimeError가 그대로 올라온다 - 호출부에서 str(exc)로 사용자에게 보여주면 된다.
    """
    try:
        from doc2report.pipeline import convert
    except ImportError as exc:
        raise DocxExportUnavailable(
            "doc2report가 설치되지 않았습니다. requirements.txt를 다시 설치해주세요."
        ) from exc

    if not is_url_configured():
        raise DocxExportUnavailable(
            "CONFLUENCE_URL 환경변수가 설정되지 않았습니다. .env.example을 참고해 설정해주세요."
        )

    effective_token = token or os.environ.get("CONFLUENCE_API_TOKEN", "").strip()
    if not effective_token:
        raise DocxExportUnavailable(
            "Confluence 개인 액세스 토큰(PAT)이 없습니다. 화면에서 내 PAT을 등록하거나, "
            "관리자가 CONFLUENCE_API_TOKEN 환경변수를 설정해야 합니다."
        )

    with _env_token_lock:
        previous = os.environ.get("CONFLUENCE_API_TOKEN")
        os.environ["CONFLUENCE_API_TOKEN"] = effective_token
        try:
            with tempfile.TemporaryDirectory(prefix="docx-export-") as tmp:
                out_path = Path(tmp) / "report.docx"
                result = convert(url, output=out_path)
                data = out_path.read_bytes()
        finally:
            if previous is None:
                os.environ.pop("CONFLUENCE_API_TOKEN", None)
            else:
                os.environ["CONFLUENCE_API_TOKEN"] = previous

    title = (result.document.title or "").strip() or "report"
    return data, _safe_filename(title)
