"""Confluence 페이지 가져오기 — REST API로 storage format(XHTML)을 직접 읽는다.

마크다운으로 한 번 내렸다 올리는 경로(예전엔 confluence-markdown-exporter에 의존했다)는
표의 병합 셀(rowspan/colspan) 정보를 잃는다. GFM 마크다운 표 문법에는 그걸 표현할 방법이
없기 때문이다. Confluence는 표 폭에 제약이 없어 사용자가 자유롭게 셀을 합치므로, 이
손실이 특히 크다. 그래서 REST API가 주는 storage XHTML을 곧장
parsers/confluence_storage.py 로 넘긴다.

인증 정보는 환경변수로만 받는다 (코드·프로파일에 넣지 않는다):
    CONFLUENCE_URL       https://회사.atlassian.net/wiki  (Server/DC면 https://wiki.회사.com)
    CONFLUENCE_USERNAME  계정 이메일 — 있으면 Cloud(API 토큰 + Basic 인증)로 인식
    CONFLUENCE_API_TOKEN API 토큰(Cloud) 또는 개인 액세스 토큰(Server/DC, USERNAME 없이 Bearer로 씀)

CONFLUENCE_URL은 REST 엔드포인트 앞의 기본 주소여야 한다(`/rest/api/content/...`는
코드가 붙인다). 회사에 따라 REST 전용 게이트웨이가 브라우저 주소와 다른 서브도메인에
있고(예: `wiki.회사.com`이 아니라 `api.confluence.회사.com`), 안내받은 주소 자체에
`/rest/api`가 이미 포함된 경우도 있다 — 둘 다 그대로 넣어도 되도록 `/rest/api` 접미사는
있으면 떼고 쓴다(`_normalize_base_url`).

받는 URL은 숫자 페이지 ID를 담고 있어야 한다 (`/pages/123456`, `?pageId=123456`,
또는 페이지 ID 숫자 그 자체). `/x/AbCd` 같은 단축 링크는 그 자체로 리다이렉트라
REST API 한 번으로는 못 푼다 — 페이지를 열어 실제 URL(또는 "..." 메뉴의 페이지 ID)을
확인해서 넣어야 한다.

사내망 SSL 인증서 문제(2026-09-29, 사내 PC 실측): 사내 프록시가 자체 발급한 인증서로
HTTPS를 중계하면, 그 루트 인증서가 파이썬 기본 CA 번들(certifi)에는 없어서 httpx가
"SSL 인증서 검증 실패"로 연결 자체를 못 하는 경우가 있다(사내 LLM이 직접 진단). 두 가지로
대응한다:
  1. CONFLUENCE_CA_BUNDLE(또는 REQUESTS_CA_BUNDLE, SSL_CERT_FILE) 환경변수로 사내
     루트 인증서(.crt/.pem) 경로를 주면 그걸 신뢰 기준으로 쓴다. **httpx는 requests와
     달리 REQUESTS_CA_BUNDLE을 자동으로 읽지 않아서**, 이 세 변수를 명시적으로 읽어
     verify=에 넘긴다(`_ssl_verify`).
  2. 인증서 파일을 아직 못 구했거나 그래도 안 될 때: httpx 연결이 실패하면(Windows
     에서만) Windows 인증서 저장소를 그대로 쓰는 PowerShell의 Invoke-WebRequest로
     한 번 더 시도한다(다른 에이전트가 직접 검증한 경로). 같은 대체 로직이 403 응답을
     받았을 때도 동작한다(사내 보안 게이트웨이가 요청 형태로 걸러내는 경우 대응).
     성공하면 --report에 남긴다. DOC2REPORT_CONFLUENCE_TRANSPORT=powershell 로
     처음부터 이 경로를 강제할 수도 있다.
"""

from __future__ import annotations

import base64
import os
import platform
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import httpx

from . import LoadedSource

_TIMEOUT = 30.0
_FALLBACK_ATTR = "_doc2report_use_powershell"  # httpx.Client에 붙이는 표식: 이 변환은 PowerShell로
_TRANSPORT_ENV = "DOC2REPORT_CONFLUENCE_TRANSPORT"  # 비어 있으면 자동, "powershell"이면 강제

