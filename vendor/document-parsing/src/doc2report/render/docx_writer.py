"""IR → .docx.

서식 값은 전부 프로파일에서 온다. 이 파일에는 pt·mm·글꼴명 상수가 없다.
표의 열 폭과 글자 크기는 layout 단계가 정해 준 TableLayout을 그대로 쓴다.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

from docx import Document as DocxDocument
from docx.enum.section import WD_SECTION
from docx.shared import Emu

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
    PageBreak,
    Paragraph,
    Run,
    Table,
    plain,
)
from ..layout.flow import FlowPlan
from ..layout.table_fit import TableLayout, plan_tables
from ..profile import FontSpec, Profile
from . import oxml
from .base_template import open_base_template
from .markers import format_marker

from ..ir import DATE_LINE  # noqa: F401  (pipeline이 여기서 가져다 쓴다)

def _base_template(profile: Profile):
    """profile.template(사내 template.docx 경로)이 있으면 그 경로, 없으면 메모리에 든
    기본 템플릿. 파일(.docx)로 두지 않는 이유는 base_template.py 참고."""
    return profile.template or open_base_template()


@dataclass
class RenderResult:
    document: object  # docx.Document
    notes: list[str] = field(default_factory=list)


class DocxRenderer:
    def __init__(self, profile: Profile, layouts: dict[int, TableLayout] | None = None,
                 flow: FlowPlan | None = None):
        self.profile = profile
        self.layouts = layouts or {}
        self.flow = flow or FlowPlan()
        self.notes: list[str] = list(self.flow.notes)
        self._table_seq = 0
        self._figure_seq = 0
        self._counters: dict[int, int] = {}
        self._previous: Block | None = None  # 바로 앞에 무엇이 왔는지 (표 뒤 간격 판단용)
        self._break_before = False  # 다음 본문 문단을 새 쪽에서 시작 (PageBreak)
        self._base_indent = 0  # 마지막 (※가 아닌) 문단의 왼쪽 들여쓰기 — ※ 문단은 이보다 더 들여쓴다

    # ── 진입점 ──────────────────────────────────────────────────────────

    def render(self, doc: Document) -> RenderResult:
        if not self.layouts:
            self.layouts = plan_tables(doc, self.profile)

        self.docx = DocxDocument(_base_template(self.profile))
        self._apply_document_defaults()
        oxml.apply_page_setup(self.docx.sections[0], self.profile.page)

        if doc.title:
            self._paragraph([Run(doc.title)], self.profile.font("title"))

        blocks = self._dateline(doc.blocks)
        self._blocks(blocks)
        return RenderResult(document=self.docx, notes=self.notes)

    def _dateline(self, blocks: list[Block]) -> list[Block]:
        """제목 바로 아래 날짜 한 줄은 본문이 아니라 날짜 서식으로 쓴다."""
        if not blocks or not self.profile.has_font("date"):
            return blocks
        first = blocks[0]
        if not isinstance(first, Paragraph):
            return blocks
        text = plain(first.runs).strip()
        if not DATE_LINE.match(text):
            return blocks
        self._paragraph([Run(text)], self.profile.font("date"))
        return blocks[1:]

    def save(self, doc: Document, path: str | Path) -> RenderResult:
        result = self.render(doc)
        result.document.save(str(path))
        return result

    # ── 문서 기본값 ─────────────────────────────────────────────────────

    def _apply_document_defaults(self) -> None:
        """Normal 스타일 자체를 프로파일 본문 서식으로 맞춘다.

        표 안 문단처럼 우리가 직접 만들지 않는 요소까지 일관되게 하기 위함이다.
        """
        body = self.profile.font("body")
        style = self.docx.styles["Normal"]
        if body.latin:
            style.font.name = body.latin
        if body.size:
            style.font.size = Emu(body.size)
        if body.east_asia:
            rpr = style.element.get_or_add_rPr()
            rfonts = rpr.get_or_add_rFonts()
            from docx.oxml.ns import qn

            rfonts.set(qn("w:eastAsia"), body.east_asia)
            if not body.latin:
                rfonts.set(qn("w:ascii"), body.east_asia)
                rfonts.set(qn("w:hAnsi"), body.east_asia)

    # ── 블록 디스패치 ───────────────────────────────────────────────────

    def _blocks(self, blocks: list[Block], container=None) -> None:
        for index, block in enumerate(blocks):
            following = blocks[index + 1] if index + 1 < len(blocks) else None
            self._block(block, container, following)
            if container is None:
                self._previous = block

    def _block(self, block: Block, container=None, next_block: Block | None = None) -> None:
        if isinstance(block, Heading):
            self._after_table_gap(self._heading(block, container), container)
        elif isinstance(block, Paragraph):
            text = plain(block.runs).lstrip()
            spec = self._noted(self.profile.font("body"), text)
            if (container is None and isinstance(self._previous, Heading) and self._previous.page_title
                    and self.profile.has_font("date") and DATE_LINE.match(text.strip())):
                spec = self.profile.font("date")  # 쪽 제목 바로 아래 날짜
            paragraph = self._paragraph(block.runs, spec, container)
            if container is None:
                if self._is_note(text):
                    paragraph.paragraph_format.left_indent = Emu(self._note_indent())
                else:
                    self._base_indent = 0
            self._after_table_gap(paragraph, container)
        elif isinstance(block, ListItem):
            self._list_item(block, container, next_block)
        elif isinstance(block, Table):
            self._table(block, container)
        elif isinstance(block, CodeBlock):
            self._after_table_gap(self._code(block, container), container)
        elif isinstance(block, Callout):
            self._callout(block, container)
        elif isinstance(block, Image):
            self._after_table_gap(self._image(block, container), container)
        elif isinstance(block, HorizontalRule):
            self._after_table_gap(self._rule(container), container)
        elif isinstance(block, PageBreak):
            if container is None and next_block is not None and not isinstance(next_block, Table):
                # 다음 문단에 "쪽 나눔 앞"을 건다 — 나눔 문자를 넣은 빈 문단을 쓰면 새 쪽 맨 위에
                # 빈 줄이 한 줄 생긴다(쪽 제목이 맨 위에 붙지 않음).
                self._break_before = True
                return
            from docx.enum.text import WD_BREAK

            self._new_paragraph(container).add_run().add_break(WD_BREAK.PAGE)

    # ── 개별 블록 ───────────────────────────────────────────────────────

    def _heading(self, block: Heading, container=None) -> None:
        self._counters.clear()  # 제목이 나오면 항목 번호를 다시 1부터
        self._base_indent = 0
        if block.page_title and self.profile.has_font("title"):
            # 입력마다 새 쪽 — 쪽 제목은 문서 제목 서식(큰 글씨·가운데·밑줄)
            return self._paragraph(block.runs, self.profile.font("title"), container)
        key = f"heading{block.level}"
        if not self.profile.has_font(key):
            for level in range(block.level - 1, 0, -1):
                if self.profile.has_font(f"heading{level}"):
                    key = f"heading{level}"
                    break
        return self._paragraph(block.runs, self.profile.font(key), container)

    def _after_table_gap(self, paragraph, container) -> None:
        """표 바로 뒤 문단은 표와 붙어 보이므로 앞 간격을 확보한다.

        (Word는 표 자체에 '단락 뒤 간격'을 줄 수 없어 다음 문단 쪽에서 띄운다.)
        """
        gap = self.profile.tables.space_after
        if container is None and gap and isinstance(self._previous, Table):
            current = paragraph.paragraph_format.space_before
            paragraph.paragraph_format.space_before = Emu(max(int(current or 0), gap))

    def _list_item(self, block: ListItem, container=None, next_block: Block | None = None) -> None:
        level = self.profile.numbering_level(block.depth)
        spec = self.profile.font("body")
        if level.size:
            spec = spec.resized(level.size)
        if self.profile.text.level_bold and block.marker is None and level.bold is not None:
            # 말머리뿐 아니라 그 항목 문장 전체에 적용된다 ("1. □로 시작하는 문장은 굵은체").
            # **도구가 말머리를 붙인 항목에만** — 원문에 말머리가 있던 줄(Confluence·Word·직접 친 □)은
            # 원문 굵기 그대로다(2026-09-30 사용자: 원문에서 굵은 글씨만 굵게).
            spec = spec.model_copy(update={"bold": level.bold})
        elif block.from_heading:
            # 단계 굵게를 끈 프로파일: 굵은 건 원문 굵은 글씨(run)와 제목뿐이다.
            spec = spec.model_copy(update={"bold": True})

        marker = self._marker(block, level)
        spec = self._noted(spec, block.marker or plain(block.runs).lstrip())

        paragraph = self._new_paragraph(container)
        oxml.apply_paragraph_format(paragraph, spec, indent=False)
        indent = level.indent
        note = container is None and self._is_note(block.marker or plain(block.runs).lstrip())
        if note:
            indent = self._note_indent()
        elif container is None:
            self._base_indent = indent
        # 말머리를 일부러 뺀 항목(꺾쇠 표기)은 내어쓰기 없이 첫 줄과 나머지 줄을 맞춘다.
        hanging = 0 if block.marker == "" else level.hanging
        oxml.set_list_indent(paragraph, indent, hanging)

        space = self._item_spacing(block, level, next_block)
        if space is not None:
            paragraph.paragraph_format.space_after = Emu(space)
        before = self._space_before(block, level)
        if before:
            paragraph.paragraph_format.space_before = Emu(before)

        if marker:
            run = paragraph.add_run(marker + "\t")
            oxml.apply_run_format(run, spec)
        self._runs(paragraph, block.runs, spec)

    def _is_note(self, text: str) -> bool:
        rules = self.profile.text
        return bool(rules.note_indent and any(text.startswith(m) for m in rules.note_marks))

    def _note_indent(self) -> int:
        """※ 참고사항: 바로 윗줄 문단의 들여쓰기 + text.note_indent (2026-09-29 사용자: +0.4cm)."""
        return self._base_indent + (self.profile.text.note_indent or 0)

    def _noted(self, spec: FontSpec, text: str) -> FontSpec:
        """※ 같은 참고사항 표시로 시작하면 본문보다 text.note_size_delta만큼 작게."""
        rules = self.profile.text
        if rules.note_size_delta and any(text.startswith(m) for m in rules.note_marks):
            return spec.resized(max(spec.size - rules.note_size_delta, 1))
        return spec

    def _marker(self, block: ListItem, level) -> str:
        """말머리 문자열. {n} 같은 자리표시자가 있으면 깊이별 번호를 매긴다."""
        template = (level.ordered_marker if block.ordered and level.ordered_marker
                    else level.marker)
        if block.marker is not None:
            # 원문 말머리를 그대로 쓴다. 번호 단계면 번호는 세어 둬야 다음 자동 번호가 맞는다.
            if template and "{" in template and block.marker:
                self._next_number(block.depth)
            return block.marker
        if not template:
            return ""
        if "{" not in template:
            return template
        return format_marker(template, self._next_number(block.depth))

    def _next_number(self, depth: int) -> int:
        for deeper in [d for d in self._counters if d > depth]:
            del self._counters[deeper]  # 상위 단계로 돌아오면 하위 번호는 초기화
        self._counters[depth] = self._counters.get(depth, 0) + 1
        return self._counters[depth]

    def _item_spacing(self, block: ListItem, level, next_block: Block | None) -> int | None:
        """단계가 바뀌는 자리(1. → □ → -)에서만 단락 뒤 간격을 준다."""
        same_level = isinstance(next_block, ListItem) and next_block.depth == block.depth
        if same_level:
            return level.space_after
        return level.level_change_space(self.flow.relaxed)

    def _space_before(self, block: ListItem, level) -> int:
        """새 절이 시작되는 자리(… - 다음의 2.)와 표 바로 뒤를 넉넉히 띄운다."""
        previous = self._previous
        if previous is None:
            return 0  # 문서 첫 항목은 제목·날짜 간격으로 충분하다

        before = 0
        deeper_before = isinstance(previous, ListItem) and previous.depth > block.depth
        if deeper_before and level.space_before:
            before = level.space_before
        if isinstance(previous, Table) and self.profile.tables.space_after:
            before = max(before, self.profile.tables.space_after)
        return before

    def _code(self, block: CodeBlock, container=None):
        spec = self.profile.font("code")
        paragraph = self._new_paragraph(container)
        oxml.apply_paragraph_format(paragraph, spec)
        lines = block.text.splitlines() or [""]
        for i, line in enumerate(lines):
            run = paragraph.add_run()
            if i:
                run.add_break()
            run.add_text(line)
            oxml.apply_run_format(run, spec)
        if self.profile.tables.border_width:
            oxml.add_border(paragraph, "left", self.profile.tables.border_width,
                            self.profile.tables.border_color, space="6")
        return paragraph

    def _callout(self, block: Callout, container=None) -> None:
        spec = self.profile.font("callout")
        start = len(self._paragraphs(container))
        for inner in block.blocks:
            if isinstance(inner, Paragraph):
                self._paragraph(inner.runs, spec, container)
            else:
                self._block(inner, container)
        paragraphs = self._paragraphs(container)
        if len(paragraphs) > start:
            self._after_table_gap(paragraphs[start], container)
        if self.profile.tables.border_width:
            for paragraph in paragraphs[start:]:
                oxml.add_border(paragraph, "left", self.profile.tables.border_width,
                                self.profile.tables.border_color, space="6")

    def _image(self, block: Image, container=None):
        path = Path(block.src)
        if not path.exists():
            self.notes.append(f"이미지를 찾을 수 없어 건너뜀: {block.src}")
            return None
        paragraph = self._new_paragraph(container)
        try:
            paragraph.add_run().add_picture(str(path), width=Emu(self._image_width(block)))
        except Exception as exc:  # 형식 미지원 등
            self.notes.append(f"이미지 삽입 실패({block.src}): {exc}")
            return None
        oxml.apply_paragraph_format(paragraph, self.profile.font("caption"))
        if block.caption:
            self._figure_seq += 1
            self._paragraph([Run(f"[그림 {self._figure_seq}] {block.caption}")],
                            self.profile.font("caption"), container)
        return paragraph

    def _image_width(self, block: Image) -> int:
        usable = self.profile.page.usable_width
        if not block.width_px:
            return usable
        from ..units import parse_length

        natural = parse_length(block.width_px, default_unit="px")
        return min(natural, usable)

    def _rule(self, container=None):
        paragraph = self._new_paragraph(container)
        rules = self.profile.tables
        if rules.border_width:
            oxml.add_border(paragraph, "bottom", rules.border_width, rules.border_color)
        return paragraph

    # ── 표 ──────────────────────────────────────────────────────────────

    def _table(self, block: Table, container=None) -> None:
        layout = self.layouts.get(id(block))
        if layout is None:
            layout = plan_tables(Document(blocks=[block]), self.profile)[id(block)]
        self.notes.extend(f"표 {self._table_seq + 1}: {note}" for note in layout.notes)

        section_switched = layout.landscape and container is None
        if section_switched:
            section = self.docx.add_section(WD_SECTION.NEW_PAGE)
            oxml.apply_page_setup(section, self.profile.page.landscape())

        self._table_seq += 1
        if block.caption:
            spec = self.profile.font("caption")
            if block.caption_align:
                spec = spec.model_copy(update={"align": block.caption_align})
            caption = self._paragraph([Run(block.caption)], spec, container)
            if layout.indent and container is None:  # 표 제목도 표와 같은 왼쪽 끝(가운데면 표 폭의 가운데)
                caption.paragraph_format.left_indent = Emu(layout.indent)

        cols = block.col_count
        rows = len(block.rows)
        if cols == 0 or rows == 0:
            return

        target = container if container is not None else self.docx
        table = target.add_table(rows=rows, cols=cols)
        oxml.set_fixed_layout(table, layout.total_width, self.profile.tables.align)
        oxml.set_table_borders(table, self.profile.tables)
        oxml.set_cell_margins(table, layout.cell_margin_x, self.profile.tables.cell_margin_y)
        oxml.set_grid(table, layout.col_widths)

        self._fill_cells(table, block, layout)

        rules = self.profile.tables
        min_height = rules.min_row_height(self.flow.relaxed)
        for index, row in enumerate(table.rows):
            if rules.keep_row_together:
                oxml.forbid_row_split(row)
            if rules.repeat_header and index < block.header_rows:
                oxml.mark_header_row(row)
            if min_height:
                oxml.set_min_row_height(row, min_height)

        self._table_notes(block, container)

        if section_switched:
            back = self.docx.add_section(WD_SECTION.NEW_PAGE)
            oxml.apply_page_setup(back, self.profile.page)

    def _table_notes(self, block: Table, container=None) -> None:
        """표 바로 아래 주석 — "* 측정 기준은 …" 형태로 작은 글씨."""
        if not block.notes:
            return
        spec = self.profile.font("table_note")
        marker = self.profile.tables.note_marker
        for runs in block.notes:
            prefix = [Run(f"{marker} ")] if marker else []
            self._paragraph(prefix + list(runs), spec, container)

    def _fill_cells(self, table, block: Table, layout: TableLayout) -> None:
        occupied: set[tuple[int, int]] = set()
        for r, row in enumerate(block.rows):
            col = 0
            for index, cell in enumerate(row.cells):
                override = layout.header_text.get((r, index))
                if override:
                    cell = _with_text(cell, override[1])
                align_override = layout.cell_align.get((r, index))
                if align_override and not cell.align:
                    cell = replace(cell, align=align_override)
                while (r, col) in occupied:
                    col += 1
                if col >= len(layout.col_widths):
                    break
                end_row = min(r + cell.rowspan - 1, len(table.rows) - 1)
                end_col = min(col + cell.colspan - 1, len(layout.col_widths) - 1)
                for rr in range(r, end_row + 1):
                    for cc in range(col, end_col + 1):
                        occupied.add((rr, cc))

                target = table.cell(r, col)
                if (end_row, end_col) != (r, col):
                    target = target.merge(table.cell(end_row, end_col))
                self._fill_cell(target, cell, layout,
                                sum(layout.col_widths[col : end_col + 1]),
                                font_override=layout.cell_font.get((r, index)))
                col = end_col + 1

    def _fill_cell(self, docx_cell, cell: Cell, layout: TableLayout, width: int,
                  *, font_override: tuple[int, float] | None = None) -> None:
        oxml.set_cell_width(docx_cell, width)
        oxml.set_vertical_align(docx_cell, self.profile.tables.valign)
        if cell.is_header and self.profile.tables.header_shading:
            oxml.shade_cell(docx_cell, self.profile.tables.header_shading)

        size, scale = font_override or (layout.font_size, layout.char_scale)
        spec = self.profile.font("table_header" if cell.is_header else "table")
        spec = spec.resized(size)
        if scale != (spec.char_scale or 1.0):
            spec = spec.model_copy(update={"char_scale": scale})
        if cell.align:
            spec = spec.model_copy(update={"align": cell.align})

        # python-docx가 만들어 둔 빈 문단을 첫 블록에 재사용한다.
        blocks = cell.blocks or [Paragraph(runs=[])]
        for i, inner in enumerate(blocks):
            reuse = docx_cell.paragraphs[0] if i == 0 else None
            if isinstance(inner, Paragraph):
                self._paragraph(inner.runs, spec, docx_cell, reuse=reuse)
            elif isinstance(inner, Heading):
                # 셀 안 제목은 본문용 제목 서식(14pt·앞 간격)이 아니라 표 글자 크기의 굵은 글씨로.
                bold = [replace(run, bold=True) for run in inner.runs]
                self._paragraph(bold, spec, docx_cell, reuse=reuse)
            elif isinstance(inner, ListItem):
                self._paragraph(self._cell_item_runs(inner), spec, docx_cell, reuse=reuse)
            else:
                self._block(inner, docx_cell)
        _drop_leading_blank(docx_cell)

    def _cell_item_runs(self, item: ListItem) -> list[Run]:
        """표 안 목록 항목: 본문용 번호 체계(1. □ -)와 들여쓰기를 쓰면 좁은 칸에서 깨지고
        글자도 본문 크기로 나온다 — 원문 말머리(또는 tables.cell_list_markers)를 앞에 붙여
        표 글자 서식으로 그대로 쓴다."""
        markers = self.profile.tables.cell_list_markers
        marker = item.marker or (markers[min(item.depth, len(markers) - 1)] if markers else "")
        head = [Run(marker + " ")] if marker else []
        return head + item.runs

    # ── 문단/런 ─────────────────────────────────────────────────────────

    def _paragraph(self, runs: list[Run], spec: FontSpec, container=None, reuse=None):
        paragraph = reuse if reuse is not None else self._new_paragraph(container)
        oxml.apply_paragraph_format(paragraph, spec)
        self._runs(paragraph, runs, spec)
        return paragraph

    def _runs(self, paragraph, runs: list[Run], spec: FontSpec) -> None:
        for item in runs:
            if not item.text:
                continue
            run_spec = spec
            if item.code and self.profile.has_font("code"):
                code = self.profile.font("code")
                run_spec = code.model_copy(update={"size": spec.size})
            updates: dict = {}
            if item.bold:
                updates["bold"] = True
            if item.italic:
                updates["italic"] = True
            if item.href and self.profile.has_font("link"):
                link = self.profile.font("link")
                updates.update({k: v for k, v in link.model_dump().items()
                                if v is not None and k in ("color", "underline")})
            if updates:
                run_spec = run_spec.model_copy(update=updates)
            run = paragraph.add_run(item.text)
            oxml.apply_run_format(run, run_spec)

    def _new_paragraph(self, container=None):
        target = container if container is not None else self.docx
        paragraph = target.add_paragraph()
        if container is None and self._break_before:
            paragraph.paragraph_format.page_break_before = True
            self._break_before = False
        return paragraph

    def _paragraphs(self, container=None):
        target = container if container is not None else self.docx
        return target.paragraphs


def _drop_leading_blank(docx_cell) -> None:
    """첫 블록이 문단이 아니어서(코드·이미지·인용 등) 재사용 못 한 빈 첫 문단은 지운다 —
    남으면 셀 맨 위에 엔터 한 줄이 들어간 것처럼 보인다. 셀은 문단으로 끝나야 하므로
    다른 문단이 뒤에 있을 때만 지운다."""
    paragraphs = docx_cell._tc.p_lst
    if len(paragraphs) > 1 and not "".join(paragraphs[0].itertext()).strip():
        docx_cell._tc.remove(paragraphs[0])


def _with_text(cell: Cell, text: str) -> Cell:
    """머리 축약 결과로 바꾼 셀 (서식은 원래 첫 런을 따른다). IR 원본은 건드리지 않는다."""
    template = next((run for block in cell.blocks if isinstance(block, Paragraph)
                     for run in block.runs), Run(""))
    return replace(cell, blocks=[Paragraph(runs=[template.copy_with(text)])])
