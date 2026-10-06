"""Confluence 페이지를 조회해 Word(.docx)로 변환하는 기능.

wcoffee77/document-parsing(doc2report)을 참고했지만, 그 코드를 가져오지는
않았다 - REST API 호출과 storage XHTML 처리 방식만 참고해서 이 파일에 직접
새로 구현했다. doc2report는 사내 보고서 규격에 맞춘 표 분할·쪽 배치 계산·
한국어 문구 다듬기까지 하는 꽤 큰 파이프라인인데, 여기서는 그 전체를 가져올
필요가 없어서 "본문을 읽을 수 있는 Word 문서로" 수준으로 범위를 줄였다.

지원하는 것: 제목(h1~h6, 아래 "구조적 변환" 설명처럼 번호 체계로 접힘),
문단(굵게/기울임/밑줄/줄바꿈), 목록(ul/ol, 중첩 깊이 반영), 표(칸 병합 중
colspan만 반영, 캡션/주석 자동 첨부), 패널/펼치기류 매크로(rich-text-body가
있으면 그 내용만 펼침, 인라인 위치의 anchor류처럼 보이는 내용이 없는
매크로는 조용히 건너뜀), 다른 페이지를 끌어오는 매크로(include/excerpt-
include/children - doc2report의 sources/confluence.py::LinkedPages를
참고해 직접 구현, 아래 "연결된 페이지" 설명 참고). 지원하지 않는 것
(건너뛰고 자리만 표시): 이미지/첨부 다운로드, 행 병합(rowspan), 본문 링크
(단순 하이퍼링크) 따라가기.

글꼴/서식은 doc2report의 profiles/confluence.yaml(+ extends: default인
profiles/default.yaml) 값을 그대로 옮겼다(_configure_document_styles) -
맑은 고딕, 본문 12pt, 제목(문서 맨 위) 18pt·가운데·밑줄, 표 10pt, 여백
20mm, 표 머리행 음영 F2F2F2.

구조적 변환(_fold_headings_into_levels/_tag_table_captions_and_notes)도
doc2report의 transform/structure.py를 참고해 들여왔다 - 제목(h1~h6)과 그
아래 문단/목록을 "1. → □ → -" 사내 보고서 번호 체계로 접어 넣고(원문에
이미 "1." "□" 같은 말머리가 쳐 있으면 그대로 쓰고 새로 붙이지 않음,
h2 없이 h3부터 시작해도 첫 단계가 0cm가 되도록 전체 최저 단계를 민다 -
이 프로젝트의 builder.py가 쓰는 h3 전용 제목 구조가 바로 이 경우다), 표
바로 위의 꺾쇠 캡션과 표 바로 뒤의 주석(*, ※, 인용문)은 번호 항목이 아니라
표에 붙는 설명으로 처리한다. 다만 "같은 단계의 짧은 항목을 '및'으로
병합"(merge_short_list_items)과 한국어 문장을 개조식 명사형 종결로 바꾸는
변환·LLM 기반 문장 다듬기는 일부러 가져오지 않았다 - doc2report 자신의
confluence.yaml이 Confluence 입력에는 전부 꺼 두는 옵션들이다(각각
text.merge_short_items: false, text.polish: none) - 같은 이유로 여기서도
생략했다. 자세한 설명은 "구조적 변환" 섹션의 주석 참고.

연결된 페이지(include/excerpt-include/children 매크로)는 실제로 다른
페이지를 REST API로 더 불러와 그 자리에 쪽 나눔 + 제목으로 펼친다 - 이
프로젝트 자신의 안건 생성기(builder.py)가 만드는 "(첨부 N) 제목" 하위
페이지를 include로 연결하는 구조와 정확히 맞아떨어진다(포함된 페이지
제목에서 "(첨부 N)" 접두어를 떼는 것도 doc2report의 text.page_title_strip
규칙을 그대로 따옴). 순환 참조/과도한 중첩은 깊이 4단계·전체 40페이지로
제한한다(doc2report의 MAX_LINK_DEPTH/MAX_LINKED_PAGES와 동일한 값).

CONFLUENCE_URL이 없으면 이 기능 자체가 꺼진다(is_feature_available() False).

인증은 Server/Data Center의 개인 액세스 토큰(PAT) 방식이 기본이다 -
CONFLUENCE_USERNAME을 안 채우면 Authorization: Bearer <토큰>으로 보낸다
(doc2report의 sources/confluence.py::_auth_header()가 쓰는 방식을 그대로
따름). CONFLUENCE_USERNAME을 채우면 Cloud용 Basic(이메일+API 토큰)으로
바뀌지만, 이 프로젝트가 실제로 쓰는 환경(사내 Confluence, Server/DC)에서는
절대 채우면 안 된다 - 채워져 있으면 PAT이 Basic 인증 비밀번호로 잘못
보내져 인증이 깨진다.

토큰(PAT)은 두 가지 경로를 지원한다:
  1. 사용자별 등록 (confluence_credentials.py, 로그인 DB에 암호화 저장) - 우선.
     등록 시 verify_token()으로 Confluence에 직접 물어 실제 유효한 PAT인지,
     누구 것인지 확인한다(doc2report의 confluence_whoami() 참고 - Server/DC는
     틀린 토큰에 401 대신 익명 사용자로 응답하는 경우가 있어 상태 코드만으로는
     못 걸러낸다).
  2. 전역 CONFLUENCE_API_TOKEN 환경변수 - 사용자별 등록이 없을 때 fallback
     (로그인 기능을 안 쓰는 배포, 또는 CLI 전용 사용 등).

사내 API 게이트웨이를 거쳐야 하는 환경이면 _request_headers()가 추가로
처리한다(dhkwon1122/Researcher-board의 pipeline/confluence_client.py가 같은
사내 Confluence를 대상으로 실측해 둔 것 참고): requests 기본 User-Agent를
WAF가 스크립트로 식별해 연결을 끊는 문제(CONFLUENCE_USER_AGENT로 curl 스타일
기본값), Authorization 외 다른 헤더명/스킴 요구(CONFLUENCE_AUTH_HEADER/
CONFLUENCE_AUTH_SCHEME), 게이트웨이 전용 보안 헤더(CONFLUENCE_DEP_TICKET/
CONFLUENCE_DATA_CLASSIFICATION) - 전부 .env.example 참고.
"""

from __future__ import annotations

import base64
import html
import os
import re
from io import BytesIO
from pathlib import Path
from typing import Callable, List, Optional, Tuple

import requests

from . import confluence_credentials

try:
    from docx import Document as DocxDocument
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.shared import Mm, Pt
    from docx.table import Table as DocxTable
    from docx.text.paragraph import Paragraph as DocxParagraph
    from lxml import etree
except ImportError:  # python-docx/lxml 미설치 - 이 기능만 비활성화된다.
    DocxDocument = None
    WD_ALIGN_PARAGRAPH = None
    qn = None
    Mm = None
    Pt = None
    DocxTable = None
    DocxParagraph = None
    etree = None

# Confluence storage format이 쓰는 매크로/리소스 네임스페이스. REST API가 주는
# body.storage.value는 이 접두어들이 선언 없이 그냥 쓰인 "조각"이라, 파싱 전에
# 이 둘을 선언하는 가상의 <root>로 감싸야 한다(_parse_storage 참고).
_AC_NS = "http://www.atlassian.com/schema/confluence/4/ac/"
_RI_NS = "http://www.atlassian.com/schema/confluence/4/ri/"


class DocxExportUnavailable(RuntimeError):
    """Confluence 연동에 필요한 설정(URL/토큰)이 없을 때."""


def is_url_configured() -> bool:
    return bool(os.environ.get("CONFLUENCE_URL", "").strip())


def _rendering_dependencies_installed() -> bool:
    return DocxDocument is not None and etree is not None


def is_feature_available() -> bool:
    """화면에 "컨플루언스 → Word" 카드를 보여줄지.

    python-docx/lxml이 설치돼 있어야 하고, URL은 전역 설정이 필수다. 토큰은
    전역(CONFLUENCE_API_TOKEN) 또는 사용자별 DB 등록(confluence_credentials)이
    가능한 상태면 된다 - 아직 아무도 등록 전이어도, 등록할 수 있는 상태라면
    카드는 보여주고 등록 폼을 그 안에 띄운다.
    """
    if not _rendering_dependencies_installed() or not is_url_configured():
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


# ── Confluence REST API 호출 ────────────────────────────────────────────

_PAGE_ID_PATTERNS = (
    re.compile(r"/pages/(\d+)(?:/|$)"),
    re.compile(r"[?&]pageId=(\d+)"),
)


def _page_id_from_url(url: str) -> Optional[str]:
    for pattern in _PAGE_ID_PATTERNS:
        match = pattern.search(url)
        if match:
            return match.group(1)
    if url.isdigit():
        return url
    return None


_REST_API_SUFFIX = re.compile(r"/rest/api/?$", re.IGNORECASE)


