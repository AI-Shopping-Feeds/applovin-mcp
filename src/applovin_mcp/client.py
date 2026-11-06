"""One HTTP choke point for every AppLovin API this server calls.

Every request goes through `AppLovinClient.request()`, which adds the right key
for the API, keeps to AppLovin's rate limits, retries only what is safe to
retry, caps response size, sends writes through dry-run and the audit log, and
redacts keys from logs and errors. It only talks to AppLovin's API hosts and,
for user-level report files, the signed storage link AppLovin returns.
"""

from __future__ import annotations

import contextlib
import json
import random
import threading
import time
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from . import __version__
from ._logging import get_logger
from .audit import AuditLog
from .config import Config
from .credentials import redact

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 4
MAX_BACKOFF = 60.0
# The Campaign Management API blocks an account for 24 hours after 100 failed
# requests in five minutes. Stop well before that.
ERROR_GUARD_LIMIT = 40
ERROR_GUARD_WINDOW = 300.0
# User-level report files are served from signed storage links.
DOWNLOAD_HOST_SUFFIXES = (".amazonaws.com", ".applovin.com")

logger = get_logger("client")
Params = dict[str, Any] | Sequence[tuple[str, Any]] | None


@dataclass(frozen=True)
class ApiTarget:
    name: str
    side: str  # "ads" (Ads Manager) or "max"
    label: str
    base_url: str
    key_attr: str  # Config attribute holding the key
    key_env: str
    auth: str  # "authorization" header, "api-key" header or "query" (api_key=)
    needs_account: bool = False
    max_requests: int | None = None  # client-side rate limit per window
    window: float = 60.0
    slow: bool = False  # report endpoints: allow a longer timeout


TARGETS: dict[str, ApiTarget] = {
    "ads": ApiTarget(
        "ads",
        "ads",
        "Campaign Management API",
        "https://api.ads.axon.ai/manage/v1",
        "cm_api_key",
        "APPLOVIN_CAMPAIGN_MANAGEMENT_API_KEY",
        "authorization",
        needs_account=True,
        max_requests=900,  # AppLovin allows 1000 per 60 seconds
        window=60.0,
    ),
    "ads_reporting": ApiTarget(
        "ads_reporting",
        "ads",
        "Reporting API",
        "https://r.applovin.com",
        "ads_reporting_key",
        "APPLOVIN_REPORTING_API_KEY",
        "query",
        slow=True,
    ),
    "max_reporting": ApiTarget(
        "max_reporting",
        "max",
        "MAX Reporting API",
        "https://r.applovin.com",
        "max_report_key",
        "APPLOVIN_MAX_REPORT_KEY",
        "query",
        slow=True,
    ),
    "max": ApiTarget(
        "max",
        "max",
        "MAX Ad Unit Management API",
        "https://o.applovin.com/mediation/v1",
        "max_management_key",
        "APPLOVIN_MAX_MANAGEMENT_KEY",
        "api-key",
        max_requests=1900,  # AppLovin allows 2000 per hour
        window=3600.0,
    ),
    "ad_review": ApiTarget(
        "ad_review",
        "max",
        "MAX Ad Review Rules Management API",
        "https://api-safedk.applovin.com",
        "ad_review_key",
        "APPLOVIN_AD_REVIEW_KEY",
        "api-key",
    ),
}
API_HOSTS = frozenset(urlparse(t.base_url).hostname for t in TARGETS.values())

_HINTS = {
    ("ads", 401): (
        "The Campaign Management API is whitelist-only (limited beta): ask your AppLovin "
        "account team to enable it for this account. Also check that "
        "APPLOVIN_CAMPAIGN_MANAGEMENT_API_KEY is the Campaign Management key from Ads Manager "
        "(account menu > Keys; Admins only) and APPLOVIN_ACCOUNT_ID is that account's ID. "
        "Reporting tools such as applovin_ads_report use the Report Key and still work."
    ),
    ("ads_reporting", 401): (
        "Check APPLOVIN_REPORTING_API_KEY: it's the Report Key from Ads Manager (account menu > Keys)."
    ),
    ("max_reporting", 401): (
        "Check APPLOVIN_MAX_REPORT_KEY: it's the Report Key from the MAX dashboard "
        "(Account > General > Keys)."
    ),
    ("max", 401): (
        "Check APPLOVIN_MAX_MANAGEMENT_KEY: it's the Management Key from the MAX dashboard "
        "(Account > General > Keys)."
    ),
    ("ad_review", 401): (
        "Check APPLOVIN_AD_REVIEW_KEY: it's the Ad Review Key from the MAX dashboard "
        "(Account > General > Keys)."
    ),
}


