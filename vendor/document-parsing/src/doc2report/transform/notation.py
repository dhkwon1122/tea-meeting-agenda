"""표기 통일 — 날짜·숫자·띄어쓰기·잔재 제거.

규칙은 rules/notation.yaml 에 있고 이 파일은 적용기다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .stylize_ko import RULES_DIR, Change

_DATE_PATTERNS = [
    re.compile(r"(?<!\d)(\d{4})\s*[-./]\s*(\d{1,2})\s*[-./]\s*(\d{1,2})\.?(?!\d)"),
    re.compile(r"(?<!\d)(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일"),
]


@dataclass
class NotationRules:
    date_enabled: bool = True
    date_format: str = "{y}. {m}. {d}."
    date_pad: bool = False
    thousands: bool = True
    units: list[str] = field(default_factory=list)
    strip_patterns: list[str] = field(default_factory=list)
    replacements: list[tuple[str, str]] = field(default_factory=list)
    spacing: list[tuple[str, str]] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path | None = None) -> NotationRules:
        path = path or RULES_DIR / "notation.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        date = data.get("date", {})
        numbers = data.get("numbers", {})
        return cls(
            date_enabled=date.get("enabled", True),
            date_format=date.get("format", "{y}. {m}. {d}."),
            date_pad=date.get("pad", False),
            thousands=numbers.get("thousands_separator", True),
            units=list(numbers.get("units", [])),
            strip_patterns=list(data.get("strip_patterns", [])),
            replacements=[(a, b) for a, b in data.get("replacements", [])],
            spacing=[(a, b) for a, b in data.get("spacing", [])],
        )


class Notation:
    def __init__(self, rules: NotationRules | None = None):
        self.rules = rules or NotationRules.load()
        self.changes: list[Change] = []
        self._strip = [re.compile(p) for p in self.rules.strip_patterns]
        self._spacing = [(re.compile(p), r) for p, r in self.rules.spacing]
        units = "|".join(re.escape(u) for u in self.rules.units)
        self._number = (re.compile(rf"(?<![\d,.])(\d{{4,}})(\s*)({units})") if units else None)

    def apply(self, text: str) -> str:
        if not text.strip():
            return text
        original = text

        for pattern in self._strip:
            text = pattern.sub("", text)
        for before, after in self.rules.replacements:
            text = text.replace(before, after)
        if self.rules.date_enabled:
            text = self._dates(text)
        if self.rules.thousands and self._number:
            text = self._number.sub(self._comma, text)
        for pattern, replacement in self._spacing:
            text = pattern.sub(replacement, text)

        text = text.strip()
        if text != original.strip():
            self.changes.append(Change(original.strip(), text, "표기"))
        return text

    def _dates(self, text: str) -> str:
        def repl(match: re.Match) -> str:
            y, m, d = (int(g) for g in match.groups())
            if not (1 <= m <= 12 and 1 <= d <= 31):
                return match.group(0)
            fmt = self.rules.date_format
            if self.rules.date_pad:
                return fmt.format(y=y, m=f"{m:02d}", d=f"{d:02d}")
            return fmt.format(y=y, m=m, d=d)

        for pattern in _DATE_PATTERNS:
            text = pattern.sub(repl, text)
        return text

    @staticmethod
    def _comma(match: re.Match) -> str:
        return f"{int(match.group(1)):,}{match.group(2)}{match.group(3)}"