def _normalize_base_url(url: str) -> str:
    """끝의 슬래시와, 있다면 '/rest/api'까지 뗀다 - 위키 주소와 REST 게이트웨이
    주소 둘 중 뭘 CONFLUENCE_URL에 넣어도 되게(코드가 항상 그 뒤에 /rest/api/...를 붙인다)."""
    return _REST_API_SUFFIX.sub("", url.rstrip("/"))


def _auth_header(token: str) -> tuple:
    """(헤더 이름, 헤더 값). 기본은 표준 "Authorization: Bearer <토큰>"(Server/DC
    PAT)이지만, 사내 API 게이트웨이가 다른 헤더명/스킴을 요구하면 CONFLUENCE_AUTH_HEADER/
    CONFLUENCE_AUTH_SCHEME로 바꿀 수 있다(dhkwon1122/Researcher-board의
    pipeline/confluence_client.py::_auth_header()가 같은 사내망에서 실측해 쓰는 방식).
    CONFLUENCE_AUTH_SCHEME을 빈 문자열로 두면 스킴 접두사 없이 토큰 값만 그대로 싣는다.

    CONFLUENCE_USERNAME이 설정돼 있으면(Cloud) 이 커스터마이징과 무관하게 Basic
    인증(이메일+API 토큰)으로 보낸다 - 사내 Server/DC 환경에서는 이 값을 비워둬야 한다."""
    username = os.environ.get("CONFLUENCE_USERNAME", "").strip()
    if username:
        basic = base64.b64encode(f"{username}:{token}".encode()).decode()
        return "Authorization", f"Basic {basic}"

    header_name = os.environ.get("CONFLUENCE_AUTH_HEADER", "").strip() or "Authorization"
    scheme = os.environ.get("CONFLUENCE_AUTH_SCHEME", "Bearer").strip()
    value = f"{scheme} {token}" if scheme else token
    return header_name, value


def _gateway_headers() -> dict:
    """사내 API 게이트웨이가 REST 호출에 추가로 요구하는 보안 정책 헤더
    (dhkwon1122/Researcher-board의 _extra_headers()와 같은 사내 게이트웨이 대상 -
    표준 Confluence API 스펙이 아니라 사내 정책 헤더라 값이 없으면 보내지 않는다).
    헤더 "이름" 자체도 .env로 바꿀 수 있다 - 게이트웨이가 요구하는 정확한 헤더명이
    CONFLUENCE_DEP_TICKET_HEADER/CONFLUENCE_DATA_CLASSIFICATION_HEADER 기본값과
    한 글자라도 다르면 게이트웨이가 못 알아보고 인증 실패로 처리할 수 있다."""
    headers = {}
    dep_ticket = os.environ.get("CONFLUENCE_DEP_TICKET", "").strip()
    if dep_ticket:
        header_name = os.environ.get("CONFLUENCE_DEP_TICKET_HEADER", "").strip() or "X-Dep-Ticket"
        headers[header_name] = dep_ticket
    data_classification = os.environ.get("CONFLUENCE_DATA_CLASSIFICATION", "").strip()
    if data_classification:
        header_name = (
            os.environ.get("CONFLUENCE_DATA_CLASSIFICATION_HEADER", "").strip()
            or "X-Data-Classification"
        )
        headers[header_name] = data_classification
    return headers


def _request_headers(token: str) -> dict:
    headers = _gateway_headers()
    auth_name, auth_value = _auth_header(token)
    headers[auth_name] = auth_value
    # Accept: application/json - 사내 게이트웨이가 인증 실패 응답을 JSON이 아니라
    # XML로 내려줄 때가 있어(클라이언트가 원하는 형식을 안 밝히면 게이트웨이
    # 기본값으로 응답하는 경우가 흔함), 에러 응답이라도 일관되게 JSON으로 받으려고
    # 명시한다(Researcher-board에서 실측).
    headers.setdefault("Accept", "application/json")
    # User-Agent - 같은 URL/인증 헤더로도 curl은 200인데 requests(기본 UA가
    # "python-requests/x.y.z"로 스크립트임이 드러남)는 WAF/게이트웨이가 연결을
    # 끊어버리는 경우가 실제로 있었다(Researcher-board에서 실측, "Remote end
    # closed connection without response"). curl 스타일 UA를 기본값으로 둔다.
    headers.setdefault(
        "User-Agent", os.environ.get("CONFLUENCE_USER_AGENT", "").strip() or "curl/8.0.0"
    )
    return headers


_CA_BUNDLE_ENV_VARS = ("CONFLUENCE_CA_BUNDLE", "REQUESTS_CA_BUNDLE", "SSL_CERT_FILE")


def _ssl_verify():
    """CA 인증서 경로를 찾아 반환한다 - 파일이 실제로 있을 때만. requests는
    verify=에 존재하지 않는 경로를 주면 RequestException이 아니라 그냥
    OSError를 던져서("Could not find a suitable TLS CA certificate bundle,
    invalid path: ...") 우리 try/except가 못 잡고 혼란스러운 에러로 샐 수
    있다 - 특히 Docker로 띄울 때 호스트의 절대경로를 그대로 .env에 넣어두면
    컨테이너 안에는 그 경로가 없어서 매번 이렇게 깨진다(흔한 실수). 파일이
    없으면 조용히 기본 인증서로 넘어간다."""
    for key in _CA_BUNDLE_ENV_VARS:
        path = os.environ.get(key, "").strip()
        if not path:
            continue
        if Path(path).is_file():
            return path
        print(
            f"[docx_export] {key}={path} 이지만 그 경로에 파일이 없어 무시합니다"
            "(기본 인증서로 시도). Docker로 띄운 상태라면 호스트 경로가 아니라 "
            "컨테이너 안에서 보이는 경로를 줘야 합니다 - .env.example의 "
            "CONFLUENCE_CA_BUNDLE 설명을 참고하세요."
        )
    return True


def _parse_bool_env(value: Optional[str], *, default: bool) -> bool:
    if value is None or not value.strip():
        return default
    return value.strip().lower() not in ("false", "0", "no", "off")


def _proxies():
    """사내 프록시(HTTP_PROXY/HTTPS_PROXY)가 사내 Confluence API 호출을 제대로
    못 넘기는 경우가 있다(mailer.py의 MAIL_API_NO_PROXY와 같은 문제).
    CONFLUENCE_NO_PROXY=true면 환경변수 프록시를 무시하고 이 호출만 직접
    나가도록 강제한다."""
    no_proxy = _parse_bool_env(os.environ.get("CONFLUENCE_NO_PROXY"), default=False)
    return {"http": None, "https": None} if no_proxy else None


def _wrap_connection_error(exc: Exception) -> RuntimeError:
    """requests가 던지는 연결/SSL 예외를 구체적인 조치 안내로 감싼다.

    SSLError는 원인별로 조치가 전혀 다른데, 화면/로그에는 파이썬 예외
    텍스트만 짧게 보여서 사용자가 그 안의 세부(자체 서명/체인 불완전/
    발급자 못 찾음 등)를 매번 옮겨 적어 알려줘야 했다 - 대신 지금 실제로
    적용 중인 verify 값(_ssl_verify())을 같이 보여줘서, "기본 인증서로도
    사내 루트 CA를 못 믿는 것"과 "CONFLUENCE_CA_BUNDLE로 지정한 파일 자체가
    문제인 것"을 구분해 다음에 뭘 확인해야 하는지 바로 알 수 있게 한다.
    """
    if isinstance(exc, requests.exceptions.SSLError):
        if "WRONG_VERSION_NUMBER" in str(exc):
            # 인증서 신뢰 문제가 아니다 - TLS가 아닌 응답을 받았다는 뜻으로,
            # https://로 접속했는데 그 주소/포트가 실제로는 http만 쓰는 경우에
            # 정확히 이 에러가 난다(dhkwon1122/Researcher-board가 겪은 최종
            # 원인도 CONFLUENCE_GATEWAY_BASE_URL의 http/https 스킴 오타였다).
            return RuntimeError(
                f"Confluence 서버 연결 실패(SSL 프로토콜 불일치): {exc}\n"
                "이건 인증서를 못 믿는 게 아니라 'TLS가 아닌 응답을 받았다'는 뜻입니다 - "
                "CONFLUENCE_URL이 https://로 시작하는데 실제 그 주소/포트는 http만 "
                "쓰는 경우에 정확히 이렇게 납니다. CONFLUENCE_URL의 스킴(http vs "
                "https)과 포트 번호를 IT/보안팀이 안내한 값과 한 글자도 다르지 않게 "
                "다시 확인하세요 - 둘 다 맞다면 http://로 한번 바꿔서 시도해보세요."
            )
        verify = _ssl_verify()
        if verify is True:
            hint = (
                "CONFLUENCE_CA_BUNDLE 등을 안 줬거나 지정한 경로에 파일이 없어서 "
                "기본(시스템) 인증서로 검증했는데도 실패했습니다 - 사내 루트 CA가 "
                "신뢰 저장소에 없는 것으로 보입니다. Docker로 띄웠다면: "
                "(1) certs/ 안의 파일을 텍스트 에디터로 열어 '-----BEGIN "
                "CERTIFICATE-----'로 시작하는 PEM 형식이 맞는지 확인(Windows에서 "
                "내보낸 .cer/.p7b는 대개 이 형식이 아니라 실패함), "
                "(2) 고친 뒤 이미지를 다시 빌드했는지 확인(코드만 pull하고 재빌드를 "
                "안 하면 예전 이미지가 그대로 실행됨)."
            )
        else:
            hint = (
                f"CONFLUENCE_CA_BUNDLE={verify} 로 검증했는데도 실패했습니다 - 이 "
                "파일이 실제 사내 루트 CA가 맞는지, PEM 형식인지, 중간 인증서까지 "
                "포함된 전체 체인인지 확인하세요."
            )
        return RuntimeError(f"Confluence 서버 연결 실패(SSL 인증서 검증 실패): {exc}\n{hint}")

    return RuntimeError(
        f"Confluence 서버 연결 실패: {exc}\n"
        "사내망 SSL 인증서 문제일 수 있습니다 - CONFLUENCE_CA_BUNDLE 환경변수로 "
        "사내 루트 인증서(.crt/.pem) 경로를 지정해 보세요."
    )


