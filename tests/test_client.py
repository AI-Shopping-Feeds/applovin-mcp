"""The HTTP choke point: keys, account scoping, retries, rate limits, dry-run, audit, caps."""

from __future__ import annotations

import dataclasses
import os
import stat
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx

from applovin_mcp import client as client_mod
from applovin_mcp.audit import AuditLog
from applovin_mcp.client import AppLovinAPIError, AppLovinClient, RateLimiter
from applovin_mcp.config import Config

from .conftest import (
    ACCOUNT,
    ADS,
    CM_KEY,
    MAX,
    MAX_REPORT_KEY,
    MGMT_KEY,
    REPORT_KEY,
    REVIEW,
    REVIEW_KEY,
    R,
    audit_entries,
)


@respx.mock
def test_campaign_api_sends_key_header_and_account(client: AppLovinClient) -> None:
    route = respx.get(f"{ADS}/campaign/list").mock(return_value=httpx.Response(200, json=[]))
    client.request("ads", "GET", "/campaign/list", params={"page": 1})
    req = route.calls.last.request
    assert req.headers["Authorization"] == CM_KEY
    q = parse_qs(urlparse(str(req.url)).query)
    assert q["account_id"] == [ACCOUNT]
    assert "api_key" not in q


@respx.mock
def test_reporting_sends_key_as_query_only(client: AppLovinClient) -> None:
    route = respx.get(f"{R}/report").mock(return_value=httpx.Response(200, json={"results": []}))
    client.request("ads_reporting", "GET", "/report", params={"columns": "day"})
    req = route.calls.last.request
    assert parse_qs(urlparse(str(req.url)).query)["api_key"] == [REPORT_KEY]
    assert "Authorization" not in req.headers
    assert "account_id" not in str(req.url)


@respx.mock
def test_max_reporting_uses_the_max_key(client: AppLovinClient) -> None:
    route = respx.get(f"{R}/maxReport").mock(return_value=httpx.Response(200, json={"results": []}))
    client.request("max_reporting", "GET", "/maxReport")
    assert parse_qs(urlparse(str(route.calls.last.request.url)).query)["api_key"] == [
        MAX_REPORT_KEY
    ]


@respx.mock
def test_management_apis_use_api_key_header(client: AppLovinClient) -> None:
    units = respx.get(f"{MAX}/ad_units").mock(return_value=httpx.Response(200, json=[]))
    rules = respx.get(f"{REVIEW}/v1/rules/risky").mock(return_value=httpx.Response(200, json={}))
    client.request("max", "GET", "/ad_units")
    client.request("ad_review", "GET", "/v1/rules/risky")
    assert units.calls.last.request.headers["Api-Key"] == MGMT_KEY
    assert rules.calls.last.request.headers["Api-Key"] == REVIEW_KEY
    assert "api_key" not in str(units.calls.last.request.url)


def test_missing_key_names_the_variable(config: Config, audit: AuditLog) -> None:
    c = AppLovinClient(dataclasses.replace(config, max_management_key=None), audit)
    with pytest.raises(RuntimeError, match="APPLOVIN_MAX_MANAGEMENT_KEY"):
        c.request("max", "GET", "/ad_units")


def test_missing_account_id(config: Config, audit: AuditLog) -> None:
    c = AppLovinClient(dataclasses.replace(config, account_id=None), audit)
    with pytest.raises(RuntimeError, match="APPLOVIN_ACCOUNT_ID"):
        c.request("ads", "GET", "/campaign/list")


@pytest.mark.parametrize("path", ["campaign/list", "/../x", "https://evil.example/x", "/a?b=1"])
def test_bad_paths_are_refused(client: AppLovinClient, path: str) -> None:
    with pytest.raises(ValueError):
        client.request("ads", "GET", path)


def test_reserved_params_are_refused(client: AppLovinClient) -> None:
    with pytest.raises(ValueError, match="api_key"):
        client.request("ads_reporting", "GET", "/report", params={"api_key": "other"})
    with pytest.raises(ValueError, match="account_id"):
        client.request("ads", "GET", "/campaign/list", params={"account_id": "1"})


@respx.mock
def test_whitelist_error_is_helpful_and_redacted(client: AppLovinClient) -> None:
    respx.get(f"{ADS}/campaign/list").mock(
        return_value=httpx.Response(
            403,
            headers={
                "x-al-error-code": "100001",
                "x-al-error-message": f"key {CM_KEY} not allowed",
                "X-TRACE-ID": "trace-1",
            },
        )
    )
    with pytest.raises(AppLovinAPIError) as exc_info:
        client.request("ads", "GET", "/campaign/list")
    text = str(exc_info.value)
    assert "whitelist-only" in text
    assert "100001" in text
    assert "trace-1" in text
    assert CM_KEY not in text


@respx.mock
def test_campaign_api_429_is_not_retried(client: AppLovinClient) -> None:
    route = respx.get(f"{ADS}/campaign/list").mock(
        return_value=httpx.Response(429, headers={"x-al-error-code": "100007"})
    )
    with pytest.raises(AppLovinAPIError, match="24 hours"):
        client.request("ads", "GET", "/campaign/list")
    assert route.call_count == 1


@respx.mock
def test_reads_retry_server_errors(client: AppLovinClient, _no_sleep: list[float]) -> None:
    route = respx.get(f"{MAX}/ad_units").mock(
        side_effect=[httpx.Response(503), httpx.Response(200, json=[{"id": "a"}])]
    )
    assert client.request("max", "GET", "/ad_units") == [{"id": "a"}]
    assert route.call_count == 2
    assert _no_sleep