# Cloud: https://x.atlassian.net/wiki/spaces/TEAM/pages/123456/제목
# Server/DC(고전 URL, 흔함): https://wiki.회사.com/pages/viewpage.action?pageId=123456
# Server/DC(짧은 링크): https://wiki.회사.com/x/AbCd — 이건 REST로 못 푼다(모듈 docstring 참고)
_PAGE_ID_PATTERNS = (
    re.compile(r"/pages/(\d+)(?:/|$)"),
    re.compile(r"[?&]pageId=(\d+)"),
)


def page_id_from_url(url: str) -> str | None:
    for pattern in _PAGE_ID_PATTERNS:
        match = pattern.search(url)
        if match:
            return match.group(1)
    if url.isdigit():
        return url
    return None


def load_confluence(url_or_id: str, *, linked: bool = True) -> LoadedSource:
    base_url = _normalize_base_url(_require("CONFLUENCE_URL"))
    page_id = page_id_from_url(url_or_id)
    if page_id is None:
        raise RuntimeError(
            f"URL에서 페이지 ID를 못 찾았습니다: {url_or_id}\n"
            "'/pages/123456', '?pageId=123456' 형태이거나 페이지 ID 숫자 자체여야 합니다. "
            "('/x/AbCd' 같은 단축 링크는 지원하지 않습니다 — 페이지를 열어 실제 URL을 쓰세요.)"
        )

    notes: list[str] = []
    with _client(base_url, notes) as client:
        page = _get_page(client, page_id, notes)
        storage = page.get("body", {}).get("storage", {}).get("value")
        if not storage:
            raise RuntimeError(
                f"페이지 {page_id}에 storage 본문이 없습니다. "
                "권한이 있는 페이지인지, 첨부파일이 아니라 페이지인지 확인하세요."
            )

        title = page.get("title") or url_or_id
        out_dir = Path(tempfile.mkdtemp(prefix="doc2report-confluence-"))
        notes.insert(0, f"Confluence 페이지 '{title}'를 REST API로 읽음 (id={page_id})")
        notes.extend(_download_attachments(client, page_id, out_dir, notes))

    space = (page.get("space") or {}).get("key")
    resolver = LinkedPages(base_url, page_id, space, out_dir) if linked else None
    return LoadedSource(
        text=storage,
        name=url_or_id,
        base_dir=out_dir,
        notes=notes,
        format="confluence_storage",
        title=title,
        linked=resolver,
    )


# ── 연결 페이지 (페이지 포함·발췌 포함·하위 페이지·첨부 보기 매크로) ─────────
#
# 2026-09-29 사용자: Confluence 문서를 여러 페이지로 나눠 두고 본문에서 "+로 펼쳐" 보게 연결해 둔다 —
# 본문 하나만 주면 연결된 페이지까지 한 번에 변환하고 싶다. 파서가 그 자리에 PageRef를 남기면 여기서
# REST로 대상 페이지를 가져와 파싱한 블록으로 바꾼다(대상 페이지 안의 연결도 재귀로, 단 순환·폭주 방지).

MAX_LINKED_PAGES = 40   # 한 번 변환에 더 불러올 페이지 수 상한
MAX_LINK_DEPTH = 4      # 연결의 연결… 몇 단계까지