def verify_token(token: str) -> str:
    """이 PAT이 실제로 유효한지, Confluence가 보기에 누구 것인지 확인한다.

    Server/Data Center는 토큰이 틀려도 401이 아니라 익명 사용자로 응답하는
    경우가 있어서(wcoffee77/document-parsing의 confluence_whoami()가 실측해
    남긴 주의사항), 상태 코드만 보면 틀린 토큰을 "유효함"으로 착각할 수 있다.
    PAT을 등록할 때(app.py의 save_confluence_pat) 이 확인을 거쳐, 등록자가
    적은 이름이 아니라 Confluence가 확인한 실제 사용자 이름으로 "누구 토큰인지"
    보여준다(토큰 도용 방지).

    반환값은 "표시 이름 (로그인 ID)" 형태. 실패하면 RuntimeError.
    """
    if not is_url_configured():
        raise DocxExportUnavailable(
            "CONFLUENCE_URL 환경변수가 설정되지 않았습니다. .env.example을 참고해 설정해주세요."
        )

    base_url = _normalize_base_url(os.environ.get("CONFLUENCE_URL", "").strip())
    try:
        resp = requests.get(
            f"{base_url}/rest/api/user/current",
            headers=_request_headers(token),
            timeout=30,
            verify=_ssl_verify(),
            proxies=_proxies(),
        )
    except (requests.RequestException, OSError) as exc:
        raise _wrap_connection_error(exc) from exc

    if resp.status_code == 401:
        raise RuntimeError("PAT이 올바르지 않습니다(401).")
    if resp.status_code >= 400:
        raise RuntimeError(f"Confluence API 오류 (HTTP {resp.status_code}): {resp.text[:300]}")

    data = resp.json()
    if data.get("type") == "anonymous" or not (data.get("displayName") or data.get("username")):
        raise RuntimeError(
            "PAT이 인정되지 않았습니다(익명 사용자로 응답받음) - 토큰을 다시 확인하세요. "
            "Server/Data Center는 토큰이 틀려도 401 대신 이렇게 응답하는 경우가 있습니다."
        )
    name = data.get("displayName") or data.get("username")
    login = data.get("username") or data.get("email") or ""
    return f"{name} ({login})" if login and login != name else name


def diagnose_connection(token: Optional[str] = None) -> List[str]:
    """Confluence 연결이 안 될 때 어느 단계에서 막히는지 확인하는 진단 함수.

        python -m confluence_agenda.web.confluence_check [토큰]

    auth.diagnose_login()과 같은 역할 - 화면의 에러 메시지는 뭉뚱그려 보여줄
    수밖에 없어서(사용자에게 토큰 값 등 민감한 세부를 노출하면 안 됨), 실제
    원인은 이 함수를 직접 실행해서 추적한다. 이 앱을 띄운 것과 같은 환경
    (.env 포함, Docker라면 컨테이너 안)에서 실행해야 의미가 있다 - 특히
    CONFLUENCE_CA_BUNDLE 같은 경로 값은 호스트에서 실행하면 있는 파일이
    컨테이너 안에는 없을 수 있어서, 반드시 실제 배포 환경에서 돌려봐야 한다.
    """
    lines: List[str] = []

    url = os.environ.get("CONFLUENCE_URL", "").strip()
    if not url:
        lines.append("✗ CONFLUENCE_URL 환경변수가 비어있습니다.")
        return lines
    base_url = _normalize_base_url(url)
    lines.append(f"✓ CONFLUENCE_URL 설정됨 → 실제 요청 기준 주소: {base_url}")

    effective_token = token or resolve_token(None)
    if not effective_token:
        lines.append("✗ 쓸 수 있는 토큰(PAT)이 없습니다 - 인자로 넘기거나 CONFLUENCE_API_TOKEN을 설정하세요.")
        return lines
    lines.append(
        "✓ 토큰 확인됨 (인자로 받음)" if token else "✓ 토큰 확인됨 (CONFLUENCE_API_TOKEN 환경변수)"
    )

    username = os.environ.get("CONFLUENCE_USERNAME", "").strip()
    lines.append(
        f"  인증 방식: Basic(Cloud, CONFLUENCE_USERNAME={username})" if username
        else "  인증 방식: Bearer(Server/DC, PAT) - CONFLUENCE_USERNAME 비어있음"
    )

    for key in _CA_BUNDLE_ENV_VARS:
        path = os.environ.get(key, "").strip()
        if not path:
            continue
        exists = Path(path).is_file()
        mark = "✓" if exists else "✗"
        note = "" if exists else " ← 이 경로에 파일이 없음(Docker라면 컨테이너 안 경로가 맞는지 확인)"
        lines.append(f"{mark} {key}={path}{note}")
    verify = _ssl_verify()
    lines.append(f"  실제 적용될 SSL 검증: {verify if verify is not True else '기본 인증서(True)'}")

    no_proxy = _parse_bool_env(os.environ.get("CONFLUENCE_NO_PROXY"), default=False)
    has_proxy_env = bool(os.environ.get("HTTP_PROXY") or os.environ.get("HTTPS_PROXY"))
    lines.append(
        f"  프록시: CONFLUENCE_NO_PROXY={no_proxy} "
        f"(HTTP_PROXY/HTTPS_PROXY {'설정됨' if has_proxy_env else '미설정'})"
    )

    # X-Dep-Ticket/X-Data-Classification - 표준 Confluence API 스펙이 아니라
    # 사내 게이트웨이가 요구하는 보안 정책 헤더다. dhkwon1122/Researcher-board가
    # 같은 사내 Confluence를 2026-09월에 조회할 때, 보안정책이 바뀌면서 이
    # 헤더 없이는 403이 났고 값을 채운 뒤에야 됐다는 기록이 있다 - 등록이
    # 막힌다면 가장 먼저 의심해볼 것(IT/보안팀에서 티켓 번호·분류값을 받아
    # CONFLUENCE_DEP_TICKET/CONFLUENCE_DATA_CLASSIFICATION에 넣어야 함).
    dep_ticket_set = bool(os.environ.get("CONFLUENCE_DEP_TICKET", "").strip())
    data_class_set = bool(os.environ.get("CONFLUENCE_DATA_CLASSIFICATION", "").strip())
    lines.append(
        f"  {'✓' if dep_ticket_set else '✗'} CONFLUENCE_DEP_TICKET "
        f"{'설정됨' if dep_ticket_set else '미설정'}"
        + ("" if dep_ticket_set else " ← 사내 게이트웨이가 이 헤더를 요구할 수 있음(IT/보안팀 문의)")
    )
    lines.append(
        f"  {'✓' if data_class_set else '✗'} CONFLUENCE_DATA_CLASSIFICATION "
        f"{'설정됨' if data_class_set else '미설정'}"
    )

    headers = _request_headers(effective_token)
    lines.append(f"  보낼 헤더 이름: {', '.join(sorted(headers.keys()))}")

    verify_url = f"{base_url}/rest/api/user/current"
    lines.append(f"→ 실제 요청 URL: GET {verify_url}")
    try:
        owner = verify_token(effective_token)
    except Exception as exc:  # noqa: BLE001 - 진단 스크립트라 원인 분류 없이 그대로 보여준다
        lines.append(f"✗ 요청 실패: {type(exc).__name__}: {exc}")
        if not dep_ticket_set:
            lines.append(
                "  ↳ CONFLUENCE_DEP_TICKET이 비어있는데 403/401이 났다면 이게 원인일 가능성이 높다."
            )
        return lines

    lines.append(f"✓ 연결 성공 - 확인된 토큰 소유자: {owner}")
    lines.append(
        f"  (참고) 실제 페이지 조회 시 URL 형태: {base_url}/rest/api/content/<페이지ID>"
    )
    return lines


