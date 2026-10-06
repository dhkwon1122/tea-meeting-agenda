"""입력 소스 어댑터 — 무엇을 읽었는지(format)와 내용을 파이프라인에 넘긴다.

markdown(.md, 붙여넣은 글) / confluence_storage(Confluence REST) / docx(Word 파일).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class LoadedSource:
    text: str
    name: str  # 원본 경로/URL — 로그용
    base_dir: Path | None = None  # 이미지 등 상대 경로 기준
    notes: list[str] = field(default_factory=list)
    format: str = "markdown"  # "markdown" | "confluence_storage" | "docx" — pipeline이 파서를 고를 때 씀
    title: str | None = None  # Confluence 페이지 제목처럼 본문과 별도로 오는 제목
    path: Path | None = None  # docx처럼 텍스트가 아닌 입력의 파일 경로
    linked: object | None = None  # Confluence: 연결 페이지를 불러올 LinkedPages (없으면 안 불러옴)


def load_source(source: str, *, linked: bool = True) -> LoadedSource:
    if source == "-":
        return LoadedSource(text=sys.stdin.read(), name="<stdin>", base_dir=Path.cwd())
    if source.startswith(("http://", "https://")):
        from .confluence import load_confluence

        return load_confluence(source, linked=linked)
    path = Path(source)
    if not path.exists():
        raise FileNotFoundError(f"입력 파일을 찾을 수 없음: {path}")
    if path.suffix.lower() == ".docx":
        return LoadedSource(text="", name=str(path), base_dir=path.parent, format="docx", path=path)
    if path.suffix.lower() == ".txt":
        return load_text(path.read_text(encoding="utf-8"), name=str(path), base_dir=path.parent)
    return LoadedSource(text=path.read_text(encoding="utf-8"), name=str(path),
                        base_dir=path.parent)


def load_text(text: str, *, name: str = "붙여넣은 글", base_dir: Path | None = None) -> LoadedSource:
    """붙여넣은 글. 줄마다 문단으로 나누고, 엑셀에서 복사한 탭 표는 표로 바꾼다
    (Markdown으로 쓴 글이면 그대로)."""
    from ..parsers.plaintext import text_to_markdown

    return LoadedSource(text=text_to_markdown(text), name=name, base_dir=base_dir or Path.cwd())
