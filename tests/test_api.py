"""API client tests — bearer headers, product proof, redirects, auth errors."""

from __future__ import annotations

import traceback

import pytest
from aiohttp import ClientSession, web

from zigbeelens.api import ZigbeeLensApiClient
from zigbeelens.exceptions import (
    ZigbeeLensAuthError,
    ZigbeeLensConnectionError,
    ZigbeeLensInvalidResponseError,
)

SENTINEL_TOKEN = "zl-hacs-sentinel-token-value-aaaa"


@pytest.fixture
async def recording_core(aiohttp_server, sample_health, sample_dashboard, sample_config_status):
    """aiohttp Core stub that records path/method/Authorization."""
    requests: list[dict[str, object]] = []

    async def record(request: web.Request) -> None:
        requests.append(
            {
                "path": request.path,
                "method": request.method,
                "authorization": request.headers.get("Authorization"),
                "x_api_key": request.headers.get("X-ZigbeeLens-Api-Key"),
                "query": dict(request.query),
                "cookie": request.headers.get("Cookie"),
                "has_body": request.can_read_body and request.content_length not in (None, 0),
            }
        )

    async def health(request: web.Request):
        await record(request)
        return web.json_response(sample_health)

    async def dashboard(request: web.Request):
        await record(request)
        return web.json_response(sample_dashboard)

    async def config_status(request: web.Request):
        await record(request)
        return web.json_response(sample_config_status)

    async def version(request: web.Request):
        await record(request)
        return web.json_response({"version": "0.1.0", "name": "zigbeelens-core"})

    async def capabilities(request: web.Request):
        await record(request)
        return web.json_response(
            {
                "product": "zigbeelens",
                "version": "0.1.0",
                "decision_contract_version": 2,
                "capabilities": {
                    "dashboard": True,
                    "shared_decisions": True,
                    "companion_decision_summary": True,
                    "decision_only_diagnostic_payloads": True,
                    "report_contract_v3": True,
                    "decision_mqtt_summary": True,
                    "legacy_health_lens_payloads": False,
                },
                "decision_surfaces": {
                    "dashboard_decision_summary": True,
                    "dashboard_investigation_priorities": True,
                    "dashboard_data_coverage_warnings": True,
                    "network_decision_badges": True,
                    "device_decision_badges": True,
                },
            }
        )

    app = web.Application()
    app["requests"] = requests
    app.router.add_get("/api/health", health)
    app.router.add_get("/api/dashboard", dashboard)
    app.router.add_get("/api/config/status", config_status)
    app.router.add_get("/api/version", version)
    app.router.add_get("/api/capabilities", capabilities)
    server = await aiohttp_server(app)
    return server, requests


@pytest.fixture
async def core_url(recording_core):
    server, _ = recording_core
    return str(server.make_url("/"))


@pytest.mark.asyncio
async def test_health_success(core_url, sample_health):
    async with ClientSession() as session:
        client = ZigbeeLensApiClient(session, core_url)
        payload = await client.async_get_health()
    assert payload["status"] == sample_health["status"]


@pytest.mark.asyncio
async def test_dashboard_success(core_url):
    async with ClientSession() as session:
        client = ZigbeeLensApiClient(session, core_url)
        payload = await client.async_get_dashboard()
    assert payload["decision_summary"]["overall_status"] == "review_first"
    assert "generated_at" in payload
    assert "networks" in payload


@pytest.mark.asyncio
async def test_url_join_handles_trailing_slash(core_url):
    async with ClientSession() as session:
        client = ZigbeeLensApiClient(session, core_url.rstrip("/") + "/")
        assert client.core_url == core_url.rstrip("/")
        assert client.api_url("api/health").endswith("/api/health")


@pytest.mark.asyncio
async def test_invalid_json(aiohttp_server):
    async def bad(_request):
        return web.Response(text="not-json", content_type="application/json")

    app = web.Application()
    app.router.add_get("/api/health", bad)
    server = await aiohttp_server(app)
    async with ClientSession() as session:
        client = ZigbeeLensApiClient(session, str(server.make_url("/")))
        with pytest.raises(ZigbeeLensInvalidResponseError) as exc_info:
            await client.async_get_health()
    assert SENTINEL_TOKEN not in str(exc_info.value)
    assert SENTINEL_TOKEN not in "".join(
        traceback.format_exception(type(exc_info.value), exc_info.value, exc_info.value.__traceback__)
    )