class LinkedPages:
    """불러온 문서(페이지 포함·하위 페이지·첨부 Word)는 **새 쪽에서 그 문서 제목과 함께** 시작한다
    (2026-09-30 사용자: "아래로 쭉 붙어지는데 각각 새 쪽에서, 제목도"). 제목은 Confluence 페이지 제목
    (첨부 Word는 파일 이름)을 쪽 제목 서식(`Heading.page_title` — 큰 글씨·가운데·밑줄)으로 쓴다.
    발췌 포함·책갈피 구간은 문서가 아니라 조각이라 그 자리에 이어 붙이고, 패널(Callout) 안에서도
    쪽을 나누지 않는다.

    follow_links: 본문 링크(다른 페이지, 또는 그 페이지의 책갈피)가 가리키는 내용도 넣는다. 사용자
    실측에서 매크로만으로 충분해(2026-09-30) 화면 옵션은 뺐고 CLI `--follow-links`로만 남겼다."""

    def __init__(self, base_url: str, page_id: str, space: str | None, out_dir: Path):
        self.base_url = base_url
        self.root = (page_id, space, out_dir)
        self.visited = {page_id}   # 통째로 넣은 페이지
        self.sections: set = set()  # 책갈피 구간만 넣은 (페이지, 책갈피)
        self.loaded = 0
        self.follow_links = False

    def expand(self, blocks: list, notes: list[str], *, progress=None) -> list:
        from ..ir import PageBreak
        from ..parsers.confluence_storage import PageRef, drop_page_refs

        if not self.follow_links:
            blocks = drop_page_refs(blocks, notes, kinds={"link"})
        if not _has_ref(blocks, PageRef):
            return drop_page_refs(blocks, notes)  # 남은 건 펼치기 제목(label)뿐 — 글자로 되돌린다
        self.say = progress or (lambda message: None)
        with _client(self.base_url, notes) as client:
            blocks = self._expand(client, blocks, notes, self.root, depth=0)
        # 본문이 곧바로 불러온 문서로 시작하면(목차만 있는 페이지) 첫 쪽을 제목만 두고 비우지 않는다
        while blocks and isinstance(blocks[0], PageBreak):
            blocks.pop(0)
        return blocks

    # 한 페이지(ctx = (id, space, 첨부 폴더))의 블록에서 PageRef를 채운다
    def _expand(self, client, blocks: list, notes: list[str], ctx, depth: int, *,
                inline: bool = False) -> list:
        from ..ir import Callout, Paragraph, Run, Table
        from ..parsers.confluence_storage import PageRef, drop_page_refs

        out: list = []
        for block in blocks:
            if isinstance(block, PageRef):
                out.extend(self._resolve(client, block, notes, ctx, depth, inline=inline))
            elif isinstance(block, Callout):
                block.blocks = self._expand(client, block.blocks, notes, ctx, depth, inline=True)
                out.append(block)
            elif isinstance(block, Table):
                for row in block.rows:  # 표 칸 안에 페이지 통째로는 넣지 않는다 — 이름만 남김
                    for cell in row.cells:  # (본문 링크는 글자가 이미 칸에 있으니 그냥 뺀다)
                        cell.blocks = [Paragraph(runs=[Run(f"({b.describe()})")])
                                       if isinstance(b, PageRef) and b.kind != "link" else b
                                       for b in cell.blocks]
                        cell.blocks = drop_page_refs(cell.blocks, notes)
                out.append(block)
            else:
                out.append(block)
        return out

    def _resolve(self, client, ref, notes: list[str], ctx, depth: int, *, inline: bool) -> list:
        from ..ir import Paragraph, Run

        page_id, space, folder = ctx
        label = [Paragraph(runs=[Run(ref.label, bold=True)])] if ref.label else []
        if ref.kind == "attachment":
            return self._attachment(ref, notes, folder, label, inline=inline)
        if depth >= MAX_LINK_DEPTH:
            notes.append(f"{ref.describe()}: 연결이 {MAX_LINK_DEPTH}단계를 넘어 불러오지 않음")
            return label
        try:
            if ref.kind == "children":
                parent = page_id
                if ref.title:
                    parent = self._find(client, ref.title, ref.space or space, notes)["id"]
                children = self._children(client, parent, notes, depth, inline=inline,
                                          levels=ref.depth or MAX_LINK_DEPTH)
                return children if children and not inline else label + children
            page = self._find(client, ref.title, ref.space or space, notes)
        except Exception as exc:
            notes.append(f"{ref.describe()}을(를) 불러오지 못함: {exc}")
            if ref.kind == "link":  # 링크 글자는 본문에 이미 있다
                return []
            return label + [Paragraph(runs=[Run(f"({ref.describe()} — 불러오지 못함)")])]
        anchor = ref.anchor if ref.kind == "link" else None
        # 문서 한 편(페이지 포함·책갈피 없는 링크)은 새 쪽, 조각(발췌·책갈피 구간)은 그 자리에
        new_page = not inline and ref.kind != "excerpt" and not anchor
        blocks = self._page_blocks(client, page, notes, depth, excerpt=ref.kind == "excerpt",
                                   anchor=anchor, new_page=new_page)
        # 새 쪽에는 페이지 제목이 붙으므로 펼치기 제목은 겹친다 — 조각일 때만 남긴다
        return blocks if new_page and blocks else label + blocks

    def _page_blocks(self, client, page: dict, notes: list[str], depth: int, *,
                     excerpt: bool = False, anchor: str | None = None,
                     new_page: bool = False) -> list:
        from ..ir import resolve_image_paths
        from ..parsers.confluence_storage import drop_page_refs, parse_confluence_storage

        pid, title = str(page.get("id")), page.get("title") or ""
        label = f"'{title}'" + (f"의 책갈피 '{anchor}'" if anchor else "")
        if pid in self.visited or (pid, anchor) in self.sections:
            notes.append(f"{label}은(는) 이미 넣었으므로 다시 넣지 않음(순환 연결 방지)")
            return []
        if self.loaded >= MAX_LINKED_PAGES:
            notes.append(f"연결 페이지가 {MAX_LINKED_PAGES}개를 넘어 {label}부터는 불러오지 않음")
            return []
        if anchor:
            self.sections.add((pid, anchor))  # 같은 페이지의 다른 책갈피는 따로 넣을 수 있다
        else:
            self.visited.add(pid)
        self.loaded += 1
        self.say(f"연결 페이지 불러오는 중 ({self.loaded}): {title}")
        storage = ((page.get("body") or {}).get("storage") or {}).get("value") or ""
        parsed = parse_confluence_storage(storage, source=title, title=title, keep_refs=True,
                                          excerpt_only=excerpt, anchor=anchor)
        notes.extend(parsed.notes)
        folder = self.root[2] / pid
        folder.mkdir(parents=True, exist_ok=True)
        notes.extend(_download_attachments(client, pid, folder, notes))
        blocks = parsed.document.blocks
        resolve_image_paths(blocks, folder)
        space = (page.get("space") or {}).get("key") or self.root[1]
        if not self.follow_links:
            blocks = drop_page_refs(blocks, notes, kinds={"link"})
        blocks = self._expand(client, blocks, notes, (pid, space, folder), depth + 1)
        notes.append(f"연결 페이지 {label}를 함께 불러옴 (id={pid})"
                     + (" — 새 쪽에서 시작" if new_page else ""))
        return _new_page(title, blocks) if new_page else blocks

    def _children(self, client, parent: str, notes: list[str], depth: int, *, levels: int,
                  inline: bool = False) -> list:
        from ..ir import Heading, Run

        out: list = []
        resp = _get(client, f"/rest/api/content/{parent}/child/page", notes,
                    params={"limit": 200, "expand": "body.storage,space"})
        _raise_for_status(resp, parent)
        for child in resp.json().get("results", []):
            blocks = self._page_blocks(client, child, notes, depth, new_page=not inline)
            if inline and blocks:  # 패널 안: 쪽을 나누지 않고 제목만 굵게
                blocks = [Heading(level=3, runs=[Run(child.get("title") or "")])] + blocks
            out.extend(blocks)
            if levels > 1 and self.loaded < MAX_LINKED_PAGES:
                out.extend(self._children(client, str(child.get("id")), notes, depth + 1,
                                          levels=levels - 1, inline=inline))
        return out

    def _find(self, client, title: str, space: str | None, notes: list[str]) -> dict:
        params = {"title": title, "expand": "body.storage,space", "limit": 1}
        if space:
            params["spaceKey"] = space
        resp = _get(client, "/rest/api/content", notes, params=params)
        _raise_for_status(resp, title)
        results = resp.json().get("results") or []
        if not results:
            raise RuntimeError(f"'{title}' 페이지를 찾을 수 없음(스페이스 {space or '전체'})")
        return results[0]

    def _attachment(self, ref, notes: list[str], folder: Path, label: list, *,
                    inline: bool) -> list:
        """첨부 보기 매크로: Word(.docx)면 내용을 넣고(새 쪽, 파일 이름이 제목), 그 밖의 파일은 이름만."""
        from ..ir import Paragraph, Run

        path = folder / (ref.filename or "")
        if path.suffix.lower() == ".docx" and path.is_file():
            from ..parsers.docx_reader import parse_docx

            try:
                parsed = parse_docx(path, image_dir=folder / (path.stem + "_images"))
            except Exception as exc:  # 문서보안(DRM)이 걸린 첨부 등
                notes.append(f"첨부 '{ref.filename}'를 읽지 못함: {exc}")
            else:
                notes.append(f"첨부 Word 파일 '{ref.filename}'의 내용을 함께 넣음")
                blocks = parsed.document.blocks
                if inline:
                    return label + blocks
                return _new_page(parsed.document.title or path.stem, blocks)
        notes.append(f"첨부 파일 '{ref.filename}'은 내용을 넣을 수 없어 이름만 남김")
        return label + [Paragraph(runs=[Run(f"※ 첨부: {ref.filename}")])]