def _fetch_page(base_url: str, page_id: str, token: str) -> dict:
    try:
        resp = requests.get(
            f"{base_url}/rest/api/content/{page_id}",
            params={"expand": "body.storage,space"},
            headers=_request_headers(token),
            timeout=30,
            verify=_ssl_verify(),
            proxies=_proxies(),
        )
    except (requests.RequestException, OSError) as exc:
        raise _wrap_connection_error(exc) from exc

    if resp.status_code == 401:
        raise RuntimeError("Confluence 인증 실패(401). 토큰(PAT)을 확인하세요.")
    if resp.status_code == 403:
        raise RuntimeError(f"페이지 {page_id}에 접근 권한이 없습니다(403).")
    if resp.status_code == 404:
        raise RuntimeError(f"페이지 {page_id}를 찾을 수 없습니다(404). URL을 확인하세요.")
    if resp.status_code >= 400:
        raise RuntimeError(f"Confluence API 오류 (HTTP {resp.status_code}): {resp.text[:300]}")
    return resp.json()


def _find_page_by_title(base_url: str, token: str, title: str, space: Optional[str]) -> Optional[dict]:
    """제목(+스페이스)으로 페이지를 찾는다 - include/excerpt-include 매크로가
    가리키는 대상(doc2report의 LinkedPages._find()와 같은 엔드포인트)."""
    params = {"title": title, "expand": "body.storage,space", "limit": 1}
    if space:
        params["spaceKey"] = space
    try:
        resp = requests.get(
            f"{base_url}/rest/api/content",
            params=params,
            headers=_request_headers(token),
            timeout=30,
            verify=_ssl_verify(),
            proxies=_proxies(),
        )
    except (requests.RequestException, OSError) as exc:
        raise _wrap_connection_error(exc) from exc
    if resp.status_code != 200:
        return None
    results = resp.json().get("results") or []
    return results[0] if results else None


def _fetch_child_pages(base_url: str, token: str, page_id: str) -> List[dict]:
    """children 매크로가 가리키는 하위 페이지 목록(doc2report의
    LinkedPages._children()과 같은 엔드포인트)."""
    try:
        resp = requests.get(
            f"{base_url}/rest/api/content/{page_id}/child/page",
            params={"limit": 200, "expand": "body.storage,space"},
            headers=_request_headers(token),
            timeout=30,
            verify=_ssl_verify(),
            proxies=_proxies(),
        )
    except (requests.RequestException, OSError) as exc:
        raise _wrap_connection_error(exc) from exc
    if resp.status_code != 200:
        return []
    return resp.json().get("results") or []


# ── 연결된 페이지(include/excerpt-include/children) 펼치기 ──────────────
#
# doc2report의 sources/confluence.py::LinkedPages를 참고해 직접 구현했다(코드
# 자체를 들여오지는 않음). storage XHTML을 파싱한 트리 위에서, 매크로 노드를
# 실제로 불러온 하위 페이지의 내용(쪽 나눔 + 제목 + 본문)으로 그 자리에서
# 바꿔치기한다 - 그 뒤에 이어지는 렌더링(_render_blocks)은 이게 원래부터
# 거기 있던 내용인 것처럼 그냥 처리한다.

_LINKED_MACRO_NAMES = {"include", "excerpt-include", "children"}

# "(첨부 1) 안건1" 같은 하위 페이지 제목에서 "(첨부 N)" 접두어를 뗀다 -
# builder.py가 만드는 제목 규칙과 doc2report의 text.page_title_strip 규칙을
# 그대로 따름.
_TITLE_STRIP_RE = re.compile(r"^\s*\(\s*첨부\s*[0-9]*\s*\)\s*")


def _clean_included_title(title: str) -> str:
    return _TITLE_STRIP_RE.sub("", title or "").strip()


class _LinkContext:
    """include/excerpt-include/children 매크로를 펼치는 동안의 상태(기준
    URL/토큰/스페이스 + 이미 포함한 페이지 집합 + 누적 로드 수). 순환 참조나
    과도한 중첩을 doc2report와 같은 한도(MAX_DEPTH=4, MAX_PAGES=40)로 막는다."""

    MAX_DEPTH = 4
    MAX_PAGES = 40

    def __init__(self, base_url: str, token: str, space: Optional[str], root_page_id: str):
        self.base_url = base_url
        self.token = token
        self.space = space
        self.visited = {root_page_id}
        self.loaded = 0


def _link_limit_reached(ctx: _LinkContext, depth: int) -> bool:
    return depth >= _LinkContext.MAX_DEPTH or ctx.loaded >= _LinkContext.MAX_PAGES


def _extract_link_target(
    macro: etree._Element,
) -> Optional[Tuple[Optional[str], Optional[str], Optional[str]]]:
    """매크로 안의 ac:parameter > ac:link > ri:page에서 (페이지ID, 제목, 스페이스)를
    뽑는다 - builder.py가 실제로 만드는 include 매크로는 ri:content-title만
    쓰고 ri:space-key는 없는 형태라, 그 경우 스페이스는 ctx.space(원본 페이지의
    스페이스)로 보충한다."""
    for param in macro.findall("ac:parameter", namespaces={"ac": _AC_NS}):
        link = param.find("ac:link", namespaces={"ac": _AC_NS})
        if link is None:
            continue
        page_ref = link.find("ri:page", namespaces={"ri": _RI_NS})
        if page_ref is None:
            continue
        content_id = page_ref.get(f"{{{_RI_NS}}}content-id")
        title = page_ref.get(f"{{{_RI_NS}}}content-title")
        space = page_ref.get(f"{{{_RI_NS}}}space-key")
        if content_id or title:
            return content_id, title, space
    return None


def _resolve_target_page(
    ctx: _LinkContext, content_id: Optional[str], title: Optional[str], space: Optional[str]
) -> Optional[dict]:
    if content_id:
        try:
            return _fetch_page(ctx.base_url, content_id, ctx.token)
        except Exception:
            return None
    if title:
        return _find_page_by_title(ctx.base_url, ctx.token, title, space or ctx.space)
    return None


def _replace_macro_with_placeholder(macro: etree._Element, message: str) -> None:
    placeholder = etree.Element("p")
    placeholder.text = message
    macro.addnext(placeholder)
    macro.getparent().remove(macro)


def _splice_elements_in_place(macro: etree._Element, new_elements: List[etree._Element]) -> None:
    anchor = macro
    for new_elem in new_elements:
        anchor.addnext(new_elem)
        anchor = new_elem
    macro.getparent().remove(macro)


def _page_section_elements(title: str, sub_root: etree._Element) -> List[etree._Element]:
    """쪽 나눔 + (접두어를 뗀) 제목 + 하위 페이지 본문으로 이어지는 요소 목록을
    만든다 - include/children 둘 다 이걸로 매크로 자리를 채운다. "pagetitle"은
    진짜 Confluence 태그가 아니라 _fold_headings_into_levels가 doc2report의
    Heading.page_title과 똑같이 다루는(번호 체계에 접지 않고 문서 제목 서식을
    그대로 쓰며, 항목 번호를 거기서부터 다시 센다) 합성 태그다."""
    pagebreak = etree.Element("pagebreak")
    heading = etree.Element("pagetitle")
    heading.text = _clean_included_title(title)
    return [pagebreak, heading] + list(sub_root)


def _resolve_linked_pages(
    container: etree._Element, ctx: _LinkContext, current_page_id: str, depth: int
) -> None:
    macros = [
        m
        for m in container.iter(f"{{{_AC_NS}}}structured-macro")
        if (m.get(f"{{{_AC_NS}}}name") or "") in _LINKED_MACRO_NAMES
    ]
    for macro in macros:
        name = macro.get(f"{{{_AC_NS}}}name")
        if name == "children":
            _expand_children_macro(macro, ctx, current_page_id, depth)
        else:
            _expand_include_macro(macro, ctx, depth)


