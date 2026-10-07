"""Confluence 페이지를 조회해 Word(.docx)로 변환하는 기능.

wcoffee77/document-parsing(doc2report)을 참고했지만, 그 코드를 가져오지는
않았다 - REST API 호출과 storage XHTML 처리 방식만 참고해서 이 파일에 직접
새로 구현했다. doc2report는 사내 보고서 규격에 맞춘 표 분할·쪽 배치 계산·
한국어 문구 다듬기까지 하는 꽤 큰 파이프라인인데, 여기서는 그 전체를 가져올
필요가 없어서 "본문을 읽을 수 있는 Word 문서로" 수준으로 범위를 줄였다.

지원하는 것: 제목(h1~h6, 아래 "구조적 변환" 설명처럼 번호 체계로 접힘),
문단(굵게/기울임/밑줄/줄바꿈), 목록(ul/ol, 중첩 깊이 반영), 표(칸 안에
문단/목록이 여러 개면 각각 줄바꿈으로 구분, colspan·rowspan 병합(둘이
같이 쓰여도 사각형으로 합쳐짐), 캡션/주석 자동 첨부), 이미지(그 페이지의
첨부파일을 실제로 내려받아 그려 넣음, 아래 "이미지" 설명 참고), 패널/
펼치기류 매크로(rich-text-body가 있으면 그 내용만 펼침, 인라인 위치의
anchor류처럼 보이는 내용이 없는 매크로는 조용히 건너뜀), 다른 페이지를
끌어오는 매크로(include/excerpt-include/children - doc2report의
sources/confluence.py::LinkedPages를 참고해 직접 구현, 아래 "연결된 페이지"
설명 참고). 지원하지 않는 것(건너뛰고 자리만 표시): 행 병합이 아닌 첨부
파일(문서 등 이미지가 아닌 것), 본문 링크(단순 하이퍼링크) 따라가기.

글꼴/서식은 doc2report의 profiles/confluence.yaml(+ extends: default인
profiles/default.yaml) 값을 그대로 옮겼다(_configure_document_styles) -
맑은 고딕, 본문 12pt, 제목(문서 맨 위 + 연결된 각 페이지 제목) 18pt·
가운데·굵게·밑줄(글자 앞뒤에 공백을 하나씩 둬서 밑줄이 조금 더 길어
보이게 함), 표 10pt, 여백 20mm, 표 머리행 음영 F2F2F2. python-docx
기본 템플릿의 Title/Heading 스타일은 글꼴을 이름이 아니라 테마 참조로도
갖고 있어서(우리가 지정한 이름과 같이 있으면 워드가 테마를 우선시함) 그
테마 참조를 명시적으로 지운다(_set_east_asian_font) - 안 지우면 제목이
다른 글꼴로 보인다. Title 스타일은 밑줄과는 별개로 문단 테두리(가로줄)도
기본으로 갖고 있어서 그것도 지운다(안 지우면 밑줄 밑에 테두리가 하나 더
생겨 "긴 밑줄"처럼 보인다).

표 칸의 폭은 python-docx 기본값(모든 열이 똑같은 폭)이 아니라 칸 안
글자 양에 비례해서 미리 계산해 지정한다(_apply_content_based_column_widths,
한글/한자 등 전각 문자는 2칸으로 셈) - 글자가 거의 없는 열이 글자가
많은 열과 같은 폭으로 어색하게 눌리거나 늘어나는 문제가 있었다.

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

이미지(ac:image)는 그 페이지의 첨부파일 목록(_fetch_page_attachments)에서
같은 파일명을 찾아 실제 내용을 내려받아(_fetch_attachment_bytes) 그려
넣는다(_render_image) - 본문 폭(170mm)보다 큰 이미지만 비율을 유지한 채
줄이고, 작은 이미지는 원본 크기 그대로 둔다. 연결된 페이지에 있는
이미지는 "그 페이지 자신"의 첨부파일 목록에서 찾아야 한다 - 여러 페이지가
include/children으로 한 문서에 합쳐지고 나면 어느 이미지가 원래 어느
페이지 것이었는지 더는 구분할 수 없으므로, 각 페이지를 펼치는 바로 그
자리에서(합치기 전에) 미리 받아 둔다. 첨부파일을 못 찾거나 받아오지
못하면(네트워크 오류 등) 예전처럼 파일명만 보여주는 자리표시자로 빠진다.
이미지는 블록 자리(_render_image, 새 문단으로 그림)와 문단 "안"(인라인
위치, _add_inline_image, 그 문단의 런으로 그림) 둘 다에서 나올 수 있다 -
Confluence는 "<p><ac:image>...</ac:image></p>"처럼 문단 안에 끼워 넣는
경우가 흔한데, 처음엔 인라인 쪽을 몰라서 이미지가 자리표시자조차 없이
통째로 사라지는 버그가 있었다(실사용에서 발견).

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
import itertools
import os
import re
from io import BytesIO
from pathlib import Path
from typing import Callable, List, Optional, Tuple
from urllib.parse import quote

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


def _fetch_page_attachments(base_url: str, token: str, page_id: str) -> Tuple[dict, Optional[str]]:
    """그 페이지에 실제로 붙어 있는 첨부파일 목록을 (파일명 -> 다운로드
    경로) 맵으로 가져온다 - ac:image가 참조하는 ri:attachment는 파일명만
    있고 실제로 받을 수 있는 URL이 없어서, 이 목록에서 같은 파일명을 찾아
    그 다운로드 경로(_links.download, 사이트 루트 기준 상대 경로)로
    내려받는다.

    반환값은 (맵, 실패 이유 또는 None) - 실패해도 예외를 던지지 않는다
    (이미지 하나를 못 가져왔다고 변환 전체가 실패하면 안 되므로). 대신
    실패 이유를 돌려줘서, 호출한 쪽이 그 이미지의 자리표시자에 "왜"
    못 가져왔는지 적을 수 있게 한다 - docx 파일을 직접 못 보내주는
    환경에서도 변환된 문서 자체에서 원인을 바로 읽을 수 있다."""
    try:
        resp = requests.get(
            f"{base_url}/rest/api/content/{page_id}/child/attachment",
            params={"limit": 200},
            headers=_request_headers(token),
            timeout=30,
            verify=_ssl_verify(),
            proxies=_proxies(),
        )
    except (requests.RequestException, OSError) as exc:
        return {}, f"첨부파일 목록 조회 중 오류: {exc}"
    if resp.status_code != 200:
        return {}, f"첨부파일 목록 조회 실패(HTTP {resp.status_code})"
    mapping = {}
    for item in resp.json().get("results") or []:
        filename = item.get("title")
        download = (item.get("_links") or {}).get("download")
        if filename and download:
            mapping[filename] = download
    return mapping, None


def _direct_attachment_download_url(base_url: str, page_id: str, filename: str) -> str:
    """"GET /download/attachments/{페이지ID}/{파일명}"으로 바로 받을 수
    있다고 확인된 경로를 직접 조합한다(사내망에서 실측 확인) - 첨부파일
    목록 API의 _links.download 값을 거치지 않고 바로 이 URL부터 시도한다.
    파일명에 공백·한글 등이 있으면 URL 인코딩이 필요하다."""
    return f"{base_url.rstrip('/')}/download/attachments/{page_id}/{quote(filename)}"


def _fetch_attachment_bytes(url: str, token: str) -> Tuple[Optional[bytes], Optional[str]]:
    """반환값은 (바이트 또는 None, 실패 이유 또는 None) - _fetch_page_attachments와 같은 이유.

    실패 이유에 실제로 요청한 URL을 그대로 적는다 - "다운로드 실패(HTTP 404)"
    만으로는 사내 API 게이트웨이가 /rest/api/* 경로만 허용하고 이 첨부파일
    다운로드 경로(/download/attachments/...)는 막아 둔 경우인지 구분할 수
    없는데, URL이 있으면 브라우저로 직접 열어서(로그인한 상태로) 확인해 볼
    수 있고 사내망 담당자에게 바로 전달할 수도 있다."""
    try:
        resp = requests.get(
            url,
            headers=_request_headers(token),
            timeout=30,
            verify=_ssl_verify(),
            proxies=_proxies(),
        )
    except (requests.RequestException, OSError) as exc:
        return None, f"다운로드 중 오류({url}): {exc}"
    if resp.status_code != 200:
        return None, f"다운로드 실패(HTTP {resp.status_code}): {url}"
    return resp.content, None


_image_key_counter = itertools.count()


class _ImageFetchFailure:
    """이미지를 못 가져온 이유를 담아 둔다(images 딕셔너리에 바이트 대신
    이 객체가 들어가면 실패했다는 뜻) - _render_image/_add_inline_image가
    자리표시자 글자에 이 이유를 그대로 적어서, docx 파일을 못 보내주는
    상황에서도 변환된 문서를 열어 보기만 하면 원인을 알 수 있게 한다."""

    def __init__(self, reason: str):
        self.reason = reason


def _fetch_images_for_page(
    container: etree._Element, base_url: str, token: str, page_id: str, images_out: dict
) -> None:
    """container(루트 페이지 전체 또는 연결된 한 페이지의 본문) 안의
    ac:image가 가리키는 첨부파일을 전부 내려받아 images_out에 채운다 -
    (키 -> 이미지 바이트 또는 _ImageFetchFailure). 키는 id(element)가
    아니라 요소에 직접 심어 둔 속성("data-image-key")이다 - lxml은 같은
    노드라도 트리를 손댄 뒤 다시 접근하면 다른 Python 객체(다른 id())를
    돌려줄 수 있어서, 나중에 렌더링할 때 object id로 찾으면 못 찾는
    경우가 있었다(실제로 이 문제로 이미지가 전부 자리표시자로 빠졌다) -
    속성은 XML 데이터 자체라 항상 그대로다.

    container에 이미지가 하나도 없으면 아무 API도 안 부른다(불필요한 호출
    방지). 반드시 그 이미지가 실제로 속한 페이지(=이 함수를 부르는 시점의
    page_id)로 조합/조회해야 한다 - 연결된 여러 페이지가 하나의 트리로
    합쳐지고 나면 "이 이미지가 원래 어느 페이지 것이었는지"를 더는 알 수
    없기 때문에, 페이지를 펼치는 바로 그 자리에서 처리한다.

    먼저 "GET /download/attachments/{페이지ID}/{파일명}"을 직접 조합해
    시도한다(사내망에서 실측 확인된 방식) - 첨부파일 목록 API를 아예 안
    거쳐도 되므로 보통은 이거 하나로 끝난다. 혹시 이 방식이 안 되는
    첨부파일이 있으면(예: 파일명이 최신 버전에서 바뀐 경우) 첨부파일
    목록 API의 _links.download 값으로 한 번 더 시도한다."""
    image_elements = list(container.iter(f"{{{_AC_NS}}}image"))
    if not image_elements:
        return

    attachments: Optional[dict] = None
    list_error: Optional[str] = None

    for image in image_elements:
        key = str(next(_image_key_counter))
        image.set("data-image-key", key)

        attachment = image.find("ri:attachment", namespaces={"ri": _RI_NS})
        filename = attachment.get(f"{{{_RI_NS}}}filename") if attachment is not None else None
        if not filename:
            images_out[key] = _ImageFetchFailure("ri:attachment에 ri:filename이 없음")
            continue

        direct_url = _direct_attachment_download_url(base_url, page_id, filename)
        data, direct_error = _fetch_attachment_bytes(direct_url, token)
        if data:
            images_out[key] = data
            continue

        # 직접 조합한 주소가 안 되면 첨부파일 목록에서 받은 공식 다운로드
        # 링크로 한 번 더 시도한다 - 목록 조회는 필요할 때 한 번만 한다.
        if attachments is None:
            attachments, list_error = _fetch_page_attachments(base_url, token, page_id)

        download_path = attachments.get(filename)
        if not download_path:
            sample = ", ".join(sorted(attachments)[:5]) or "(이 페이지에 첨부파일 없음)"
            reason = (
                f"'{filename}' 직접 주소 실패({direct_error}), "
                f"이 페이지 첨부파일 목록에 없음 - 목록에 있는 파일 예: {sample}"
            )
            if list_error:
                reason += f" (목록 조회 자체도 실패: {list_error})"
            images_out[key] = _ImageFetchFailure(reason)
            continue

        list_url = (
            download_path
            if download_path.startswith("http")
            else f"{base_url.rstrip('/')}/{download_path.lstrip('/')}"
        )
        data2, list_download_error = _fetch_attachment_bytes(list_url, token)
        if data2:
            images_out[key] = data2
        else:
            images_out[key] = _ImageFetchFailure(
                f"'{filename}' 두 방식 모두 실패 - 직접 주소: {direct_error} / 목록 주소: {list_download_error}"
            )


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

    def __init__(
        self, base_url: str, token: str, space: Optional[str], root_page_id: str, images: dict
    ):
        self.base_url = base_url
        self.token = token
        self.space = space
        self.visited = {root_page_id}
        self.loaded = 0
        # (요소 id -> 이미지 바이트) - 연결된 각 페이지 자신의 첨부파일에서
        # 가져온 이미지를 전부 이 하나의 맵에 모은다(렌더링할 때 그대로 씀).
        self.images = images


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


def _unwrap_lone_p_ancestor(element: etree._Element) -> etree._Element:
    """교체할 자리를 찾는다 - 매크로가 <p> 안에 홀로 들어있으면(이 프로젝트
    builder.py의 실제 패턴: "<p>{include_html}</p>") 매크로 자리가 아니라
    그 <p> 전체를 대신해야 한다. <p> 안에 표/제목 같은 블록 내용을 그대로
    끼워 넣으면 _render_blocks가 그 <p>를 한 문단으로 통째로 평평하게
    펼쳐버려서(안에 있던 표가 안 그려지고 줄바꿈도 전부 사라짐) 안 된다 -
    실제로 연결된 페이지에서 이 증상으로 나타난 버그였다. <p>에 이 매크로
    말고 다른 내용이 같이 있으면(드문 경우) 매크로 자리만 바꾸는 쪽이 더
    안전하므로 그때는 올라가지 않는다."""
    target = element
    parent = target.getparent()
    while (
        parent is not None
        and _local(parent.tag) == "p"
        and len(parent) == 1
        and not (parent.text or "").strip()
        and not (target.tail or "").strip()
    ):
        target = parent
        parent = target.getparent()
    return target


def _replace_macro_with_placeholder(macro: etree._Element, message: str) -> None:
    target = _unwrap_lone_p_ancestor(macro)
    placeholder = etree.Element("p")
    placeholder.text = message
    target.addnext(placeholder)
    target.getparent().remove(target)


def _splice_elements_in_place(macro: etree._Element, new_elements: List[etree._Element]) -> None:
    target = _unwrap_lone_p_ancestor(macro)
    anchor = target
    for new_elem in new_elements:
        anchor.addnext(new_elem)
        anchor = new_elem
    target.getparent().remove(target)


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
    _fetch_images_for_page(sub_root, ctx.base_url, ctx.token, page_id, ctx.images)
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
        _fetch_images_for_page(sub_root, ctx.base_url, ctx.token, child_id, ctx.images)
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
# 참고해 이 파일에 새로 구현) 제목(h1~h6)과 그 아래 문단/목록을 depth(들여쓰기)
# 단계로 접어 넣는다 - 제목은 h2가 depth 0, h3이 depth 1, ... 식으로 깊어지고
# (normalize_levels로 문서 전체의 최저 단계를 0으로 민다), 제목 아래 평문단은
# 그보다 한 단계 더 들여쓴다.
#
# 다만 "1." "□" "-" 같은 말머리는 새로 만들어 붙이지 않는다(doc2report의
# confluence.yaml::auto_markers=false와 같은 취지 - "이미 Confluence 제목
# 스타일로 구분된 문서라 말머리가 필요 없다"). 처음엔 제목에만 예외적으로
# "제목에 번호가 보여야 의미 있다"는 이유로 번호를 새로 붙여 봤지만, 그러면
# 본문이 긴 문서는 거의 모든 줄 앞에 말머리가 붙어 버려서(제목 자신에게만
# 붙여도 그 아래 평문단과 뒤섞여 똑같이 번잡해 보임) 실사용 피드백으로
# 도로 뺐다("그냥 안 붙여도 되겠어") - 결국 doc2report의 원래 설정을 그대로
# 따르는 쪽이 맞았다. 원래부터 목록(ul/li)이던 항목은 다르게 취급한다 -
# 그건 "제목 아래로 접혀 들어온 평문단"이 아니라 애초에 목록이었다는 것
# 자체가 말머리로 드러나야 하므로, 원문에 말머리가 없으면 depth에 따라
# "1." "□" "-" "·"를 새로 매겨 보여준다(건 보통 ul/li 자체가 "항목 나열"이라는
# 저자의 의도를 담고 있어서 혼동 위험이 적음). 원문에 이미 "1." "□" 같은
# 말머리가 쳐 있으면(keep_leading_markers) 제목·문단·목록 어디서든 그건
# 그대로 쓰고 새로 붙이지 않는다 - 이 프로젝트의 builder.py가 만드는 안건
# 제목("1. 안건1", "2. 안건2")이 바로 이 경우라 그 번호는 그대로 보인다.
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


def _convert_to_listitem(
    element: etree._Element, depth: int, *, from_heading: bool = False, suppress_auto_marker: bool = False
) -> None:
    """요소를 그 자리에서(태그만 바꿔서) "listitem"으로 바꾼다 - 내용(자식/서식)은
    그대로 두고 번호 체계 렌더링에 필요한 정보만 속성으로 얹는다.

    suppress_auto_marker=True면 원문에 말머리가 없을 때 새 말머리를 만들어
    붙이지 않는다(doc2report의 confluence.yaml::auto_markers=false와 같은
    취지) - 제목 아래로 접혀 들어온 평문단에 쓴다. 제목 자신과 원래부터
    목록(ul/li)이던 항목은 이 억제 없이 그대로 말머리가 보여야 "1. → □ → -"
    체계가 실제로 보이므로 suppress하지 않는다."""
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
    if suppress_auto_marker:
        element.set("data-suppress-auto-marker", "1")


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
                # 제목에도 "1."/"□" 같은 말머리를 새로 붙이지 않는다(사용자 피드백:
                # "그냥 안 붙여도 되겠어") - 원문에 이미 말머리가 있으면(keep_leading_
                # markers) 그건 그대로 쓰지만, 없으면 들여쓰기+굵게만 적용하고
                # 말머리는 비워 둔다.
                _convert_to_listitem(child, depth, from_heading=True, suppress_auto_marker=True)
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
                    _convert_to_listitem(child, target_depth, suppress_auto_marker=True)
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


_THEME_FONT_ATTRS = ("asciiTheme", "hAnsiTheme", "eastAsiaTheme", "cstheme")


def _set_east_asian_font(font, name: str) -> None:
    """python-docx의 Font.name은 ascii/hAnsi만 설정하고 eastAsia(한글 글꼴)는
    안 건드려서, 한글 문서에서 영문 글꼴로 보이는 걸 막으려면 w:eastAsia를
    직접 oxml로 설정해야 한다. font._element는 런(CT_R)이든 스타일(CT_Style)이든
    get_or_add_rPr()을 지원해서 둘 다 같은 방식으로 처리된다.

    python-docx의 기본 Title/Heading 스타일은 글꼴을 이름이 아니라 테마
    참조(w:asciiTheme="majorHAnsi" 등)로 지정해 두는데, font.name을 설정해도
    이 테마 속성은 안 지워져서 w:ascii(우리가 지정한 이름)와 테마 참조가
    동시에 남는다 - 워드는 이때 테마 참조를 우선해 버려서 우리가 지정한
    글꼴(맑은 고딕)이 무시되고 테마 기본 글꼴로 보이는 문제가 실제로 있었다
    (제목만 다른 글꼴로 보이던 원인). 테마 속성을 직접 지워서 우리가 지정한
    이름이 확실히 적용되게 한다."""
    font.name = name
    r_pr = font._element.get_or_add_rPr()
    r_fonts = r_pr.get_or_add_rFonts()
    r_fonts.set(qn("w:eastAsia"), name)
    for attr in _THEME_FONT_ATTRS:
        if r_fonts.get(qn(f"w:{attr}")) is not None:
            del r_fonts.attrib[qn(f"w:{attr}")]


def _shade_cell(cell, hex_color: str) -> None:
    shading = etree.SubElement(cell._tc.get_or_add_tcPr(), qn("w:shd"))
    shading.set(qn("w:val"), "clear")
    shading.set(qn("w:color"), "auto")
    shading.set(qn("w:fill"), hex_color)


def _modernize_compatibility_mode(document: DocxDocument) -> None:
    """python-docx 기본 템플릿은 "호환 모드"(워드 2010, compatibilityMode=14)로
    표시돼 있다. 이 호환 모드에서는 워드 데스크톱이 표의 열 폭 같은 일부
    레이아웃을 옛 버전 방식으로 다시 계산해서, 우리가 지정한 열 폭(w:tblGrid/
    w:tblW)을 무시하고 전부 똑같은 폭으로 그려 버리는 경우가 있다 - 실사용
    보고("글자 양이 뚜렷하게 다른 표에서도 계속 동일해")가 표 하나의 문제가
    아니라 문서 전체에 걸쳐 똑같이 일어난 것과 정확히 들어맞는다. 워드
    2013 이후 호환 모드(15)로 올려서 "호환 모드" 표시 없는 일반 문서로
    취급되게 한다."""
    settings = document.settings.element
    for setting in settings.iter(qn("w:compatSetting")):
        if setting.get(qn("w:name")) == "compatibilityMode":
            setting.set(qn("w:val"), "15")


def _configure_document_styles(document: DocxDocument) -> None:
    _modernize_compatibility_mode(document)

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
    # python-docx 기본 템플릿의 "Title" 스타일은 밑줄과는 별개로 문단 아래에
    # 가로줄(테두리, w:pBdr)이 하나 더 있어서, 우리가 지정한 글자 밑줄과
    # 합쳐져 "제목 아래에 긴 밑줄이 생긴다"처럼 보였다(사용자 보고) - 그
    # 테두리를 지운다. 밑줄(위에서 설정한 font.underline)은 그대로 남는다.
    p_pr = title_style.element.get_or_add_pPr()
    p_bdr = p_pr.find(qn("w:pBdr"))
    if p_bdr is not None:
        p_pr.remove(p_bdr)

    for level in range(1, 7):
        try:
            style = document.styles[f"Heading {level}"]
        except KeyError:
            continue
        _set_east_asian_font(style.font, _DOCUMENT_FONT_NAME)
        style.font.size = Pt(_HEADING_FONT_SIZES_PT.get(level, _HEADING_FONT_SIZES_PT[3]))
        style.font.bold = True
        style.paragraph_format.keep_with_next = True


class _RenderState:
    """렌더링 전체에서 공유해야 하는 상태 - 번호 매기기 카운터(_format_marker,
    연결된 페이지의 "pagetitle"마다 비워짐)와 미리 받아 둔 이미지 바이트
    (_fetch_images_for_page가 채워 둔 것, "data-image-key" 속성값 -> 바이트)."""

    def __init__(self, images: Optional[dict] = None):
        self.counters: dict = {}
        self.images: dict = images or {}


def _pad_title_text(text: Optional[str]) -> str:
    """제목 글자 앞뒤에 공백 하나씩 붙인다 - 밑줄(글자 밑줄)이 그 공백까지
    덮어서 제목이 조금 더 길게 밑줄 쳐진 것처럼 보이게 한다(사용자 요청:
    "앞뒤로 공백을 줘서 밑줄이 조금 더 길어보여도 좋겠네")."""
    stripped = (text or "").strip()
    return f" {stripped} " if stripped else stripped


def _render_document(title: str, root: etree._Element, images: Optional[dict] = None) -> bytes:
    _tag_table_captions_and_notes(root)
    _fold_headings_into_levels(root)

    document = DocxDocument()
    _configure_document_styles(document)
    document.add_heading(_pad_title_text(title), level=0)
    _render_blocks(document, root, _RenderState(images))
    buf = BytesIO()
    document.save(buf)
    return buf.getvalue()


def _render_blocks(document: DocxDocument, container: etree._Element, state: "_RenderState") -> None:
    for element in container:
        tag = _local(element.tag)

        if tag == "pagebreak":
            # 연결된 페이지(include/children) 사이에 끼워 넣는 쪽 나눔 - 진짜
            # Confluence 태그가 아니라 _resolve_linked_pages가 만들어 넣는 합성 요소.
            document.add_page_break()
        elif tag == "pagetitle":
            # 연결된 페이지의 제목 - doc2report의 Heading.page_title과 같이
            # 번호 체계에 접지 않고 문서 제목 서식을 쓰며, 항목 번호를 새로 센다.
            state.counters.clear()
            element.text = _pad_title_text(element.text)
            _add_inline_runs(document.add_heading("", level=0), element, state.images)
        elif tag == "listitem":
            _render_listitem(document, element, state.counters, state.images)
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
                _add_inline_runs(paragraph, source, state.images)
                for run in paragraph.runs:
                    run.font.size = Pt(10)
        elif tag in _HEADING_LEVELS:
            # _fold_headings_into_levels가 보통 다 listitem으로 바꿔서 여기까지
            # 안 오지만(표 셀 안 등 그 변환이 안 들어간 자리를 위한 안전망), 혹시
            # 남아 있으면 기존 방식(Heading 스타일)으로라도 렌더링한다.
            _add_inline_runs(document.add_heading("", level=_HEADING_LEVELS[tag]), element, state.images)
        elif tag == "p":
            _add_inline_runs(document.add_paragraph(), element, state.images)
        elif tag in ("ul", "ol"):
            _render_list(document, element, state.images, ordered=(tag == "ol"))
        elif tag == "table":
            _render_table(document, element, state.images)
        elif tag == "blockquote":
            try:
                paragraph = document.add_paragraph(style="Intense Quote")
            except KeyError:  # 기본 템플릿에 그 스타일이 없을 수도 있음
                paragraph = document.add_paragraph()
            _add_inline_runs(paragraph, element, state.images)
        elif _is_ac(element, "image"):
            _render_image(document, element, state.images)
        elif _is_ac(element, "structured-macro"):
            _render_macro(document, element, state)
        elif tag in _BLOCK_FLATTEN_TAGS:
            # 레이아웃용 래퍼(ac:layout 등) - 내용만 순서대로 펼친다.
            _render_blocks(document, element, state)
        elif _clean_text(element.text).strip() or len(element):
            # 알 수 없는 블록 요소 - 내용은 최대한 살려서 평문단으로.
            _add_inline_runs(document.add_paragraph(), element, state.images)


def _render_listitem(
    document: DocxDocument, element: etree._Element, counters: dict, images: dict
) -> None:
    """번호 체계 단계(1./□/-/·) 항목 하나를 렌더링한다 - doc2report의
    render/docx_writer.py::_list_item을 참고해 새로 구현(내어쓰기/탭 정렬,
    말머리 자동 번호 매기기)."""
    depth = int(element.get("data-depth", "0"))
    level = _NUMBERING_LEVELS[min(depth, len(_NUMBERING_LEVELS) - 1)]
    marker = element.get("data-marker")
    if marker is None and element.get("data-suppress-auto-marker") != "1":
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
    _add_inline_runs(paragraph, element, images, bold=bold)


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


def _render_macro(document: DocxDocument, macro: etree._Element, state: "_RenderState") -> None:
    body = macro.find("ac:rich-text-body", namespaces={"ac": _AC_NS})
    if body is not None:
        _render_blocks(document, body, state)
        return
    name = macro.get(f"{{{_AC_NS}}}name") or "매크로"
    if name in _SILENT_BLOCK_MACRO_NAMES:
        return
    note = document.add_paragraph(f"[{name} 매크로 - 이 변환에서는 지원하지 않습니다]")
    note.runs[0].italic = True


# 본문 폭(A4, 좌우 여백 20mm씩 제외)을 넘지 않게 - 이보다 큰 이미지만 줄이고,
# 작은 이미지는 원본 크기 그대로 둔다(작은 아이콘을 억지로 키우지 않도록).
_MAX_IMAGE_WIDTH_MM = 170


def _resolve_image_bytes(image: etree._Element, images: dict) -> Optional[bytes]:
    result = images.get(image.get("data-image-key"))
    return result if isinstance(result, bytes) else None


def _image_placeholder_text(image: etree._Element, images: dict) -> str:
    attachment = image.find("ri:attachment", namespaces={"ri": _RI_NS})
    filename = attachment.get(f"{{{_RI_NS}}}filename") if attachment is not None else None
    base = f"[이미지: {filename}]" if filename else "[이미지]"
    result = images.get(image.get("data-image-key"))
    if isinstance(result, _ImageFetchFailure):
        return f"{base} - {result.reason}"
    return base


def _shrink_picture_to_max_width(picture) -> None:
    max_width = Mm(_MAX_IMAGE_WIDTH_MM)
    if picture.width > max_width:
        ratio = max_width / picture.width
        picture.width = max_width
        picture.height = int(picture.height * ratio)


def _render_image(document: DocxDocument, image: etree._Element, images: dict) -> None:
    """블록 위치(문단 바로 자리)의 ac:image를 실제 첨부파일 내용으로 그려
    넣는다 - convert_confluence_url_to_docx가 렌더링 전에
    _fetch_images_for_page()로 미리 받아 둔 바이트를 쓴다. 못 받아왔으면
    (첨부파일을 못 찾음, 네트워크 오류 등) 예전처럼 파일명만 보여주는
    자리표시자로 대신한다."""
    data = _resolve_image_bytes(image, images)
    if data:
        try:
            picture = document.add_picture(BytesIO(data))
            _shrink_picture_to_max_width(picture)
            return
        except Exception:
            pass  # 깨진 이미지 등 - 아래 자리표시자로 대신한다.
    note = document.add_paragraph(_image_placeholder_text(image, images))
    note.runs[0].italic = True


def _add_inline_image(paragraph, image: etree._Element, images: dict) -> None:
    """문단 "중간"(인라인 위치)의 ac:image - Confluence가 이미지를
    <p><ac:image>...</ac:image></p>처럼 문단 안에 끼워 넣는 경우가 많아서
    (실제 변환에서 발견: _render_image는 블록 자리에서만 호출되고,
    _add_inline_runs는 ac:image를 모르는 태그로 보고 그냥 건너뛰어
    버려서 이미지가 자리표시자조차 없이 통째로 사라졌다) 문단 밖에 새
    문단을 만들 수 없는 이 자리에서는 run.add_picture()로 같은 문단
    안에 그려 넣는다."""
    data = _resolve_image_bytes(image, images)
    if data:
        try:
            run = paragraph.add_run()
            picture = run.add_picture(BytesIO(data))
            _shrink_picture_to_max_width(picture)
            return
        except Exception:
            pass
    run = paragraph.add_run(_image_placeholder_text(image, images))
    run.italic = True


def _render_list(
    document: DocxDocument, list_element: etree._Element, images: dict, *, ordered: bool
) -> None:
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
        _add_inline_runs(paragraph, item, images)
        for nested_list in nested:
            _render_list(document, nested_list, images, ordered=(_local(nested_list.tag) == "ol"))


# 표 전체 폭 - A4 폭(210mm)에서 좌우 여백(20mm씩)을 뺀 값과 같다(이미지의
# 최대 폭과도 같은 기준).
_TABLE_USABLE_WIDTH_MM = 170
# 빈 칸이나 아주 짧은 칸도 너무 좁게 눌리지 않게 주는 최소 가중치(글자 수
# 기준) - 숫자가 아니라 "최소 이 정도 폭은 있어야 한다"는 바닥값 역할이다.
_TABLE_MIN_COLUMN_WEIGHT = 6
# 한글/한자/가나 등 전각 문자는 라틴 문자보다 약 2배 넓게 보이므로, 폭 계산
# 때 한 글자를 2칸으로 센다(그래야 한글 위주 칸과 숫자 위주 칸의 비율이
# 실제 보이는 폭 비율과 비슷해진다).
_WIDE_CHAR_RANGES = (
    (0x1100, 0x11FF),  # 한글 자모
    (0x3130, 0x318F),  # 한글 호환 자모
    (0xAC00, 0xD7A3),  # 한글 음절
    (0x3040, 0x30FF),  # 가나(히라가나/가타카나)
    (0x4E00, 0x9FFF),  # 한자
    (0xFF00, 0xFFEF),  # 전각 기호/문자
)


def _is_wide_char(ch: str) -> bool:
    code = ord(ch)
    return any(start <= code <= end for start, end in _WIDE_CHAR_RANGES)


def _visual_text_width(text: str) -> int:
    return sum(2 if _is_wide_char(ch) else 1 for ch in text)


_CSS_WIDTH_PX_RE = re.compile(r"width\s*:\s*([0-9.]+)\s*px", re.IGNORECASE)


def _colgroup_column_widths(
    table_element: etree._Element, col_count: int
) -> Optional[List[float]]:
    """사용자가 Confluence 표에서 직접 열 폭을 조정하면 <colgroup><col
    style="width: Npx"/>...</colgroup>에 그 값이 그대로 저장된다 - 글자
    양으로 추정하기보다 이 값이 있으면 그 비율을 먼저 쓰는 게 더 정확하다
    (사용자 요청: "원본 표에 설정된 폭이 있다면 그걸 비율로 먼저 치환").
    모든 열에 대해 폭을 다 구할 수 있을 때만 쓰고, colgroup이 없거나 일부
    열이라도 폭을 못 구하면 None을 돌려줘서 글자 수 기반 계산으로 넘어가게
    한다."""
    colgroup = next((child for child in table_element if _local(child.tag) == "colgroup"), None)
    if colgroup is None:
        return None

    widths: List[Optional[float]] = []
    for col in colgroup:
        if _local(col.tag) != "col":
            continue
        span = max(1, _int_attr(col, "span"))
        match = _CSS_WIDTH_PX_RE.search(col.get("style") or "")
        width = float(match.group(1)) if match else None
        widths.extend([width] * span)

    if len(widths) < col_count or any(not w or w <= 0 for w in widths[:col_count]):
        return None
    return widths[:col_count]


def _apply_content_based_column_widths(
    table,
    table_element: etree._Element,
    placements: List[Tuple[int, int, int, int, etree._Element]],
    col_count: int,
) -> None:
    """각 열의 폭을 미리 계산해 지정한다 - 지금까지는 python-docx가 만드는
    기본값(모든 열이 똑같은 폭)을 그대로 뒀는데, 실제 표는 열마다 내용
    길이가 크게 달라서 요청이 들어왔다(사용자: "내용에 따라 동적으로
    조절이 가능할까?"). 원본 표에 사용자가 직접 조정해 둔 폭(colgroup)이
    있으면 그 비율을 그대로 쓰고, 없으면 글자 양(_visual_text_width)으로
    추정한다. colspan으로 합쳐진 칸은 글자 수 기반 추정에서는 "한 열의
    폭"이 뭘 뜻하는지 애매해서 건너뛴다 - 합쳐지지 않은 다른 행의 같은 열
    내용으로도 충분히 가늠할 수 있다.

    table.autofit=False로 바꿔 워드가 자체적으로 다시 계산하지 않고 우리가
    지정한 폭을 그대로 쓰게 한다(열려서 바로 보일 모양을 우리가 보장).

    열 폭(w:tblGrid/w:gridCol, w:tcW)만 설정하면 워드가 실제로는 무시하고
    다시 균등하게 그리는 경우가 있었다(실사용에서 발견) - 표 전체 폭
    (w:tblPr/w:tblW)이 python-docx 기본값인 "auto"(0)로 남아 있어서,
    tblLayout이 "fixed"라도 워드가 전체 폭을 다시 계산하면서 열 폭도
    같이 재분배해 버린 것이었다. 그래서 표 전체 폭도 "고정값"(각 열 폭의
    합)으로 명시해야 열 폭이 실제로 그대로 지켜진다."""
    weights = _colgroup_column_widths(table_element, col_count)
    if weights is None:
        weights = [0] * col_count
        for _row_index, col_index, _rowspan, colspan, cell in placements:
            if colspan != 1 or col_index >= col_count:
                continue
            text = "".join(cell.itertext())
            weights[col_index] = max(weights[col_index], _visual_text_width(text))
        weights = [max(w, _TABLE_MIN_COLUMN_WEIGHT) for w in weights]

    total_weight = sum(weights)
    if total_weight <= 0:
        return

    table.autofit = False
    for index, column in enumerate(table.columns):
        width = Mm(_TABLE_USABLE_WIDTH_MM * weights[index] / total_weight)
        column.width = width
        # python-docx의 Column.width setter는 w:tblGrid/w:gridCol만 바꾸고
        # 각 행에 있는 칸들의 w:tcW는 그대로 둔다(실사용에서 발견: 미리보기는
        # 맞는데 실제 워드 파일만 전부 같은 폭 - 워드가 gridCol보다 각 칸의
        # w:tcW를 우선해서 렌더링하는 것으로 보임). 그래서 열의 모든 칸에도
        # 같은 폭을 직접 맞춰 줘야 한다.
        for cell in column.cells:
            cell.width = width

    # EMU(python-docx의 길이 단위) -> dxa(OOXML 표 폭 단위, 1/20pt) 변환:
    # 1pt = 12700EMU이므로 1dxa(=1/20pt) = 635EMU.
    total_dxa = sum(int(column.width / 635) for column in table.columns)
    tbl_w = table._tbl.tblPr.find(qn("w:tblW"))
    if tbl_w is None:
        tbl_w = etree.SubElement(table._tbl.tblPr, qn("w:tblW"))
    tbl_w.set(qn("w:type"), "dxa")
    tbl_w.set(qn("w:w"), str(total_dxa))


def _render_table(document: DocxDocument, table_element: etree._Element, images: dict) -> None:
    rows: List[etree._Element] = []
    for section in table_element:
        section_tag = _local(section.tag)
        if section_tag in ("thead", "tbody", "tfoot"):
            rows.extend([tr for tr in section if _local(tr.tag) == "tr"])
        elif section_tag == "tr":
            rows.append(section)

    html_rows = [[cell for cell in row if _local(cell.tag) in ("td", "th")] for row in rows]
    if not html_rows:
        return
    row_count = len(html_rows)

    # HTML 표는 rowspan으로 가린 칸을 그 행의 <td>/<th> 목록에 안 쓴다(표 자체가
    # "건너뛴 자리"를 아는 게 아니라, 렌더러가 이전 행들의 rowspan을 추적해서
    # 알아내야 한다) - occupied[행]에 그 행에서 이미 이전 행의 rowspan으로 찬
    # 칸의 열 번호를 모아 둔다.
    occupied: List[set] = [set() for _ in range(row_count)]
    placements: List[Tuple[int, int, int, int, etree._Element]] = []
    col_count = 0

    for row_index, cells in enumerate(html_rows):
        col_index = 0
        for cell in cells:
            while col_index in occupied[row_index]:
                col_index += 1
            colspan = max(1, _int_attr(cell, "colspan"))
            rowspan = max(1, _int_attr(cell, "rowspan"))
            placements.append((row_index, col_index, rowspan, colspan, cell))
            for future_row in range(row_index + 1, min(row_index + rowspan, row_count)):
                occupied[future_row].update(range(col_index, col_index + colspan))
            col_index += colspan
        col_count = max(col_count, col_index)

    if col_count == 0:
        return

    table = document.add_table(rows=row_count, cols=col_count)
    try:
        table.style = "Table Grid"
    except KeyError:
        pass
    _apply_content_based_column_widths(table, table_element, placements, col_count)

    for row_index, col_index, rowspan, colspan, cell in placements:
        end_row = min(row_index + rowspan, row_count) - 1
        end_col = min(col_index + colspan, col_count) - 1
        docx_cell = table.cell(row_index, col_index)
        if end_row != row_index or end_col != col_index:
            docx_cell = docx_cell.merge(table.cell(end_row, end_col))
        is_header = _local(cell.tag) == "th"
        _render_cell_content(docx_cell, cell, images, bold=is_header)
        if is_header:
            _shade_cell(docx_cell, _TABLE_HEADER_SHADING_HEX)


_CELL_BLOCK_TAGS = ("p", "ul", "ol")


def _render_cell_content(docx_cell, source_cell: etree._Element, images: dict, *, bold: bool) -> None:
    """표 칸 안의 내용을 채운다 - 칸 안에 <p>가 여러 개면(Confluence 표 칸은
    흔히 그렇다) 각각 별도 문단으로 넣어야 줄바꿈이 보인다. 예전에는 칸
    전체를 _add_inline_runs 한 번으로 평문단에 몰아 넣어서, <p> 여러 개가
    줄바꿈 없이 한 줄로 붙어 버렸다(실제 변환에서 발견된 버그)."""
    blocks = [child for child in source_cell if _local(child.tag) in _CELL_BLOCK_TAGS]
    if not blocks:
        blocks = [source_cell]  # 블록 태그 없이 텍스트/<br/>만 있는 칸 - 기존처럼 한 문단으로.

    # <ul>/<ol>은 중첩 깊이 없이 각 <li>를 그냥 줄 하나로 펼친다(표 칸 안
    # 목록은 알려진 단순화 범위). 그 외(<p> 등)는 그 자체가 한 문단.
    paragraph_sources: List[etree._Element] = []
    for block in blocks:
        if _local(block.tag) in ("ul", "ol"):
            paragraph_sources.extend(item for item in block if _local(item.tag) == "li")
        else:
            paragraph_sources.append(block)
    if not paragraph_sources:
        paragraph_sources = [source_cell]

    for index, source in enumerate(paragraph_sources):
        paragraph = docx_cell.paragraphs[0] if index == 0 else docx_cell.add_paragraph()
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        _add_inline_runs(paragraph, source, images, bold=bold)
        for run in paragraph.runs:
            run.font.size = Pt(10)
            _set_east_asian_font(run.font, _DOCUMENT_FONT_NAME)


def _int_attr(element: etree._Element, name: str) -> int:
    value = element.get(name)
    try:
        return int(value) if value else 1
    except ValueError:
        return 1


_INLINE_STYLE_TAGS = {"strong": "bold", "b": "bold", "em": "italic", "i": "italic", "u": "underline"}


def _add_inline_runs(
    paragraph, element: etree._Element, images: dict, *, bold=False, italic=False, underline=False
) -> None:
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
            _add_inline_runs(paragraph, child, images, **kwargs)
        elif _is_ac(child, "image"):
            # Confluence는 이미지를 흔히 <p><ac:image>...</ac:image></p>처럼
            # 문단 "안"에 끼워 넣는다 - 이 경로를 몰라서 그냥 건너뛰면(알 수
            # 없는 태그로 보고 재귀했다가 텍스트가 없어 아무것도 안 생김)
            # 이미지가 자리표시자조차 없이 통째로 사라진다(실제 변환에서
            # 발견된 버그 - "이미지 파일명 그런 자리표시자도 안 보여").
            _add_inline_image(paragraph, child, images)
        elif _is_ac(child, "structured-macro"):
            # 문단 중간에 끼어드는 매크로(anchor/create-from-template 등, 이
            # 프로젝트의 builder.py가 실제로 이렇게 쓴다) - rich-text-body가
            # 있으면 그 내용만 펼치고, 없으면 화면에 보이는 내용이 없는
            # 매크로이므로 건너뛴다. ac:parameter 값(매크로 설정·버튼 ID 등)을
            # 본문 텍스트로 잘못 끌어오면 안 된다(예: "제목1" 북마크 이름이나
            # "3877634148" 템플릿 ID가 본문에 그대로 섞여 나오던 문제).
            body = child.find("ac:rich-text-body", namespaces={"ac": _AC_NS})
            if body is not None:
                _add_inline_runs(paragraph, body, images, bold=bold, italic=italic, underline=underline)
        else:
            # a(링크, 글자만 살리고 하이퍼링크는 안 만듦)/span/code/ac:link 등 -
            # 내용은 그대로 펼친다.
            _add_inline_runs(paragraph, child, images, bold=bold, italic=italic, underline=underline)

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

    say("이미지 가져오는 중...")
    images: dict = {}
    _fetch_images_for_page(root, base_url, effective_token, page_id, images)

    say("연결된 하위 페이지 포함 중...")
    space = (page.get("space") or {}).get("key")
    link_ctx = _LinkContext(base_url, effective_token, space, page_id, images)
    _resolve_linked_pages(root, link_ctx, page_id, depth=0)

    say("Word 문서 생성 중...")
    data = _render_document(title, root, images)
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
