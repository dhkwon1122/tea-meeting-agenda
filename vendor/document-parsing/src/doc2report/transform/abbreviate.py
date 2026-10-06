"""표 머리 축약 후보 생성기.

언제 줄일지는 layout/table_fit.py가 (열 폭을 정한 뒤) 판단하고, 어떻게 줄일지는
rules/abbreviations.yaml 이 정한다. 여기는 그 규칙을 차례로 겹쳐 적용해
점점 짧아지는 후보 목록을 만들 뿐이다.
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .stylize_ko import RULES_DIR

_PAREN = re.compile(r"\s*[(\[（][^)\]）]*[)\]）]")


@dataclass
class Abbreviator:
    drop_parenthetical: bool = True
    drop_words: list[str] = field(default_factory=list)
    replacements: list[tuple[str, str]] = field(default_factory=list)
    keep_before: list[str] = field(default_factory=list)
    drop_shared_suffix: bool = False
    remove_spaces: bool = False

    @classmethod
    def load(cls, path: Path | None = None) -> Abbreviator:
        path = path or RULES_DIR / "abbreviations.yaml"
        if not path.exists():
            return cls()
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls(
            drop_parenthetical=bool(data.get("drop_parenthetical", True)),
            drop_words=list(data.get("drop_words", [])),
            replacements=[(a, b) for a, b in data.get("replacements", [])],
            keep_before=list(data.get("keep_before", [])),
            drop_shared_suffix=bool(data.get("drop_shared_suffix", False)),
            remove_spaces=bool(data.get("remove_spaces", False)),
        )

    def candidates(self, text: str, siblings: list[str] | None = None) -> list[str]:
        """원문보다 짧아지는 후보를 덜 줄인 것부터 차례로.

        siblings: 같은 머리행의 다른 머리들. "개선 전 평균 응답시간"과 "개선 후 평균 응답시간"
        처럼 뒷부분이 같으면 그 공통부를 떼어 "개선 전"/"개선 후"로 줄이는 데 쓴다.
        """
        basic = self._basic_steps()
        peers = [self._apply(basic, s) for s in (siblings or []) if s != text]

        steps = list(basic)
        if self.drop_shared_suffix and peers:
            steps.append(lambda t: _drop_shared_suffix(t, peers))
        if self.keep_before:
            steps.append(self._keep_before)
        if self.remove_spaces:
            steps.append(lambda t: t.replace(" ", ""))

        out: list[str] = []
        current = text
        for step in steps:
            shorter = " ".join(step(current).split())
            if shorter and shorter != current:
                out.append(shorter)
                current = shorter
        return out

    def _basic_steps(self) -> list:
        steps = []
        if self.drop_parenthetical:
            steps.append(lambda t: _PAREN.sub("", t))
        if self.drop_words:
            steps.append(self._drop_words)
        if self.replacements:
            steps.append(self._replace)
        return steps

    @staticmethod
    def _apply(steps: list, text: str) -> str:
        for step in steps:
            text = " ".join(step(text).split()) or text
        return text

    def _drop_words(self, text: str) -> str:
        words = [w for w in text.split() if w not in self.drop_words]
        return " ".join(words) or text

    def _replace(self, text: str) -> str:
        for before, after in self.replacements:
            text = text.replace(before, after)
        return text

    def _keep_before(self, text: str) -> str:
        for separator in self.keep_before:
            head, found, _ = text.partition(separator)
            if found and head.strip():
                return head
        return text


def _drop_shared_suffix(text: str, peers: list[str]) -> str:
    """이웃 머리와 같은 뒷부분(단어 단위)을 뗀다. 앞에 한 단어 이상 남을 때만."""
    words = text.split()
    best = 0
    for peer in peers:
        other = peer.split()
        shared = 0
        while (shared < min(len(words), len(other)) - 1
               and words[-1 - shared] == other[-1 - shared]):
            shared += 1
        best = max(best, shared)
    return " ".join(words[: len(words) - best]) if best else text


@functools.lru_cache(maxsize=1)
def default_abbreviator() -> Abbreviator:
    return Abbreviator.load()
