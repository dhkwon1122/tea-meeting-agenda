"""붙여넣은 글 → Markdown 텍스트 (그다음은 Markdown 파서가 IR로 옮긴다).

메신저·메일·Word에서 복사한 글은 **한 줄이 한 문단**이다. 그대로 Markdown에 넣으면 빈 줄이
없는 줄들이 한 문단으로 합쳐져("□ 항목1 - 항목2") 말머리 구분이 사라진다. 그래서 줄마다
문단을 나눈다. Markdown으로 쓴 글(# 제목, |---| 표, ``` 코드가 있는 글)은 손대지 않고 Markdown으로 읽는다.

일반 글에서 줄 앞의 "- ", "1. "은 Markdown 목록 문법이 아니라 **글쓴이가 친 말머리**다 —
목록으로 읽으면 말머리가 프로파일 것(□ 등)으로 바뀌어 "원문 말머리 유지" 원칙이 깨진다. 그래서
글자 그대로 남도록 이스케이프하고, 단계(들여쓰기)는 제목 접기가 그 말머리를 보고 정한다.

엑셀에서 복사한 표는 칸이 탭으로 나뉘어 들어온다 — 탭으로 나뉜 줄이 두 줄 이상 이어지면
Markdown 표로 바꿔 준다(첫 줄 = 머리행).
"""

from __future__ import annotations

import re

_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_MD_LIST = re.compile(r"^(\s*)([-*+>]|\d{1,9}[.)])(\s+)")
_MARKDOWN_SIGNS = re.compile(r"^(?:#{1,6}\s|```|~~~|\s*\|?\s*:?-{3,}:?\s*\|)", re.MULTILINE)


def looks_like_markdown(text: str) -> bool:
    return bool(_MARKDOWN_SIGNS.search(text))


def text_to_markdown(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if looks_like_markdown(text):
        return text
    lines = text.split("\n")
    out: list[str] = []
    in_fence = False
    i = 0
    while i < len(lines):
        line = lines[i]
        if _FENCE.match(line):
            in_fence = not in_fence
            out.append(line)
            i += 1
            continue
        if in_fence:
            out.append(line)
            i += 1
            continue

        tabbed = _tab_block(lines, i)
        if tabbed:
            _blank(out)
            out.extend(_tab_table(lines[i:i + tabbed]))
            out.append("")
            i += tabbed
            continue

        if _TABLE_ROW.match(line):
            if out and out[-1] and not _TABLE_ROW.match(out[-1]):
                out.append("")
            out.append(line)  # 표 행은 붙여 둬야 한 표가 된다
        elif not line.strip():
            _blank(out)
        else:
            _blank(out)
            out.append(_literal(line))
        i += 1
    return "\n".join(out).strip() + "\n"


def _blank(out: list[str]) -> None:
    if out and out[-1] != "":
        out.append("")


def _literal(line: str) -> str:
    """줄을 글자 그대로 한 문단으로. 앞 공백은 걷고(4칸이면 코드 블록이 된다) 줄 앞 말머리는
    Markdown 목록·인용으로 안 읽히게 이스케이프한다("- 항목" → "\\- 항목")."""
    line = line.strip()
    match = _MD_LIST.match(line)
    if match:
        mark = match.group(2)
        escaped = "\\" + mark if not mark[0].isdigit() else mark[:-1] + "\\" + mark[-1]
        line = escaped + line[len(mark):]
    return line


def _tab_block(lines: list[str], start: int) -> int:
    """start부터 탭으로 나뉜(칸 수가 같은) 줄이 몇 줄 이어지는지. 2줄 미만이면 0."""
    width = lines[start].count("\t")
    if width == 0:
        return 0
    count = 0
    for line in lines[start:]:
        if line.count("\t") != width or not line.strip():
            break
        count += 1
    return count if count >= 2 else 0


def _tab_table(rows: list[str]) -> list[str]:
    cells = [[c.strip().replace("|", "\\|") for c in row.split("\t")] for row in rows]
    header, body = cells[0], cells[1:]
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(row) + " |" for row in body]
    return out
