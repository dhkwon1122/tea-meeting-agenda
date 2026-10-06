"""Confluence 페이지를 조회해 Word(.docx)로 변환하는 기능.

wcoffee77/document-parsing(doc2report)을 참고했지만, 그 코드를 가져오지는
않았다 - REST API 호출과 storage XHTML 처리 방식만 참고해서 이 파일에 직접
새로 구현했다. doc2report는 사내 보고서 규격에 맞춘 표 분할·쪽 배치 계산·
한국어 문구 다듬기까지 하는 꽤 큰 파이프라인인데, 여기서는 그 전체를 가져올
필요가 없어서 "본문을 읽을 수 있는 Word 문서로" 수준으로 범위를 줄였다.

지원하는 것: 제목(h1~h6), 문단(굵게/기울임/밑줄/줄바꿈), 목록(ul/ol, 평평하게),
표(칸 병합 중 colspan만 반영), 패널/펼치기류 매크로(rich-text-body가 있으면
그 내용만 펼침). 지원하지 않는 것(건너뛰고 자리만 표시): 이미지/첨부 다운로드,
행 병합(rowspan), 다른 페이지를 끌어오는 매크로(include/하위 페이지 등).

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
import os
import re
from io import BytesIO
from pathlib import Path
from typing import Callable, List, Optional, Tuple

import requests

from . import confluence_credentials

try:
    from docx import Document as DocxDocument
    from lxml import etree
except ImportError:  # python-docx/lxml 미설치 - 이 기능만 비활성화된다.
    DocxDocument = None
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
            params={"expand": "body.storage"},
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


# ── storage XHTML → .docx ───────────────────────────────────────────────

_HEADING_LEVELS = {f"h{n}": n for n in range(1, 7)}
_BLOCK_FLATTEN_TAGS = {"div", "root", "layout", "layout-section", "layout-cell"}


def _render_document(title: str, root: etree._Element) -> bytes:
    document = DocxDocument()
    document.add_heading(title or "", level=0)
    _render_blocks(document, root)
    buf = BytesIO()
    document.save(buf)
    return buf.getvalue()


def _render_blocks(document: DocxDocument, container: etree._Element) -> None:
    for element in container:
        tag = _local(element.tag)

        if tag in _HEADING_LEVELS:
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
            _render_macro(document, element)
        elif tag in _BLOCK_FLATTEN_TAGS:
            # 레이아웃용 래퍼(ac:layout 등) - 내용만 순서대로 펼친다.
            _render_blocks(document, element)
        elif _clean_text(element.text).strip() or len(element):
            # 알 수 없는 블록 요소 - 내용은 최대한 살려서 평문단으로.
            _add_inline_runs(document.add_paragraph(), element)


def _render_macro(document: DocxDocument, macro: etree._Element) -> None:
    body = macro.find("ac:rich-text-body", namespaces={"ac": _AC_NS})
    if body is not None:
        _render_blocks(document, body)
        return
    name = macro.get(f"{{{_AC_NS}}}name") or "매크로"
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
            docx_cell.text = ""
            _add_inline_runs(docx_cell.paragraphs[0], cell)
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
        else:
            # a(링크, 글자만 살리고 하이퍼링크는 안 만듦)/span/code 등 - 내용은 그대로 펼친다.
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

    say("Word 문서 생성 중...")
    data = _render_document(title, root)
    say("완료")
    return data, _safe_filename(title)