@pytest.mark.asyncio
async def test_non_2xx(aiohttp_server):
    async def err(_request):
        raise web.HTTPBadRequest()

    app = web.Application()
    app.router.add_get("/api/health", err)
    server = await aiohttp_server(app)
    async with ClientSession() as session:
        client = ZigbeeLensApiClient(session, str(server.make_url("/")))
        with pytest.raises(ZigbeeLensInvalidResponseError):
            await client.async_get_health()


@pytest.mark.asyncio
async def test_connection_error():
    async with ClientSession() as session:
        client = ZigbeeLensApiClient(session, "http://127.0.0.1:1/")
        with pytest.raises(ZigbeeLensConnectionError):
            await client.async_get_health()


@pytest.mark.asyncio
async def test_capabilities_success(core_url):
    async with ClientSession() as session:
        client = ZigbeeLensApiClient(session, core_url)
        payload = await client.async_get_capabilities()
    assert payload["product"] == "zigbeelens"
    assert payload["decision_contract_version"] == 2


@pytest.mark.asyncio
async def test_validate_core(core_url, recording_core):
    _, requests = recording_core
    async with ClientSession() as session:
        client = ZigbeeLensApiClient(session, core_url, api_token=SENTINEL_TOKEN)
        health = await client.async_validate_core()
    assert health["status"] == "ok"
    assert [r["path"] for r in requests] == ["/api/version", "/api/health"]
    assert requests[0]["authorization"] is None
    assert requests[1]["authorization"] == f"Bearer {SENTINEL_TOKEN}"


@pytest.mark.asyncio
async def test_no_token_sends_no_authorization(core_url, recording_core):
    _, requests = recording_core
    async with ClientSession() as session:
        client = ZigbeeLensApiClient(session, core_url)
        await client.async_get_version()
        await client.async_get_health()
    assert requests[0]["path"] == "/api/version"
    assert requests[0]["authorization"] is None
    assert requests[1]["path"] == "/api/health"
    assert requests[1]["authorization"] is None
    assert all(r["x_api_key"] is None for r in requests)
    assert all(not r["query"] for r in requests)
    assert all(r["cookie"] is None for r in requests)


@pytest.mark.asyncio
async def test_token_on_protected_only(core_url, recording_core):
    _, requests = recording_core
    async with ClientSession() as session:
        client = ZigbeeLensApiClient(session, core_url, api_token=SENTINEL_TOKEN)
        await client.async_get_version()
        await client.async_get_health()
        await client.async_get_dashboard()
        await client.async_get_config_status()
        await client.async_get_capabilities()
    assert requests[0]["authorization"] is None
    for r in requests[1:]:
        assert r["authorization"] == f"Bearer {SENTINEL_TOKEN}"
        assert r["x_api_key"] is None
        assert not r["query"]
        assert r["cookie"] is None


@pytest.mark.asyncio
async def test_wrong_product_never_sees_token(aiohttp_server):
    seen: list[str | None] = []

    async def version(request: web.Request):
        seen.append(request.headers.get("Authorization"))
        return web.json_response({"version": "1.0", "name": "other-product"})

    async def health(request: web.Request):
        seen.append(request.headers.get("Authorization"))
        return web.json_response({"status": "ok", "version": "1.0"})

    app = web.Application()
    app.router.add_get("/api/version", version)
    app.router.add_get("/api/health", health)
    server = await aiohttp_server(app)
    async with ClientSession() as session:
        client = ZigbeeLensApiClient(
            session, str(server.make_url("/")), api_token=SENTINEL_TOKEN
        )
        with pytest.raises(ZigbeeLensInvalidResponseError):
            await client.async_validate_core()
    assert seen == [None]
    assert SENTINEL_TOKEN not in repr(client)


