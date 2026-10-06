"""문자열 단위("30mm", "11pt", "160%")를 내부 단위로 바꾸는 유일한 통로.

내부 길이 단위는 EMU(English Metric Unit, 1 inch = 914400 EMU)로 통일한다.
docx가 쓰는 dxa(1/20 pt), pt, mm 변환도 여기 모아 둔다.
소스 어디에도 서식 상수를 두지 않기 위해, 이 모듈만이 단위 리터럴을 안다.
"""

from __future__ import annotations

import re

EMU_PER_INCH = 914400
EMU_PER_PT = EMU_PER_INCH / 72
EMU_PER_MM = EMU_PER_INCH / 25.4
EMU_PER_CM = EMU_PER_MM * 10

# 용지 규격도 '단위를 아는 지식'이라 여기 둔다 (ISO 216 A 계열 등).
PAGE_SIZES: dict[str, tuple[str, str]] = {
    "A4": ("210mm", "297mm"),
    "A3": ("297mm", "420mm"),
    "B5": ("176mm", "250mm"),
    "LETTER": ("8.5in", "11in"),
}

_LENGTH_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*(mm|cm|pt|in|emu|px)?\s*$", re.I)
_PERCENT_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*%\s*$")


def parse_length(value: str | int | float, *, default_unit: str = "pt") -> int:
    """'30mm' → EMU(int). 단위가 없으면 default_unit으로 해석."""
    if isinstance(value, (int, float)):
        return int(round(float(value) * _factor(default_unit)))
    m = _LENGTH_RE.match(str(value))
    if not m:
        raise ValueError(f"길이 값을 해석할 수 없음: {value!r} (예: '30mm', '11pt')")
    num, unit = m.group(1), (m.group(2) or default_unit).lower()
    return int(round(float(num) * _factor(unit)))


def _factor(unit: str) -> float:
    unit = unit.lower()
    if unit == "emu":
        return 1.0
    if unit == "pt":
        return EMU_PER_PT
    if unit == "mm":
        return EMU_PER_MM
    if unit == "cm":
        return EMU_PER_CM
    if unit == "in":
        return float(EMU_PER_INCH)
    if unit == "px":  # 96dpi 가정 — 이미지 크기에만 쓰임
        return EMU_PER_INCH / 96
    raise ValueError(f"알 수 없는 단위: {unit}")


def parse_ratio(value: str | int | float) -> float:
    """'160%' → 1.6, 1.6 → 1.6."""
    if isinstance(value, (int, float)):
        return float(value)
    m = _PERCENT_RE.match(str(value))
    if m:
        return float(m.group(1)) / 100.0
    return float(value)


def emu_to_pt(emu: int | float) -> float:
    return emu / EMU_PER_PT


def emu_to_mm(emu: int | float) -> float:
    return emu / EMU_PER_MM


def emu_to_dxa(emu: int | float) -> int:
    """docx의 twentieth-of-a-point 단위."""
    return int(round(emu_to_pt(emu) * 20))


def emu_to_eighth_pt(emu: int | float) -> int:
    """표 테두리 두께 단위(1/8 pt)."""
    return max(1, int(round(emu_to_pt(emu) * 8)))


def fmt_pt(emu: int | float) -> str:
    """로그·리포트 출력용."""
    pt = emu_to_pt(emu)
    return f"{pt:.10g}pt"
