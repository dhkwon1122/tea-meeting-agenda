"""Word(.docx) → IR.

사내 Word 보고서를 다시 규격 보고서로 옮기거나, 여러 입력(Confluence·Word·붙여넣은 글)을
하나로 합칠 때 쓴다. python-docx의 고수준 객체 대신 XML을 직접 훑는다 — 문단·표·내용 컨트롤
(w:sdt)이 섞인 **본문 순서**를 지켜야 하고, 병합 셀(gridSpan/vMerge)과 자동 번호(numPr)는
고수준 API가 드러내 주지 않기 때문이다.

원칙은 Confluence 파서와 같다: 서식 판단은 하지 않고 구조만 옮긴다.
- 제목 스타일(Heading N / 개요 수준) → Heading. Word는 "제목(Title)" 스타일이 문서 제목이고
  Heading 1이 첫 절이라, Heading N은 IR 수준 N+1로 옮긴다(Markdown의 ## = 첫 절과 맞춘다).
- 자동 번호·글머리 목록 → ListItem. 화면에 보이던 말머리("1.", "가.", "①", "-")를
  번호 정의(numbering.xml)로 다시 만들어 `ListItem.marker`에 넣는다 — 원문 말머리 유지 원칙.
- 굵게는 run → 문자 스타일 → 문단 스타일 순으로 따진다(스타일 자체가 굵은 문단도 굵게 보였으므로).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from docx import Document as open_docx
from docx.oxml.ns import qn

from ..ir import Block, Cell, Document, Heading, Image, ListItem, Paragraph, Row, Run, Table, plain
from ..render.markers import format_marker

_HEADING_NAME = re.compile(r"^(?:heading|제목)\s*(\d)$", re.IGNORECASE)
_TITLE_NAMES = {"title", "제목"}
_PUA = re.compile(r"[-]")  # Symbol/Wingdings 글머리 문자는 다른 글꼴에선 깨진다
_NUMFMT = {"decimal": "{n}", "decimalZero": "{n}", "lowerLetter": "{alpha}",
           "upperLetter": "{ALPHA}", "lowerRoman": "{roman}", "upperRoman": "{ROMAN}",
           "decimalEnclosedCircle": "{circled}", "ganada": "{hangul}"}
_EMU_PER_PX = 9525  # 96dpi


@dataclass
class ParsedDocx:
    document: Document
    notes: list[str] = field(default_factory=list)


def parse_docx(path: str | Path, *, source: str | None = None,
               image_dir: str | Path | None = None) -> ParsedDocx:
    reader = _Reader(open_docx(str(path)), Path(image_dir) if image_dir else Path(path).parent)
    blocks = reader.blocks(reader.docx.element.body)
    title = reader.title
    first = blocks[0] if blocks else None
    if title is None and isinstance(first, Heading) and first.level <= 2 and _is_lonely(blocks, first):
        title = plain(first.runs)  # 제목 스타일 없이 Heading 1 하나로 제목을 쓴 문서
        blocks = blocks[1:]
    elif (title is None and isinstance(first, Paragraph) and first.align == "center"
          and len(plain(first.runs)) <= 80 and any(r.bold for r in first.runs)):
        title = plain(first.runs).strip()  # 가운데 정렬·굵은 첫 줄 = 제목 (우리 출력도 이 모양)
        blocks = blocks[1:]
    return ParsedDocx(Document(blocks=blocks, title=title, source=source), reader.notes)


class _Reader:
    def __init__(self, docx, image_dir: Path):
        self.docx = docx
        self.image_dir = image_dir
        self.notes: list[str] = []
        self.title: str | None = None
        self.styles = {s.style_id: s for s in docx.styles}
        self.counters: dict[str, dict[int, int]] = {}
        self.image_count = 0
        self._numbering = self._numbering_element()

    # ── 블록 ────────────────────────────────────────────────────────────

    def blocks(self, container, *, in_cell: bool = False) -> list[Block]:
        out: list[Block] = []
        for child in container.iterchildren():
            if child.tag == qn("w:p"):
                out.extend(self.paragraph(child, in_cell=in_cell))
            elif child.tag == qn("w:tbl"):
                out.append(self.table(child))
            elif child.tag == qn("w:sdt"):  # 내용 컨트롤 안의 문단·표도 본문이다
                content = child.find(qn("w:sdtContent"))
                if content is not None:
                    out.extend(self.blocks(content, in_cell=in_cell))
        return out

    def paragraph(self, p, *, in_cell: bool) -> list[Block]:
        style = self._style_of(p)
        runs, images = self._runs(p, style)
        lines = _split_lines(runs)
        text = "".join(plain(line) for line in lines).strip()
        out: list[Block] = []

        name = (style.name or "").strip().lower() if style is not None else ""
        level = self._heading_level(p, style, name)
        numbering = None if in_cell else self._numbering_of(p, style)

        if text and not in_cell and name in _TITLE_NAMES and self.title is None:
            self.title = text
        elif text and level is not None and not in_cell:
            out.append(Heading(level=min(level + 1, 6), runs=_join_lines(lines)))
        elif text and numbering is not None:
            ilvl, marker, ordered = numbering
            out.append(ListItem(depth=ilvl, runs=_join_lines(lines), ordered=ordered, marker=marker))
        elif text:
            align = _align(p)
            out.extend(Paragraph(runs=line, align=align) for line in lines if plain(line).strip())
        out.extend(images)
        return out

    def table(self, tbl) -> Table:
        rows: list[Row] = []
        open_cells: dict[int, Cell] = {}  # 세로 병합이 시작된 열 → 그 셀
        header_rows = 0
        counting_header = True
        for tr in tbl.findall(qn("w:tr")):
            row = Row()
            col = 0
            for tc in tr.findall(qn("w:tc")):
                pr = tc.find(qn("w:tcPr"))
                span = _int_val(pr, "w:gridSpan", 1)
                vmerge = pr.find(qn("w:vMerge")) if pr is not None else None
                if vmerge is not None and vmerge.get(qn("w:val")) != "restart":
                    origin = open_cells.get(col)
                    if origin is not None:
                        origin.rowspan += 1
                    col += span
                    continue
                cell = Cell(blocks=self.blocks(tc, in_cell=True), colspan=span,
                            align=_first_align(tc))
                for c in range(col, col + span):
                    open_cells.pop(c, None)
                if vmerge is not None:
                    open_cells[col] = cell
                row.cells.append(cell)
                col += span
            trpr = tr.find(qn("w:trPr"))
            is_header = trpr is not None and trpr.find(qn("w:tblHeader")) is not None
            if counting_header and is_header:
                header_rows += 1
            else:
                counting_header = False
            rows.append(row)

        if header_rows == 0 and len(rows) > 1 and _looks_like_header(tbl):
            header_rows = 1  # 머리행 반복을 안 켰어도 첫 행이 음영/굵게면 머리행으로 본다
        for row in rows[:header_rows]:
            for cell in row.cells:
                cell.is_header = True
        return Table(rows=rows, header_rows=header_rows)

    # ── 인라인 ──────────────────────────────────────────────────────────

    def _runs(self, p, style) -> tuple[list[Run], list[Image]]:
        runs: list[Run] = []
        images: list[Image] = []
        para_bold = _style_bold(style)
        for r in p.iter(qn("w:r")):
            if _inside(r, p, (qn("w:del"), qn("w:moveFrom"))):
                continue  # 변경 추적으로 지운 글자
            bold = self._bold(r, para_bold)
            italic = _flag(r.find(qn("w:rPr")), "w:i")
            href = self._href(r, p)
            for child in r.iterchildren():
                tag = child.tag
                if tag == qn("w:t"):
                    runs.append(Run(child.text or "", bold=bold, italic=bool(italic), href=href))
                elif tag == qn("w:tab"):
                    runs.append(Run(" ", bold=bold, italic=bool(italic), href=href))
                elif tag in (qn("w:br"), qn("w:cr")):
                    if child.get(qn("w:type")) != "page":
                        runs.append(Run("\n", bold=bold))
                elif tag in (qn("w:drawing"), qn("w:pict")):
                    images.extend(self._images(child))
        return _merge(runs), images

    def _bold(self, r, para_bold: bool) -> bool:
        rpr = r.find(qn("w:rPr"))
        own = _flag(rpr, "w:b")
        if own is not None:
            return own
        rstyle = rpr.find(qn("w:rStyle")) if rpr is not None else None
        if rstyle is not None:
            char_style = self.styles.get(rstyle.get(qn("w:val")))
            if char_style is not None and char_style.font.bold is not None:
                return bool(char_style.font.bold)
        return para_bold

    def _href(self, r, p) -> str | None:
        parent = r.getparent()
        while parent is not None and parent is not p:
            if parent.tag == qn("w:hyperlink"):
                rid = parent.get(qn("r:id"))
                if rid and rid in self.docx.part.rels:
                    rel = self.docx.part.rels[rid]
                    return rel.target_ref if rel.is_external else None
                return None
            parent = parent.getparent()
        return None

    def _images(self, element) -> list[Image]:
        out = []
        rids = [b.get(qn("r:embed")) for b in element.iter(qn("a:blip"))]
        rids += [d.get(qn("r:id")) for d in element.iter("{urn:schemas-microsoft-com:vml}imagedata")]
        extent = next(element.iter(qn("wp:extent")), None)
        width = int(extent.get("cx")) // _EMU_PER_PX if extent is not None else None
        height = int(extent.get("cy")) // _EMU_PER_PX if extent is not None else None
        for rid in filter(None, rids):
            part = self.docx.part.related_parts.get(rid)
            if part is None:
                continue
            self.image_count += 1
            ext = Path(str(part.partname)).suffix or ".png"
            self.image_dir.mkdir(parents=True, exist_ok=True)
            path = self.image_dir / f"image{self.image_count}{ext}"
            path.write_bytes(part.blob)
            out.append(Image(src=str(path), width_px=width, height_px=height))
        return out

    # ── 스타일·번호 ─────────────────────────────────────────────────────

    def _style_of(self, p):
        ppr = p.find(qn("w:pPr"))
        pstyle = ppr.find(qn("w:pStyle")) if ppr is not None else None
        if pstyle is not None and pstyle.get(qn("w:val")) in self.styles:
            return self.styles[pstyle.get(qn("w:val"))]
        return self.docx.styles.default(1) if hasattr(self.docx.styles, "default") else None

    def _heading_level(self, p, style, name: str) -> int | None:
        match = _HEADING_NAME.match(name)
        if match:
            return int(match.group(1))
        for ppr in [p.find(qn("w:pPr"))] + [s.element.find(qn("w:pPr")) for s in _chain(style)]:
            outline = ppr.find(qn("w:outlineLvl")) if ppr is not None else None
            if outline is not None:
                value = int(outline.get(qn("w:val"), "9"))
                return value + 1 if value < 9 else None
        return None

    def _numbering_of(self, p, style) -> tuple[int, str | None, bool] | None:
        for ppr in [p.find(qn("w:pPr"))] + [s.element.find(qn("w:pPr")) for s in _chain(style)]:
            numpr = ppr.find(qn("w:numPr")) if ppr is not None else None
            if numpr is None:
                continue
            num_id = _int_val(numpr, "w:numId", 0)
            if num_id == 0:
                return None  # 스타일의 번호를 문단에서 끈 경우
            ilvl = _int_val(numpr, "w:ilvl", 0)
            marker, ordered = self._marker(str(num_id), ilvl)
            return ilvl, marker, ordered
        return None

    def _numbering_element(self):
        try:
            return self.docx.part.numbering_part.element
        except (KeyError, NotImplementedError, AttributeError):
            return None

    def _marker(self, num_id: str, ilvl: int) -> tuple[str | None, bool]:
        """화면에 보이던 말머리를 다시 만든다. 모르는 형식이면 (None, 번호 여부)."""
        lvl = self._level_def(num_id, ilvl)
        if lvl is None:
            return None, False
        fmt = _val(lvl.find(qn("w:numFmt"))) or "decimal"
        text = _val(lvl.find(qn("w:lvlText"))) or ""
        counters = self.counters.setdefault(num_id, {})
        for deeper in [d for d in counters if d > ilvl]:
            del counters[deeper]
        start = _int_val(lvl, "w:start", 1)
        counters[ilvl] = counters.get(ilvl, start - 1) + 1
        if fmt == "bullet":
            return (None if not text or _PUA.search(text) else text), False

        def number(match):
            level = int(match.group(1)) - 1
            level_def = self._level_def(num_id, level)
            level_fmt = _val(level_def.find(qn("w:numFmt"))) if level_def is not None else fmt
            template = _NUMFMT.get(level_fmt or "decimal", "{n}")
            return format_marker(template, counters.get(level, 1))

        return re.sub(r"%(\d)", number, text) or None, True

    def _level_def(self, num_id: str, ilvl: int):
        if self._numbering is None:
            return None
        num = next((n for n in self._numbering.findall(qn("w:num"))
                    if n.get(qn("w:numId")) == num_id), None)
        if num is None:
            return None
        for override in num.findall(qn("w:lvlOverride")):
            if override.get(qn("w:ilvl")) == str(ilvl) and override.find(qn("w:lvl")) is not None:
                return override.find(qn("w:lvl"))
        abstract_id = _val(num.find(qn("w:abstractNumId")))
        abstract = next((a for a in self._numbering.findall(qn("w:abstractNum"))
                         if a.get(qn("w:abstractNumId")) == abstract_id), None)
        if abstract is None:
            return None
        return next((lvl for lvl in abstract.findall(qn("w:lvl"))
                     if lvl.get(qn("w:ilvl")) == str(ilvl)), None)


# ── 도우미 ──────────────────────────────────────────────────────────────


def _is_lonely(blocks: list[Block], first: Heading) -> bool:
    """첫 제목과 같은 수준의 제목이 뒤에 또 없으면 문서 제목으로 본다."""
    return not any(isinstance(b, Heading) and b.level == first.level for b in blocks[1:])


def _chain(style):
    """문단 스타일과 그 상위(based on) 스타일들."""
    seen = 0
    while style is not None and seen < 10:
        yield style
        style = style.base_style
        seen += 1


def _style_bold(style) -> bool:
    for s in _chain(style):
        if s.font.bold is not None:
            return bool(s.font.bold)
    return False


def _flag(rpr, tag: str) -> bool | None:
    if rpr is None:
        return None
    element = rpr.find(qn(tag))
    if element is None:
        return None
    return element.get(qn("w:val"), "true") not in ("0", "false", "off")


def _val(element) -> str | None:
    return element.get(qn("w:val")) if element is not None else None


def _int_val(parent, tag: str, default: int) -> int:
    element = parent.find(qn(tag)) if parent is not None else None
    try:
        return int(element.get(qn("w:val"))) if element is not None else default
    except (TypeError, ValueError):
        return default


def _inside(element, stop, tags) -> bool:
    parent = element.getparent()
    while parent is not None and parent is not stop:
        if parent.tag in tags:
            return True
        parent = parent.getparent()
    return False


def _align(p) -> str | None:
    ppr = p.find(qn("w:pPr"))
    value = _val(ppr.find(qn("w:jc"))) if ppr is not None else None
    return {"center": "center", "right": "right", "end": "right"}.get(value or "")


def _first_align(tc) -> str | None:
    p = tc.find(qn("w:p"))
    return _align(p) if p is not None else None


def _looks_like_header(tbl) -> bool:
    first = tbl.find(qn("w:tr"))
    cells = first.findall(qn("w:tc")) if first is not None else []
    if not cells:
        return False
    for tc in cells:
        shd = tc.find(f"{qn('w:tcPr')}/{qn('w:shd')}")
        fill = (shd.get(qn("w:fill")) or "").upper() if shd is not None else ""
        if fill in ("", "AUTO", "FFFFFF"):
            return False
    return True


def _merge(runs: list[Run]) -> list[Run]:
    out: list[Run] = []
    for r in runs:
        if not r.text:
            continue
        if (out and "\n" not in r.text and "\n" not in out[-1].text
                and (out[-1].bold, out[-1].italic, out[-1].href) == (r.bold, r.italic, r.href)):
            out[-1] = out[-1].copy_with(out[-1].text + r.text)
        else:
            out.append(r)
    return out


def _split_lines(runs: list[Run]) -> list[list[Run]]:
    """줄바꿈(Shift+Enter)으로 나뉜 줄들. 표 셀과 본문 문단은 줄마다 따로 쓴다."""
    lines: list[list[Run]] = [[]]
    for r in runs:
        if r.text == "\n":
            lines.append([])
        else:
            lines[-1].append(r)
    return lines


def _join_lines(lines: list[list[Run]]) -> list[Run]:
    """제목·목록 항목은 한 줄로 — 줄바꿈 자리는 공백으로."""
    out: list[Run] = []
    for i, line in enumerate(lines):
        if i and out and line:
            out.append(Run(" "))
        out.extend(line)
    return _merge(out)
