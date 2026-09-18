"""사용자로부터 안건(대략 N개, 보통 10개 내외)을 입력받아
Confluence 에디터의 '마크업 삽입'에 그대로 붙여넣을 storage-format 소스를 출력한다.

사용 예:
    python -m confluence_agenda.cli              # 화면에 바로 출력
    python -m confluence_agenda.cli -o out.xml    # 파일로도 저장
"""
from __future__ import annotations

import argparse
from typing import List, Optional

from .builder import AgendaItem, build_agenda_page_body


def _read_multiline(prompt: str) -> str:
    print(f"{prompt} (입력 종료: 빈 줄에서 Enter)")
    lines: List[str] = []
    while True:
        line = input()
        if line == "":
            break
        lines.append(line)
    return "\n".join(lines)


def prompt_items() -> List[AgendaItem]:
    while True:
        raw = input("안건 개수(N)를 입력하세요: ").strip()
        if raw.isdigit() and int(raw) > 0:
            n = int(raw)
            break
        print("1 이상의 정수를 입력해주세요.")

    items: List[AgendaItem] = []
    for i in range(1, n + 1):
        print(f"\n--- 안건 {i} ---")
        title = input(f"안건 {i} 제목: ").strip()
        body = _read_multiline(f"안건 {i} 본문")
        attachment_body = _read_multiline(f"안건 {i} 첨부 내용(요약, 링크 설명 등)")
        items.append(AgendaItem(title=title, body=body, attachment_body=attachment_body))
    return items


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Confluence 안건 보고 페이지 소스 생성기")
    parser.add_argument("-o", "--output", help="storage-format 소스를 저장할 파일 경로 (생략 시 화면 출력)")
    args = parser.parse_args(argv)

    items = prompt_items()
    body_storage = build_agenda_page_body(items)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(body_storage)
        print(f"\nstorage-format 소스를 저장했습니다: {args.output}")
    else:
        print("\n=== 아래 소스를 Confluence 편집기 '마크업 삽입'에 붙여넣으세요 ===\n")
        print(body_storage)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
