"""Confluence storage format(XHTML) → IR.

Markdown 경유(마크다운 내보내기 → 마크다운 파서)와 달리 Confluence REST API가 주는
`body.storage`를 곧장 읽는다. 가장 큰 차이는 표다 — GFM 마크다운 표에는 병합 셀
(colspan/rowspan) 문법이 없어 마크다운을 거치면 정보가 사라지지만, storage format은
HTML 표 그대로라 병합 정보를 IR의 Cell.colspan/rowspan에 그대로 옮길 수 있다.
Confluence는 표 폭에 제약이 없어(자유롭게 열을 늘리고 줄을 병합) 이 경로가 특히 중요하다.

매크로(`ac:structured-macro`)는 이름으로 분기한다: info/note/warning류는 Callout,
code는 CodeBlock, 나머지는 알려진 하위 구조(rich-text-body)가 있으면 그 안만 펼치고
없으면 건너뛴다 (노트에 남긴다).

**다른 페이지·첨부를 끌어오는 매크로**(페이지 포함 include, 발췌 포함 excerpt-include, 하위 페이지
children, 페이지 트리 pagetree, 첨부 보기 view-file 등)는 이 파서가 직접 불러올 수 없다(네트워크는
sources/의 일). 그래서 그 자리에 `PageRef` 자리표시를 남기고(keep_refs=True), sources/confluence.py의
`LinkedPages`가 실제 페이지를 가져와 채운다(2026-09-29 사용자: "+로 펼쳐 보게 연결해 둔 페이지를 한 번에").
PageRef는 IR 블록이 아니다 — 채우지 않을 거면(keep_refs=False) 여기서 바로 노트로 바꿔 없앤다.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field

from lxml import etree

from ..ir import (
    Block,
    Callout,
    Cell,
    CodeBlock,
    Document,
    Heading,
    HorizontalRule,
    Image,
    ListItem,
    Paragraph,
    Row,
    Run,
    Table,
    is_blank,
    plain,
)

AC_NS = "http://www.atlassian.com/schema/confluence/4/ac/"
RI_NS = "http://www.atlassian.com/schema/confluence/4/ri/"

_KIND_BY_MACRO = {
    "info": "info", "tip": "info", "note": "note",
    "warning": "warning", "caution": "warning", "important": "note",
}

_INLINE_TAGS = {"strong", "b", "em", "i", "u", "code", "a", "span", "br", "sup", "sub"}


@dataclass
class PageRef:
    """다른 페이지(또는 첨부 파일)를 끌어오는 매크로의 자리. IR이 아니라 파서↔소스 사이 임시 표식."""

    kind: str                 # include | excerpt | children | attachment | link
    title: str | None = None  # 대상 페이지 제목 (children: 없으면 이 페이지의 하위 페이지)
    space: str | None = None  # 대상 스페이스 키 (없으면 이 페이지와 같은 스페이스)
    filename: str | None = None  # attachment: 첨부 파일 이름
    depth: int | None = None  # children/pagetree: 몇 단계 아래까지
    anchor: str | None = None  # link: 대상 페이지 안의 책갈피(앵커) — 있으면 그 구간만
    label: str | None = None  # 이 매크로를 감싼 펼치기(+)의 제목 — 불러온 문서가 새 쪽·제목을 가지면
                              # 겹치므로 빼고, 조각이거나 못 불러오면 굵은 줄로 되살린다

    def describe(self) -> str:
        if self.kind == "link":
            where = f"의 책갈피 '{self.anchor}'" if self.anchor else ""
            return f"링크 페이지 '{self.title}'{where}"
        if self.kind == "attachment":
            return f"첨부 파일 '{self.filename}'"
        if self.kind == "children":
            return f"'{self.title}'의 하위 페이지" if self.title else "하위 페이지 목록"
        return f"{'발췌 포함' if self.kind == 'excerpt' else '포함 페이지'} '{self.title}'"


_VIEW_FILE_MACROS = {"view-file", "viewdoc", "viewpdf", "viewxls", "viewppt", "viewfile"}


@dataclass
class ParsedConfluence:
    document: Document
    notes: list[str] = field(default_factory=list)  # 건너뛴 매크로·요소 (--report에 남긴다)


def parse_confluence_storage(
    xhtml: str, *, source: str | None = None, title: str | None = None,
    keep_refs: bool = False, excerpt_only: bool = False, anchor: str | None = None,
) -> ParsedConfluence:
    """title을 주면 그대로 문서 제목으로 쓴다 (Confluence 페이지 제목은 본문과 별도
    메타데이터라 storage XHTML 안에 없는 게 보통이다). 안 주면 본문 첫 H1을 제목으로
    승격한다 — Markdown 문서와 동일한 규칙."""
    root = _fragment_root(xhtml)
    anchor_notes: list[str] = []
    if anchor:  # 링크가 책갈피를 가리키면 그 책갈피부터 다음 같은 급 제목 전까지만
        section = _anchor_section(root, anchor)
        if section is None:
            anchor_notes.append(f"'{title}'에서 책갈피 '{anchor}'를 찾지 못해 페이지 전체를 넣음")
        else:
            root = section
    excerpts = [m for m in root.iter(f"{{{AC_NS}}}structured-macro")
                if _macro_name(m) == "excerpt"] if excerpt_only else []
    if excerpts:  # 발췌 포함: 대상 페이지의 excerpt 매크로 안쪽만
        blocks, notes = [], []
        for macro in excerpts:
            body = _macro_child(macro, "rich-text-body")
            if body is not None:
                sub, sub_notes = _children_blocks(body)
                blocks.extend(sub)
                notes.extend(sub_notes)
    else:
        blocks, notes = _children_blocks(root)
    notes = anchor_notes + notes
    if not keep_refs:
        blocks = drop_page_refs(blocks, notes)
    if title is None and blocks and isinstance(blocks[0], Heading) and blocks[0].level == 1:
        title = plain(blocks[0].runs)
        blocks = blocks[1:]
    doc = Document(blocks=blocks, title=title, source=source)
    return ParsedConfluence(document=doc, notes=notes)


def _fragment_root(xhtml: str):
    wrapped = (
        f'<root xmlns:ac="{AC_NS}" xmlns:ri="{RI_NS}">{xhtml}</root>'
    )
    parser = etree.XMLParser(recover=True, resolve_entities=False)
    root = etree.fromstring(wrapped.encode("utf-8"), parser=parser)
    if root is None:
        raise ValueError("Confluence storage XHTML을 해석하지 못했습니다.")
    return root


def _local(el) -> str:
    return etree.QName(el).localname


def _is_ac(el, name: str) -> bool:
    return el.tag == f"{{{AC_NS}}}{name}"


_LINK_MACROS = {"include", "excerpt-include", "children", "pagetree"} | _VIEW_FILE_MACROS


def _is_block_macro(el) -> bool:
    """문단 안에 있어도 따로 떼어 블록으로 다뤄야 하는 매크로 — 다른 페이지를 끌어오는 것,
    패널·펼치기·코드처럼 본문이 있는 것. 앵커(책갈피)·상태 표시처럼 글자 사이에 끼는 것은 아니다."""
    if not _is_ac(el, "structured-macro"):
        return False
    name = _macro_name(el)
    return (name in _LINK_MACROS or name in _KIND_BY_MACRO or name in ("code", "expand")
            or _macro_child(el, "rich-text-body") is not None)


def _inline_macro_label(el) -> str:
    """글자 사이에 낀 매크로가 화면에 보이는 글자. 상태(status)는 제목, 앵커(책갈피)는 없음.
    매개변수(색·앵커 이름 등)는 화면에 안 보이므로 본문으로 새어 나오면 안 된다
    (실제로 "sec1제목", "Green완료"처럼 새어 나왔다)."""
    if _macro_name(el) == "status":
        title = _param(el, "title")
        return (title.text or "").strip() if title is not None else ""
    return ""


def _bare_link_label(el) -> str | None:
    """<ac:link>에 보이는 글자(link-body)가 없으면 Confluence는 대상 이름을 보여 준다."""
    if any(_local(c).endswith("link-body") for c in el):
        return None
    title, _ = _page_link(el)
    if title:
        return title
    attachment = next(el.iter(f"{{{RI_NS}}}attachment"), None)
    if attachment is not None:
        return attachment.get(f"{{{RI_NS}}}filename") or attachment.get("ri:filename")
    return el.get(f"{{{AC_NS}}}anchor") or el.get("ac:anchor")


# ── 블록 ────────────────────────────────────────────────────────────────


def _children_blocks(el, *, links: bool = True) -> tuple[list[Block], list[str]]:
    """links: 블록 안 다른 페이지 링크를 그 블록 바로 뒤에 PageRef("link")로 남긴다(불러올지는
    sources/가 정한다). 표 칸 안에서는 끈다 — 칸의 링크는 표 전체 뒤에 한꺼번에 남는다."""
    blocks: list[Block] = []
    notes: list[str] = []
    for child in el:
        sub, sub_notes = _block(child)
        blocks.extend(sub)
        notes.extend(sub_notes)
        if links and isinstance(child.tag, str) and not _is_ac(child, "structured-macro"):
            blocks.extend(_link_refs(child))
    return blocks, notes


def _block(el) -> tuple[list[Block], list[str]]:
    if _is_ac(el, "structured-macro"):
        return _macro(el)

    tag = _local(el)

    if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
        runs, images = _inline(el)
        return [Heading(level=int(tag[1]), runs=runs), *images], []

    if tag == "p":
        if any(_is_block_macro(c) for c in el):
            return _split_paragraph(el)
        lines, images = _inline_lines(el)
        return _paragraphs_from_lines(lines, images, align=_align_of(el)), []

    if tag in {"ul", "ol"}:
        return _list(el, depth=0, ordered=(tag == "ol")), []

    if tag == "table":
        return [_table(el)], []

    if tag == "blockquote":
        inner, notes = _children_blocks(el)
        return [Callout(kind="quote", blocks=inner)], notes

    if tag == "hr":
        return [HorizontalRule()], []

    if _is_ac(el, "image"):
        image = _image(el)
        return ([image] if image else []), []

    if tag in ("div", "root", "layout", "layout-section", "layout-cell"):
        return _children_blocks(el)

    # 알 수 없는 블록 요소는 건너뛰되, 무엇을 놓쳤는지는 남긴다.
    if el.text and el.text.strip():
        return [Paragraph(runs=[Run(el.text.strip())])], []
    return [], [f"알 수 없는 요소를 건너뜀: <{tag}>"]


def drop_page_refs(blocks: list, notes: list[str], *, reason: str = "연결 페이지 불러오기를 끔",
                   kinds: set[str] | None = None) -> list:
    """채우지 않은 PageRef를 없애고 노트로 남긴다(표 셀·패널 안쪽까지). kinds를 주면 그 종류만.
    본문 링크(link)는 조용히 뺀다 — 링크 글자는 본문에 그대로 있고, 링크마다 노트를 남기면 리포트가 넘친다."""
    out = []
    for block in blocks:
        if isinstance(block, PageRef) and (kinds is None or block.kind in kinds):
            if block.label:
                out.append(Paragraph(runs=[Run(block.label, bold=True)]))
            if block.kind != "link":
                notes.append(f"{block.describe()}을(를) 불러오지 않음 — {reason}")
            continue
        if isinstance(block, Callout):
            block.blocks = drop_page_refs(block.blocks, notes, reason=reason, kinds=kinds)
        elif isinstance(block, Table):
            for row in block.rows:
                for cell in row.cells:
                    cell.blocks = drop_page_refs(cell.blocks, notes, reason=reason, kinds=kinds)
        out.append(block)
    return out


def _link_refs(el) -> list[PageRef]:
    """블록 안의 <ac:link><ri:page …/></ac:link> — 다른 페이지(또는 그 페이지의 책갈피)로 가는 링크.
    매크로 안은 보지 않는다: 페이지 포함의 매개변수도 같은 모양이고, 본문이 있는 매크로(펼치기 등)는
    제 본문을 읽을 때 따로 센다. 같은 페이지 안 책갈피 링크(ri:page 없음)는 내용이 이미 본문에 있다."""
    refs: list[PageRef] = []
    seen: set = set()

    def walk(node) -> None:
        for child in node:
            if _is_ac(child, "structured-macro"):
                continue
            if _is_ac(child, "link"):
                title, space = _page_link(child)
                anchor = child.get(f"{{{AC_NS}}}anchor") or child.get("ac:anchor") or None
                if title and (title, space, anchor) not in seen:
                    seen.add((title, space, anchor))
                    refs.append(PageRef("link", title, space, anchor=anchor))
                continue
            walk(child)

    walk(el)
    return refs


def _anchor_section(root, anchor: str):
    """책갈피(앵커 매크로, 또는 그 이름과 같은 제목)가 있는 블록부터, 다음에 같은 급 이상 제목이
    나오기 전까지를 새 root로. 책갈피가 제목 안에 있으면 그 제목 급 기준, 아니면 다음 제목 전까지.
    못 찾으면 None."""
    target = None
    for macro in root.iter(f"{{{AC_NS}}}structured-macro"):
        if _macro_name(macro) == "anchor":
            param = _param(macro, "")
            if param is not None and (param.text or "").strip() == anchor:
                target = macro
                break
    if target is None:  # Confluence는 제목 자체에도 책갈피를 준다 (링크의 ac:anchor = 제목 글자)
        key = re.sub(r"\s+", "", anchor)
        target = next((h for h in root.iter() if isinstance(h.tag, str) and _local(h) in _HEADINGS
                       and re.sub(r"\s+", "", "".join(h.itertext())) == key), None)
    if target is None:
        return None

    # 블록을 담는 그릇(root 또는 매크로 본문) 바로 아래 조상까지 올라간다
    start = target
    while start.getparent() is not None and not (
            start.getparent() is root or _is_ac(start.getparent(), "rich-text-body")):
        start = start.getparent()
    if start.getparent() is None:
        return None
    level = int(_local(start)[1]) if _local(start) in _HEADINGS else 7

    section = etree.Element("root")
    node = start
    while node is not None:
        if node is not start and isinstance(node.tag, str) and _local(node) in _HEADINGS \
                and int(_local(node)[1]) <= min(level, 6):
            break
        section.append(copy.deepcopy(node))
        node = node.getnext()
    return section


def _split_paragraph(el) -> tuple[list[Block], list[str]]:
    """<p>글 <include …/> 글</p>처럼 문단 안에 블록 매크로가 든 경우. 편집기가 매크로를 문단 안에
    넣는 일이 흔한데, 그대로 인라인으로 읽으면 매크로가 통째로 사라진다(페이지 포함이 조용히 빠짐).
    매크로 앞뒤 글은 각각 문단으로, 매크로는 블록으로 나눈다."""
    blocks: list[Block] = []
    notes: list[str] = []
    align = _align_of(el)

    def flush(segment) -> None:
        lines, images = _inline_lines(segment)
        blocks.extend(_paragraphs_from_lines(lines, images, align=align))

    segment = etree.Element(el.tag)
    segment.text = el.text
    for child in el:
        if _is_block_macro(child):
            flush(segment)
            inner, inner_notes = _macro(child)
            blocks.extend(inner)
            notes.extend(inner_notes)
            segment = etree.Element(el.tag)
            segment.text = child.tail
        else:
            segment.append(copy.deepcopy(child))
    flush(segment)
    return blocks, notes


def _macro_name(el) -> str:
    return el.get("ac:name") or el.get("{%s}name" % AC_NS) or ""


def _param(el, name: str):
    for param in el.findall(f"{{{AC_NS}}}parameter"):
        if (param.get("{%s}name" % AC_NS) or param.get("ac:name") or "") == name:
            return param
    return None


def _page_link(el) -> tuple[str | None, str | None]:
    """매크로 매개변수 안의 <ac:link><ri:page ri:content-title=… ri:space-key=…/></ac:link>."""
    page = next(el.iter(f"{{{RI_NS}}}page"), None) if el is not None else None
    if page is None:
        return None, None
    return (page.get(f"{{{RI_NS}}}content-title") or page.get("ri:content-title"),
            page.get(f"{{{RI_NS}}}space-key") or page.get("ri:space-key"))


def _link_macro(el, name: str) -> tuple[list, list[str]] | None:
    """다른 페이지·첨부를 끌어오는 매크로면 PageRef, 아니면 None."""
    if name in ("include", "excerpt-include"):
        param = _param(el, "")  # lxml 요소는 자식이 없으면 거짓이라 `or`로 이으면 안 된다
        title, space = _page_link(param if param is not None else el)
        if not title:
            return [], [f"매크로 '{name}'의 대상 페이지를 찾지 못해 건너뜀"]
        return [PageRef("excerpt" if name == "excerpt-include" else "include", title, space)], []
    if name in ("children", "pagetree"):
        root = _param(el, "root") if name == "pagetree" else _param(el, "page")
        title, space = _page_link(root)
        text = (root.text or "").strip() if root is not None else ""
        if name == "pagetree" and not title and text not in ("@self",):
            # 기본값(@home)은 스페이스 전체 — 너무 많아 불러오지 않는다
            return [], [f"페이지 트리({text or '@home'})는 스페이스 전체라 불러오지 않음"]
        depth_param = _param(el, "depth")
        try:
            depth = int((depth_param.text or "").strip()) if depth_param is not None else None
        except ValueError:
            depth = None
        if depth is None and name == "children":
            all_param = _param(el, "all")
            depth = None if all_param is not None and (all_param.text or "").strip() == "true" else 1
        return [PageRef("children", title, space, depth=depth)], []
    if name in _VIEW_FILE_MACROS:
        attachment = next(el.iter(f"{{{RI_NS}}}attachment"), None)
        filename = attachment.get(f"{{{RI_NS}}}filename") if attachment is not None else None
        return ([PageRef("attachment", filename=filename)] if filename else []), []
    return None


def _macro(el) -> tuple[list[Block], list[str]]:
    name = _macro_name(el)
    body = _macro_child(el, "rich-text-body")

    linked = _link_macro(el, name)
    if linked is not None:
        return linked

    if name == "anchor":
        return [], []  # 책갈피(앵커)는 위치 표시일 뿐 보이는 내용이 없다

    if name == "expand" and body is not None:
        # 펼치기(+) 매크로: 접힌 제목도 살린다 — 없으면 무엇을 펼친 내용인지 모른다.
        inner, notes = _children_blocks(body)
        title_param = _param(el, "title")
        title = (title_param.text or "").strip() if title_param is not None else ""
        refs = [b for b in inner if isinstance(b, PageRef)]
        if title and inner and len(refs) == len(inner) and refs[0].kind != "link":
            # 펼치기 안이 연결 문서뿐이면 제목은 그 자리표시가 가져간다 (불러온 문서는 새 쪽·제목)
            refs[0].label = title
            return inner, notes
        head = [Paragraph(runs=[Run(title, bold=True)])] if title else []
        return head + inner, notes

    if name in _KIND_BY_MACRO:
        inner, notes = _children_blocks(body) if body is not None else ([], [])
        return [Callout(kind=_KIND_BY_MACRO[name], blocks=inner)], notes

    if name == "code":
        text_body = _macro_child(el, "plain-text-body")
        text = (text_body.text or "") if text_body is not None else ""
        lang = None
        for param in el.findall(f"{{{AC_NS}}}parameter"):
            if param.get("ac:name") == "language" or param.get("{%s}name" % AC_NS) == "language":
                lang = (param.text or "").strip() or None
        return [CodeBlock(text=text.rstrip("\n"), lang=lang)], []

    if body is not None:
        # panel, expand 등 아직 모르는 매크로라도 본문(rich-text-body)이 있으면 펼친다.
        inner, notes = _children_blocks(body)
        return inner, notes + [f"매크로 '{name}'을(를) 본문만 펼쳐서 처리함"]

    return [], [f"지원하지 않는 매크로를 건너뜀: {name or '(이름 없음)'}"]


def _macro_child(el, local_name: str):
    return el.find(f"{{{AC_NS}}}{local_name}")


def _list(el, depth: int, ordered: bool) -> list[Block]:
    """<li>는 <p>로 감싼 항목과, 인라인 서식이 직접 들어간 항목이 둘 다 흔하다
    (<li>텍스트</li> vs <li><p>텍스트</p></li>). 후자도 굵게·링크 등을 살리려면
    <li> 자체를 인라인으로 훑어야 한다 — 텍스트만 뽑으면 서식이 사라진다.
    """
    out: list[Block] = []
    number = 1
    for li in el:
        if _local(li) != "li":
            continue
        block_children = [c for c in li
                          if _local(c) in ("p", "ul", "ol", "table") or _is_block_macro(c)]
        para = next((c for c in block_children if _local(c) == "p"), None)
        if para is not None:
            runs, images = _inline(para)
        else:
            # walk()가 ul/ol/table 자식은 건너뛰므로, 뒤이은 중첩 목록과 안 섞인다.
            runs, images = _inline(li)
        if not is_blank(plain(runs)) or images:
            out.append(ListItem(depth=depth, runs=runs, ordered=ordered,
                                number=number if ordered else None))
        # else: Confluence는 들여쓰기용으로 글자 없는 <li><ol>…</ol></li> 껍데기를 만든다 —
        # 항목으로 만들면 "□"만 덜렁 찍힌 줄이 생긴다. 안쪽 목록의 깊이는 그대로 둔다.
        out.extend(images)
        if para is not None:  # 항목 문단 안에 든 페이지 포함·펼치기 등은 항목 바로 뒤에
            out.extend(b for c in para if _is_block_macro(c) for b in _macro(c)[0])
        for child in block_children:
            tag = _local(child)
            if _is_block_macro(child):
                out.extend(_macro(child)[0])
            elif tag in ("ul", "ol"):
                out.extend(_list(child, depth + 1, ordered=(tag == "ol")))
            elif tag == "table":
                out.append(_table(child))
        number += 1
    return out


def _table(el) -> Table:
    rows: list[Row] = []
    header_rows = 0
    trs: list = []
    for section in el:
        if _local(section) in ("thead", "tbody", "tfoot"):
            for tr in section:
                if _local(tr) == "tr":
                    trs.append((tr, _local(section) == "thead"))
        elif _local(section) == "tr":
            trs.append((section, False))

    counted_header = True
    for tr, in_thead in trs:
        cells: list[Cell] = []
        all_th = True
        for cell_el in tr:
            tag = _local(cell_el)
            if tag not in ("td", "th"):
                continue
            all_th = all_th and tag == "th"
            blocks = _cell_blocks(cell_el)
            if not blocks:
                blocks = [Paragraph(runs=[])]
            cells.append(Cell(
                blocks=blocks,
                colspan=_int_attr(cell_el, "colspan", 1),
                rowspan=_int_attr(cell_el, "rowspan", 1),
                is_header=in_thead or tag == "th",
            ))
        rows.append(Row(cells=cells))
        if counted_header and (in_thead or (cells and all_th)):
            header_rows += 1
        else:
            counted_header = False

    return Table(rows=rows, header_rows=header_rows)


_HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
_BLOCK_TAGS = {"p", "ul", "ol", "table", "blockquote", "hr", "h1", "h2", "h3", "h4", "h5", "h6"}


def _cell_blocks(el) -> list[Block]:
    """표 셀은 <td><p>구분</p></td>처럼 <p>로 감싸기도 하고, <td>구분</td>처럼 바로
    텍스트를 넣기도 한다. 후자를 _children_blocks에 그대로 넘기면 자식 요소가 없어
    아무것도 못 건지고 빈 문단이 된다 — 리스트 항목과 같은 문제라 같은 방식으로 푼다."""
    has_block = any(
        _local(c) in _BLOCK_TAGS or _is_block_macro(c) or _is_ac(c, "image")
        for c in el
    )
    if has_block:
        blocks, _ = _children_blocks(el, links=False)
        return blocks
    lines, images = _inline_lines(el)
    return _paragraphs_from_lines(lines, images)


def _paragraphs_from_lines(lines: list[list[Run]], images: list[Image],
                           align: str | None = None) -> list[Block]:
    blocks: list[Block] = [Paragraph(runs=line, align=align)
                           for line in lines if any(r.text.strip() for r in line)]
    blocks.extend(images)
    return blocks


_ALIGN_RE = re.compile(r"text-align\s*:\s*(left|center|right)", re.I)


def _align_of(el) -> str | None:
    """Confluence 편집기의 정렬 버튼이 넣는 style="text-align: ..."(또는 옛
    align="..." 속성)을 읽는다 — 이게 있으면 표 제목으로 옮겨도 원래 정렬을
    유지할 수 있다(2026-09-29 사용자 요청)."""
    style = el.get("style")
    if style:
        m = _ALIGN_RE.search(style)
        if m:
            return m.group(1).lower()
    align = el.get("align")
    if align and align.lower() in ("left", "center", "right"):
        return align.lower()
    return None


def _int_attr(el, name: str, default: int) -> int:
    value = el.get(name)
    try:
        return int(value) if value else default
    except ValueError:
        return default


def _image(el) -> Image | None:
    attachment = el.find(f"{{{RI_NS}}}attachment")
    if attachment is not None:
        filename = attachment.get(f"{{{RI_NS}}}filename") or attachment.get("ri:filename")
        if filename:
            width = el.get(f"{{{AC_NS}}}width") or el.get("ac:width")
            return Image(src=filename, width_px=int(width) if width and width.isdigit() else None)
    url = el.find(f"{{{RI_NS}}}url")
    if url is not None:
        href = url.get(f"{{{RI_NS}}}value") or url.get("ri:value")
        if href:
            return Image(src=href)
    return None


# ── 인라인 ──────────────────────────────────────────────────────────────


def _inline(el) -> tuple[list[Run], list[Image]]:
    """storage XHTML은 흔히 태그 사이에 줄바꿈·들여쓰기가 그대로 남아 있다
    (source-view로 편집했거나 Confluence가 그대로 보존한 경우). HTML의
    white-space:normal 규칙대로 연속 공백을 하나로 접고 블록 앞뒤는 잘라 낸다 —
    안 그러면 "복합 인덱스 3건 추가\\n      " 처럼 원본 들여쓰기가 문서에 새어 나온다.
    """
    runs: list[Run] = []
    images: list[Image] = []

    def walk(node, bold: bool, italic: bool, code: bool, href: str | None) -> None:
        if node.text:
            runs.append(Run(_ws(node.text), bold=bold, italic=italic, code=code, href=href))
        for child in node:
            if _is_ac(child, "image"):
                image = _image(child)
                if image:
                    images.append(image)
                if child.tail:
                    runs.append(Run(_ws(child.tail), bold=bold, italic=italic, code=code, href=href))
                continue

            if _is_ac(child, "structured-macro") or _is_ac(child, "link"):
                label = (_inline_macro_label(child) if _is_ac(child, "structured-macro")
                         else _bare_link_label(child))
                if label:
                    runs.append(Run(label, bold=bold, italic=italic, code=code, href=href))
                else:
                    for part in child:
                        if not _is_ac(part, "parameter"):
                            walk(part, bold, italic, code, href)
                if child.tail:
                    runs.append(Run(_ws(child.tail), bold=bold, italic=italic, code=code, href=href))
                continue

            tag = _local(child)
            if tag == "br":
                runs.append(Run(" ", bold=bold, italic=italic, code=code, href=href))
                if child.tail:
                    runs.append(Run(_ws(child.tail), bold=bold, italic=italic, code=code, href=href))
                continue

            child_bold = bold or tag in ("strong", "b")
            child_italic = italic or tag in ("em", "i")
            child_code = code or tag == "code"
            child_href = href
            if tag == "a":
                child_href = child.get("href") or href

            if tag in _INLINE_TAGS or tag not in ("ul", "ol", "table", "p", "blockquote"):
                walk(child, child_bold, child_italic, child_code, child_href)
            if child.tail:
                runs.append(Run(_ws(child.tail), bold=bold, italic=italic, code=code, href=href))

    walk(el, False, False, False, None)
    return _trim(_merge_runs(runs)), images


def _inline_lines(el) -> tuple[list[list[Run]], list[Image]]:
    """_inline()과 같지만 <br>를 (공백이 아니라) 줄 경계로 취급한다.

    표 셀은 <p> 없이 텍스트 사이사이에 <br>만 넣어 여러 줄을 표현하는 경우가
    흔한데, 이를 _inline()으로 읽으면 <br>가 공백 하나로 접혀 여러 줄이 한
    문장으로 뭉개진다(실제로 겪음: "ㅇㅇㅇ<br>ㅁㅁㅁ<br>ㄷㄷㄷ" → 한 줄).
    <p> 블록 하나에서 이 함수가 돌려주는 줄마다 별도 Paragraph를 만들면
    셀 안에 여러 문단이 되어 원래 줄 구분이 살아난다."""
    lines: list[list[Run]] = [[]]
    images: list[Image] = []

    def walk(node, bold: bool, italic: bool, code: bool, href: str | None) -> None:
        if node.text:
            lines[-1].append(Run(_ws(node.text), bold=bold, italic=italic, code=code, href=href))
        for child in node:
            if _is_ac(child, "image"):
                image = _image(child)
                if image:
                    images.append(image)
                if child.tail:
                    lines[-1].append(Run(_ws(child.tail), bold=bold, italic=italic, code=code, href=href))
                continue

            if _is_ac(child, "structured-macro") or _is_ac(child, "link"):
                label = (_inline_macro_label(child) if _is_ac(child, "structured-macro")
                         else _bare_link_label(child))
                if label:
                    lines[-1].append(Run(label, bold=bold, italic=italic, code=code, href=href))
                else:
                    for part in child:
                        if not _is_ac(part, "parameter"):
                            walk(part, bold, italic, code, href)
                if child.tail:
                    lines[-1].append(Run(_ws(child.tail), bold=bold, italic=italic, code=code, href=href))
                continue

            tag = _local(child)
            if tag == "br":
                lines.append([])
                if child.tail:
                    lines[-1].append(Run(_ws(child.tail), bold=bold, italic=italic, code=code, href=href))
                continue

            child_bold = bold or tag in ("strong", "b")
            child_italic = italic or tag in ("em", "i")
            child_code = code or tag == "code"
            child_href = href
            if tag == "a":
                child_href = child.get("href") or href

            if tag in _INLINE_TAGS or tag not in ("ul", "ol", "table", "p", "blockquote"):
                walk(child, child_bold, child_italic, child_code, child_href)
            if child.tail:
                lines[-1].append(Run(_ws(child.tail), bold=bold, italic=italic, code=code, href=href))

    walk(el, False, False, False, None)
    return [_trim(_merge_runs(line)) for line in lines], images


def _ws(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def _trim(runs: list[Run]) -> list[Run]:
    """블록 맨 앞/뒤의 공백만 잘라 낸다 (중간 공백은 단어 사이 구분이라 그대로 둔다)."""
    if not runs:
        return runs
    runs = list(runs)
    runs[0] = runs[0].copy_with(runs[0].text.lstrip())
    runs[-1] = runs[-1].copy_with(runs[-1].text.rstrip())
    return [r for r in runs if r.text]


def _merge_runs(runs: list[Run]) -> list[Run]:
    out: list[Run] = []
    for r in runs:
        if not r.text:
            continue
        if out and (out[-1].bold, out[-1].italic, out[-1].code, out[-1].href) == (
            r.bold, r.italic, r.code, r.href
        ):
            out[-1] = out[-1].copy_with(out[-1].text + r.text)
        else:
            out.append(r)
    return out