def _expand_include_macro(macro: etree._Element, ctx: _LinkContext, depth: int) -> None:
    target = _extract_link_target(macro)
    if target is None or _link_limit_reached(ctx, depth):
        _replace_macro_with_placeholder(
            macro, "[연결된 페이지 - 가져올 수 없거나 중첩 한도를 넘어 건너뛰었습니다]"
        )
        return

    content_id, title, space = target
    page = _resolve_target_page(ctx, content_id, title, space)
    if page is None:
        _replace_macro_with_placeholder(
            macro, f"[연결된 페이지({title or content_id}) - 찾을 수 없습니다]"
        )
        return

    page_id = page.get("id")
    if not page_id or page_id in ctx.visited:
        _replace_macro_with_placeholder(
            macro,
            f"[연결된 페이지({page.get('title') or title}) - "
            "이미 포함되어 순환을 막기 위해 건너뛰었습니다]",
        )
        return

    storage_html = page.get("body", {}).get("storage", {}).get("value")
    if not storage_html:
        _replace_macro_with_placeholder(
            macro, f"[연결된 페이지({page.get('title')}) - 본문이 없습니다]"
        )
        return

    ctx.visited.add(page_id)
    ctx.loaded += 1
    sub_root = _parse_storage(storage_html)
    _resolve_linked_pages(sub_root, ctx, page_id, depth + 1)
    _splice_elements_in_place(macro, _page_section_elements(page.get("title") or title or "", sub_root))


def _expand_children_macro(
    macro: etree._Element, ctx: _LinkContext, current_page_id: str, depth: int
) -> None:
    target = _extract_link_target(macro)
    parent_page_id = target[0] if target and target[0] else current_page_id
    if _link_limit_reached(ctx, depth):
        _replace_macro_with_placeholder(macro, "[하위 페이지 목록 - 중첩 한도를 넘어 건너뛰었습니다]")
        return

    children = _fetch_child_pages(ctx.base_url, ctx.token, parent_page_id)
    sections: List[etree._Element] = []
    for child in children:
        if _link_limit_reached(ctx, depth):
            break
        child_id = child.get("id")
        if not child_id or child_id in ctx.visited:
            continue
        storage_html = child.get("body", {}).get("storage", {}).get("value")
        if not storage_html:
            continue
        ctx.visited.add(child_id)
        ctx.loaded += 1
        sub_root = _parse_storage(storage_html)
        _resolve_linked_pages(sub_root, ctx, child_id, depth + 1)
        sections.extend(_page_section_elements(child.get("title") or "", sub_root))

    if not sections:
        _replace_macro_with_placeholder(macro, "[하위 페이지 없음]")
        return
    _splice_elements_in_place(macro, sections)


# ── storage XHTML 파싱 ──────────────────────────────────────────────────


def _parse_storage(storage_html: str) -> etree._Element:
    wrapped = f'<root xmlns:ac="{_AC_NS}" xmlns:ri="{_RI_NS}">{storage_html}</root>'
    parser = etree.XMLParser(recover=True, resolve_entities=False)
    root = etree.fromstring(wrapped.encode("utf-8"), parser=parser)
    if root is None:
        raise RuntimeError("Confluence storage XHTML을 해석하지 못했습니다.")
    return root


def _local(tag: str) -> str:
    return etree.QName(tag).localname if isinstance(tag, str) else ""


def _is_ac(element, name: str) -> bool:
    return element.tag == f"{{{_AC_NS}}}{name}"


_WHITESPACE_RE = re.compile(r"\s+")


def _clean_text(text: Optional[str]) -> str:
    return _WHITESPACE_RE.sub(" ", text) if text else ""


# ── 구조적 변환: 제목을 번호 체계로 접어넣기 + 표 캡션/주석 자동 첨부 ──────
#
# doc2report의 transform/structure.py를 참고해(코드는 가져오지 않고 알고리즘만
# 참고해 이 파일에 새로 구현) 제목(h1~h6)과 그 아래 문단/목록을 "1. → □ → -"
# 식 사내 보고서 단락 체계로 접어 넣는다(그 모듈 docstring의 예: "## 추진 배경
# → 1. 추진 배경"). doc2report의 confluence.yaml은 auto_markers(말머리 없는
# 제목에 새 말머리를 붙이는 옵션)를 꺼 두지만 - "이미 Confluence 제목 스타일로
# 구분된 문서라 말머리가 필요 없다"는 판단 - 여기서는 default.yaml 쪽인
# auto_markers=true를 그대로 썼다. 그 예시가 보여주는 "제목에 번호가 실제로
# 보이는" 결과가 이 기능의 핵심이라고 판단했기 때문이다. 원문에 이미 "1."
# "□" 같은 말머리가 쳐 있으면(keep_leading_markers) 그건 그대로 쓰고 새로
# 붙이지 않는다.
#
# 표 캡션/주석 자동 첨부(attach_table_captions/attach_table_notes)도 그대로
# 들여왔다 - 표 바로 위의 꺾쇠 캡션("【사업현황】")과 표 바로 뒤의 주석(*, ※,
# 인용문)은 번호 항목이 되지 않고 표에 붙는 설명으로 처리된다.
#
# 다만 "같은 단계의 짧은 항목을 '및'으로 병합"(merge_short_list_items)은
# 일부러 들여오지 않았다 - doc2report 자신의 confluence.yaml이 "및 병합도
# 문장을 바꾸는 일"이라며 Confluence 입력에는 이 옵션을 꺼 둔다
# (text.merge_short_items: false). 같은 이유로 여기서도 생략했다. 한국어
# 문장을 개조식 명사형 종결로 바꾸는 변환(gaechosik/noun_ending)과 LLM 기반
# 문장 다듬기도 범위 밖이다 - 둘 다 doc2report의 confluence.yaml 자체가
# Confluence 입력에는 끄는 옵션이고(text.polish: none), 특히 문장 다듬기는
# 별도의 한국어 어미 변환 규칙 시스템이나 LLM 호출이 필요해 이 기능의 범위를
# 크게 넘어선다.

_NUMBERING_LEVELS = [
    {"marker": "{n}.", "indent_mm": 0, "hanging_mm": 7},
    {"marker": "□", "indent_mm": 4, "hanging_mm": 6},
    {"marker": "-", "indent_mm": 8, "hanging_mm": 5},
    {"marker": "·", "indent_mm": 12, "hanging_mm": 5},
]
_HEADING_FOLD_BASE = 2  # h2가 첫 단계(depth 0)가 된다 - h1은 문서/쪽 제목용.

_LEVEL_SYMBOL_ALIASES = {
    "□": ["■", "◻", "ㅁ"],
    "-": ["–", "—"],
    "·": ["ㆍ", "ᆞ", "‧", "∙", "•"],
}
_LEVEL_SYMBOL_TO_DEPTH: dict = {}
for _depth, _level in enumerate(_NUMBERING_LEVELS):
    if "{n}" not in _level["marker"]:
        _LEVEL_SYMBOL_TO_DEPTH[_level["marker"]] = _depth
for _marker, _aliases in _LEVEL_SYMBOL_ALIASES.items():
    for _alias in _aliases:
        _LEVEL_SYMBOL_TO_DEPTH[_alias] = _LEVEL_SYMBOL_TO_DEPTH[_marker]

# 원문에 이미 쳐 있는 말머리 인식: "1." "1)" "(1)" 숫자 번호, "가." "나)" 한글
# 번호, "□"/"-"/"·" 계열 기호(그 변형 포함). doc2report의 leading_markers 중
# ①~⑳/※ 등은 범위를 줄여 뺐다.
_EXISTING_NUMBER_RE = re.compile(r"^\s*(?:\(\d{1,2}\)|\d{1,2}(?:\.\d{1,2})*[.)])(?!\d)\s*")
_HANGUL_ENUM_RE = re.compile(
    r"^\s*(?:\([가나다라마바사아자차카타파하]\)|[가나다라마바사아자차카타파하][.)])\s+"
)
_MARKER_SYMBOL_RE = re.compile(
    "^\\s*(?:"
    + "|".join(
        f"{re.escape(m)}(?!{re.escape(m)})" if not m.isascii() else rf"{re.escape(m)}\s+"
        for m in sorted(_LEVEL_SYMBOL_TO_DEPTH, key=len, reverse=True)
    )
    + ")\\s*"
)


def _find_leading_marker(text: str) -> Optional[Tuple[str, str]]:
    """문단/제목 맨 앞의 말머리를 찾는다 - (말머리 문자열, 말머리를 뗀 나머지
    텍스트). 말머리만 있고 본문이 없으면("1." 단독) 못 찾은 것으로 본다."""
    for pattern in (_EXISTING_NUMBER_RE, _HANGUL_ENUM_RE, _MARKER_SYMBOL_RE):
        match = pattern.match(text)
        if match and match.end() < len(text.rstrip()):
            return text[: match.end()].strip(), text[match.end() :]
    return None


def _marker_depth(marker: str) -> Optional[int]:
    first = marker[:1]
    if first.isdigit() or first in "가나다라마바사아자차카타파하":
        return 0
    return _LEVEL_SYMBOL_TO_DEPTH.get(marker)


def _leading_text_holder(element: etree._Element) -> Optional[etree._Element]:
    """맨 앞 글자를 실제로 담고 있는 요소를 찾는다 - element.text가 비어 있으면
    첫 자식으로 계속 내려간다. builder.py가 만드는 안건 제목처럼
    "<h3><strong><span>1. 안건1 …"처럼 말머리가 서식 태그 안에 중첩돼 있는
    경우에도 찾을 수 있게(doc2report의 IR은 처음부터 runs가 평평해서 이
    문제가 없지만, 여기서는 원본 lxml 트리를 그대로 쓰므로 직접 찾아야 한다)."""
    node = element
    while True:
        if node.text:
            return node
        if len(node) == 0:
            return None
        node = node[0]


