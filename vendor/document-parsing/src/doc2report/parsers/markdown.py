"""Markdown → IR.

markdown-it-py의 토큰 스트림을 IR 블록으로 옮긴다. 서식 판단은 하지 않는다
(어떤 블릿을 쓸지, 몇 pt로 쓸지는 프로파일과 렌더러의 몫).
"""

from __future__ import annotations

import re

from markdown_it import MarkdownIt
from markdown_it.token import Token

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
    plain,
)

_CALLOUT_PREFIX = re.compile(r"^\s*(?:\[!(?P<gh>[A-Za-z]+)\]|(?P<emoji>ℹ️|⚠️|❗|📌|💡))\s*")
_KIND_BY_KEYWORD = {
    "note": "note", "info": "info", "tip": "info", "important": "note",
    "warning": "warning", "caution": "warning", "danger": "warning",
}


def parse_markdown(text: str, *, source: str | None = None) -> Document:
    md = MarkdownIt("commonmark").enable(["table", "strikethrough"])
    tokens = md.parse(text)
    blocks = _blocks(tokens, depth=0)
    title = None
    # 문서 첫 블록이 H1이면 제목으로 승격한다.
    if blocks and isinstance(blocks[0], Heading) and blocks[0].level == 1:
        title = plain(blocks[0].runs)
        blocks = blocks[1:]
    return Document(blocks=blocks, title=title, source=source)


# ── 토큰 → 블록 ─────────────────────────────────────────────────────────


def _match(tokens: list[Token], start: int) -> int:
    """tokens[start]의 여는 토큰에 대응하는 닫는 토큰 인덱스."""
    level = 0
    for j in range(start, len(tokens)):
        level += tokens[j].nesting
        if level == 0 and j > start:
            return j
    return len(tokens) - 1


def _blocks(tokens: list[Token], depth: int) -> list[Block]:
    out: list[Block] = []
    i = 0
    while i < len(tokens):
        t = tokens[i]

        if t.type == "heading_open":
            close = _match(tokens, i)
            runs, images = _inline(tokens[i + 1]) if close > i + 1 else ([], [])
            out.append(Heading(level=int(t.tag[1:]), runs=runs))
            out.extend(images)
            i = close + 1

        elif t.type == "paragraph_open":
            close = _match(tokens, i)
            runs, images = _inline(tokens[i + 1])
            if plain(runs).strip():
                out.append(_paragraph_or_callout(runs))
            out.extend(images)
            i = close + 1

        elif t.type in ("bullet_list_open", "ordered_list_open"):
            close = _match(tokens, i)
            ordered = t.type == "ordered_list_open"
            start_num = int(t.attrGet("start") or 1)
            out.extend(_list_items(tokens[i + 1 : close], depth, ordered, start_num))
            i = close + 1

        elif t.type == "blockquote_open":
            close = _match(tokens, i)
            inner = _blocks(tokens[i + 1 : close], depth)
            out.append(Callout(kind="quote", blocks=inner))
            i = close + 1

        elif t.type in ("fence", "code_block"):
            lang = (t.info or "").strip().split()[0] if t.info else None
            out.append(CodeBlock(text=t.content.rstrip("\n"), lang=lang or None))
            i += 1

        elif t.type == "hr":
            out.append(HorizontalRule())
            i += 1

        elif t.type == "table_open":
            close = _match(tokens, i)
            out.append(_table(tokens[i + 1 : close], depth))
            i = close + 1

        elif t.type == "html_block":
            i += 1  # 원시 HTML은 무시. Confluence는 이제 storage XHTML을 직접 파싱하므로
            # (parsers/confluence_storage.py) 여기로 오지 않는다 — .md 원본 안의 raw HTML만 해당.

        else:
            i += 1

    return out


def _paragraph_or_callout(runs: list[Run]) -> Block:
    """`[!NOTE] ...` 또는 이모지로 시작하는 문단은 강조 박스로 본다."""
    head = runs[0].text if runs else ""
    m = _CALLOUT_PREFIX.match(head)
    if not m:
        return Paragraph(runs=runs)
    kind = _KIND_BY_KEYWORD.get((m.group("gh") or "").lower(), "info")
    stripped = [runs[0].copy_with(head[m.end() :])] + runs[1:]
    return Callout(kind=kind, blocks=[Paragraph(runs=stripped)])


