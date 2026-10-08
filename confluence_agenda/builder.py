"""Confluence 안건 보고 페이지의 storage-format 소스(복붙용)를 생성하는 코어 로직.

생성되는 구조 (본문에 해당하는 안건 하나하나는 각자 별도의
ac:layout-section이라 Confluence에서 시각적으로 구분되고, 첨부 ui-expand
전체는 안건별로 나누지 않고 하나의 ac:layout-section에 다 같이 담긴다):

    [layout-section] "템플릿에서 페이지 만들기" 버튼 + 만들어야 할 하위 페이지
                      제목 목록("(첨부 1) 안건1", "(첨부 2) 안건2", ...).
                      이 버튼으로 include 매크로가 참조할 하위 페이지를 실제로
                      만든 뒤에는, 이 섹션 자체를 지우고 발행하면 된다.
    [layout-section] 1. 안건1 (첨부1)   <- "(첨부1)"은 "첨부1" 앵커(하단 ui-expand 앞)로 이동하는 링크
                        (이 줄 자체에 "제목1" 앵커가 심어져, 아래에서 되돌아올 수 있음)
                        (본문 자리표시자 3줄)
    [layout-section] 2. 안건2 (첨부2)
                        (본문 자리표시자 3줄)
    ...

    [layout-section]
        [anchor: 첨부1]
        ▽ ui-expand: "(첨부 1) 안건1"
            - include 매크로로 같은 제목의 하위 페이지("(첨부 1) 안건1")를 포함
            - "제목1" 앵커로 돌아가는 "(돌아가기)" 링크
        [anchor: 첨부2]
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
import re
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


def _include_page_macro(page_title: str, space_key: Optional[str] = None) -> str:
    """같은 제목의 하위 페이지를 현재 위치에 포함시키는 include 매크로.

    space_key를 안 주면 ri:page에 스페이스를 안 적는데, 그러면 Confluence가
    "이 매크로가 들어 있는 페이지"의 스페이스에서 제목을 찾는다 - 안건
    페이지와 상세 페이지가 같은 스페이스라면 문제없지만, 미러 페이지처럼
    매크로가 들어가는 페이지가 상세 페이지와 다른 스페이스에 있으면 못
    찾는다(사용자 확인: "미러링 페이지는 안건 페이지와는 다른 스페이스").
    그래서 상세 페이지가 실제로 있는 스페이스(= 처음 만든 안건 페이지의
    스페이스)를 명시적으로 적어 둔다."""
    space_attr = f'ri:space-key="{html.escape(space_key)}" ' if space_key else ""
    return (
        f'<ac:structured-macro ac:name="include" ac:schema-version="1" '
        f'ac:macro-id="{_new_macro_id()}">'
        f'<ac:parameter ac:name="">'
        f'<ac:link><ri:page {space_attr}ri:content-title="{html.escape(page_title)}"/></ac:link>'
        f"</ac:parameter>"
        f"</ac:structured-macro>"
    )


def attachment_page_title(index: int, title: str) -> str:
    """include 매크로가 참조하는 하위 페이지 제목: "(첨부 N) {안건 제목}" (공백 있음)."""
    return f"(첨부 {index}) {title}"


# API로 자동 생성되는 상세 페이지(docx_export.create_agenda_page)의 본문 -
# 실제 Confluence 템플릿을 API로 가져오는 방법은 엔드포인트가 불확실해서
# (표준 Content Template API인지, 그냥 일반 페이지인지조차 사내 환경마다
# 다를 수 있음) 위험했는데, 팀에서 쓰는 형식 자체가 단순해서 고정 문구로
# 대신한다(사용자 확인: "템플릿에 별다른 내용은 없어. 아래 내용 정도면
# 돼").
DEFAULT_DETAIL_PAGE_BODY_HTML = (
    "<h3>□ 가나다라마바사</h3>"
    "<p>&nbsp;&nbsp;- 가나다라마바사</p>"
    "<p>&nbsp;&nbsp;- 가나다라마바사</p>"
    "<p><br/></p>"
    "<p><strong>【 표/그림 또는 특정 안건 개요 제목 】</strong></p>"
    "<p>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;· 가나다라마바사</p>"
)


def create_from_template_button(template_id: str, button_label: str) -> str:
    """컨플루언스 '템플릿에서 페이지 만들기' 버튼 매크로.

    templateName과 templateId에 같은 값을 넣는다(팀 인스턴스에서 실제로
    쓰는 방식 - 다른 값이 필요하면 build_agenda_page_body 호출 시
    template_id를 바꿔서 넘기면 된다).
    """
    return (
        f'<ac:structured-macro ac:name="create-from-template" ac:schema-version="1" '
        f'ac:macro-id="{_new_macro_id()}">'
        f'<ac:parameter ac:name="templateName">{html.escape(template_id)}</ac:parameter>'
        f'<ac:parameter ac:name="templateId">{html.escape(template_id)}</ac:parameter>'
        f'<ac:parameter ac:name="buttonLabel">{html.escape(button_label)}</ac:parameter>'
        f"</ac:structured-macro>"
    )


def attachment_setup_section_html(
    items: Sequence[AgendaItem],
    template_id: str,
    button_label: str,
) -> str:
    """맨 위 섹션: 하위 페이지 생성 버튼 하나 + 만들어야 할 페이지 제목 목록.

    버튼은 클릭할 때마다 템플릿으로 새 페이지를 하나씩 만드는 용도라 하나만
    두고, 그 옆에 include 매크로들이 참조하는 제목을 그대로 나열해서 어떤
    제목으로 몇 개를 만들어야 하는지 보여준다. 하위 페이지를 다 만들고 나면
    이 섹션 자체를 지우면 된다(보고서 본문에는 필요 없는 준비용 섹션).
    """
    button_html = create_from_template_button(template_id, button_label)
    titles_html = "\n".join(
        f"<li>{html.escape(attachment_page_title(idx, item.title))}</li>"
        for idx, item in enumerate(items, start=1)
    )
    content = (
        f"<p>{button_html}</p>"
        "<p>아래 제목으로 하위 페이지를 하나씩 만드세요(버튼을 누르면 나오는 제목 입력창에 그대로 입력):</p>"
        f"<ul>\n{titles_html}\n</ul>"
    )
    return layout_section(content)


def attachment_section_html(index: int, title: str) -> str:
    """'첨부N' 앵커 + ui-expand 매크로(하위 페이지 include + '(돌아가기)' 링크).

    레이아웃 섹션으로 감싸지 않은 조각 하나만 돌려준다 - 모든 안건의 첨부
    블록을 build_agenda_page_body에서 한 섹션에 몰아 담기 위해서다.
    """
    attachment_anchor = f"첨부{index}"
    title_anchor = f"제목{index}"
    expand_title = attachment_page_title(index, title)

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

    return anchor_macro(attachment_anchor) + "\n" + ui_expand_html


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


_BLANK_LINE_RE = re.compile(r"\n[ \t]*\n+")


def parse_agenda_input(raw: str) -> List[AgendaItem]:
    """빈 줄로 안건을 구분하는 자유 입력 텍스트를 AgendaItem 목록으로 바꾼다.

    각 안건 블록의 첫 줄이 제목, 그 아래 줄들(있으면)이 본문이 된다. 본문을
    안 쓰면 기존처럼 자리표시자 3줄이 채워지고(AgendaItem.body 기본 동작),
    이미 본문을 다 써둔 안건을 그대로 다시 붙여넣으면 그 본문이 그대로
    반영된다 - 안건 제목만 한 줄씩 쓰는 기존 방식도 그대로 지원한다(그 경우
    각 줄 사이에 빈 줄이 있어야 서로 다른 안건으로 구분된다).

    예)
        예산안 승인
        부서별 예산안을 검토하고 승인합니다.

        채용 계획
    """
    # 브라우저 <textarea>는 폼 전송 시 줄바꿈을 "\r\n"으로 보낸다(HTML
    # 스펙) - 그대로 두면 빈 줄이 "\r\n\r\n"이 되어 아래 정규식의 "\n"
    # 전용 패턴과 안 맞아 빈 줄을 전혀 못 찾고 안건이 전부 하나로 합쳐진다.
    # "\r\n"과 옛 맥 줄바꿈 "\r"을 먼저 "\n"으로 통일한다.
    normalized = raw.replace("\r\n", "\n").replace("\r", "\n")
    items: List[AgendaItem] = []
    for block in _BLANK_LINE_RE.split(normalized.strip()):
        lines = block.splitlines()
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        if not lines:
            continue
        title = lines[0].strip()
        if not title:
            continue
        items.append(AgendaItem(title=title, body=lines[1:]))
    return items


DEFAULT_ATTACHMENT_TEMPLATE_ID = "3877634148"
DEFAULT_ATTACHMENT_BUTTON_LABEL = "첨부 페이지 만들기"


def build_agenda_page_body(
    items: Sequence[AgendaItem],
    intro: Optional[TextInput] = None,
    template_id: str = DEFAULT_ATTACHMENT_TEMPLATE_ID,
    button_label: str = DEFAULT_ATTACHMENT_BUTTON_LABEL,
    include_setup_section: bool = True,
) -> str:
    """안건 목록을 받아 컨플루언스 에디터에 그대로 붙여넣을 storage-format 소스를 만든다.

    맨 위 섹션에는 include 매크로들이 참조할 하위 페이지를 실제로 만들 때
    쓰는 "템플릿에서 페이지 만들기" 버튼 + 만들어야 할 제목 목록을 넣는다.
    하위 페이지를 다 만든 뒤에는 이 섹션만 지우고 발행하면 된다.

    include_setup_section=False면 이 버튼 섹션을 아예 안 넣는다 - API로
    상세 페이지까지 자동으로 만드는 경로(docx_export.create_agenda_page)는
    버튼을 누를 필요가 없으므로 이 섹션이 필요 없다.
    """
    if not items:
        raise ValueError("최소 1개 이상의 안건이 필요합니다.")

    sections: List[str] = (
        [attachment_setup_section_html(items, template_id, button_label)]
        if include_setup_section
        else []
    )

    if intro:
        sections.append(layout_section(_paragraphs_html(intro)))

    # 1) 안건 목록: 안건 하나(제목 + 본문)가 레이아웃 섹션 하나.
    # 제목과 본문 사이, 안건 끝에 각각 빈 줄을 한 줄씩 넣어 여백을 준다.
    blank_line = "<p><br/></p>"
    for idx, item in enumerate(items, start=1):
        content = "\n".join(
            [heading_html(idx, item.title), blank_line, _resolve_body_html(item), blank_line]
        )
        sections.append(layout_section(content))

    # 2) 첨부 ui-expand 목록: 안건별로 나누지 않고 전부 하나의 레이아웃 섹션에 담는다.
    attachments = "\n".join(
        attachment_section_html(idx, item.title) for idx, item in enumerate(items, start=1)
    )
    sections.append(layout_section(attachments))

    return "<ac:layout>\n" + "\n".join(sections) + "\n</ac:layout>"


def mirror_page_body_html(page_title: str, space_key: Optional[str] = None) -> str:
    """미러 페이지 본문 - 안건 페이지 전체를 복사하지 않고, include 매크로로
    원본 안건 페이지를 그대로 가리키기만 한다(사용자 요청: "미러링 페이지는
    페이지 포함 매크로로 처음 만든 안건 페이지를 가리키기만 하면 돼").
    미러 페이지가 안건 페이지와 다른 스페이스에 있을 수 있으므로, space_key로
    안건 페이지가 실제로 있는 스페이스를 명시해야 한다."""
    return f"<p>{_include_page_macro(page_title, space_key)}</p>"


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
