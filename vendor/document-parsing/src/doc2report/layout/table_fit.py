"""표를 A4 폭에 밀어 넣는 엔진 — 이 프로젝트의 핵심.

브라우저의 CSS 자동 테이블 레이아웃과 비슷하되, 사내 보고서 관례에 맞게 세 가지를 바꿨다.
  1. 열 폭은 '내용'이 정한다. 머리행은 최소 폭(끊을 수 없는 덩어리)만 요구하고
     자연 폭은 요구하지 않는다 — 값은 짧은데 제목만 긴 열이 넓어지지 않도록.
  2. 폭을 나눌 때 열 크기를 비슷하게 맞춘다(수위 채우기).
     남으면 좁은 열부터 같은 높이까지 채우고, 모자라면 넓은 열부터 같은 높이로 깎는다.
  3. 안 들어가면 (글자 크기, 장평) 조합을 '글자 폭이 덜 줄어드는' 순서로 내려간다
     → 셀 여백 ↓ → (허용 시) 가로 페이지.
폭을 정한 뒤 머리가 tables.max_header_lines보다 길게 줄바꿈되면 머리 문구를 축약한다
(규칙은 rules/abbreviations.yaml, 결과는 --report에 남는다).
확정된 폭은 렌더러가 tblLayout=fixed + tcW로 못 박아 Word가 다시 흐트러뜨리지 못하게 한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..ir import Cell, Document, Heading, ListItem, Paragraph, Table, iter_tables, plain
from ..profile import Profile
from ..transform.abbreviate import default_abbreviator
from ..units import fmt_pt
from .measure import TextMeasurer

_BISECT_STEPS = 60


@dataclass
class TableLayout:
    """한 표에 대한 확정된 배치. 렌더러는 이 값을 그대로 쓴다."""

    col_widths: list[int]  # EMU
    font_size: int  # EMU
    cell_margin_x: int  # EMU
    char_scale: float = 1.0  # 장평
    landscape: bool = False
    overflow: bool = False  # 최소 폭조차 못 맞춰 강제로 줄인 경우
    notes: list[str] = field(default_factory=list)
    # (행 번호, 행 안의 칸 번호) → (원래 머리, 축약한 머리)
    header_text: dict[tuple[int, int], tuple[str, str]] = field(default_factory=dict)
    # (행 번호, 행 안의 칸 번호) → 정렬 재지정 (내용이 많아 여러 줄인 셀은 왼쪽 맞춤이 낫다)
    cell_align: dict[tuple[int, int], str] = field(default_factory=dict)
    # (행 번호, 행 안의 칸 번호) → (글자 크기, 장평) 재지정. 표 전체 크기는 그대로 두고
    # 유난히 내용이 많은 셀만 더 줄인다(다른 셀까지 덩달아 작아지지 않게).
    cell_font: dict[tuple[int, int], tuple[int, float]] = field(default_factory=dict)
    # 표 왼쪽 끝 = 표 바로 윗줄 문장의 왼쪽 끝(본문 영역 왼쪽에서 잰 EMU). 표 폭은 여기서 오른쪽 여백까지
    indent: int = 0

    @property
    def total_width(self) -> int:
        return sum(self.col_widths)


def plan_tables(doc: Document, profile: Profile) -> dict[int, TableLayout]:
    """문서 안 모든 표의 배치를 계산해 id(table) → TableLayout 으로 돌려준다.

    IR에는 서식·치수를 넣지 않기 위해 별도 사이드 테이블로 관리한다.
    """
    available = int(profile.page.usable_width * profile.tables.width_ratio)
    indents = table_indents(doc, profile)
    layouts = {}
    for table in iter_tables(doc):
        indent = min(indents.get(id(table), 0), available // 2)  # 아주 깊은 단계라도 반 폭은 남긴다
        layout = fit_table(table, profile, available - indent)
        layout.indent = 0 if layout.landscape else indent
        layouts[id(table)] = layout
    return layouts


def table_indents(doc: Document, profile: Profile) -> dict[int, int]:
    """본문 표마다 '바로 윗줄 문장의 왼쪽 끝'(2026-09-30 사용자: 표 폭은 표 위 제목 문장의 왼쪽
    끝선부터 오른쪽 여백까지). 말머리 항목은 말머리가 찍히는 자리(단계 들여쓰기) — 렌더러가
    `set_list_indent`로 첫 줄을 거기서 시작한다. 제목·일반 문단은 0. ※ 참고 문단은 윗줄보다 더
    들여 쓴 부속 줄이라 기준으로 삼지 않는다."""
    rules = profile.text
    notes = tuple(rules.note_marks) if rules.note_indent else ()
    out: dict[int, int] = {}
    start = 0
    for block in doc.blocks:
        if isinstance(block, ListItem):
            if not (notes and (block.marker or plain(block.runs).lstrip()).startswith(notes)):
                start = profile.numbering_level(block.depth).indent or 0
        elif isinstance(block, Paragraph):
            if not (notes and plain(block.runs).lstrip().startswith(notes)):
                start = 0
        elif isinstance(block, Heading):
            start = 0
        elif isinstance(block, Table):
            out[id(block)] = start
    return out


def fit_table(table: Table, profile: Profile, available_width: int) -> TableLayout:
    layout = _fit_widths(table, profile, available_width)
    _abbreviate_headers(table, profile, layout)
    _left_align_long_cells(table, profile, layout)
    _shrink_long_cells(table, profile, layout)
    _unify_column_fonts(table, layout)
    _balance_table_font(profile, layout)
    _drop_redundant_cell_fonts(layout)
    _stretch_to(layout, available_width)
    return layout


def _stretch_to(layout: TableLayout, available_width: int) -> None:
    """폭 계산은 글꼴 메트릭 오차에 대비해 안전 여유(safety_margin)를 두고 하지만, 표 자체는
    주어진 폭을 끝까지 쓴다(2026-09-30 사용자: 표 폭은 최대한). 남은 여유를 열 폭 비율대로 나눠
    준다 — 열이 넓어질 뿐이라 줄바꿈이 늘지 않는다. 가로 쪽·강제 축소한 표는 그대로."""
    total = layout.total_width
    if layout.landscape or layout.overflow or total <= 0 or total >= available_width:
        return
    widths = [w * available_width // total for w in layout.col_widths]
    widths[-1] += available_width - sum(widths)
    layout.col_widths = widths


def _unify_column_fonts(table: Table, layout: TableLayout) -> None:
    """글자 크기도 정렬처럼 **열 단위**로 맞춘다(2026-09-29 사용자 요청). 같은 열에서 셀마다
    크기가 다르면 들쭉날쭉해 보인다 — 열에 줄인 셀이 하나라도 있으면 그 열의 본문 셀 전부가
    그 열에서 가장 작은 크기를 쓴다. 머리행은 행 단위로 표 크기를 유지하고, 여러 열에 걸친
    병합 셀은 열 전체를 좌우하지 못하게 자기 값만 쓴다."""
    if not layout.cell_font:
        return
    body = [(ri, ci, col) for ri, ci, cell, col, span in _iter_grid_indexed(table)
            if not cell.is_header and span == 1]
    smallest: dict[int, tuple[int, float]] = {}
    for ri, ci, col in body:
        step = layout.cell_font.get((ri, ci))
        if step is not None and (col not in smallest
                                 or step[0] * step[1] < smallest[col][0] * smallest[col][1]):
            smallest[col] = step
    for ri, ci, col in body:
        if col in smallest:
            layout.cell_font[(ri, ci)] = smallest[col]
    for col, (size, _) in sorted(smallest.items()):
        layout.notes.append(f"열 {col + 1}: 글자 크기를 {fmt_pt(size)}로 열 전체 통일")


def _drop_redundant_cell_fonts(layout: TableLayout) -> None:
    """표 전체 크기가 내려가서 표 크기와 같거나 커진 셀별 크기는 뺀다(표 크기가 곧 그 값)."""
    base = layout.font_size * layout.char_scale
    for key in [k for k, (size, scale) in layout.cell_font.items() if size * scale >= base]:
        del layout.cell_font[key]


def _balance_table_font(profile: Profile, layout: TableLayout) -> None:
    """바쁜 셀 하나만 많이 줄면(예: 표는 12pt인데 그 셀만 9pt) 불균형해 보인다
    (2026-09-29 사용자 지적: "12pt·9pt는 불균형, 11pt·9pt 정도가 낫다"). 표 전체
    크기와 가장 작은 셀 크기의 차이가 `tables.max_font_spread`를 넘으면 표
    전체를 그만큼 낮춘다 — 이미 맞는 폭에서 더 작게만 바꾸는 것이라 다시
    맞춰 볼 필요 없이 그대로 적용 가능하다."""
    spread = profile.tables.max_font_spread
    if not spread or not layout.cell_font:
        return
    min_cell_size = min(size for size, _ in layout.cell_font.values())
    if layout.font_size - min_cell_size <= spread:
        return
    ladder = profile.table_font_ladder()
    candidates = [s for s in ladder if s <= min_cell_size + spread]
    target = max(candidates) if candidates else min(ladder)
    if target < layout.font_size:
        old = layout.font_size
        layout.font_size = target
        layout.notes.append(
            f"셀별 글자 크기 차이를 {fmt_pt(spread)} 이내로 맞추려 표 전체 크기 "
            f"{fmt_pt(old)} → {fmt_pt(target)}로 낮춤"
        )


def _shrink_long_cells(table: Table, profile: Profile, layout: TableLayout) -> None:
    """유난히 내용이 많은 셀은 표 전체 크기는 그대로 두고 그 셀만 더 줄인다
    (2026-09-29 사용자 요청). 표 전체를 검사하는 `_max_cell_lines`는 셀 하나의
    **가장 긴 한 줄**이 폭 때문에 여러 줄로 쪼개지는 것만 본다(원래 목적: 열이
    1글자로 좁아지는 것 방지) — `<br>`/문단이 여러 개라 원래도 줄이 많은 셀은
    아무리 폭이 넓어도 그 수가 안 줄어 이 검사에 안 걸린다. 그래서 총 줄 수를
    따로 세어, 표 전체 크기보다 작은 후보 중 큰 것부터 시도해 맞는 걸 쓰고
    (표에 있는 다른 셀은 그대로 두어야 하니 전체를 다시 줄이지 않는다),
    끝까지 못 맞추면 사다리의 가장 작은 값으로 멈춘다."""
    limit = profile.tables.max_cell_lines
    if not limit:
        return
    base = (layout.font_size, layout.char_scale)
    smaller = sorted((s for s in profile.table_steps() if s[0] * s[1] < base[0] * base[1]),
                     key=lambda s: -(s[0] * s[1]))
    if not smaller:
        return
    content = [w - layout.cell_margin_x * 2 for w in layout.col_widths]

    for row_index, cell_index, cell, col, span in _iter_grid_indexed(table):
        if cell.is_header or col >= len(content):
            continue
        width = sum(content[col: min(col + span, len(content))])
        if _cell_total_lines(cell, profile, width, *base) <= limit:
            continue
        chosen = smaller[-1]  # 못 맞추면 사다리의 가장 작은 값
        for step in smaller:
            if _cell_total_lines(cell, profile, width, *step) <= limit:
                chosen = step
                break
        layout.cell_font[(row_index, cell_index)] = chosen


def _cell_total_lines(cell: Cell, profile: Profile, width: float, size: int, scale: float) -> int:
    measurer = _measurer(cell, profile, size, scale)
    return sum(measurer.wrap_count(line, width) for line in cell_lines(cell))


def _left_align_long_cells(table: Table, profile: Profile, layout: TableLayout) -> None:
    """내용이 여러 줄인 셀은 가운데 정렬이면 줄마다 들쭉날쭉해 읽기 불편하다 —
    max_cell_lines(기본 3)줄 이상이 되는 셀이 있으면 **그 열의 본문 셀 전부**를 왼쪽 맞춤으로
    바꾼다(2026-09-29 사용자 요청). 셀마다 따로 정하면 같은 열 안에서 정렬이 섞여 보인다.
    머리행은 제 서식(가운데)을 유지하고, 저자가 정렬을 지정한 셀(cell.align)도 건드리지 않는다.
    병합 셀은 자기 자신만 판단한다(여러 열에 걸쳐 열 전체를 좌우하지 못하게)."""
    limit = profile.tables.max_cell_lines
    if not limit:
        return
    content = [w - layout.cell_margin_x * 2 for w in layout.col_widths]
    body = [(ri, ci, cell, col, span) for ri, ci, cell, col, span in _iter_grid_indexed(table)
            if not cell.is_header and col < len(content)]

    long_cells: set[tuple[int, int]] = set()
    long_cols: set[int] = set()
    for row_index, cell_index, cell, col, span in body:
        width = sum(content[col: min(col + span, len(content))])
        if _cell_total_lines(cell, profile, width, layout.font_size, layout.char_scale) >= limit:
            long_cells.add((row_index, cell_index))
            if span == 1:
                long_cols.add(col)

    for row_index, cell_index, cell, col, span in body:
        if cell.align:
            continue
        if (row_index, cell_index) in long_cells or (span == 1 and col in long_cols):
            layout.cell_align[(row_index, cell_index)] = "left"


def _fit_widths(table: Table, profile: Profile, available_width: int) -> TableLayout:
    rules = profile.tables
    steps = profile.table_steps()
    margins = [rules.cell_margin_x]
    if rules.cell_margin_x_min is not None and rules.cell_margin_x_min < rules.cell_margin_x:
        margins.append(rules.cell_margin_x_min)

    widths = [available_width]
    if rules.allow_landscape:
        widths.append(int(profile.page.landscape().usable_width * rules.width_ratio))

    base = steps[0]

    # 1차: 폭도 맞고 셀 줄 수도 읽을 만한 조합을 위에서부터 찾는다.
    for page_idx, avail in enumerate(widths):
        for step in steps:
            for margin in margins:
                attempt = _try_fit(table, profile, avail, step, margin, check_lines=True)
                if attempt is not None:
                    attempt.landscape = page_idx > 0
                    _annotate(attempt, base, step, rules.cell_margin_x, margin)
                    return attempt

    # 2차: 줄 수 제약은 못 지키더라도 폭은 맞춘다. 줄 수는 글자가 좁을수록 적으므로 최소 조합으로.
    for page_idx, avail in enumerate(widths):
        attempt = _try_fit(table, profile, avail, steps[-1], margins[-1], check_lines=False)
        if attempt is not None:
            attempt.landscape = page_idx > 0
            _annotate(attempt, base, steps[-1], rules.cell_margin_x, margins[-1])
            if rules.max_cell_lines:
                attempt.notes.append(
                    f"셀 줄 수가 {rules.max_cell_lines}줄을 넘어 가장 좁은 글자 폭을 적용함"
                )
            return attempt

    # 3차: 최소 폭조차 넘치면 비례 축소 (텍스트는 세로로 길어질 뿐 잘리지 않음)
    result = _force_fit(table, profile, widths[-1], steps[-1], margins[-1])
    result.landscape = len(widths) > 1
    _annotate(result, base, steps[-1], rules.cell_margin_x, margins[-1])
    result.notes.append("가장 좁은 글자 폭으로도 최소 폭을 맞출 수 없어 비례 축소함 (줄바꿈이 늘어남)")
    return result


def _annotate(layout: TableLayout, base: tuple[int, float], step: tuple[int, float],
              base_margin: int, margin: int) -> None:
    if step[0] != base[0]:
        layout.notes.append(f"표 글자 크기 {fmt_pt(base[0])} → {fmt_pt(step[0])} 하향")
    if step[1] != base[1]:
        layout.notes.append(f"표 장평 {base[1]:.0%} → {step[1]:.0%} 축소")
    if margin != base_margin:
        layout.notes.append(f"셀 여백 {fmt_pt(base_margin)} → {fmt_pt(margin)} 축소")
    if layout.landscape:
        layout.notes.append("가로 방향 페이지로 전환")


def _try_fit(
    table: Table, profile: Profile, available: int, step: tuple[int, float], margin: int,
    *, check_lines: bool = True,
) -> TableLayout | None:
    """주어진 (글자 크기, 장평)·여백에서 배치 가능하면 TableLayout, 아니면 None."""
    size, scale = step
    cols = table.col_count
    if cols == 0:
        return None
    usable = _content_budget(profile, available, cols, margin)
    if usable <= 0:
        return None

    mins, maxs = column_demands(table, profile, size, scale, content_first=True)
    if sum(maxs) <= usable:
        widths = _fill_up(maxs, usable)
    elif sum(mins) <= usable:
        widths = _cap_down(mins, maxs, usable)
    else:
        return None

    limit = profile.tables.max_cell_lines
    if check_lines and limit and _max_cell_lines(table, profile, size, scale, widths) > limit:
        return None

    return TableLayout(
        col_widths=_to_cell_widths(widths, margin),
        font_size=size,
        cell_margin_x=margin,
        char_scale=scale,
    )


def _fill_up(maxs: list[float], target: float) -> list[float]:
    """남는 폭을 좁은 열부터 채운다 — 모든 열이 최소 L이 되도록 (넓은 열은 그대로)."""
    level = _bisect(lambda lv: sum(max(mx, lv) for mx in maxs), target, max(target, 1))
    return _scale_to([max(mx, level) for mx in maxs], target)


def _cap_down(mins: list[float], maxs: list[float], target: float) -> list[float]:
    """모자란 폭을 넓은 열부터 깎는다 — 넓은 열들이 같은 폭 L로 맞춰진다."""
    level = _bisect(lambda lv: sum(max(mn, min(mx, lv)) for mn, mx in zip(mins, maxs)),
                    target, max(maxs, default=1))
    return _scale_to([max(mn, min(mx, level)) for mn, mx in zip(mins, maxs)], target)


def _bisect(total_at, target: float, high: float) -> float:
    """total_at(level)이 target이 되는 level (total_at은 단조 증가)."""
    low = 0.0
    for _ in range(_BISECT_STEPS):
        mid = (low + high) / 2
        if total_at(mid) < target:
            low = mid
        else:
            high = mid
    return high


def _max_cell_lines(
    table: Table, profile: Profile, size: int, scale: float, content_widths: list[float]
) -> int:
    """확정 폭에서 가장 많이 줄바꿈되는 '내용' 셀의 줄 수. (머리는 축약으로 따로 다룬다)

    열을 1글자 폭까지 좁혀 '폭은 맞지만 세로로 한없이 길어지는' 표를 막는 기준이다.
    """
    worst = 0
    for cell, col, span in iter_grid(table):
        if col >= len(content_widths) or (cell.is_header and _has_body(table)):
            continue
        width = sum(content_widths[col : min(col + span, len(content_widths))])
        measurer = _measurer(cell, profile, size, scale)
        for line in cell_lines(cell):
            worst = max(worst, measurer.wrap_count(line, width))
    return worst


def _force_fit(
    table: Table, profile: Profile, available: int, step: tuple[int, float], margin: int
) -> TableLayout:
    """어떤 조합으로도 min 합을 못 맞출 때: 최소 폭을 비례 축소해서라도 넣는다."""
    size, scale = step
    cols = max(1, table.col_count)
    usable = max(_content_budget(profile, available, cols, margin), cols * 1)
    mins, _ = column_demands(table, profile, size, scale, content_first=True)
    widths = _scale_to(mins, usable)
    return TableLayout(
        col_widths=_to_cell_widths(widths, margin),
        font_size=size,
        cell_margin_x=margin,
        char_scale=scale,
        overflow=True,
    )


def _content_budget(profile: Profile, available: int, cols: int, margin: int) -> int:
    """텍스트가 실제로 쓸 수 있는 폭 = 표 폭 − 셀 여백 − 테두리 − 안전 여유."""
    rules = profile.tables
    border = (rules.border_width or 0) * (cols + 1)
    padding = margin * 2 * cols
    return int(available * (1 - rules.safety_margin)) - padding - border


def _to_cell_widths(content_widths: list[float], margin: int) -> list[int]:
    """내용 폭 → 셀 전체 폭(여백 포함). docx의 tcW는 여백을 포함한 값이다."""
    return [int(round(w)) + margin * 2 for w in content_widths]


def _scale_to(widths: list[float], target: float) -> list[float]:
    total = sum(widths)
    if total <= 0:
        return [target / max(1, len(widths))] * len(widths)
    factor = target / total
    return [w * factor for w in widths]


# ── 머리 축약 ───────────────────────────────────────────────────────────


def _abbreviate_headers(table: Table, profile: Profile, layout: TableLayout) -> None:
    limit = profile.tables.max_header_lines
    if not limit or not _has_body(table):
        return
    content = [w - layout.cell_margin_x * 2 for w in layout.col_widths]
    abbreviator = default_abbreviator()
    row_texts: dict[int, list[str]] = {}
    for row_index, _, cell, _, _ in _iter_grid_indexed(table):
        if cell.is_header:
            row_texts.setdefault(row_index, []).append(" ".join(cell_lines(cell)))

    for row_index, cell_index, cell, col, span in _iter_grid_indexed(table):
        if not cell.is_header or col >= len(content):
            continue
        lines = [line for line in cell_lines(cell) if line]
        if len(lines) != 1:
            continue  # 여러 문단짜리 머리는 사람이 일부러 그렇게 쓴 것 — 건드리지 않는다
        text = lines[0]
        width = sum(content[col : min(col + span, len(content))])
        measurer = _measurer(cell, profile, layout.font_size, layout.char_scale)
        if measurer.wrap_count(text, width) <= limit:
            continue
        candidates = abbreviator.candidates(text, row_texts.get(row_index, []))
        if not candidates:
            continue
        # 줄 수 안에 드는 첫 후보. 없으면 줄 수가 가장 적은 것 중 덜 줄인 것.
        chosen = next((c for c in candidates if measurer.wrap_count(c, width) <= limit),
                      min(candidates, key=lambda c: measurer.wrap_count(c, width)))
        layout.header_text[(row_index, cell_index)] = (text, chosen)


# ── 열별 요구 폭 ────────────────────────────────────────────────────────


def column_demands(
    table: Table, profile: Profile, size: int, scale: float = 1.0,
    *, content_first: bool = False,
) -> tuple[list[float], list[float]]:
    """각 열의 (최소 폭, 자연 폭). colspan은 걸친 열들에 나눠 반영한다.

    content_first면 머리행은 최소 폭만 요구한다 (열 폭은 내용이 정한다).
    """
    cols = table.col_count
    mins = [0.0] * cols
    maxs = [0.0] * cols
    spans: list[tuple[int, int, float, float]] = []  # (start, span, min, max)
    header_min_only = content_first and _has_body(table)

    for cell, col, span in iter_grid(table):
        if col >= cols:
            continue
        mn, mx = _cell_demand(cell, profile, size, scale)
        if header_min_only and cell.is_header:
            # 머리는 자연 폭을 요구하지 않되, 어절 중간에서 끊기지는 않게("순/번" 방지).
            mn = mx = max(mn, _longest_eojeol(cell, profile, size, scale))
        if span == 1:
            mins[col] = max(mins[col], mn)
            maxs[col] = max(maxs[col], mx)
        else:
            spans.append((col, span, mn, mx))

    for col, span, mn, mx in spans:
        end = min(col + span, cols)
        for target, value in ((mins, mn), (maxs, mx)):
            current = sum(target[col:end])
            if current < value:
                extra = (value - current) / (end - col)
                for k in range(col, end):
                    target[k] += extra

    for i in range(cols):
        maxs[i] = max(maxs[i], mins[i])
    return mins, maxs


def iter_grid(table: Table):
    """(셀, 시작 열, colspan) 순회 — rowspan으로 밀리는 자리를 고려."""
    for _, _, cell, col, span in _iter_grid_indexed(table):
        yield cell, col, span


def _iter_grid_indexed(table: Table):
    """(행 번호, 행 안의 칸 번호, 셀, 시작 열, colspan)."""
    occupied: dict[tuple[int, int], bool] = {}
    for r, row in enumerate(table.rows):
        col = 0
        for index, cell in enumerate(row.cells):
            while occupied.get((r, col)):
                col += 1
            for rr in range(r, r + cell.rowspan):
                for cc in range(col, col + cell.colspan):
                    occupied[(rr, cc)] = True
            yield r, index, cell, col, cell.colspan
            col += cell.colspan


def _has_body(table: Table) -> bool:
    return any(not cell.is_header for row in table.rows for cell in row.cells)


def _measurer(cell: Cell, profile: Profile, size: int, scale: float) -> TextMeasurer:
    spec = profile.font("table_header" if cell.is_header else "table")
    return TextMeasurer(spec.east_asia, spec.latin, size, bold=bool(spec.bold), scale=scale)


def _cell_demand(cell: Cell, profile: Profile, size: int, scale: float) -> tuple[float, float]:
    measurer = _measurer(cell, profile, size, scale)
    mn = mx = 0.0
    for line in cell_lines(cell):
        mn = max(mn, measurer.longest_word_width(line))
        mx = max(mx, measurer.width(line))
    return mn, mx


def _longest_eojeol(cell: Cell, profile: Profile, size: int, scale: float) -> float:
    measurer = _measurer(cell, profile, size, scale)
    return max((measurer.width(word) for line in cell_lines(cell) for word in line.split()),
               default=0.0)


def cell_lines(cell: Cell) -> list[str]:
    """셀 안 텍스트를 줄 단위로. (셀 안의 표는 폭 계산에서 제외 — 드물고 재귀가 복잡)"""
    lines: list[str] = []
    for block in cell.blocks:
        if isinstance(block, Paragraph):
            text = plain(block.runs).strip()
            if text:
                lines.append(text)
        elif hasattr(block, "runs"):
            text = plain(block.runs).strip()
            if text:
                lines.append(text)
        elif hasattr(block, "text"):
            lines.extend(str(block.text).splitlines())
    return lines or [""]