def _new_page(title: str, blocks: list) -> list:
    """불러온 문서 한 편: 쪽 나눔 + 쪽 제목(문서 제목 서식) + 내용."""
    from ..ir import Heading, PageBreak, Run

    head = [Heading(level=1, runs=[Run(title)], page_title=True)] if title else []
    return [PageBreak(), *head, *blocks]


def _has_ref(blocks: list, ref_type) -> bool:
    for block in blocks:
        if isinstance(block, ref_type):
            return True
        if _has_ref(getattr(block, "blocks", None) or [], ref_type):
            return True
        for row in getattr(block, "rows", None) or []:
            if any(_has_ref(cell.blocks, ref_type) for cell in row.cells):
                return True
    return False


def confluence_status() -> dict:
    """웹 화면 상단에 보여 줄 연결 설정 상태 (토큰 값은 절대 내보내지 않는다)."""
    url = os.environ.get("CONFLUENCE_URL", "")
    return {
        "configured": bool(url and os.environ.get("CONFLUENCE_API_TOKEN")),
        "url": url,
        "auth": "Cloud(이메일+토큰)" if os.environ.get("CONFLUENCE_USERNAME") else "Server/DC(PAT)",
        "transport": "PowerShell 강제" if _forced_powershell() else "자동",
    }


