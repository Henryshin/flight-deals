"""수집기 공용 HTTP 헬퍼 (stdlib urllib 만 사용 — 의존성 추가 없음).

Meta Graph API 의 오류를 base.py 의 STATUS_* 로 옮기는 규칙을 한 곳에 모아둔다.
"""
import json
import urllib.error
import urllib.parse
import urllib.request

from .base import (
    STATUS_AUTH, STATUS_ERROR, STATUS_OK, STATUS_PERMISSION, STATUS_RATE_LIMITED,
    STATUS_TIMEOUT,
)

TIMEOUT_SEC = 20
USER_AGENT = "saju-corpus/0.1 (+https://github.com/Henryshin/flight-deals)"

# Meta 가 레이트리밋에 쓰는 오류 코드 (HTTP 는 400 으로 오는 경우가 많다).
_RATE_LIMIT_CODES = {4, 17, 32, 613}
# 토큰 자체가 없거나 만료된 경우 — 사람이 토큰을 다시 발급해야 한다.
_AUTH_CODES = {102, 190, 463, 467}
# 토큰은 유효하나 스코프가 없는 경우 — 앱 심사 통과 전의 '정상 상태'.
# 이걸 AUTH 로 뭉뚱그리면 "토큰이 깨졌나?" 하고 엉뚱한 곳을 보게 된다.
_PERMISSION_CODES = {10, 200, 803}


def get_json(url, params, timeout=TIMEOUT_SEC):
    """GET 후 (status, payload, detail) 반환. 예외를 밖으로 내지 않는다."""
    query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    req = urllib.request.Request(f"{url}?{query}", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
        return STATUS_OK, json.loads(body), ""
    except urllib.error.HTTPError as e:
        raw = ""
        try:
            raw = e.read().decode("utf-8", errors="replace")
        except OSError:
            pass
        return _classify_http_error(e.code, raw)
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", e)
        if "timed out" in str(reason).lower():
            return STATUS_TIMEOUT, None, str(reason)
        return STATUS_ERROR, None, f"네트워크 오류: {reason}"
    except TimeoutError as e:
        return STATUS_TIMEOUT, None, str(e)
    except ValueError as e:
        return STATUS_ERROR, None, f"JSON 파싱 실패: {e}"


def _classify_http_error(http_code, raw_body):
    """HTTP 코드 + Meta 오류 본문을 STATUS_* 로 분류."""
    err = {}
    try:
        err = (json.loads(raw_body) or {}).get("error") or {}
    except ValueError:
        pass
    code = err.get("code")
    message = err.get("message") or raw_body[:200]

    if http_code == 429 or code in _RATE_LIMIT_CODES:
        return STATUS_RATE_LIMITED, None, f"레이트리밋({http_code}/{code}): {message}"
    if code in _PERMISSION_CODES:
        return STATUS_PERMISSION, None, (
            f"권한 미승인({http_code}/{code}): {message} "
            "— 앱 심사 통과 전이면 정상입니다."
        )
    if code in _AUTH_CODES or http_code == 401:
        return STATUS_AUTH, None, f"토큰 오류({http_code}/{code}): {message}"
    if http_code == 403:
        # 코드가 안 실려 오면 403 은 스코프 문제일 가능성이 더 높다.
        return STATUS_PERMISSION, None, f"권한 거부(403): {message}"
    return STATUS_ERROR, None, f"HTTP {http_code}: {message}"
