from __future__ import annotations

import json
import subprocess
import sys
from urllib.request import ProxyHandler

import pytest

from src.antibot_cv.automation.npc_census_http import LocalCensusHttpTransport
from src.antibot_cv.automation.npc_census_model import AreaNpcEndpointObservation


def endpoint() -> AreaNpcEndpointObservation:
    return AreaNpcEndpointObservation(
        actor_key="client", document_revision=1, location_id="102",
        location_name="Area", endpoint_id="0", endpoint_name="Npc",
        snapshot_id="area-npcs-epoch-1", generated_at="2026-07-20T00:00:00Z",
        route_ref="398",
    )


def test_http_transport_sends_exact_zero_endpoint_contract(monkeypatch) -> None:
    calls = []

    class Response:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def read(self, _limit):
            return json.dumps({"ok": True, "dialog": {"ok": True}}).encode()

    def fake_open(request, timeout):
        calls.append((request, timeout))
        return Response()

    transport = LocalCensusHttpTransport(
        server_url="http://127.0.0.1:17654", client_id="client",
    )
    transport._open = fake_open
    assert transport.inspect_endpoint(endpoint()) == {"ok": True}
    payload = json.loads(calls[0][0].data)
    assert payload["npcId"] == "0"
    assert payload["expectedRouteRef"] == "398"


def test_http_transport_does_not_return_unreconciled_dialogue(monkeypatch) -> None:
    class Response:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def read(self, _limit): return b'{"ok":false,"dialog":{"npcId":"0"}}'

    transport = LocalCensusHttpTransport(
        server_url="http://127.0.0.1:17654", client_id="client",
    )
    transport._open = lambda *_args, **_kwargs: Response()
    with pytest.raises(OSError):
        transport.inspect_endpoint(endpoint())


def test_http_transport_rejects_oversized_response() -> None:
    class Response:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def read(self, limit):
            assert limit == 1_000_001
            return b"x" * limit

    transport = LocalCensusHttpTransport(
        server_url="http://127.0.0.1:17654", client_id="client",
    )
    transport._open = lambda *_args, **_kwargs: Response()
    with pytest.raises(OSError, match="hard cap"):
        transport.area_snapshot()


@pytest.mark.parametrize("url", [
    "https://127.0.0.1:17654", "http://example.com:17654",
    "http://127.0.0.1:17654/path", "http://127.0.0.1",
])
def test_http_transport_rejects_non_loopback_or_non_origin_url(url) -> None:
    with pytest.raises(ValueError):
        LocalCensusHttpTransport(server_url=url, client_id="client")


def test_direct_launcher_imports_repository_and_requires_explicit_live() -> None:
    help_result = subprocess.run(
        [sys.executable, "scripts/collect_npc_census_current.py", "--help"],
        text=True, capture_output=True, check=False,
    )
    assert help_result.returncode == 0
    denied = subprocess.run(
        [sys.executable, "scripts/collect_npc_census_current.py", "--client-id", "x"],
        text=True, capture_output=True, check=False,
    )
    assert denied.returncode != 0
    assert "explicit --live" in denied.stderr


def test_http_transport_disables_environment_proxies(monkeypatch) -> None:
    monkeypatch.setenv("HTTP_PROXY", "http://203.0.113.1:9999")
    monkeypatch.setenv("HTTPS_PROXY", "http://203.0.113.1:9999")
    transport = LocalCensusHttpTransport(
        server_url="http://127.0.0.1:17654", client_id="client",
    )
    opener = transport._open.__self__
    proxy_handlers = [handler for handler in opener.handlers if isinstance(handler, ProxyHandler)]
    assert all(handler.proxies == {} for handler in proxy_handlers)
    assert not any(getattr(handler, "proxies", None) for handler in opener.handlers)
