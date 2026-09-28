"""사용자로부터 안건 제목(대략 N개, 보통 10개 내외)을 입력받아
Confluence 에디터의 '마크업 삽입'에 그대로 붙여넣을 storage-format 소스를 출력하고,
필요하면 사내 메일 API로 안건 요약도 함께 보낸다.

본문은 자리표시자 3줄로 자동 채워지고, 첨부 내용은 "(첨부 N) {안건 제목}" 이라는
이름의 하위 페이지를 include 매크로로 자동 포함하므로 별도 입력이 필요 없다.
(해당 하위 페이지는 미리 만들어져 있어야 한다.)

메일 발송은 MAIL_API_TOKEN / MAIL_API_SYSTEM_ID / MAIL_API_USER_ID 환경변수가
모두 설정되어 있을 때만 동작한다 (mailer.py 참고).

사용 예:
    python -m confluence_agenda.cli                       # 화면에 바로 출력
    python -m confluence_agenda.cli -o out.xml             # 파일로도 저장
    python -m confluence_agenda.cli --to me@example.com,other@example.com  # 생성 후 메일도 자동 발송
"""
from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from .builder import AgendaItem, build_agenda_email_html, build_agenda_page_body, build_email_subject
from .mailer import MailConfigError, is_mail_configured, send_report_email


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


def _split_emails(raw: str) -> List[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


def _resolve_recipients(cli_to: Optional[str]) -> List[str]:
    if cli_to:
        return _split_emails(cli_to)
    if not is_mail_configured():
        return []
    answer = input("\n메일로도 보낼까요? (y/N): ").strip().lower()
    if answer != "y":
        return []
    return _split_emails(input("받는 사람 이메일 (콤마로 여러 명 가능): "))


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Confluence 안건 보고 페이지 소스 생성기")
    parser.add_argument("-o", "--output", help="storage-format 소스를 저장할 파일 경로 (생략 시 화면 출력)")
    parser.add_argument("--to", help="안건 요약을 보낼 이메일 주소, 콤마로 여러 명 (지정하면 생성 후 자동 발송)")
    parser.add_argument("--subject", help="메일 제목 (생략 시 자동 생성)")
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

    to_emails = _resolve_recipients(args.to)
    if to_emails:
        if not is_mail_configured():
            print(
                "\nMAIL_API_TOKEN 등 메일 API 환경변수가 설정되지 않아 메일을 보낼 수 없습니다.",
                file=sys.stderr,
            )
            return 1
        subject = args.subject or build_email_subject(items)
        try:
            send_report_email(to_emails, subject=subject, body_html=build_agenda_email_html(items))
        except MailConfigError as e:
            print(f"\n메일 발송 실패: {e}", file=sys.stderr)
            return 1
        print(f"\n메일을 보냈습니다: {', '.join(to_emails)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
