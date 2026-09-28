"""Confluence 안건 보고 페이지의 storage-format 소스(복붙용)를 생성하는 코어 로직.

생성되는 구조 (안건 하나하나, 첨부 하나하나가 각각 ac:layout-section으로
나뉜 별도 섹션이라 Confluence에서 시각적으로 구분된다):

    [layout-section] 1. 안건1 (첨부1)   <- "(첨부1)"은 "첨부1" 앵커(하단 ui-expand 앞)로 이동하는 링크
                        (이 줄 자체에 "제목1" 앵커가 심어져, 아래에서 되돌아올 수 있음)
                        (본문 자리표시자 3줄)
    [layout-section] 2. 안건2 (첨부2)
                        (본문 자리표시자 3줄)
    ...

    [layout-section] [anchor: 첨부1]
                      ▽ ui-expand: "(첨부 1) 안건1"
                          - include 매크로로 같은 제목의 하위 페이지("(첨부 1) 안건1")를 포함
                          - "제목1" 앵커로 돌아가는 "(돌아가기)" 링크
    [layout-section] [anchor: 첨부2]
                      ▽ ui-expand: "(첨부 2) 안건2"
                        ...

앵커 이름 규칙(팀 컨플루언스 매크로 소스 기준):
    - 안건 제목 줄에 심는 앵커: "제목{N}" (공백 없음)
    - ui-expand 앞에 심는 앵커: "첨부{N}" (공백 없음)
    - 제목의 "(첨부N)" 링크는 "첨부{N}" 앵커로 이동
    - ui-expand 안의 "(돌아가기)" 링크는 "제목{N}" 앵커로 이동
    - ui-expand의 제목 / 포함할 하위 페이지 제목은 "(첨부 N) {안건 제목}" (공백 있음)
"""
from __future__ import annotations

import html
import uuid
from dataclasses import dataclass
from datetime import date
from typing import List, Optional, Sequence, Union

# 문자열 하나 또는 줄 단위 문자열 목록을 본문으로 받는다.
TextInput = Union[str, Sequence[str]]

# 본문을 비워두면 채워 넣는 자리표시자: 3칸 들여쓰기(&nbsp;) + 안내 문구, 3줄.
_PLACEHOLDER_LINE_TEXT = "가나다라마바사 내용을 입력해주세요"
_PLACEHOLDER_INDENT = "&nbsp;" * 3
_PLACEHOLDER_LINE_COUNT = 3


def _cdata_escape(text: str) -> str:
    """CDATA 안에 ']]>' 가 그대로 들어가면 태그가 깨지므로 분리해준다."""
    return text.replace("]]>", "]]]]><![CDATA[>")


def _paragraphs_html(text: TextInput) -> str:
    """일반 텍스트(줄바꿈 구분)를 <p> 문단들로 변환하며 HTML 이스케이프한다."""
    lines = text.splitlines() if isinstance(text, str) else list(text)
    if not lines:
        return ""
    return "\n".join(
        f"<p>{html.escape(line)}</p>" if line.strip() else "<p><br/></p>"
        for line in lines
    )


def _default_body_html() -> str:
    """본문을 채우지 않았을 때 들어가는 자리표시자 3줄."""
    line = f"{_PLACEHOLDER_INDENT}{_PLACEHOLDER_LINE_TEXT}"
    return "\n".join(f"<p>{line}</p>" for _ in range(_PLACEHOLDER_LINE_COUNT))


def _resolve_body_html(item: "AgendaItem") -> str:
    if not item.body:
        return _default_body_html()
    if item.raw_body:
        return item.body if isinstance(item.body, str) else "\n".join(item.body)
    return _paragraphs_html(item.body)


def _new_macro_id() -> str:
    return str(uuid.uuid4())


def layout_section(content: str) -> str:
    """content를 단일 컬럼 레이아웃 섹션(ac:layout-section/ac:layout-cell)으로 감싼다.

    안건별로 시각적으로 구분되는 블록이 되도록, 안건 하나하나(제목+본문)와
    첨부 ui-expand 하나하나를 각각 이 섹션으로 감싼다.
    """
    return f'<ac:layout-section ac:type="single"><ac:layout-cell>\n{content}\n</ac:layout-cell></ac:layout-section>'


def anchor_macro(name: str) -> str:
    """지정한 이름의 북마크(anchor)를 생성하는 매크로."""
    return (
        f'<ac:structured-macro ac:name="anchor" ac:schema-version="1" '
        f'ac:macro-id="{_new_macro_id()}">'
        f'<ac:parameter ac:name="">{html.escape(name)}</ac:parameter>'
        f"</ac:structured-macro>"
    )


def anchor_link(anchor_name: str, link_text: str) -> str:
    """같은 페이지 내 anchor_macro(anchor_name) 위치로 이동하는 링크."""
    return (
        f'<ac:link ac:anchor="{html.escape(anchor_name)}">'
        f"<ac:plain-text-link-body><![CDATA[{_cdata_escape(link_text)}]]></ac:plain-text-link-body>"
        f"</ac:link>"
    )


