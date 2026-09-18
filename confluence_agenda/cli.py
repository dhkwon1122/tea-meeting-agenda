"""사용자로부터 안건을 대화형으로 입력받아 Confluence 보고 페이지 본문을 생성한다.

사용 예:
    python -m confluence_agenda.cli -o page_body.xml
    python -m confluence_agenda.cli --publish --title "주간 회의록" --space TEAM

--publish 옵션을 사용하려면 아래 환경변수가 필요하다.
    CONFLUENCE_BASE_URL   예) https://your-domain.atlassian.net/wiki
    CONFLUENCE_EMAIL      Confluence 계정 이메일
    CONFLUENCE_API_TOKEN  Atlassian API 토큰
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request
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


def publish(title: str, space_key: str, body_storage: str, parent_id: Optional[str]) -> None:
    base_url = os.environ["CONFLUENCE_BASE_URL"].rstrip("/")
    email = os.environ["CONFLUENCE_EMAIL"]
    token = os.environ["CONFLUENCE_API_TOKEN"]

    payload = {
        "type": "page",
        "title": title,
        "space": {"key": space_key},
        "body": {"storage": {"value": body_storage, "representation": "storage"}},
    }
    if parent_id:
        payload["ancestors"] = [{"id": parent_id}]

    auth = base64.b64encode(f"{email}:{token}".encode("utf-8")).decode("ascii")
    req = urllib.request.Request(
        f"{base_url}/rest/api/content",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            links = result.get("_links", {})
            print(f"페이지 생성 완료: {links.get('base', '')}{links.get('webui', '')}")
    except urllib.error.HTTPError as exc:
        print(f"페이지 생성 실패 ({exc.code}): {exc.read().decode('utf-8')}", file=sys.stderr)
        raise


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Confluence 안건 보고 페이지 생성기")
    parser.add_argument("-o", "--output", help="storage-format 본문을 저장할 파일 경로")
    parser.add_argument("--title", help="페이지 제목 (게시 시 필요)")
    parser.add_argument("--space", help="Confluence Space Key (게시 시 필요)")
    parser.add_argument("--parent-id", help="상위 페이지 ID (선택)")
    parser.add_argument(
        "--publish",
        action="store_true",
        help="환경변수를 사용해 실제로 Confluence에 페이지를 생성한다",
    )
    args = parser.parse_args(argv)

    items = prompt_items()
    body_storage = build_agenda_page_body(items)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(body_storage)
        print(f"\nstorage-format 본문을 저장했습니다: {args.output}")
    else:
        print("\n=== Confluence storage-format 본문 ===\n")
        print(body_storage)

    if args.publish:
        if not (args.title and args.space):
            parser.error("--publish 사용 시 --title, --space 가 필요합니다.")
        publish(args.title, args.space, body_storage, args.parent_id)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