def _element_leading_marker(element: etree._Element) -> Optional[Tuple[str, str]]:
    holder = _leading_text_holder(element)
    return _find_leading_marker(holder.text) if holder is not None else None


def _convert_to_listitem(element: etree._Element, depth: int, *, from_heading: bool = False) -> None:
    """요소를 그 자리에서(태그만 바꿔서) "listitem"으로 바꾼다 - 내용(자식/서식)은
    그대로 두고 번호 체계 렌더링에 필요한 정보만 속성으로 얹는다."""
    holder = _leading_text_holder(element)
    if holder is not None:
        found = _find_leading_marker(holder.text)
        if found is not None:
            marker, rest = found
            holder.text = rest
            element.set("data-marker", marker)
    element.tag = "listitem"
    element.set("data-depth", str(max(0, depth)))
    if from_heading:
        element.set("data-from-heading", "1")


def _flatten_list_items(list_element: etree._Element, base_depth: int) -> List[etree._Element]:
    """<ul>/<ol>의 <li>들을 중첩 깊이만큼 depth를 올려 가며 평평한 listitem
    목록으로 바꾼다(nested <ul>/<ol>은 그 자리에서 떼어 바로 뒤이어 펼친다)."""
    flat: List[etree._Element] = []
    for item in list_element:
        if _local(item.tag) != "li":
            continue
        nested = [child for child in item if _local(child.tag) in ("ul", "ol")]
        for nested_list in nested:
            item.remove(nested_list)
        _convert_to_listitem(item, base_depth)
        flat.append(item)
        for nested_list in nested:
            flat.extend(_flatten_list_items(nested_list, base_depth + 1))
    return flat


def _fold_headings_into_levels(root: etree._Element) -> None:
    """제목(h1~h6)과 그 아래 문단/목록을 번호 체계 단계(listitem)로 접어
    넣는다 - doc2report의 fold_headings_into_levels와 같은 규칙, 이 파일의
    lxml 트리 위에서 직접(제자리에서) 수행한다."""
    state = {"depth": -1}

    def walk(container: etree._Element) -> None:
        for child in list(container):
            tag = _local(child.tag)
            if tag == "pagetitle":
                state["depth"] = -1
            elif tag in _HEADING_LEVELS:
                depth = max(0, _HEADING_LEVELS[tag] - _HEADING_FOLD_BASE)
                state["depth"] = depth
                _convert_to_listitem(child, depth, from_heading=True)
            elif tag in ("ul", "ol"):
                items = _flatten_list_items(child, state["depth"] + 1)
                _splice_elements_in_place(child, items)
            elif tag == "p":
                if not _clean_text("".join(child.itertext())).strip():
                    # <p><br/></p> 같은 빈 여백용 문단(이 프로젝트의 builder.py가
                    # 안건 사이 여백으로 실제로 쓴다) - 번호 항목으로 접지 않고
                    # 그대로 둔다("-\t" 같은 빈 말머리가 붙으면 안 되므로).
                    continue
                found = _element_leading_marker(child)
                if state["depth"] >= 0:
                    target_depth = state["depth"] + 1
                    if found is not None:
                        implied = _marker_depth(found[0])
                        if implied is not None:
                            target_depth = max(target_depth, implied)
                    _convert_to_listitem(child, target_depth)
                elif found is not None:
                    depth = _marker_depth(found[0])
                    if depth is not None:
                        _convert_to_listitem(child, depth)
            elif tag in _BLOCK_FLATTEN_TAGS:
                walk(child)
            elif _is_ac(child, "structured-macro"):
                body = child.find("ac:rich-text-body", namespaces={"ac": _AC_NS})
                if body is not None:
                    walk(body)
            # table/image/tablecaption/tablenote/기타 매크로 - 그대로 두고
            # 단계 상태도 바꾸지 않는다(표 아래 다음 문단이 표 때문에 얕아지거나
            # 깊어지지 않게).

    walk(root)
    _normalize_listitem_depths(root)


def _normalize_listitem_depths(root: etree._Element) -> None:
    """문서에서 가장 바깥 단계를 0(= "1.")으로 민다(doc2report의
    text.normalize_levels) - 본문이 h2 없이 h3부터 시작해도(이 프로젝트의
    builder.py가 만드는 안건 제목이 전부 h3인 경우처럼) 첫 문장이 괜히
    "□" 단계로 들여써지지 않게. 연결된 페이지까지 다 펼친 뒤의 전체 문서
    기준으로 한 번만 민다(doc2report도 쪽마다 따로가 아니라 합친 문서
    전체에서 한 번에 민다)."""
    listitems = [el for el in root.iter() if _local(el.tag) == "listitem"]
    if not listitems:
        return
    shift = min(int(el.get("data-depth", "0")) for el in listitems)
    if shift:
        for el in listitems:
            el.set("data-depth", str(int(el.get("data-depth", "0")) - shift))


_BRACKET_PAIRS = [("[", "]"), ("［", "］"), ("【", "】"), ("〔", "〕"), ("〈", "〉"), ("《", "》")]
_TABLE_NOTE_MARKERS = ("*", "※", "주)", "주:")


def _is_bracket_caption(text: str) -> bool:
    return any(
        text.startswith(open_c) and text.endswith(close_c) and len(text) > len(open_c) + len(close_c)
        for open_c, close_c in _BRACKET_PAIRS
    )


def _is_table_note_block(element: etree._Element) -> bool:
    tag = _local(element.tag)
    if tag == "blockquote":
        return True
    if tag == "p":
        return _clean_text(element.text).lstrip().startswith(_TABLE_NOTE_MARKERS)
    return False


def _tag_table_captions_and_notes(container: etree._Element) -> None:
    """표 바로 위의 꺾쇠 캡션("【사업현황】")은 "tablecaption"으로, 표 바로
    뒤의 주석(*, ※, 주) 문단이나 인용문은 "tablenote"로 태그만 바꿔 둔다 -
    _fold_headings_into_levels보다 먼저 돌아야 한다(그 전에 해두지 않으면
    캡션/주석도 번호 항목이 되어 "- 【사업현황】"처럼 말머리가 붙어 버린다)."""
    children = list(container)
    for index, child in enumerate(children):
        tag = _local(child.tag)
        if tag == "table":
            previous = children[index - 1] if index > 0 else None
            if previous is not None and _local(previous.tag) == "p":
                if _is_bracket_caption(_clean_text(previous.text).strip()):
                    previous.tag = "tablecaption"
            j = index + 1
            while j < len(children) and _is_table_note_block(children[j]):
                children[j].tag = "tablenote"
                j += 1
        elif tag in _BLOCK_FLATTEN_TAGS:
            _tag_table_captions_and_notes(child)
        elif _is_ac(child, "structured-macro"):
            body = child.find("ac:rich-text-body", namespaces={"ac": _AC_NS})
            if body is not None:
                _tag_table_captions_and_notes(body)


# ── storage XHTML → .docx ───────────────────────────────────────────────

_HEADING_LEVELS = {f"h{n}": n for n in range(1, 7)}
_BLOCK_FLATTEN_TAGS = {"div", "root", "layout", "layout-section", "layout-cell"}


# doc2report의 profiles/confluence.yaml(+ extends: default인 profiles/default.yaml)에서
# 그대로 옮긴 값들 - 그 프로파일의 구조적 변환(제목 접어넣기/표 글자 자동 축소/문장
# 다듬기)은 가져오지 않았다(모듈 docstring 참고), 글꼴/크기/여백/음영 "값"만 옮김.
_DOCUMENT_FONT_NAME = "맑은 고딕"
_HEADING_FONT_SIZES_PT = {1: 15, 2: 14, 3: 14}  # 4~6단계는 3단계 값을 그대로 재사용.
_TABLE_HEADER_SHADING_HEX = "F2F2F2"


def _set_east_asian_font(font, name: str) -> None:
    """python-docx의 Font.name은 ascii/hAnsi만 설정하고 eastAsia(한글 글꼴)는
    안 건드려서, 한글 문서에서 영문 글꼴로 보이는 걸 막으려면 w:eastAsia를
    직접 oxml로 설정해야 한다. font._element는 런(CT_R)이든 스타일(CT_Style)이든
    get_or_add_rPr()을 지원해서 둘 다 같은 방식으로 처리된다."""
    font.name = name
    r_pr = font._element.get_or_add_rPr()
    r_pr.get_or_add_rFonts().set(qn("w:eastAsia"), name)


def _shade_cell(cell, hex_color: str) -> None:
    shading = etree.SubElement(cell._tc.get_or_add_tcPr(), qn("w:shd"))
    shading.set(qn("w:val"), "clear")
    shading.set(qn("w:color"), "auto")
    shading.set(qn("w:fill"), hex_color)