class AppLovinAPIError(Exception):
    """An AppLovin API refused or failed a request. The message is safe to show."""

    def __init__(
        self,
        status: int,
        body: Any = None,
        *,
        api: str = "",
        code: str | None = None,
        message: str | None = None,
        trace_id: str | None = None,
        hint: str | None = None,
    ):
        self.status = status
        self.api = api
        self.code = code
        self.trace_id = trace_id
        self.body = redact(body)
        label = TARGETS[api].label if api in TARGETS else "AppLovin API"
        text = f"AppLovin {label} error {status}"
        if code:
            text += f" (x-al-error-code {code})"
        detail = redact(message) if message else _short(self.body)
        if detail:
            text += f": {detail}"
        if trace_id:
            text += f" [trace ID {trace_id}]"
        if hint:
            text += f". {hint}"
        super().__init__(text)


def _short(body: Any) -> str:
    if body in (None, "", {}, []):
        return ""
    text = body if isinstance(body, str) else json.dumps(body, default=str)
    return text[:500]


class RateLimiter:
    """Sliding-window limiter: at most `limit` calls per `window` seconds."""

    def __init__(self, limit: int, window: float):
        self.limit = limit
        self.window = window
        self._calls: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self, label: str, max_wait: float = 30.0) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                while self._calls and now - self._calls[0] >= self.window:
                    self._calls.popleft()
                if len(self._calls) < self.limit:
                    self._calls.append(now)
                    return
                wait = self.window - (now - self._calls[0])
            if wait > max_wait:
                raise RuntimeError(
                    f"Paused locally to stay within the {label} rate limit "
                    f"({self.limit} requests per {self.window:g} s). Try again in "
                    f"{int(wait // 60) + 1} minute(s)."
                )
            time.sleep(wait + 0.05)