def confluence_whoami() -> str:
    """이 토큰의 주인 — Confluence가 알려 주는 표시 이름(사용자 이름). 사용자 등록 때 화면 위에 "누구의
    토큰인지"를 등록자가 적은 이름이 아니라 **Confluence가 확인한 이름**으로 보여 주려고
    (2026-09-30 사용자: 토큰 도용 방지). 토큰이 틀리면 Server/DC는 401 대신 익명 사용자로 답하기도 한다."""
    notes: list[str] = []
    with _client(_normalize_base_url(_require("CONFLUENCE_URL")), notes) as client:
        resp = _get(client, "/rest/api/user/current", notes)
        _raise_for_status(resp, "user/current")
        data = resp.json()
    if data.get("type") == "anonymous" or not (data.get("displayName") or data.get("username")):
        raise RuntimeError("토큰이 인정되지 않았습니다(익명 사용자로 응답) — 토큰을 다시 확인하세요")
    name = data.get("displayName") or data.get("username")
    login = data.get("username") or data.get("email") or data.get("publicName") or ""
    return f"{name} ({login})" if login and login != name else name


def fetch_page_title(url_or_id: str) -> tuple[str, str]:
    """본문 없이 제목만 가볍게 확인 — 입력 목록에 페이지를 추가할 때 주소가 맞는지 보여 주려고.
    (페이지 ID, 제목)"""
    page_id = page_id_from_url(url_or_id)
    if page_id is None:
        raise RuntimeError("URL에서 페이지 ID를 못 찾았습니다 ('/pages/123456' 또는 '?pageId=123456' 형태)")
    notes: list[str] = []
    with _client(_normalize_base_url(_require("CONFLUENCE_URL")), notes) as client:
        resp = _get(client, f"/rest/api/content/{page_id}", notes)
        _raise_for_status(resp, page_id)
        return page_id, resp.json().get("title") or page_id