def _configure_document_styles(document: DocxDocument) -> None:
    for section in document.sections:
        section.top_margin = Mm(20)
        section.bottom_margin = Mm(20)
        section.left_margin = Mm(20)
        section.right_margin = Mm(20)

    normal = document.styles["Normal"]
    _set_east_asian_font(normal.font, _DOCUMENT_FONT_NAME)
    normal.font.size = Pt(12)
    normal.paragraph_format.space_before = Pt(0)
    normal.paragraph_format.space_after = Pt(0)

    title_style = document.styles["Title"]
    _set_east_asian_font(title_style.font, _DOCUMENT_FONT_NAME)
    title_style.font.size = Pt(18)
    title_style.font.bold = True
    title_style.font.underline = True
    title_style.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER

    for level in range(1, 7):
        try:
            style = document.styles[f"Heading {level}"]
        except KeyError:
            continue
        _set_east_asian_font(style.font, _DOCUMENT_FONT_NAME)
        style.font.size = Pt(_HEADING_FONT_SIZES_PT.get(level, _HEADING_FONT_SIZES_PT[3]))
        style.font.bold = True
        style.paragraph_format.keep_with_next = True


def _render_document(title: str, root: etree._Element) -> bytes:
    _tag_table_captions_and_notes(root)
    _fold_headings_into_levels(root)

    document = DocxDocument()
    _configure_document_styles(document)
    document.add_heading(title or "", level=0)
    _render_blocks(document, root, {})
    buf = BytesIO()
    document.save(buf)
    return buf.getvalue()


def _render_blocks(document: DocxDocument, container: etree._Element, counters: dict) -> None:
    for element in container:
        tag = _local(element.tag)

        if tag == "pagebreak":
            # 연결된 페이지(include/children) 사이에 끼워 넣는 쪽 나눔 - 진짜
            # Confluence 태그가 아니라 _resolve_linked_pages가 만들어 넣는 합성 요소.
            document.add_page_break()
        elif tag == "pagetitle":
            # 연결된 페이지의 제목 - doc2report의 Heading.page_title과 같이
            # 번호 체계에 접지 않고 문서 제목 서식을 쓰며, 항목 번호를 새로 센다.
            counters.clear()
            _add_inline_runs(document.add_heading("", level=0), element)
        elif tag == "listitem":
            _render_listitem(document, element, counters)
        elif tag == "tablecaption":
            paragraph = document.add_paragraph()
            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            run = paragraph.add_run(_clean_text(element.text))
            run.bold = True
            run.font.size = Pt(12)
        elif tag == "tablenote":
            inner_paragraphs = [child for child in element if _local(child.tag) == "p"]
            for source in inner_paragraphs or [element]:
                paragraph = document.add_paragraph()
                _add_inline_runs(paragraph, source)
                for run in paragraph.runs:
                    run.font.size = Pt(10)
        elif tag in _HEADING_LEVELS:
            # _fold_headings_into_levels가 보통 다 listitem으로 바꿔서 여기까지
            # 안 오지만(표 셀 안 등 그 변환이 안 들어간 자리를 위한 안전망), 혹시
            # 남아 있으면 기존 방식(Heading 스타일)으로라도 렌더링한다.
            _add_inline_runs(document.add_heading("", level=_HEADING_LEVELS[tag]), element)
        elif tag == "p":
            _add_inline_runs(document.add_paragraph(), element)
        elif tag in ("ul", "ol"):
            _render_list(document, element, ordered=(tag == "ol"))
        elif tag == "table":
            _render_table(document, element)
        elif tag == "blockquote":
            try:
                paragraph = document.add_paragraph(style="Intense Quote")
            except KeyError:  # 기본 템플릿에 그 스타일이 없을 수도 있음
                paragraph = document.add_paragraph()
            _add_inline_runs(paragraph, element)
        elif _is_ac(element, "image"):
            _render_image_placeholder(document, element)
        elif _is_ac(element, "structured-macro"):
            _render_macro(document, element, counters)
        elif tag in _BLOCK_FLATTEN_TAGS:
            # 레이아웃용 래퍼(ac:layout 등) - 내용만 순서대로 펼친다.
            _render_blocks(document, element, counters)
        elif _clean_text(element.text).strip() or len(element):
            # 알 수 없는 블록 요소 - 내용은 최대한 살려서 평문단으로.
            _add_inline_runs(document.add_paragraph(), element)


def _render_listitem(document: DocxDocument, element: etree._Element, counters: dict) -> None:
    """번호 체계 단계(1./□/-/·) 항목 하나를 렌더링한다 - doc2report의
    render/docx_writer.py::_list_item을 참고해 새로 구현(내어쓰기/탭 정렬,
    말머리 자동 번호 매기기)."""
    depth = int(element.get("data-depth", "0"))
    level = _NUMBERING_LEVELS[min(depth, len(_NUMBERING_LEVELS) - 1)]
    marker = element.get("data-marker")
    if marker is None:
        marker = _format_marker(level["marker"], depth, counters)
    # 제목에서 접어 넣은 항목은 원문 굵기와 무관하게 항상 굵게(doc2report와 동일) -
    # 그 외(원래 목록/문단이던 항목)는 원문 서식(굵게/기울임 등)만 그대로 쓴다.
    bold = element.get("data-from-heading") == "1"

    paragraph = document.add_paragraph()
    paragraph.paragraph_format.left_indent = Mm(level["indent_mm"])
    if marker:
        paragraph.paragraph_format.first_line_indent = Mm(-level["hanging_mm"])
        paragraph.paragraph_format.tab_stops.add_tab_stop(Mm(level["indent_mm"]))
        marker_run = paragraph.add_run(marker + "\t")
        marker_run.bold = bold or None
    _add_inline_runs(paragraph, element, bold=bold)


def _format_marker(template: str, depth: int, counters: dict) -> str:
    if "{n}" not in template:
        return template
    for deeper in [d for d in counters if d > depth]:
        del counters[d]
    counters[depth] = counters.get(depth, 0) + 1
    return template.format(n=counters[depth])


# rich-text-body가 없고 화면에 보이는 내용도 전혀 없는 매크로(둘 다 이
# 프로젝트의 builder.py가 실제로 블록 위치에 단독으로 쓴다) - "지원하지
# 않습니다" 자리표시자를 보여줄 필요 없이 조용히 건너뛴다.
_SILENT_BLOCK_MACRO_NAMES = {"anchor", "create-from-template"}


def _render_macro(document: DocxDocument, macro: etree._Element, counters: dict) -> None:
    body = macro.find("ac:rich-text-body", namespaces={"ac": _AC_NS})
    if body is not None:
        _render_blocks(document, body, counters)
        return
    name = macro.get(f"{{{_AC_NS}}}name") or "매크로"
    if name in _SILENT_BLOCK_MACRO_NAMES:
        return
    note = document.add_paragraph(f"[{name} 매크로 - 이 변환에서는 지원하지 않습니다]")
    note.runs[0].italic = True


def _render_image_placeholder(document: DocxDocument, image: etree._Element) -> None:
    attachment = image.find("ri:attachment", namespaces={"ri": _RI_NS})
    filename = attachment.get(f"{{{_RI_NS}}}filename") if attachment is not None else None
    note = document.add_paragraph(f"[이미지: {filename}]" if filename else "[이미지]")
    note.runs[0].italic = True


def _render_list(document: DocxDocument, list_element: etree._Element, *, ordered: bool) -> None:
    style = "List Number" if ordered else "List Bullet"
    for item in list_element:
        if _local(item.tag) != "li":
            continue
        # 중첩 리스트는 들여쓰기를 구분하지 않고 바로 뒤이어 평평하게 펼친다(알려진 단순화).
        nested = [child for child in item if _local(child.tag) in ("ul", "ol")]
        for nested_list in nested:
            item.remove(nested_list)
        try:
            paragraph = document.add_paragraph(style=style)
        except KeyError:
            paragraph = document.add_paragraph()
        _add_inline_runs(paragraph, item)
        for nested_list in nested:
            _render_list(document, nested_list, ordered=(_local(nested_list.tag) == "ol"))