class AppLovinClient:
    """Thin HTTP wrapper over the AppLovin Ads Manager and MAX APIs."""

    def __init__(self, config: Config, audit: AuditLog):
        self.config = config
        self.audit = audit
        self.enabled_sides: set[str] = {"ads", "max"}
        self._http = httpx.Client(
            timeout=config.timeout,
            headers={"User-Agent": f"applovin-mcp/{__version__}"},
            follow_redirects=False,
        )
        self._limiters = {
            name: RateLimiter(t.max_requests, t.window)
            for name, t in TARGETS.items()
            if t.max_requests
        }
        self._ads_errors: deque[float] = deque()
        self._errors_lock = threading.Lock()

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> AppLovinClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------ credentials

    @staticmethod
    def target(api: str) -> ApiTarget:
        if api not in TARGETS:
            raise ValueError(f"Unknown API '{api}'. Use one of: {', '.join(TARGETS)}.")
        return TARGETS[api]

    def key_for(self, api: str) -> str:
        t = self.target(api)
        key = getattr(self.config, t.key_attr)
        if not key:
            raise RuntimeError(
                f"{t.key_env} isn't set, so the {t.label} can't be used. Run "
                "`applovin-mcp setup` or set it in the client config; see "
                "docs/setup-applovin-api.md."
            )
        return str(key)

    def has_key(self, api: str) -> bool:
        return bool(getattr(self.config, self.target(api).key_attr))

    def _account_id(self) -> str:
        if not self.config.account_id:
            raise RuntimeError(
                "APPLOVIN_ACCOUNT_ID isn't set. Find it in Ads Manager: account menu > "
                "Go to settings; it's the number after the account name."
            )
        return self.config.account_id

    # ------------------------------------------------------------ requests

    @staticmethod
    def _check_path(path: str) -> None:
        if not path.startswith("/") or "://" in path or ".." in path:
            raise ValueError(
                "path must start with '/' and be relative to the API, e.g. '/campaign/list'."
            )
        if any(c in path for c in "?#\\ \t\r\n"):
            raise ValueError("Put query parameters in params/query, not in the path.")

    def _params(self, t: ApiTarget, params: Params) -> list[tuple[str, Any]]:
        items = list(params.items()) if isinstance(params, dict) else list(params or [])
        reserved = {"api_key", "account_id"}
        clean: list[tuple[str, Any]] = []
        for k, v in items:
            if str(k).lower() in reserved:
                raise ValueError(f"Don't pass '{k}'; the server adds it from its configuration.")
            if v is None:
                continue
            if isinstance(v, bool):
                v = "true" if v else "false"
            elif isinstance(v, list | tuple):
                v = ",".join(str(x) for x in v)
            clean.append((str(k), v))
        if t.needs_account:
            clean.insert(0, ("account_id", self._account_id()))
        if t.auth == "query":
            clean.insert(0, ("api_key", self.key_for(t.name)))
        return clean

    def _headers(self, t: ApiTarget, has_json: bool) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if t.auth == "authorization":
            headers["Authorization"] = self.key_for(t.name)
        elif t.auth == "api-key":
            headers["Api-Key"] = self.key_for(t.name)
        if has_json:
            headers["Content-Type"] = "application/json"
        return headers

    def _guard_ads_errors(self) -> None:
        with self._errors_lock:
            now = time.monotonic()
            while self._ads_errors and now - self._ads_errors[0] > ERROR_GUARD_WINDOW:
                self._ads_errors.popleft()
            if len(self._ads_errors) >= ERROR_GUARD_LIMIT:
                raise RuntimeError(
                    f"{len(self._ads_errors)} Campaign Management API requests failed in the last "
                    "five minutes. AppLovin blocks the API for 24 hours after 100, so this server "
                    "is pausing those calls for a few minutes. Fix the cause (see the earlier "
                    "errors) before trying again."
                )

    def _note_ads_error(self) -> None:
        with self._errors_lock:
            self._ads_errors.append(time.monotonic())

    def _read_capped(self, resp: httpx.Response) -> bytes:
        cap = self.config.max_download_mb * 1024 * 1024
        declared = resp.headers.get("Content-Length")
        if declared and declared.isdigit() and int(declared) > cap:
            raise ValueError(
                f"The response is {int(declared) // (1024 * 1024)} MB, more than "
                f"APPLOVIN_MAX_DOWNLOAD_MB ({self.config.max_download_mb}). Narrow the request "
                "(fewer days, columns or rows) or raise the limit."
            )
        chunks: list[bytes] = []
        total = 0
        for chunk in resp.iter_bytes():
            total += len(chunk)
            if total > cap:
                raise ValueError(
                    f"The response is larger than APPLOVIN_MAX_DOWNLOAD_MB "
                    f"({self.config.max_download_mb}). Narrow the request or raise the limit."
                )
            chunks.append(chunk)
        return b"".join(chunks)

    @staticmethod
    def _parse(raw: bytes, content_type: str) -> Any:
        if not raw.strip():
            return {}
        text = raw.decode("utf-8", errors="replace")
        if "json" in content_type or text.lstrip()[:1] in ("{", "["):
            try:
                return json.loads(text)
            except ValueError:
                pass
        return text

    def _error(
        self, t: ApiTarget, status: int, headers: httpx.Headers, body: Any
    ) -> AppLovinAPIError:
        code = headers.get("x-al-error-code")
        message = headers.get("x-al-error-message")
        if message is None and isinstance(body, dict):
            message = body.get("message") or body.get("error") or body.get("msg")
            if message is not None:
                message = str(message)
        hint = _HINTS.get((t.name, 401 if status in (401, 403) else status))
        if status == 429 and t.name == "ads":
            hint = (
                "AppLovin blocked the Campaign Management API for 24 hours after 100 failed "
                "requests in five minutes; wait, don't retry."
                if code == "100007"
                else "Over 1000 requests a minute: AppLovin rejects requests for ten minutes."
            )
        return AppLovinAPIError(
            status,
            body,
            api=t.name,
            code=code,
            message=message,
            trace_id=headers.get("x-trace-id"),
            hint=hint,
        )

    def request(
        self,
        api: str,
        method: str,
        path: str,
        *,
        params: Params = None,
        json_body: Any | None = None,
        files: Sequence[tuple[str, Path, str]] | None = None,
        op: str | None = None,
        before: Any | None = None,
        read_only: bool = False,
    ) -> Any:
        """Perform one API request and return parsed JSON (text for CSV, {} if empty).

        api: one of TARGETS ('ads', 'ads_reporting', 'max_reporting', 'max', 'ad_review').
        path: relative to the API's base URL, e.g. '/campaign/list'.
        files: (file name, local path, content type) for multipart uploads.
        read_only: set True for POSTs that don't change anything. Writes follow
            dry-run, are written to the audit log and are never retried.
        """
        t = self.target(api)
        method = method.upper()
        self._check_path(path)
        query = self._params(t, params)
        headers = self._headers(t, json_body is not None)
        url = f"{t.base_url}{path}"
        is_write = method in WRITE_METHODS and not read_only
        safe_query = {k: v for k, v in query if k not in {"api_key"}}
        audit_body: Any = json_body
        if files:
            audit_body = {
                "files": [
                    {"name": name, "bytes": p.stat().st_size, "content_type": ct}
                    for name, p, ct in files
                ]
            }
        op_name = op or f"{method} {path}"

        if is_write and self.config.dry_run:
            audit_id = self.audit.record(
                op=op_name,
                api=api,
                method=method,
                path=path,
                account_id=self.config.account_id if t.needs_account else None,
                request_body={"query": safe_query, "body": audit_body},
                before=before,
                dry_run=True,
            )
            logger.info("dry-run %s %s %s (audit=%s)", api, method, path, audit_id)
            return {
                "_dry_run": True,
                "api": api,
                "method": method,
                "url": url,
                "query": safe_query,
                "body": audit_body,
                "audit_id": audit_id,
            }

        timeout = max(self.config.timeout, 180.0) if t.slow else self.config.timeout
        attempt = 0
        while True:
            if t.name == "ads":
                self._guard_ads_errors()
            limiter = self._limiters.get(t.name)
            if limiter is not None:
                limiter.acquire(t.label)
            try:
                with contextlib.ExitStack() as stack:
                    multipart = None
                    if files:
                        multipart = [
                            ("files", (name, stack.enter_context(p.open("rb")), ct))
                            for name, p, ct in files
                        ]
                    req = self._http.build_request(
                        method,
                        url,
                        params=query,
                        headers=headers,
                        content=None if json_body is None else json.dumps(json_body),
                        files=multipart,
                        timeout=timeout,
                    )
                    resp = self._http.send(req, stream=True)
                    try:
                        raw = self._read_capped(resp)
                    finally:
                        resp.close()
            except httpx.RequestError as e:
                if t.name == "ads":
                    self._note_ads_error()
                if is_write:
                    raise RuntimeError(
                        f"Network error during {method} {path}: {redact(str(e))}. The change may "
                        "or may not have been applied; read it back before trying again."
                    ) from e
                if attempt >= MAX_ATTEMPTS - 1:
                    raise RuntimeError(f"Network error calling {t.label}: {redact(str(e))}") from e
                logger.warning(
                    "transport error attempt=%d %s %s %s", attempt + 1, api, method, path
                )
                self._sleep_backoff(attempt)
                attempt += 1
                continue

            status = resp.status_code
            payload = self._parse(raw, resp.headers.get("content-type", ""))
            # r.applovin.com can report an error in the body with HTTP 200.
            body_code = payload.get("code") if isinstance(payload, dict) else None
            if 200 <= status < 300 and isinstance(body_code, int) and body_code >= 400:
                status = body_code

            if not (200 <= status < 300):
                if t.name == "ads":
                    self._note_ads_error()
                retryable = status in RETRYABLE_STATUSES and not (status == 429 and t.name == "ads")
                if retryable and not is_write and attempt < MAX_ATTEMPTS - 1:
                    logger.warning(
                        "retryable status=%d attempt=%d %s %s %s",
                        status,
                        attempt + 1,
                        api,
                        method,
                        path,
                    )
                    self._sleep_backoff(attempt, resp)
                    attempt += 1
                    continue
                error = self._error(t, status, resp.headers, payload)
                if is_write:
                    self.audit.record(
                        op=op_name,
                        api=api,
                        method=method,
                        path=path,
                        account_id=self.config.account_id if t.needs_account else None,
                        request_body={"query": safe_query, "body": audit_body},
                        response_status=status,
                        response_body=payload,
                        before=before,
                        error=str(error),
                    )
                logger.error("api error %d %s %s %s", status, api, method, path)
                raise error

            if is_write:
                audit_id = self.audit.record(
                    op=op_name,
                    api=api,
                    method=method,
                    path=path,
                    account_id=self.config.account_id if t.needs_account else None,
                    request_body={"query": safe_query, "body": audit_body},
                    response_status=status,
                    response_body=payload,
                    before=before,
                )
                logger.info("write %s %s %s -> %d (audit=%s)", api, method, path, status, audit_id)
                if isinstance(payload, dict):
                    payload = {**payload, "audit_id": audit_id}
                else:
                    payload = {"result": payload, "audit_id": audit_id}
            else:
                logger.debug("read %s %s %s -> %d", api, method, path, status)
            return payload

    # ------------------------------------------------------------ helpers

    def paginate_pages(
        self,
        api: str,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        max_results: int = 200,
        page_size: int | None = None,
    ) -> tuple[list[Any], bool]:
        """Page through Campaign Management list endpoints (page/size, bare arrays).

        The page size stays fixed so page numbers line up; extra rows are dropped.
        """
        size = max(1, min(page_size or self.config.page_size, 100))
        items: list[Any] = []
        page = 1
        while True:
            batch = self.request(
                api, "GET", path, params={**(params or {}), "page": page, "size": size}
            )
            batch = batch if isinstance(batch, list) else []
            items.extend(batch)
            if len(batch) < size:
                return items[:max_results], len(items) > max_results
            if len(items) >= max_results:
                return items[:max_results], True
            page += 1

    def snapshot(self, fn: Any, *args: Any, **kwargs: Any) -> Any | None:
        """Run a read for a `before` snapshot; returns None instead of raising."""
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            logger.warning("before-snapshot failed: %s", redact(str(e))[:300])
            return None

    def download(self, url: str) -> bytes:
        """Fetch a signed report file (kept in memory only, capped in size).

        No AppLovin key is sent: the link itself grants access, so it is never
        logged or returned.
        """
        parsed = urlparse(url)
        host = parsed.hostname or ""
        if parsed.scheme != "https" or not host.endswith(DOWNLOAD_HOST_SUFFIXES):
            raise ValueError(f"Refusing to download a report from an unexpected host: {host!r}")
        last: Exception | None = None
        for attempt in range(MAX_ATTEMPTS):
            try:
                req = self._http.build_request(
                    "GET", url, headers={"Accept": "*/*"}, timeout=max(self.config.timeout, 180.0)
                )
                resp = self._http.send(req, stream=True)
                try:
                    if resp.status_code in RETRYABLE_STATUSES and attempt < MAX_ATTEMPTS - 1:
                        self._sleep_backoff(attempt, resp)
                        continue
                    if resp.status_code != 200:
                        raise AppLovinAPIError(
                            resp.status_code,
                            None,
                            api="max_reporting",
                            message="the signed report link failed; request the report again",
                        )
                    return self._read_capped(resp)
                finally:
                    resp.close()
            except httpx.RequestError as e:
                last = e
                self._sleep_backoff(attempt)
        raise RuntimeError(f"Report download failed: {redact(str(last)) if last else 'unknown'}")

    @staticmethod
    def _sleep_backoff(attempt: int, resp: httpx.Response | None = None) -> None:
        retry_after = 0.0
        if resp is not None and "Retry-After" in resp.headers:
            try:
                retry_after = float(resp.headers["Retry-After"])
            except ValueError:
                retry_after = 0.0
        base = min(max(retry_after, min(2**attempt, 30)), MAX_BACKOFF)
        time.sleep(base + random.uniform(0, 0.5))
