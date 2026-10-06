"""목록 말머리 문자열 생성.

프로파일의 marker/ordered_marker에 들어간 자리표시자를 실제 문자로 바꾼다.
  {n} 1,2,3   {hangul} 가,나,다   {alpha} a,b,c   {roman} i,ii,iii   {circled} ①②③
어떤 체계를 쓸지는 프로파일이 정하고, 이 모듈은 변환만 한다.
"""

from __future__ import annotations

import re

_HANGUL = "가나다라마바사아자차카타파하"
_CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮"
_PLACEHOLDER = re.compile(r"\{(n|hangul|alpha|ALPHA|roman|ROMAN|circled)\}")


def format_marker(template: str, number: int) -> str:
    """'{hangul}.' + 2 → '나.'"""
    if not template:
        return ""
    return _PLACEHOLDER.sub(lambda m: _convert(m.group(1), number), template)


def _convert(kind: str, n: int) -> str:
    if kind == "n":
        return str(n)
    if kind == "hangul":
        return _cycle(_HANGUL, n)
    if kind == "alpha":
        return _cycle("abcdefghijklmnopqrstuvwxyz", n)
    if kind == "ALPHA":
        return _cycle("ABCDEFGHIJKLMNOPQRSTUVWXYZ", n)
    if kind == "circled":
        return _CIRCLED[n - 1] if 1 <= n <= len(_CIRCLED) else str(n)
    if kind in ("roman", "ROMAN"):
        value = _roman(n)
        return value if kind == "ROMAN" else value.lower()
    return str(n)


def _cycle(alphabet: str, n: int) -> str:
    """범위를 넘으면 '가가', '나나'처럼 반복해 번호가 겹치지 않게 한다."""
    if n <= 0:
        return alphabet[0]
    index = (n - 1) % len(alphabet)
    repeat = (n - 1) // len(alphabet) + 1
    return alphabet[index] * repeat


def _roman(n: int) -> str:
    table = ((1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"),
             (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"))
    out = []
    for value, symbol in table:
        while n >= value:
            out.append(symbol)
            n -= value
    return "".join(out) or "I"
