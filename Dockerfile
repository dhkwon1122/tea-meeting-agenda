# Confluence 안건 보고 생성기 — 사내 workstation 빌드용 이미지
#
# 사내망에서 빌드 시 pip 사내 저장소 / CA 인증서 / 프록시가 필요할 수 있어
# 모두 '빌드 인자(ARG)'로 받는다. 값을 주지 않으면 공용 PyPI로 동작한다.
# (dhkwon1122/researcher-board 의 Dockerfile 구성을 참고했다.)
#
# ── 빌드 예시 (사내망, 기본값 그대로) ──────────────────────────────
#   docker build -t confluence-agenda:latest .
#
#   # 사내 CA 인증서가 필요하면 빌드 전에 certs/ 에 *.crt 를 넣어둔다.
#   #   cp 사내루트CA.crt certs/corp-root-ca.crt
#
#   # 웹 UI에 자주 쓰는 수신자를 태그로 띄우려면 빌드 전에 저장소 루트에
#   # contacts.json 을 만든다(개인정보라 git에는 커밋하지 않음 — 형식은
#   # contacts.example.json 참고). 없어도 빌드/실행은 그대로 되고, 그 경우
#   # 화면에는 자유 입력란만 나온다.
#
# ── 빌드 예시 (사외 / 공용 PyPI) ───────────────────────────────────
#   docker build \
#     --build-arg PIP_INDEX_URL= --build-arg PIP_TRUSTED_HOST= \
#     --build-arg HTTP_PROXY= --build-arg HTTPS_PROXY= --build-arg NO_PROXY= \
#     -t confluence-agenda:latest .
#
# ── 실행 예시 1) 웹 UI (기본값, 포트 10001) ────────────────────────
#   docker run --rm -p 10001:10001 confluence-agenda:latest
#   # http://localhost:10001 접속. 메일 발송까지 하려면 .env(.env.example
#   # 참고)를 --env-file 로 주입:
#   docker run --rm -p 10001:10001 --env-file .env confluence-agenda:latest
#
# ── 실행 예시 2) 대화형 CLI (-it 필수, 안건을 하나씩 입력) ──────────
#   docker run --rm -it confluence-agenda:latest python -m confluence_agenda.cli

FROM python:3.11-slim

# ── 빌드 인자 (사내 기본값 내장. 빌드 시 --build-arg 로 덮어쓸 수 있음) ──
# 사외/공용 PyPI 로 빌드하려면 빈 값으로 덮어쓴다:
#   docker build --build-arg PIP_INDEX_URL= --build-arg HTTP_PROXY= ... .
ARG PIP_INDEX_URL=http://repository.samsungds.net/repository/proxy-pypi-files.pythonhosted.org/simple
ARG PIP_TRUSTED_HOST=repository.samsungds.net
ARG PIP_CERT=
ARG HTTP_PROXY=http://12.26.204.100:8080
ARG HTTPS_PROXY=http://12.26.204.100:8080
ARG NO_PROXY=localhost,127.0.0.1,::1,samsungds.net,*.samsungds.net,*.samsung.net,12.0.0.0/8,10.0.0.0/8,192.0.0.0/8,172.0.0.0/8

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# ── 1) 사내 CA 인증서 등록 ──
# certs/ 에 아래 중 하나(또는 둘 다)를 둘 수 있다:
#   (a) 개별 사내 CA:  certs/corp-root-ca.crt  → update-ca-certificates 로 등록
#   (b) 전체 CA 번들:  certs/ca-bundle.crt     → 시스템 번들을 통째로 교체
# 둘 다 없으면 컨테이너 기본 CA 로 빌드한다.
#
# (git은 더 안 받는다 - doc2report를 pip git+https로 받다가 사내망에서
# git clone이 막혀 빌드가 실패했던 문제가 있어서, 그 소스를 vendor/document-parsing/에
# 통째로 복사해 두고 보통의 pip 패키지(python-docx 등, requirements.txt)로만
# 설치한다. vendor/document-parsing/README.md 참고.)
COPY certs/ /tmp/corp-certs/
RUN http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" no_proxy="$NO_PROXY" \
    apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && for f in /tmp/corp-certs/*.crt; do \
         [ -e "$f" ] || continue; \
         case "$f" in */ca-bundle.crt) continue ;; esac; \
         cp "$f" /usr/local/share/ca-certificates/; \
       done \
    && update-ca-certificates \
    && if [ -f /tmp/corp-certs/ca-bundle.crt ]; then \
         cp /tmp/corp-certs/ca-bundle.crt /etc/ssl/certs/ca-certificates.crt; \
         echo '[build] 사내 CA 번들 적용: /etc/ssl/certs/ca-certificates.crt'; \
       fi \
    && rm -rf /var/lib/apt/lists/* /tmp/corp-certs

# 런타임 파이썬(requests)이 사내 CA를 신뢰하도록 시스템 번들 지정
# (메일 API 호출이 사내 CA로 서명된 HTTPS를 쓰는 경우 필요)
ENV REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt

# ── 2) 의존성 설치 (사내 pip 인덱스/프록시/인증서 반영) ──
# ${VAR:+--flag $VAR} : VAR 가 비어있지 않을 때만 해당 옵션을 추가.
COPY requirements.txt .
RUN http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" no_proxy="$NO_PROXY" \
    pip install --upgrade pip \
      ${PIP_INDEX_URL:+--index-url "$PIP_INDEX_URL"} \
      ${PIP_TRUSTED_HOST:+--trusted-host "$PIP_TRUSTED_HOST"} \
      ${PIP_CERT:+--cert "$PIP_CERT"} \
    && http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" no_proxy="$NO_PROXY" \
    pip install \
      ${PIP_INDEX_URL:+--index-url "$PIP_INDEX_URL"} \
      ${PIP_TRUSTED_HOST:+--trusted-host "$PIP_TRUSTED_HOST"} \
      ${PIP_CERT:+--cert "$PIP_CERT"} \
      -r requirements.txt

# ── 3) 앱 소스 복사 ──
# .env, certs/*.crt 는 .dockerignore/.gitignore 로 제외 → 실행 시 --env-file/볼륨으로 주입.
COPY . .

# 애플리케이션은 root 권한이 필요하지 않다.
RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --create-home --shell /usr/sbin/nologin app \
    && chown -R app:app /app
USER app

# 기본 실행은 웹 UI (포트는 PORT 환경변수로 바꿀 수 있음, 기본 10001).
# 대화형 CLI로 띄우려면 실행 시 커맨드를 덮어쓴다:
#   docker run --rm -it <image> python -m confluence_agenda.cli
# (-it 없이 CLI를 실행하면 input()에서 즉시 EOF로 죽는다.)
ENV PORT=10001
EXPOSE 10001
CMD ["python", "-m", "confluence_agenda.web"]
