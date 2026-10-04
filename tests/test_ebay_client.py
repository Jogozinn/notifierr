from __future__ import annotations

import asyncio

import httpx

import backend.ebay_client as ebay_client_module
from backend.config import Settings
from backend.ebay_client import EbayClient, EBAY_HTTP_TIMEOUT_SECONDS


def test_ebay_client_disables_environment_proxy_for_oauth(monkeypatch):
    client_kwargs: list[dict] = []

    class StubAsyncClient:
        def __init__(self, **kwargs):
            client_kwargs.append(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, *, headers, data):
            del headers, data
            return httpx.Response(
                200,
                json={
                    "access_token": "token",
                    "token_type": "Application Access Token",
                    "expires_in": 7200,
                },
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr(ebay_client_module.httpx, "AsyncClient", StubAsyncClient)
    settings = Settings(ebay_client_id="client-id", ebay_client_secret="client-secret")

    token = asyncio.run(EbayClient(settings)._get_access_token())

    assert token == "token"
    assert client_kwargs == [{"timeout": EBAY_HTTP_TIMEOUT_SECONDS, "trust_env": False}]


def test_ebay_client_disables_environment_proxy_for_search(monkeypatch):
    client_kwargs: list[dict] = []

    class StubAsyncClient:
        def __init__(self, **kwargs):
            client_kwargs.append(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, *, headers, data):
            del headers, data
            return httpx.Response(
                200,
                json={
                    "access_token": "token",
                    "token_type": "Application Access Token",
                    "expires_in": 7200,
                },
                request=httpx.Request("POST", url),
            )

        async def get(self, url, *, headers, params=None):
            del headers, params
            return httpx.Response(
                200,
                json={"itemSummaries": []},
                request=httpx.Request("GET", url),
            )

    monkeypatch.setattr(ebay_client_module.httpx, "AsyncClient", StubAsyncClient)
    settings = Settings(ebay_client_id="client-id", ebay_client_secret="client-secret")

    results = asyncio.run(EbayClient(settings).search(["iphone"], limit=1))

    assert results == []
    assert client_kwargs == [
        {"timeout": EBAY_HTTP_TIMEOUT_SECONDS, "trust_env": False},
        {"timeout": EBAY_HTTP_TIMEOUT_SECONDS, "trust_env": False},
    ]