@respx.mock
def test_writes_are_not_retried_and_failures_are_audited(
    client: AppLovinClient, tmp_audit_log
) -> None:
    route = respx.post(f"{ADS}/campaign/update").mock(return_value=httpx.Response(503))
    with pytest.raises(AppLovinAPIError):
        client.request("ads", "POST", "/campaign/update", json_body={"id": "1"}, op="x")
    assert route.call_count == 1
    [entry] = audit_entries(tmp_audit_log)
    assert entry["response_status"] == 503
    assert entry["error"]


@respx.mock
def test_error_guard_stops_campaign_calls(client: AppLovinClient, monkeypatch) -> None:
    monkeypatch.setattr(client_mod, "ERROR_GUARD_LIMIT", 3)
    route = respx.get(f"{ADS}/campaign/list").mock(return_value=httpx.Response(400))
    for _ in range(3):
        with pytest.raises(AppLovinAPIError):
            client.request("ads", "GET", "/campaign/list")
    with pytest.raises(RuntimeError, match="24 hours"):
        client.request("ads", "GET", "/campaign/list")
    assert route.call_count == 3


@respx.mock
def test_body_error_code_with_http_200(client: AppLovinClient) -> None:
    respx.get(f"{R}/maxReport").mock(
        return_value=httpx.Response(200, json={"code": 400, "message": "bad column"})
    )
    with pytest.raises(AppLovinAPIError, match="bad column"):
        client.request("max_reporting", "GET", "/maxReport")


def test_dry_run_sends_nothing_and_audits(config: Config, tmp_audit_log) -> None:
    c = AppLovinClient(dataclasses.replace(config, dry_run=True), AuditLog(tmp_audit_log))
    with respx.mock(assert_all_called=False) as router:
        route = router.post(f"{ADS}/campaign/update")
        out = c.request("ads", "POST", "/campaign/update", json_body={"id": "1"}, op="upd")
    assert out["_dry_run"] is True
    assert not route.called
    [entry] = audit_entries(tmp_audit_log)
    assert entry["dry_run"] is True
    assert entry["request_body"]["query"] == {"account_id": ACCOUNT}
    assert CM_KEY not in tmp_audit_log.read_text()


@respx.mock
def test_write_is_audited_with_before_snapshot(client: AppLovinClient, tmp_audit_log) -> None:
    respx.post(f"{ADS}/campaign/update").mock(return_value=httpx.Response(200, json={"id": "1"}))
    out = client.request(
        "ads",
        "POST",
        "/campaign/update",
        json_body={"id": "1", "status": "PAUSED"},
        op="pause",
        before={"id": "1", "status": "LIVE"},
    )
    assert out["audit_id"]
    [entry] = audit_entries(tmp_audit_log)
    assert entry["before"] == {"id": "1", "status": "LIVE"}
    assert entry["response_body"] == {"id": "1"}
    if os.name == "posix":
        assert stat.S_IMODE(tmp_audit_log.stat().st_mode) == 0o600
        assert stat.S_IMODE(tmp_audit_log.parent.stat().st_mode) == 0o700


@respx.mock
def test_responses_over_the_cap_are_refused(config: Config, audit: AuditLog) -> None:
    c = AppLovinClient(dataclasses.replace(config, max_download_mb=1), audit)
    respx.get(f"{R}/maxReport").mock(return_value=httpx.Response(200, content=b"x" * (2 * 1024**2)))
    with pytest.raises(ValueError, match="APPLOVIN_MAX_DOWNLOAD_MB"):
        c.request("max_reporting", "GET", "/maxReport")


@respx.mock
def test_download_only_from_signed_storage(client: AppLovinClient) -> None:
    with pytest.raises(ValueError, match="unexpected host"):
        client.download("https://evil.example.com/report.csv")
    with pytest.raises(ValueError):
        client.download("http://applovin-externalreports.s3.amazonaws.com/x.csv")
    route = respx.get("https://applovin-externalreports.s3.amazonaws.com/x.csv").mock(
        return_value=httpx.Response(200, content=b"a,b\n1,2\n")
    )
    assert client.download("https://applovin-externalreports.s3.amazonaws.com/x.csv").startswith(
        b"a,b"
    )
    req = route.calls.last.request
    assert "api_key" not in str(req.url)
    assert "Api-Key" not in req.headers
    assert "Authorization" not in req.headers


@respx.mock
def test_paginate_pages_stops_on_a_short_page(client: AppLovinClient) -> None:
    route = respx.get(f"{ADS}/campaign/list").mock(
        side_effect=[
            httpx.Response(200, json=[{"id": i} for i in range(2)]),
            httpx.Response(200, json=[{"id": 9}]),
        ]
    )
    items, truncated = client.paginate_pages("ads", "/campaign/list", page_size=2, max_results=10)
    assert [i["id"] for i in items] == [0, 1, 9]
    assert truncated is False
    pages = [parse_qs(urlparse(str(c.request.url)).query)["page"] for c in route.calls]
    assert pages == [["1"], ["2"]]


def test_rate_limiter_refuses_long_waits() -> None:
    limiter = RateLimiter(2, 3600)
    limiter.acquire("test")
    limiter.acquire("test")
    with pytest.raises(RuntimeError, match="rate limit"):
        limiter.acquire("test", max_wait=1)


def test_http_library_logs_never_carry_keys(caplog: pytest.LogCaptureFixture) -> None:
    import logging

    from applovin_mcp._logging import configure_default
    from applovin_mcp.credentials import register_secret

    configure_default()
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    register_secret(REPORT_KEY)
    with caplog.at_level(logging.WARNING, logger="httpx"):
        logging.getLogger("httpx").warning("GET %s/report?api_key=%s", R, REPORT_KEY)
    assert REPORT_KEY not in caplog.text
    assert "api_key=***" in caplog.text