def heading_html(index: int, title: str) -> str:
    """안건 제목 줄. 팀에서 쓰는 h3/strong/span 스타일 + '(첨부N)' 링크 + '제목N' 앵커."""
    attachment_anchor = f"첨부{index}"
    title_anchor = f"제목{index}"
    return (
        '<h3 style="text-align: left;"><strong style="letter-spacing: -0.006em;">'
        '<span style="color:var(--ds-background-accent-blue-bolder,#0c66e4);">'
        f"{index}. {html.escape(title)} "
        f'<ac:link ac:anchor="{attachment_anchor}">'
        f"<ac:plain-text-link-body><![CDATA[({attachment_anchor})]]></ac:plain-text-link-body>"
        f"</ac:link> "
        f'<ac:structured-macro ac:name="anchor" ac:schema-version="1" ac:macro-id="{_new_macro_id()}">'
        f'<ac:parameter ac:name="">{title_anchor}</ac:parameter>'
        f"</ac:structured-macro>"
        "</span></strong></h3>"
    )


def _include_page_macro(page_title: str) -> str:
    """같은 제목의 하위 페이지를 현재 위치에 포함시키는 include 매크로."""
    return (
        f'<ac:structured-macro ac:name="include" ac:schema-version="1" '
        f'ac:macro-id="{_new_macro_id()}">'
        f'<ac:parameter ac:name="">'
        f'<ac:link><ri:page ri:content-title="{html.escape(page_title)}"/></ac:link>'
        f"</ac:parameter>"
        f"</ac:structured-macro>"
    )


def attachment_section_html(index: int, title: str) -> str:
    """'첨부N' 앵커 + ui-expand 매크로(하위 페이지 include + '(돌아가기)' 링크)."""
    attachment_anchor = f"첨부{index}"
    title_anchor = f"제목{index}"
    expand_title = f"(첨부 {index}) {title}"

    include_html = _include_page_macro(expand_title)
    back_link_html = anchor_link(title_anchor, "(돌아가기)")

    ui_expand_html = (
        f'<ac:structured-macro ac:name="ui-expand" ac:schema-version="1" '
        f'ac:macro-id="{_new_macro_id()}">'
        f'<ac:parameter ac:name="title">{html.escape(expand_title)}</ac:parameter>'
        f"<ac:rich-text-body>"
        f"<p>{include_html}</p>"
        f"<p>{back_link_html}</p>"
        f"</ac:rich-text-body>"
        f"</ac:structured-macro>"
    )

    return layout_section(anchor_macro(attachment_anchor) + "\n" + ui_expand_html)


@dataclass
class AgendaItem:
    """안건 하나를 표현한다.

    body 를 비워두면 3줄짜리 안내 자리표시자가 자동으로 채워진다(추후 Confluence에서
    직접 채워 넣는 용도). body 를 직접 넘기면 줄바꿈 기준으로 <p> 문단이 되고
    (raw_body=True 면 이미 만들어둔 storage-format XHTML을 그대로 삽입).
    """

    title: str
    body: TextInput = ""
    raw_body: bool = False


def build_agenda_page_body(
    items: Sequence[AgendaItem],
    intro: Optional[TextInput] = None,
) -> str:
    """안건 목록을 받아 컨플루언스 에디터에 그대로 붙여넣을 storage-format 소스를 만든다."""
    if not items:
        raise ValueError("최소 1개 이상의 안건이 필요합니다.")

    sections: List[str] = []

    if intro:
        sections.append(layout_section(_paragraphs_html(intro)))

    # 1) 안건 목록: 안건 하나(제목 + 본문)가 레이아웃 섹션 하나
    for idx, item in enumerate(items, start=1):
        content = heading_html(idx, item.title) + "\n" + _resolve_body_html(item)
        sections.append(layout_section(content))

    # 2) 첨부 ui-expand 목록: 첨부 하나("첨부N" 앵커 + ui-expand)가 레이아웃 섹션 하나
    for idx, item in enumerate(items, start=1):
        sections.append(attachment_section_html(idx, item.title))

    return "<ac:layout>\n" + "\n".join(sections) + "\n</ac:layout>"


_KOREAN_WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]


def build_email_subject(on: Optional[date] = None) -> str:
    """메일 제목. 안건 내용과 무관하게 항상 고정 문구 + 날짜로 만든다.

    예: 9월 21일(월)이면 "[보고] 9.21(월) 스탭팀장 미팅 피플팀 안건".
    안건 제목이 아니라 오늘 날짜(또는 on으로 넘긴 날짜) 기준이라, 생성된
    Confluence 소스 내용을 고쳐도 제목에는 영향이 없다.
    """
    d = on or date.today()
    weekday = _KOREAN_WEEKDAYS[d.weekday()]
    return f"[보고] {d.month}.{d.day}({weekday}) 스탭팀장 미팅 피플팀 안건"


def build_agenda_email_html(source: str) -> str:
    """Confluence storage-format의 ac:* 매크로는 일반 메일 클라이언트가 렌더링하지
    못하므로, source(build_agenda_page_body의 결과 전체)를 그대로 복사해 쓸 수
    있도록 <pre> 블록에 이스케이프해서 담는다.
    """
    escaped = html.escape(source)
    return (
        "<p>아래 내용을 전체 선택해서 복사한 뒤, Confluence 편집기 "
        "<strong>⋯(더보기) 메뉴 → 마크업 삽입</strong>에 붙여넣으세요.</p>"
        '<pre style="white-space:pre-wrap; word-break:break-all; background:#f1f3f4; '
        "color:#202124; padding:16px; border-radius:8px; "
        "font-family:'SFMono-Regular',Consolas,monospace; font-size:13px; line-height:1.6;\">"
        f"{escaped}</pre>"
    )