_REST_API_SUFFIX = re.compile(r"/rest/api/?$", re.IGNORECASE)


def _normalize_base_url(url: str) -> str:
    """끝의 슬래시와, 있다면 '/rest/api'까지 뗀다.

    안내받은 주소가 사람이 쓰는 위키 주소(`.../wiki`)일 수도, REST 게이트웨이
    주소(`.../rest/api/`)일 수도 있어서 둘 다 그대로 CONFLUENCE_URL에 넣어도
    되게 한다 — 코드가 항상 `/rest/api/content/...`를 새로 붙이기 때문이다.
    """
    return _REST_API_SUFFIX.sub("", url.rstrip("/"))


def _auth_header() -> dict[str, str]:
    """Bearer/Basic 어느 쪽이든 최종 Authorization 헤더 값 하나로 만든다.

    httpx 쪽(Client.headers)과 PowerShell 대체 경로 양쪽이 같은 값을 그대로
    쓸 수 있어야 해서, httpx의 auth= 객체 대신 처음부터 헤더로 계산해 둔다.
    """
    username = os.environ.get("CONFLUENCE_USERNAME")
    token = _require("CONFLUENCE_API_TOKEN")
    if username:
        basic = base64.b64encode(f"{username}:{token}".encode()).decode()
        return {"Authorization": f"Basic {basic}"}  # Cloud: 이메일 + API 토큰
    return {"Authorization": f"Bearer {token}"}  # Server/Data Center: PAT


def _client(base_url: str, notes: list[str]) -> httpx.Client:
    return httpx.Client(base_url=base_url, headers=_auth_header(), timeout=_TIMEOUT,
                        verify=_ssl_verify(notes))


_CA_BUNDLE_ENV_VARS = ("CONFLUENCE_CA_BUNDLE", "REQUESTS_CA_BUNDLE", "SSL_CERT_FILE")


def _ssl_verify(notes: list[str]) -> bool | str:
    """사내 루트 인증서 경로가 있으면 그걸 신뢰 기준으로 쓴다.

    httpx는 requests와 달리 REQUESTS_CA_BUNDLE을 자동으로 읽지 않으므로 명시적으로
    확인한다. CONFLUENCE_CA_BUNDLE이 doc2report 전용 이름, 나머지 둘은 다른 사내
    도구가 이미 쓰고 있을 만한 관례적 이름이라 같이 봐 준다.
    """
    for env_key in _CA_BUNDLE_ENV_VARS:
        path = os.environ.get(env_key)
        if not path:
            continue
        if Path(path).is_file():
            notes.append(f"사내 CA 인증서 사용: {env_key}={path}")
            return path
        notes.append(f"{env_key}={path} 이지만 파일을 찾을 수 없어 무시함(기본 인증서로 시도)")
    return True


# ── 응답 래퍼 (httpx.Response 또는 PowerShell 결과, 둘 다 같은 인터페이스) ──


@dataclass
class _Reply:
    status_code: int
    content: bytes

    def json(self):
        import json

        return json.loads(self.content.decode("utf-8"))

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _get_page(client: httpx.Client, page_id: str, notes: list[str]) -> dict:
    resp = _get(client, f"/rest/api/content/{page_id}", notes,
               params={"expand": "body.storage,space,version"})
    _raise_for_status(resp, page_id)
    return resp.json()