def _render_table(document: DocxDocument, table_element: etree._Element) -> None:
    rows: List[List[etree._Element]] = []
    for section in table_element:
        section_tag = _local(section.tag)
        if section_tag in ("thead", "tbody", "tfoot"):
            rows.extend([tr for tr in section if _local(tr.tag) == "tr"])
        elif section_tag == "tr":
            rows.append(section)

    cell_rows = [[cell for cell in row if _local(cell.tag) in ("td", "th")] for row in rows]
    if not cell_rows:
        return
    col_count = max(len(row) for row in cell_rows)

    table = document.add_table(rows=len(cell_rows), cols=col_count)
    try:
        table.style = "Table Grid"
    except KeyError:
        pass

    for row_index, cells in enumerate(cell_rows):
        col_index = 0
        for cell in cells:
            if col_index >= col_count:
                break
            colspan = max(1, _int_attr(cell, "colspan"))
            span = min(colspan, col_count - col_index)
            docx_cell = table.cell(row_index, col_index)
            if span > 1:
                docx_cell = docx_cell.merge(table.cell(row_index, col_index + span - 1))
            paragraph = docx_cell.paragraphs[0]
            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            is_header = _local(cell.tag) == "th"
            _add_inline_runs(paragraph, cell, bold=is_header)
            for run in paragraph.runs:
                run.font.size = Pt(10)
                _set_east_asian_font(run.font, _DOCUMENT_FONT_NAME)
            if is_header:
                _shade_cell(docx_cell, _TABLE_HEADER_SHADING_HEX)
            col_index += span


def _int_attr(element: etree._Element, name: str) -> int:
    value = element.get(name)
    try:
        return int(value) if value else 1
    except ValueError:
        return 1


_INLINE_STYLE_TAGS = {"strong": "bold", "b": "bold", "em": "italic", "i": "italic", "u": "underline"}


def _add_inline_runs(paragraph, element: etree._Element, *, bold=False, italic=False, underline=False) -> None:
    text = _clean_text(element.text)
    if text:
        _add_run(paragraph, text, bold, italic, underline)

    for child in element:
        tag = _local(child.tag)
        if tag == "br":
            paragraph.add_run().add_break()
        elif tag in _INLINE_STYLE_TAGS:
            kwargs = {"bold": bold, "italic": italic, "underline": underline}
            kwargs[_INLINE_STYLE_TAGS[tag]] = True
            _add_inline_runs(paragraph, child, **kwargs)
        elif _is_ac(child, "structured-macro"):
            # 문단 중간에 끼어드는 매크로(anchor/create-from-template 등, 이
            # 프로젝트의 builder.py가 실제로 이렇게 쓴다) - rich-text-body가
            # 있으면 그 내용만 펼치고, 없으면 화면에 보이는 내용이 없는
            # 매크로이므로 건너뛴다. ac:parameter 값(매크로 설정·버튼 ID 등)을
            # 본문 텍스트로 잘못 끌어오면 안 된다(예: "제목1" 북마크 이름이나
            # "3877634148" 템플릿 ID가 본문에 그대로 섞여 나오던 문제).
            body = child.find("ac:rich-text-body", namespaces={"ac": _AC_NS})
            if body is not None:
                _add_inline_runs(paragraph, body, bold=bold, italic=italic, underline=underline)
        else:
            # a(링크, 글자만 살리고 하이퍼링크는 안 만듦)/span/code/ac:link 등 -
            # 내용은 그대로 펼친다.
            _add_inline_runs(paragraph, child, bold=bold, italic=italic, underline=underline)

        tail = _clean_text(child.tail)
        if tail:
            _add_run(paragraph, tail, bold, italic, underline)


def _add_run(paragraph, text: str, bold: bool, italic: bool, underline: bool) -> None:
    run = paragraph.add_run(text)
    run.bold = bold or None
    run.italic = italic or None
    run.underline = underline or None


_UNSAFE_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|]')


def _safe_filename(title: str) -> str:
    cleaned = _UNSAFE_FILENAME_CHARS.sub("", title or "").strip()
    return (cleaned or "report") + ".docx"


def convert_confluence_url_to_docx(
    url: str,
    *,
    token: Optional[str] = None,
    on_progress: Optional[Callable[[str], None]] = None,
) -> Tuple[bytes, str]:
    """Confluence 페이지 URL을 받아 (.docx 바이트, 파일명)을 돌려준다.

    token을 안 주면 전역 CONFLUENCE_API_TOKEN을 쓴다 - 호출부(app.py)는 보통
    resolve_token()으로 사용자별 토큰을 먼저 구해서 넘긴다.

    on_progress(단계 설명)를 주면 주요 단계(조회/분석/생성)마다 호출한다 -
    페이지 조회가 사내망을 거치면 몇 초~몇십 초 걸릴 수 있어서, 변환이 멈춘
    것처럼 보이지 않게 화면에 지금 뭘 하고 있는지 보여주려고 만든 것이다
    (app.py가 백그라운드 스레드로 돌리면서 이 콜백으로 진행 상황을 기록한다).
    """
    say = on_progress or (lambda message: None)

    if not _rendering_dependencies_installed():
        raise DocxExportUnavailable(
            "python-docx/lxml이 설치되지 않았습니다. requirements.txt를 다시 설치해주세요."
        )

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

    page_id = _page_id_from_url(url)
    if page_id is None:
        raise RuntimeError(
            f"URL에서 페이지 ID를 못 찾았습니다: {url}\n"
            "'/pages/123456', '?pageId=123456' 형태이거나 페이지 ID 숫자 자체여야 합니다."
        )

    say("컨플루언스 페이지 조회 중...")
    base_url = _normalize_base_url(os.environ.get("CONFLUENCE_URL", "").strip())
    page = _fetch_page(base_url, page_id, effective_token)

    storage_html = page.get("body", {}).get("storage", {}).get("value")
    if not storage_html:
        raise RuntimeError(f"페이지 {page_id}에 storage 본문이 없습니다. 접근 권한을 확인하세요.")

    say("본문 분석 중...")
    title = page.get("title") or url
    root = _parse_storage(storage_html)

    say("연결된 하위 페이지 포함 중...")
    space = (page.get("space") or {}).get("key")
    link_ctx = _LinkContext(base_url, effective_token, space, page_id)
    _resolve_linked_pages(root, link_ctx, page_id, depth=0)

    say("Word 문서 생성 중...")
    data = _render_document(title, root)
    say("완료")
    return data, _safe_filename(title)


# ── 다운로드 없이 미리보기 ───────────────────────────────────────────────
#
# 이미 만든 .docx 바이트를 그대로 다시 읽어서 HTML로 바꾼다 - storage
# XHTML을 처음부터 다시 렌더링하는 별도 경로를 만들면 두 렌더러가 서로
# 어긋날 수 있어서, 대신 "다운로드할 그 파일"을 python-docx로 읽어 보여주는
# 쪽을 택했다(내용은 100% 같고, 글꼴·간격 등 미세한 모양만 CSS로 대략
# 근사한다 - 정확한 모양 확인은 다운로드해서 직접 열어야 한다).


def _emu_to_mm(value) -> float:
    return round(value / 36000, 2) if value is not None else 0.0


def _preview_run_html(run) -> str:
    text = html.escape(run.text or "").replace("\t", "&emsp;")
    if not text:
        return ""
    if run.bold:
        text = f"<strong>{text}</strong>"
    if run.italic:
        text = f"<em>{text}</em>"
    if run.underline:
        text = f"<u>{text}</u>"
    return text


def _preview_paragraph_html(paragraph) -> str:
    runs_html = "".join(_preview_run_html(run) for run in paragraph.runs)
    style_name = paragraph.style.name if paragraph.style is not None else ""
    classes = ["docx-p"]
    styles = []
    if style_name == "Title":
        classes.append("docx-title")
    elif style_name.startswith("Heading"):
        classes.append("docx-heading")
    indent_mm = _emu_to_mm(paragraph.paragraph_format.left_indent)
    if indent_mm:
        styles.append(f"padding-left:{indent_mm}mm")
    alignment = paragraph.alignment
    if alignment is not None and "CENTER" in str(alignment):
        styles.append("text-align:center")
    style_attr = f' style="{";".join(styles)}"' if styles else ""
    return f'<p class="{" ".join(classes)}"{style_attr}>{runs_html or "&nbsp;"}</p>'


def _preview_table_html(table) -> str:
    rows_html = []
    for row in table.rows:
        cells_html = "".join(
            "<td>"
            + ("".join(_preview_run_html(run) for p in cell.paragraphs for run in p.runs) or "&nbsp;")
            + "</td>"
            for cell in row.cells
        )
        rows_html.append(f"<tr>{cells_html}</tr>")
    return '<table class="docx-table">' + "".join(rows_html) + "</table>"


def docx_bytes_to_preview_html(data: bytes) -> str:
    """변환된 .docx를 다운로드하지 않고 화면에서 바로 확인할 수 있게 간단한
    HTML 조각으로 바꾼다("이 내용이 맞는지" 확인하는 용도 - 픽셀 단위로
    똑같지는 않다)."""
    document = DocxDocument(BytesIO(data))
    parts = ['<div class="docx-preview">']
    for child in document.element.body:
        if child.tag == qn("w:p"):
            parts.append(_preview_paragraph_html(DocxParagraph(child, document._body)))
        elif child.tag == qn("w:tbl"):
            parts.append(_preview_table_html(DocxTable(child, document._body)))
    parts.append("</div>")
    return "\n".join(parts)