def _list_items(
    tokens: list[Token], depth: int, ordered: bool, start_num: int
) -> list[Block]:
    out: list[Block] = []
    i = 0
    number = start_num
    while i < len(tokens):
        if tokens[i].type != "list_item_open":
            i += 1
            continue
        close = _match(tokens, i)
        inner = tokens[i + 1 : close]
        out.extend(_list_item(inner, depth, ordered, number))
        number += 1
        i = close + 1
    return out


def _list_item(tokens: list[Token], depth: int, ordered: bool, number: int) -> list[Block]:
    """항목의 첫 문단은 ListItem, 그 뒤 내용(중첩 목록·표 등)은 이어지는 블록으로."""
    out: list[Block] = []
    first_done = False
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t.type in ("paragraph_open", "inline") and not first_done:
            if t.type == "paragraph_open":
                close = _match(tokens, i)
                runs, images = _inline(tokens[i + 1])
                i = close + 1
            else:
                runs, images = _inline(t)
                i += 1
            out.append(ListItem(depth=depth, runs=runs, ordered=ordered,
                                number=number if ordered else None))
            out.extend(images)
            first_done = True
        elif t.type in ("bullet_list_open", "ordered_list_open"):
            close = _match(tokens, i)
            sub_ordered = t.type == "ordered_list_open"
            sub_start = int(t.attrGet("start") or 1)
            out.extend(_list_items(tokens[i + 1 : close], depth + 1, sub_ordered, sub_start))
            i = close + 1
        else:
            close = _match(tokens, i) if t.nesting == 1 else i
            out.extend(_blocks(tokens[i : close + 1], depth + 1))
            i = close + 1
    return out


def _table(tokens: list[Token], depth: int) -> Table:
    rows: list[Row] = []
    header_rows = 0
    in_head = False
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t.type == "thead_open":
            in_head = True
        elif t.type == "thead_close":
            in_head = False
        elif t.type == "tr_open":
            close = _match(tokens, i)
            row = _row(tokens[i + 1 : close], in_head, depth)
            rows.append(row)
            if in_head:
                header_rows += 1
            i = close
        i += 1
    return Table(rows=rows, header_rows=header_rows)


def _row(tokens: list[Token], is_header: bool, depth: int) -> Row:
    cells: list[Cell] = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t.type in ("th_open", "td_open"):
            close = _match(tokens, i)
            runs, images = ([], [])
            for j in range(i + 1, close):
                if tokens[j].type == "inline":
                    runs, images = _inline(tokens[j])
            blocks: list[Block] = []
            if plain(runs).strip() or not images:
                blocks.append(Paragraph(runs=runs))
            blocks.extend(images)
            cells.append(Cell(blocks=blocks, is_header=is_header, align=_align(t)))
            i = close
        i += 1
    return Row(cells=cells)


def _align(token: Token) -> str | None:
    style = token.attrGet("style") or ""
    if "text-align:center" in style:
        return "center"
    if "text-align:right" in style:
        return "right"
    if "text-align:left" in style:
        return "left"
    return None


# ── 인라인 ──────────────────────────────────────────────────────────────


def _inline(token: Token) -> tuple[list[Run], list[Image]]:
    """인라인 토큰 → (런 목록, 문단에서 떼어낼 이미지 목록)."""
    runs: list[Run] = []
    images: list[Image] = []
    bold = italic = 0
    href: str | None = None

    for child in token.children or []:
        ty = child.type
        if ty == "text":
            runs.append(Run(child.content, bold=bool(bold), italic=bool(italic), href=href))
        elif ty == "code_inline":
            runs.append(Run(child.content, bold=bool(bold), italic=bool(italic),
                            code=True, href=href))
        elif ty == "strong_open":
            bold += 1
        elif ty == "strong_close":
            bold = max(0, bold - 1)
        elif ty in ("em_open", "s_open"):
            italic += 1
        elif ty in ("em_close", "s_close"):
            italic = max(0, italic - 1)
        elif ty == "link_open":
            href = child.attrGet("href")
        elif ty == "link_close":
            href = None
        elif ty == "image":
            images.append(Image(src=child.attrGet("src") or "",
                                caption=(child.content or None)))
        elif ty in ("softbreak", "hardbreak"):
            runs.append(Run(" ", bold=bool(bold), italic=bool(italic), href=href))

    return _merge_runs(runs), images


def _merge_runs(runs: list[Run]) -> list[Run]:
    """서식이 같은 인접 런을 합쳐 docx 런 수를 줄인다."""
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