def _download_attachments(
    client: httpx.Client, page_id: str, out_dir: Path, notes: list[str]
) -> list[str]:
    """<ac:image>가 참조하는 첨부 이미지를 파일명 그대로 내려받는다.

    parsers/confluence_storage.py는 Image.src에 첨부파일명만 넣어 두므로,
    파이프라인의 이미지 경로 해석(base_dir 기준)이 그대로 통하게 이 폴더에 그 이름으로 저장한다.
    """
    resp = _get(client, f"/rest/api/content/{page_id}/child/attachment", notes,
               params={"limit": 200, "expand": "version"})
    if resp.status_code != 200:
        return [f"첨부파일 목록을 가져오지 못함 (HTTP {resp.status_code}) — 이미지가 빠질 수 있음"]

    data = resp.json()
    # 첨부 다운로드 경로는 REST API 호스트가 아니라 위키 자체를 기준으로 한 상대 경로일 수
    # 있다(REST 게이트웨이 서브도메인이 위키와 다른 회사에서 특히). 응답이 준 기준 주소가
    # 있으면 그걸 우선 쓴다.
    link_base = (data.get("_links") or {}).get("base", "")

    attach_notes: list[str] = []
    for item in data.get("results", []):
        filename = item.get("title")
        download = (item.get("_links") or {}).get("download")
        if not filename or not download:
            continue
        url = download if download.startswith(("http://", "https://")) else link_base + download
        try:
            file_resp = _get(client, url, notes)
            file_resp.raise_for_status()
        except Exception as exc:
            attach_notes.append(f"첨부파일 내려받기 실패({filename}): {exc}")
            continue
        (out_dir / filename).write_bytes(file_resp.content)
    return attach_notes


def _raise_for_status(resp, page_id: str) -> None:
    if resp.status_code == 401:
        raise RuntimeError("Confluence 인증 실패(401). CONFLUENCE_USERNAME/API_TOKEN을 확인하세요.")
    if resp.status_code == 403:
        raise RuntimeError(f"페이지 {page_id}에 접근 권한이 없습니다(403).")
    if resp.status_code == 404:
        raise RuntimeError(f"페이지 {page_id}를 찾을 수 없습니다(404). URL 또는 페이지 ID를 확인하세요.")
    if resp.status_code >= 400:
        raise RuntimeError(f"Confluence API 오류 (HTTP {resp.status_code}): {resp.text[:300]}")


def _require(key: str) -> str:
    value = os.environ.get(key)
    if not value:
        raise RuntimeError(
            "Confluence 인증 정보가 없습니다. 웹 화면 위의 '사용자 등록'에서 Confluence 주소와 개인 토큰을 "
            "넣으세요 (명령줄만 쓴다면 환경변수 CONFLUENCE_URL, CONFLUENCE_API_TOKEN)"
        )
    return value


# ── 전송 계층: httpx가 기본, 403이면(Windows에서만) PowerShell로 대체 ────────


def _get(client: httpx.Client, path: str, notes: list[str], *, params: dict | None = None):
    """httpx로 GET하되, SSL/연결 실패나 403을 받으면(또는 강제 설정이면) PowerShell로
    다시 시도한다. SSL 인증서 검증 실패는 응답 자체를 못 받고 예외로 터지므로 403과
    따로 잡아야 한다."""
    if _forced_powershell() or getattr(client, _FALLBACK_ATTR, False):
        # 한 번 httpx가 막힌 사내망이면 이번 변환의 나머지 요청(첨부 이미지 등)은 처음부터
        # PowerShell로 — 요청마다 실패를 기다리지 않고, 리포트에도 같은 안내가 반복되지 않는다.
        return _fetch_via_powershell(client, path, params, notes, forced=True)

    try:
        resp = client.get(path, params=params)
    except httpx.TransportError as exc:
        if _is_windows():
            return _fetch_via_powershell(client, path, params, notes, forced=False,
                                         httpx_error=exc)
        raise RuntimeError(
            f"Confluence 서버 연결 실패: {exc}\n"
            "사내망 SSL 인증서 문제일 수 있습니다 — CONFLUENCE_CA_BUNDLE 환경변수로 "
            "사내 루트 인증서(.crt/.pem) 경로를 지정해 보세요."
        ) from exc

    if resp.status_code == 403 and _is_windows():
        return _fetch_via_powershell(client, path, params, notes, forced=False, httpx_403=resp)
    return resp


