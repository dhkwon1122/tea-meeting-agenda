"""사용자로부터 안건 제목(대략 N개, 보통 10개 내외)을 입력받아
Confluence 에디터의 '마크업 삽입'에 그대로 붙여넣을 storage-format 소스를 출력한다.

본문은 자리표시자 3줄로 자동 채워지고, 첨부 내용은 "(첨부 N) {안건 제목}" 이라는
이름의 하위 페이지를 include 매크로로 자동 포함하므로 별도 입력이 필요 없다.
(해당 하위 페이지는 미리 만들어져 있어야 한다.)

사용 예:
    python -m confluence_agenda.cli              # 화면에 바로 출력
    python -m confluence_agenda.cli -o out.xml    # 파일로도 저장
"""
from __future__ import annotations

import argparse
from typing import List, Optional

from .builder import AgendaItem, build_agenda_page_body


def prompt_items() -> List[AgendaItem]:
    while True:
        raw = input("안건 개수(N)를 입력하세요: ").strip()
        if raw.isdigit() and int(raw) > 0:
            n = int(raw)
            break
        print("1 이상의 정수를 입력해주세요.")

    items: List[AgendaItem] = []
    for i in range(1, n + 1):
        title = input(f"안건 {i} 제목: ").strip()
        items.append(AgendaItem(title=title))
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
