"""IR → Markdown (.md 출력).

Word와 **같은 내용·같은 말머리**가 나오게 한다 — 규칙·말머리·표 제목을 모두 적용한 뒤의 IR을
쓰므로, Word 문서를 Confluence·메신저·LLM에 다시 붙여 넣거나 Git에서 비교할 때 쓴다.
글자 크기·여백 같은 서식은 Markdown에 없으므로 버린다.

- 말머리 항목: "1. □ -" 체계를 그대로 글자로 쓰고, 단계는 전각 공백(U+3000)으로 들여쓴다.
  보통 공백 4칸은 Markdown에서 코드 블록이 되고, Markdown 목록 문법("- ")으로 쓰면
  원문 말머리(□, ㆍ)가 사라지기 때문이다.
- 표: GitHub 표. 병합 셀은 Markdown에 표현이 없어 빈 칸으로 채운다.
- 이미지: <이름>_files/ 폴더로 복사하고 상대 경로로 건다.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from ..ir import (
    Block,
    Callout,
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
from ..profile import Profile
from .markers import format_marker

_INDENT = "\u3000"


def write_markdown(doc: Document, profile: Profile, path: str | Path) -> Path:
    path = Path(path)
    writer = _Writer(profile, path)
    path.write_text(writer.document(doc), encoding="utf-8")
    return path


def render_markdown(doc: Document, profile: Profile) -> str:
    return _Writer(profile, None).document(doc)


class _Writer:
    def __init__(self, profile: Profile, path: Path | None):
        self.profile = profile
        self.path = path
        self.counters: dict[int, int] = {}
        self.images = 0

    def document(self, doc: Document) -> str:
        parts = [f"# {doc.title}"] if doc.title else []
        parts += [text for block in doc.blocks if (text := self.block(block))]
        return "\n\n".join(parts).rstrip() + "\n"

    def block(self, block: Block) -> str:
        if isinstance(block, Heading):
            self.counters.clear()
            return "#" * block.level + " " + inline(block.runs)
        if isinstance(block, Paragraph):
            return _escape_start(inline(block.runs))
        if isinstance(block, ListItem):
            return self.item(block)
        if isinstance(block, Table):
            return self.table(block)
        if isinstance(block, CodeBlock):
            return f"```{block.lang or ''}\n{block.text}\n```"
        if isinstance(block, Callout):
            inner = "\n\n".join(t for b in block.blocks if (t := self.block(b)))
            return "\n".join("> " + line if line else ">" for line in inner.splitlines())
        if isinstance(block, Image):
            return self.image(block)
        if isinstance(block, HorizontalRule):
            return "---"
        if isinstance(block, PageBreak):
            return ""
        return ""

    def item(self, block: ListItem) -> str:
        level = self.profile.numbering_level(block.depth)
        template = (level.ordered_marker if block.ordered and level.ordered_marker
                    else level.marker) or ""
        if block.marker is not None:
            if "{" in template and block.marker:
                self._next(block.depth)
            marker = block.marker
        elif "{" in template:
            marker = format_marker(template, self._next(block.depth))
        else:
            marker = template
        text = inline(block.runs)
        if block.from_heading or (self.profile.text.level_bold and level.bold):
            text = _bold_whole(text)
        head = _INDENT * block.depth + (f"{marker} " if marker else "")
        return _escape_start(head + text)

    def _next(self, depth: int) -> int:
        for deeper in [d for d in self.counters if d > depth]:
            del self.counters[deeper]
        self.counters[depth] = self.counters.get(depth, 0) + 1
        return self.counters[depth]

    def table(self, block: Table) -> str:
        grid = _grid(block)
        if not grid:
            return ""
        width = max(len(row) for row in grid)
        rows = [row + [""] * (width - len(row)) for row in grid]
        header = rows[0] if block.header_rows else [""] * width
        body = rows[1:] if block.header_rows else rows
        lines = ["| " + " | ".join(header) + " |", "|" + "---|" * width]
        lines += ["| " + " | ".join(row) + " |" for row in body]
        out = []
        if block.caption:
            out.append(_escape_start(block.caption))
        out.append("\n".join(lines))
        marker = self.profile.tables.note_marker or "*"
        out += [f"{marker} " + inline(note) for note in block.notes]
        return "\n\n".join(out)

    def image(self, block: Image) -> str:
        src = Path(block.src)
        if self.path is None or not src.exists():
            target = block.src
        else:
            folder = self.path.with_name(self.path.stem + "_files")
            folder.mkdir(parents=True, exist_ok=True)
            self.images += 1
            dest = folder / f"image{self.images}{src.suffix}"
            shutil.copyfile(src, dest)
            target = f"{folder.name}/{dest.name}"
        return f"![{block.caption or ''}]({target.replace(' ', '%20')})"


def _grid(table: Table) -> list[list[str]]:
    """병합 셀을 펼친 격자. 병합으로 덮인 자리는 빈 칸."""
    grid: list[list[str]] = []
    pending: dict[tuple[int, int], str] = {}
    for r, row in enumerate(table.rows):
        out: list[str] = []
        col = 0
        cells = iter(row.cells)
        while True:
            while (r, col) in pending:
                out.append(pending.pop((r, col)))
                col += 1
            cell = next(cells, None)
            if cell is None:
                break
            out.append(_cell_text(cell.blocks))
            for extra in range(1, cell.colspan):
                out.append("")
            for down in range(1, cell.rowspan):
                for c in range(col, col + cell.colspan):
                    pending[(r + down, c)] = ""
            col += cell.colspan
        grid.append(out)
    return grid


def _cell_text(blocks: list[Block]) -> str:
    lines = []
    for block in blocks:
        if isinstance(block, (Paragraph, Heading)):
            text = inline(block.runs)
            lines.append(f"**{text}**" if isinstance(block, Heading) and text else text)
        elif isinstance(block, ListItem):
            lines.append(((block.marker or "-") + " ") + inline(block.runs))
        elif isinstance(block, Table):
            lines.append(" / ".join(" ".join(row) for row in _grid(block)))
        elif isinstance(block, CodeBlock):
            lines.append(f"`{block.text}`")
    return "<br>".join(line for line in lines if line).replace("|", "\\|").replace("\n", " ")


def inline(runs: list[Run]) -> str:
    out = []
    for run in runs:
        text = run.text
        if not text:
            continue
        if run.code:
            out.append(f"`{text}`")
            continue
        core = text.strip()
        if not core:
            out.append(text)
            continue
        lead, trail = text[: len(text) - len(text.lstrip())], text[len(text.rstrip()):]
        core = _escape_inline(core)
        if run.italic:
            core = f"*{core}*"
        if run.bold:
            core = f"**{core}**"
        if run.href:
            core = f"[{core}]({run.href})"
        out.append(lead + core + trail)
    return "".join(out)


def _bold_whole(text: str) -> str:
    plain_text = text.replace("**", "")
    return f"**{plain_text}**" if plain_text.strip() else text


def _escape_inline(text: str) -> str:
    return text.replace("\\", "\\\\").replace("*", "\\*").replace("_", "\\_").replace("`", "\\`")


def _escape_start(text: str) -> str:
    """줄 맨 앞 "- ", "1. ", "# ", "> "가 Markdown 문법으로 읽히지 않게."""
    stripped = text.lstrip(_INDENT)
    head = text[: len(text) - len(stripped)]
    if stripped[:2] in ("- ", "+ ", "> ", "# "):
        return head + "\\" + stripped
    digits = len(stripped) - len(stripped.lstrip("0123456789"))
    if 0 < digits <= 9 and stripped[digits:digits + 2] in (". ", ") "):
        return head + stripped[:digits] + "\\" + stripped[digits:]
    return text


__all__ = ["render_markdown", "write_markdown", "inline", "plain"]