def _forced_powershell() -> bool:
    return os.environ.get(_TRANSPORT_ENV, "").strip().lower() == "powershell"


def _is_windows() -> bool:
    return platform.system() == "Windows"


def _fetch_via_powershell(
    client: httpx.Client, path: str, params: dict | None, notes: list[str],
    *, forced: bool, httpx_403: httpx.Response | None = None,
    httpx_error: Exception | None = None,
):
    url = str(client.build_request("GET", path, params=params).url)
    try:
        content = _powershell_get(url, _auth_header())
    except Exception as exc:
        if httpx_403 is not None:
            notes.append(f"httpx 403 → PowerShell(Invoke-WebRequest) 대체 시도도 실패: {exc}")
            return httpx_403
        if httpx_error is not None:
            raise RuntimeError(
                f"Confluence 서버 연결 실패: {httpx_error}\n"
                f"PowerShell(Invoke-WebRequest) 대체 시도도 실패: {exc}\n"
                "사내망 SSL 인증서 문제일 수 있습니다 — CONFLUENCE_CA_BUNDLE 환경변수로 "
                "사내 루트 인증서(.crt/.pem) 경로를 지정해 보세요."
            ) from exc
        raise RuntimeError(f"PowerShell 요청 실패: {exc}") from exc

    if not forced:
        setattr(client, _FALLBACK_ATTR, True)  # 이후 요청은 httpx를 건너뛴다(안내는 이번 한 번만)
        reason = "SSL/연결 오류" if httpx_error is not None else "403"
        notes.append(
            f"httpx 요청이 {reason}로 실패해 PowerShell(Invoke-WebRequest)로 재시도해 성공함 "
            "— 이 사내망에서 필요한 우회 경로(2026-09-29 실측, 원인 미확정)"
        )
    return _Reply(status_code=200, content=content)


def _powershell_get(url: str, headers: dict[str, str]) -> bytes:
    """PowerShell Invoke-WebRequest로 GET해 원시 바이트를 돌려준다.

    Windows 인증서 저장소·TLS 스택을 그대로 쓰는 PowerShell이 httpx보다 사내망을
    잘 통과하는 경우가 있다(docs/onprem-first-run.md 2번에서 다른 호스트로도 확인된
    패턴). subprocess 출력은 텍스트 모드라 바이너리(이미지 등)가 깨질 수 있어
    Base64로 감싸 주고받는다.
    """
    # PowerShell 해시테이블 리터럴은 항목을 쉼표가 아니라 세미콜론으로 구분한다
    # (쉼표를 쓰면 값이 배열로 묶여 버린다).
    # 헤더 **값**(토큰)은 명령 문자열에 넣지 않고 자식 프로세스 환경변수로만 넘긴다 — 명령 문자열은
    # PowerShell 스크립트 블록 로깅·보안 솔루션·프로세스 목록에 남을 수 있다(2026-09-30, 팀 공유 전 점검).
    child_env = dict(os.environ)
    parts = []
    for index, (key, value) in enumerate(headers.items()):
        child_env[f"D2R_HEADER_{index}"] = value
        parts.append(f"'{_ps_escape(key)}'=$env:D2R_HEADER_{index}")
    header_expr = "; ".join(parts)
    script = (
        "$ProgressPreference = 'SilentlyContinue'; "
        f"$r = Invoke-WebRequest -Uri '{_ps_escape(url)}' -Headers @{{{header_expr}}} "
        "-UseBasicParsing; "
        "[Convert]::ToBase64String($r.RawContentStream.ToArray())"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=60, env=child_env,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError((result.stderr or result.stdout or "빈 응답").strip()[:300])
    return base64.b64decode(result.stdout.strip())


def _ps_escape(value: str) -> str:
    """PowerShell 작은따옴표 문자열 안에 안전하게 넣도록 작은따옴표만 이스케이프."""
    return value.replace("'", "''")
