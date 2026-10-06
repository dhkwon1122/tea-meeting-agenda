"""기존 사내 보고서(.docx)에서 서식 규격을 역추출해 프로파일 초안을 만든다.

규격 문서를 읽어 옮겨 적는 것보다, 이미 결재가 끝난 보고서에서 뽑아내는 쪽이
빠르고 정확하다. 결과는 어디까지나 초안이므로 주석을 붙여 사람이 손보게 한다.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from docx import Document as DocxDocument
from docx.oxml.ns import qn

from .units import emu_to_mm, emu_to_pt

_MARKER_CHARS = "□○●◦-·∙*ㅇ▪▫◆◇▶►"


def profile_from_docx(path: Path) -> str:
    docx = DocxDocument(str(path))
    section = docx.sections[0]

    body = _dominant_font(docx)
    headings = _heading_fonts(docx, body)
    markers = _markers(docx)
    table = _table_style(docx)

    lines = [
        f"# {path.name} 에서 역추출한 초안입니다. 눈으로 확인하고 고쳐 쓰세요.",
        "# (원본에 없던 항목은 일반적인 값으로 채워 두었습니다.)",
        "",
        f"name: {path.stem}",
        f"description: {path.name} 기반 초안",
        "",
        "page:",
        "  size: A4",
        f"  orientation: {'landscape' if section.page_width > section.page_height else 'portrait'}",
        "  margin:",
        f"    top: {_mm(section.top_margin)}",
        f"    bottom: {_mm(section.bottom_margin)}",
        f"    left: {_mm(section.left_margin)}",
        f"    right: {_mm(section.right_margin)}",
        "",
        "fonts:",
        "  body:",
        f"    eastAsia: {body['east_asia']}",
        f"    ascii: {body['latin']}",
        f"    size: {body['size']}",
        f"    line_spacing: {body['line_spacing']}",
        "    space_after: 4pt",
        "    align: both",
    ]

    for key, spec in headings.items():
        lines += [f"  {key}:", f"    size: {spec['size']}", "    bold: true"]

    lines += [
        "  table:",
        f"    size: {table['font_size']}",
        "    line_spacing: 120%",
        "    space_after: 0pt",
        "  table_header:",
        f"    size: {table['font_size']}",
        "    bold: true",
        "    align: center",
        "  caption:",
        "    size: 9pt",
        "    bold: true",
        "    align: center",
        "",
        "numbering:",
    ]

    if markers:
        for marker, indent in markers:
            lines += [f'  - marker: "{marker}"', f"    indent: {indent}", "    hanging: 5mm"]
    else:
        lines += [
            "  # 원본에서 말머리를 찾지 못해 기본값을 넣었습니다.",
            '  - {marker: "□", indent: 0mm, hanging: 5mm, bold: true}',
            '  - {marker: "○", indent: 5mm, hanging: 5mm}',
            '  - {marker: "-", indent: 10mm, hanging: 4mm}',
        ]

    lines += [
        "",
        "tables:",
        f"  header_shading: \"{table['shading']}\"" if table["shading"] else
        "  header_shading: \"D9D9D9\"",
        "  border_width: 0.5pt",
        "  border_color: \"808080\"",
        "  cell_margin_x: 1.4mm",
        "  cell_margin_x_min: 0.6mm",
        "  cell_margin_y: 0.4mm",
        f"  font_ladder: {table['ladder']}",
        "  max_cell_lines: 4",
        "  safety_margin: 3%",
        "  repeat_header: true",
        "  keep_row_together: true",
        "  allow_landscape: false",
        "  align: center",
        "",
        "text:",
        "  gaechosik: true",
        "  split_long_sentences: true",
        "  max_sentence_chars: 60",
        "  rules: [endings, notation]",
        "",
    ]
    return "\n".join(lines)


def _mm(value) -> str:
    return f"{emu_to_mm(int(value)):.0f}mm" if value else "20mm"


def _pt(value) -> str:
    return f"{emu_to_pt(int(value)):.10g}pt"


def _dominant_font(docx) -> dict:
    """가장 많이 쓰인 본문 글꼴·크기 — 스타일 정의보다 실제 사용이 정확하다."""
    east = Counter()
    latin = Counter()
    sizes = Counter()
    spacing = Counter()

    for paragraph in docx.paragraphs:
        if not paragraph.text.strip():
            continue
        if paragraph.paragraph_format.line_spacing:
            spacing[round(float(paragraph.paragraph_format.line_spacing), 2)] += 1
        for run in paragraph.runs:
            weight = len(run.text)
            if not weight:
                continue
            rfonts = run._element.find(qn("w:rPr"))
            if rfonts is not None:
                fonts = rfonts.find(qn("w:rFonts"))
                if fonts is not None:
                    if fonts.get(qn("w:eastAsia")):
                        east[fonts.get(qn("w:eastAsia"))] += weight
                    if fonts.get(qn("w:ascii")):
                        latin[fonts.get(qn("w:ascii"))] += weight
            if run.font.size:
                sizes[int(run.font.size)] += weight

    normal = docx.styles["Normal"].font
    return {
        "east_asia": east.most_common(1)[0][0] if east else (normal.name or "맑은 고딕"),
        "latin": latin.most_common(1)[0][0] if latin else (normal.name or "맑은 고딕"),
        "size": _pt(sizes.most_common(1)[0][0]) if sizes
        else (_pt(int(normal.size)) if normal.size else "11pt"),
        "line_spacing": f"{int(spacing.most_common(1)[0][0] * 100)}%" if spacing else "160%",
    }


def _heading_fonts(docx, body: dict) -> dict[str, dict]:
    """Heading 스타일이 실제로 쓰인 크기를 모은다."""
    out: dict[str, dict] = {}
    for paragraph in docx.paragraphs:
        name = (paragraph.style.name or "").lower()
        if not name.startswith(("heading", "제목")):
            continue
        level = "".join(ch for ch in name if ch.isdigit()) or "1"
        sizes = [int(run.font.size) for run in paragraph.runs if run.font.size]
        if not sizes:
            continue
        out.setdefault(f"heading{level}", {"size": _pt(max(sizes))})
    return dict(sorted(out.items())[:3])


def _markers(docx) -> list[tuple[str, str]]:
    """문단 첫 글자가 말머리 기호인 경우를 모아 깊이별 (기호, 들여쓰기)로."""
    found: dict[float, Counter] = {}
    for paragraph in docx.paragraphs:
        text = paragraph.text.strip()
        if not text or text[0] not in _MARKER_CHARS:
            continue
        indent = float(paragraph.paragraph_format.left_indent or 0)
        found.setdefault(indent, Counter())[text[0]] += 1
    levels = []
    for indent in sorted(found):
        marker = found[indent].most_common(1)[0][0]
        levels.append((marker, f"{emu_to_mm(indent):.0f}mm"))
    return levels[:5]


def _table_style(docx) -> dict:
    sizes = Counter()
    shading = None
    for table in docx.tables:
        for row in table.rows:
            for cell in row.cells:
                shd = cell._tc.find(qn("w:tcPr"))
                if shading is None and shd is not None:
                    element = shd.find(qn("w:shd"))
                    if element is not None and element.get(qn("w:fill")) not in (None, "auto"):
                        shading = element.get(qn("w:fill"))
                for paragraph in cell.paragraphs:
                    for run in paragraph.runs:
                        if run.font.size and run.text.strip():
                            sizes[int(run.font.size)] += len(run.text)
    base = sizes.most_common(1)[0][0] if sizes else None
    if base:
        pt = emu_to_pt(base)
        ladder = [f"{value:.10g}pt" for value in
                  sorted({pt, pt - 0.5, pt - 1, pt - 1.5, pt - 2}, reverse=True)
                  if value >= 8]
    else:
        ladder = ["10pt", "9.5pt", "9pt", "8.5pt", "8pt"]
    return {
        "font_size": _pt(base) if base else "10pt",
        "shading": shading,
        "ladder": "[" + ", ".join(ladder) + "]",
    }
