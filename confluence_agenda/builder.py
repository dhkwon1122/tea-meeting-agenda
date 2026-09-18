"""Confluence 안건 보고 페이지의 storage-format 본문을 생성하는 코어 로직.

생성되는 구조:

    1. 안건1 (첨부 1)   <- "(첨부 1)"은 하단 Expand 앵커로 이동하는 링크
       본문...
    2. 안건2 (첨부 2)
       본문...
    ...

    ▽ Expand: (첨부 1) 안건1
        « 1. 안건1로 돌아가기   <- 위 제목1 앵커로 되돌아가는 링크
        (첨부 내용)
    ▽ Expand: (첨부 2) 안건2
        « 2. 안건2로 돌아가기
        (첨부 내용)
    ...
"""
from __future__ import annotations

import html
import uuid
from dataclasses import dataclass
from typing import List, Optional, Sequence, Union

# 문자열 하나 또는 줄 단위 문자열 목록을 본문으로 받는다.
TextInput = Union[str, Sequence[str]]


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


def _resolve_body_html(text: TextInput, raw: bool) -> str:
    if raw:
        return text if isinstance(text, str) else "\n".join(text)
    return _paragraphs_html(text)


def _new_local_id() -> str:
    return str(uuid.uuid4())


def anchor_macro(name: str) -> str:
    """지정한 이름의 북마크(anchor)를 생성하는 매크로."""
    return (
        f'<ac:structured-macro ac:name="anchor" ac:schema-version="1" '
        f'ac:local-id="{_new_local_id()}">'
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


def attachment_image_macro(filename: str, width: Optional[int] = None) -> str:
    """페이지에 업로드된 첨부 이미지를 삽입하는 매크로."""
    width_attr = f' ac:width="{width}"' if width else ""
    return (
        f"<ac:image{width_attr}>"
        f'<ri:attachment ri:filename="{html.escape(filename)}"/>'
        f"</ac:image>"
    )


def attachment_link_macro(filename: str, link_text: Optional[str] = None) -> str:
    """페이지에 업로드된 첨부 파일로 연결되는 다운로드 링크."""
    body = (
        f"<ac:plain-text-link-body><![CDATA[{_cdata_escape(link_text)}]]></ac:plain-text-link-body>"
        if link_text
        else ""
    )
    return f'<ac:link><ri:attachment ri:filename="{html.escape(filename)}"/>{body}</ac:link>'


def expand_macro(title: str, body_html: str) -> str:
    """접고 펼 수 있는 Expand UI 매크로."""
    return (
        f'<ac:structured-macro ac:name="expand" ac:schema-version="1" '
        f'ac:local-id="{_new_local_id()}">'
        f'<ac:parameter ac:name="title">{html.escape(title)}</ac:parameter>'
        f"<ac:rich-text-body>{body_html}</ac:rich-text-body>"
        f"</ac:structured-macro>"
    )


@dataclass
class AgendaItem:
    """안건 하나를 표현한다.

    body / attachment_body 는 문자열(줄바꿈으로 문단 구분) 또는 문자열 목록을 받으며,
    기본적으로 HTML 이스케이프되어 <p> 문단으로 변환된다.
    raw_body / raw_attachment_body 를 True로 주면 이미 만들어둔 storage-format
    XHTML(예: attachment_image_macro 결과)을 그대로 삽입할 수 있다.
    """

    title: str
    body: TextInput = ""
    attachment_body: TextInput = ""
    attachment_label: Optional[str] = None
    raw_body: bool = False
    raw_attachment_body: bool = False
    back_link_text: Optional[str] = None


def build_agenda_page_body(
    items: Sequence[AgendaItem],
    intro: Optional[TextInput] = None,
) -> str:
    """안건 목록을 받아 Confluence storage-format 페이지 본문 문자열을 만든다.

    반환값은 Confluence REST API의 ``body.storage.value`` 에 그대로 사용할 수 있다.
    """
    if not items:
        raise ValueError("최소 1개 이상의 안건이 필요합니다.")

    parts: List[str] = []

    if intro:
        parts.append(_paragraphs_html(intro))

    # 1) 안건 목록 (각 제목 옆에 해당 첨부 Expand로 이동하는 링크)
    for idx, item in enumerate(items, start=1):
        agenda_anchor = f"agenda-{idx}"
        attachment_anchor = f"attachment-{idx}"
        attachment_label = item.attachment_label or f"(첨부 {idx})"

        parts.append(anchor_macro(agenda_anchor))
        heading_link = anchor_link(attachment_anchor, attachment_label)
        parts.append(f"<h2>{idx}. {html.escape(item.title)} {heading_link}</h2>")
        parts.append(_resolve_body_html(item.body, item.raw_body))

    # 2) 첨부 Expand 목록 (각 Expand 안에 제목으로 되돌아가는 링크 포함)
    for idx, item in enumerate(items, start=1):
        agenda_anchor = f"agenda-{idx}"
        attachment_anchor = f"attachment-{idx}"
        expand_title = f"(첨부 {idx}) {item.title}"

        back_text = item.back_link_text or f"« {idx}. {item.title}로 돌아가기"
        back_link_html = f"<p>{anchor_link(agenda_anchor, back_text)}</p>"
        attachment_html = _resolve_body_html(item.attachment_body, item.raw_attachment_body)

        parts.append(anchor_macro(attachment_anchor))
        parts.append(expand_macro(expand_title, back_link_html + attachment_html))

    return "\n".join(p for p in parts if p)