@pytest.mark.asyncio
async def test_redirect_not_followed(aiohttp_server):
    hit_target = {"count": 0}

    async def redirect(_request: web.Request):
        raise web.HTTPFound(location="http://evil.example/steal")

    async def steal(_request: web.Request):
        hit_target["count"] += 1
        return web.json_response({})

    app = web.Application()
    app.router.add_get("/api/health", redirect)
    app.router.add_get("/steal", steal)
    server = await aiohttp_server(app)
    async with ClientSession() as session:
        client = ZigbeeLensApiClient(
            session, str(server.make_url("/")), api_token=SENTINEL_TOKEN
        )
        with pytest.raises(ZigbeeLensInvalidResponseError):
            await client.async_get_health()
    assert hit_target["count"] == 0


@pytest.mark.asyncio
async def test_protected_401_is_auth_error(aiohttp_server):
    async def deny(_request: web.Request):
        return web.json_response({"detail": "nope-secret-body"}, status=401)

    app = web.Application()
    app.router.add_get("/api/health", deny)
    server = await aiohttp_server(app)
    async with ClientSession() as session:
        client = ZigbeeLensApiClient(
            session, str(server.make_url("/")), api_token=SENTINEL_TOKEN
        )
        with pytest.raises(ZigbeeLensAuthError) as exc_info:
            await client.async_get_health()
    assert str(exc_info.value) == "Authentication required"
    rendered = "".join(
        traceback.format_exception(type(exc_info.value), exc_info.value, exc_info.value.__traceback__)
    )
    assert SENTINEL_TOKEN not in rendered
    assert "nope-secret-body" not in rendered
    assert SENTINEL_TOKEN not in repr(client)


@pytest.mark.asyncio
async def test_public_version_401_is_invalid_response(aiohttp_server):
    async def deny(_request: web.Request):
        return web.json_response({}, status=401)

    app = web.Application()
    app.router.add_get("/api/version", deny)
    server = await aiohttp_server(app)
    async with ClientSession() as session:
        client = ZigbeeLensApiClient(
            session, str(server.make_url("/")), api_token=SENTINEL_TOKEN
        )
        with pytest.raises(ZigbeeLensInvalidResponseError):
            await client.async_get_version()


@pytest.mark.asyncio
async def test_protected_403_is_invalid_response(aiohttp_server):
    async def forbid(_request: web.Request):
        return web.json_response({}, status=403)

    app = web.Application()
    app.router.add_get("/api/health", forbid)
    server = await aiohttp_server(app)
    async with ClientSession() as session:
        client = ZigbeeLensApiClient(
            session, str(server.make_url("/")), api_token=SENTINEL_TOKEN
        )
        with pytest.raises(ZigbeeLensInvalidResponseError):
            await client.async_get_health()


@pytest.mark.asyncio
async def test_token_isolation_between_clients(recording_core):
    server, requests = recording_core
    url = str(server.make_url("/"))
    token_a = "a" * 32
    token_b = "b" * 32
    async with ClientSession() as session:
        client_a = ZigbeeLensApiClient(session, url, api_token=token_a)
        client_b = ZigbeeLensApiClient(session, url, api_token=token_b)
        await client_a.async_get_health()
        await client_b.async_get_health()
    assert requests[0]["authorization"] == f"Bearer {token_a}"
    assert requests[1]["authorization"] == f"Bearer {token_b}"


@pytest.mark.asyncio
async def test_runtime_token_frozen_after_construction(recording_core):
    server, requests = recording_core
    url = str(server.make_url("/"))
    mutable = {"token": SENTINEL_TOKEN}
    async with ClientSession() as session:
        client = ZigbeeLensApiClient(session, url, api_token=mutable["token"])
        mutable["token"] = "rotated-" + ("x" * 24)
        await client.async_get_health()
    assert requests[0]["authorization"] == f"Bearer {SENTINEL_TOKEN}"


@pytest.mark.asyncio
async def test_repr_hides_token(core_url):
    async with ClientSession() as session:
        client = ZigbeeLensApiClient(session, core_url, api_token=SENTINEL_TOKEN)
    text = repr(client)
    assert SENTINEL_TOKEN not in text
    assert "api_token_configured=True" in text
    assert "Authorization" not in text
