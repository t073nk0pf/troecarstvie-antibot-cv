"""Local control-server transport for the bounded NPC census collector."""

from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from src.antibot_cv.automation.npc_census_model import AreaNpcEndpointObservation


class LocalCensusHttpTransport:
    def __init__(self, *, server_url: str, client_id: str, timeout_s: float = 10.0) -> None:
        parsed = urlsplit(server_url)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username is not None or parsed.password is not None
            or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
            or parsed.port is None
        ):
            raise ValueError("census server URL must be an explicit loopback HTTP origin")
        self.server_url = server_url.rstrip("/")
        self.client_id = client_id
        self.timeout_s = max(1.0, min(30.0, float(timeout_s)))
        self._open = build_opener(ProxyHandler({}), _NoRedirectHandler()).open

    def area_snapshot(self) -> object:
        response = self._request(
            "GET", "/api/area-npcs?" + urlencode({"clientId": self.client_id}),
        )
        snapshot = response.get("snapshot")
        if response.get("ok") is not True or not isinstance(snapshot, dict):
            raise OSError("area snapshot unavailable")
        return snapshot

    def inspect_endpoint(self, endpoint: AreaNpcEndpointObservation) -> object:
        response = self._request("POST", "/api/inspect-exact-npc", {
            "clientId": self.client_id,
            "expectedSnapshotId": endpoint.snapshot_id,
            "expectedLocationId": endpoint.location_id,
            "npcId": endpoint.endpoint_id,
            "expectedRouteRef": endpoint.route_ref,
            "expectedName": endpoint.endpoint_name,
            "expectedObservationEpoch": endpoint.snapshot_id.split("-")[-2],
            "expectedObservationRevision": endpoint.document_revision,
            "expectedGeneratedAt": endpoint.generated_at,
        })
        snapshot = response.get("dialog")
        if response.get("ok") is not True or not isinstance(snapshot, dict):
            raise OSError("NPC inspection was not reconciled")
        return snapshot

    def open_area(self) -> bool:
        response = self._request("POST", "/api/open-area", {"clientId": self.client_id})
        return response.get("ok") is True and response.get("submitted") is True

    def _request(
        self, method: str, path: str, payload: dict[str, object] | None = None,
    ) -> dict[str, object]:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(
            self.server_url + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"} if data is not None else {},
        )
        try:
            with self._open(request, timeout=self.timeout_s) as response:
                raw = response.read(1_000_001)
                if len(raw) > 1_000_000:
                    raise OSError("local census response exceeds hard cap")
                decoded = json.loads(raw.decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise OSError("local census transport failed") from exc
        if not isinstance(decoded, dict):
            raise OSError("local census response is invalid")
        return decoded


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None
