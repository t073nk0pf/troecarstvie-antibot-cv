from __future__ import annotations

import json
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Iterator
from urllib.parse import parse_qs, urlparse

from src.antibot_cv.automation.bridge_command_registry import bridge_command_is_mutating


DEFAULT_INJECTOR_HOST = "127.0.0.1"
DEFAULT_INJECTOR_PORT = 17654
CURRENT_BRIDGE_VERSION = "2026-07-21-npc-census-v80"


def _optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _command_is_mutating(command: str, payload: dict[str, Any]) -> bool:
    return bridge_command_is_mutating(command, payload)
class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    allow_reuse_port = False


@dataclass(frozen=True)
class InjectorResult:
    ok: bool
    message: str = ""
    client_id: str | None = None


class BrowserInjectorServer:
    CLIENT_TTL_S = 300.0
    MAX_CLIENTS = 64

    def __init__(self, host: str = DEFAULT_INJECTOR_HOST, port: int = DEFAULT_INJECTOR_PORT) -> None:
        self.host = host
        self.port = port
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._pending_condition = threading.Condition(self._lock)
        self._pending: dict[str, dict[str, Any]] = {}
        self._thread_local = threading.local()
        self._clients: dict[str, dict[str, Any]] = {}
        self._last_client_seen = 0.0
        self._last_client_id: str | None = None
        self._last_client_version: str | None = None
        self._trusted_extension_origin: str | None = None
        # Owner authority is monotonic across every direct/child target.  Target
        # ownership is separate: it records which owner fence may mutate one
        # concrete tab, but must never establish an independent authority clock.
        self._mutation_owner_fences: dict[
            tuple[str, int], tuple[str, int, int, int]
        ] = {}
        self._mutation_target_fences: dict[
            tuple[str, int], tuple[str, int, int, int]
        ] = {}
        self._api_handler: Callable[[str, str, dict[str, list[str]], dict[str, Any] | None], tuple[int, dict[str, Any]]] | None = None

    def set_api_handler(
        self,
        handler: Callable[[str, str, dict[str, list[str]], dict[str, Any] | None], tuple[int, dict[str, Any]]] | None,
    ) -> None:
        self._api_handler = handler

    def start(self) -> None:
        if self._server is not None:
            return
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_OPTIONS(self) -> None:
                if not self._origin_allowed(allow_pair=True):
                    self._send_json({"ok": False, "error": "origin_forbidden"}, status=403)
                    return
                self._send_json({"ok": True})

            def do_GET(self) -> None:
                if not self._origin_allowed(allow_pair=True):
                    self._send_json({"ok": False, "error": "origin_forbidden"}, status=403)
                    return
                parsed = urlparse(self.path)
                if parsed.path.startswith("/api/") and outer._api_handler is not None:
                    self._handle_api("GET", parsed.path, parse_qs(parsed.query), None)
                    return
                if parsed.path == "/health":
                    self._send_json({"ok": True, "client_seen": outer.client_seen_recently()})
                    return
                if parsed.path != "/next":
                    self._send_json({"ok": False, "error": "not_found"}, status=404)
                    return
                parsed_query = parse_qs(parsed.query)
                client_id = parsed_query.get("client", [None])[0]
                client_version = parsed_query.get("version", [None])[0]
                client_href = parsed_query.get("href", [""])[0]
                client_title = parsed_query.get("title", [""])[0]
                client_profile = parsed_query.get("profile", [""])[0]
                client_tab_id = parsed_query.get("tab", [None])[0]
                client_opener_tab_id = parsed_query.get("opener", [None])[0]
                wait_s = min(30.0, max(0.0, float(parsed_query.get("wait", ["0"])[0] or 0)))
                with outer._lock:
                    if client_id:
                        outer._record_client_locked(
                            str(client_id),
                            client_version,
                            href=client_href,
                            title=client_title,
                            profile_id=client_profile,
                            tab_id=client_tab_id,
                            opener_tab_id=client_opener_tab_id,
                        )
                    else:
                        outer._last_client_seen = time.monotonic()
                    if client_version:
                        outer._last_client_version = str(client_version)
                    command = outer._next_command_for_client_locked(str(client_id) if client_id else None, client_version)
                    deadline = time.monotonic() + wait_s
                    while command is None and wait_s > 0 and time.monotonic() < deadline:
                        outer._pending_condition.wait(deadline - time.monotonic())
                        command = outer._next_command_for_client_locked(
                            str(client_id) if client_id else None,
                            client_version,
                        )
                self._send_json({"ok": True, "command": command})

            def do_POST(self) -> None:
                if not self._origin_allowed(allow_pair=True):
                    self._send_json({"ok": False, "error": "origin_forbidden"}, status=403)
                    return
                parsed = urlparse(self.path)
                if parsed.path.startswith("/api/") and outer._api_handler is not None:
                    payload = self._read_json_body()
                    if payload is None:
                        self._send_json({"ok": False, "error": "bad_json"}, status=400)
                        return
                    self._handle_api("POST", parsed.path, parse_qs(parsed.query), payload)
                    return
                if parsed.path != "/ack":
                    self._send_json({"ok": False, "error": "not_found"}, status=404)
                    return
                payload = self._read_json_body()
                if payload is None:
                    self._send_json({"ok": False, "error": "bad_json"}, status=400)
                    return
                result = InjectorResult(
                    ok=bool(payload.get("ok")),
                    message=str(payload.get("message", "")),
                    client_id=None if payload.get("client_id") is None else str(payload.get("client_id")),
                )
                ack_error: tuple[int, str] | None = None
                with outer._lock:
                    command_id = str(payload.get("id") or "")
                    pending = outer._pending.get(command_id)
                    if pending is None:
                        ack_error = (404, "ack_unknown_command")
                    else:
                        target_client_id = str(pending.get("target_client_id") or "")
                        if not target_client_id:
                            ack_error = (409, "ack_command_not_claimed")
                        elif result.client_id != target_client_id:
                            ack_error = (409, "ack_client_mismatch")
                        elif pending.get("result") is not None:
                            ack_error = (409, "ack_duplicate")
                    if pending is not None and ack_error is None:
                        pending["result"] = result
                        event = pending.get("event")
                        if isinstance(event, threading.Event):
                            event.set()
                if ack_error is not None:
                    status, error = ack_error
                    self._send_json({"ok": False, "error": error}, status=status)
                    return
                self._send_json({"ok": True})

            def log_message(self, format: str, *args: object) -> None:
                return

            def _read_json_body(self) -> dict[str, Any] | None:
                length = int(self.headers.get("content-length", "0") or "0")
                raw = self.rfile.read(length)
                try:
                    payload = json.loads(raw.decode("utf-8")) if raw else {}
                except json.JSONDecodeError:
                    return None
                return payload if isinstance(payload, dict) else {}

            def _handle_api(self, method: str, path: str, query: dict[str, list[str]], payload: dict[str, Any] | None) -> None:
                if outer._api_handler is None:
                    self._send_json({"ok": False, "error": "api_not_configured"}, status=404)
                    return
                try:
                    status, response = outer._api_handler(method, path, query, payload)
                except Exception as exc:
                    self._send_json({"ok": False, "error": f"api_error:{exc}"}, status=500)
                    return
                self._send_json(response, status=status)

            def _send_json(self, payload: dict[str, Any], *, status: int = 200) -> None:
                body = json.dumps(payload).encode("utf-8")
                try:
                    self.send_response(status)
                    self.send_header("content-type", "application/json")
                    origin = self.headers.get("origin")
                    self.send_header("access-control-allow-origin", origin if origin and self._origin_allowed() else "*")
                    self.send_header("access-control-allow-methods", "GET,POST,OPTIONS")
                    self.send_header("access-control-allow-headers", "content-type")
                    self.send_header("access-control-allow-private-network", "true")
                    self.send_header("content-length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    return

            def _origin_allowed(self, *, allow_pair: bool = False) -> bool:
                origin = str(self.headers.get("origin") or "").strip().lower()
                if not origin:
                    return True
                if not origin.startswith("chrome-extension://"):
                    return False
                with outer._lock:
                    if outer._trusted_extension_origin is None and allow_pair:
                        outer._trusted_extension_origin = origin
                    return origin == outer._trusted_extension_origin

        self._server = ReusableThreadingHTTPServer((self.host, self.port), Handler)
        self.port = int(self._server.server_address[1])
        self._thread = threading.Thread(target=self._server.serve_forever, name="browser-injector", daemon=True)
        self._thread.start()

    def execute(
        self,
        command: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout_s: float = 2.5,
        required_version: str | None = CURRENT_BRIDGE_VERSION,
        client_id: str | None = None,
        cancellation_event: threading.Event | None = None,
        deadline_monotonic: float | None = None,
    ) -> InjectorResult:
        self.start()
        try:
            payload_snapshot = json.loads(json.dumps(payload or {}, allow_nan=False))
        except (TypeError, ValueError):
            return InjectorResult(False, "injector_payload_not_json", client_id=client_id)
        if not isinstance(payload_snapshot, dict):
            return InjectorResult(False, "injector_payload_not_object", client_id=client_id)
        command_id = uuid.uuid4().hex
        result_event = threading.Event()
        target_client_id = client_id or self.current_client_id
        with self._lock:
            if target_client_id is None:
                resolved = self._resolve_single_recent_client_locked(required_version=required_version)
                if isinstance(resolved, InjectorResult):
                    return resolved
                target_client_id = resolved
            elif target_client_id:
                target_client_id = self._resolve_client_alias_locked(
                    target_client_id,
                    required_version=required_version,
                )
            target_snapshot = self._clients.get(target_client_id) if target_client_id else None
            fence_rejection = self._validate_mutation_fence(
                command, payload_snapshot,
                target_snapshot=target_snapshot if isinstance(target_snapshot, dict) else None,
            )
            if fence_rejection is not None:
                return InjectorResult(False, fence_rejection, client_id=target_client_id)
            self._pending[command_id] = {
                "command": {
                    "id": command_id,
                    "type": command,
                    "payload": payload_snapshot,
                },
                "required_version": required_version,
                "target_client_id": target_client_id,
                "event": result_event,
                "result": None,
                "created_at": time.monotonic(),
                "delivered_count": 0,
                "mutating": _command_is_mutating(command, payload_snapshot),
            }
            self._pending_condition.notify_all()
        wait_deadline = time.monotonic() + max(0.0, timeout_s)
        if deadline_monotonic is not None:
            wait_deadline = min(wait_deadline, deadline_monotonic)
        cancelled = False
        while not result_event.is_set():
            if cancellation_event is not None and cancellation_event.is_set():
                cancelled = True
                break
            remaining = wait_deadline - time.monotonic()
            if remaining <= 0:
                break
            result_event.wait(min(0.05, remaining))
        if not result_event.is_set():
            with self._lock:
                pending = self._pending.pop(command_id, None)
                delivered_count = int(pending.get("delivered_count", 0) or 0) if isinstance(pending, dict) else 0
            if cancelled:
                return InjectorResult(False, "injector_cancelled", client_id=target_client_id)
            message = "injector_ack_timeout" if delivered_count > 0 else "injector_delivery_timeout"
            return InjectorResult(False, message, client_id=target_client_id)
        with self._lock:
            pending = self._pending.get(command_id)
            result = pending.get("result") if isinstance(pending, dict) else None
            self._pending.pop(command_id, None)
            return result if isinstance(result, InjectorResult) else InjectorResult(False, "injector_missing_result")

    def _validate_mutation_fence(
        self,
        command: str,
        payload: dict[str, Any],
        *,
        target_snapshot: dict[str, Any] | None = None,
    ) -> str | None:
        fence = payload.get("mutationFence")
        if fence is None:
            return None
        if not isinstance(fence, dict) or set(fence) != {
            "profile_id", "tab_id", "actor_generation", "fencing_token",
        }:
            return "mutation_fence_malformed"
        try:
            profile_id = str(fence["profile_id"]).strip()
            tab_id = int(fence["tab_id"])
            generation = int(fence["actor_generation"])
            token = int(fence["fencing_token"])
        except (TypeError, ValueError):
            return "mutation_fence_invalid"
        if not profile_id or min(tab_id, generation, token) < 0 or generation == 0 or token == 0:
            return "mutation_fence_invalid"
        owner_key = (profile_id, tab_id)
        target_key = owner_key
        if target_snapshot is not None and _command_is_mutating(command, payload):
            actual_profile = str(target_snapshot.get("profile_id") or "")
            actual_tab = target_snapshot.get("tab_id")
            if actual_profile == profile_id and actual_tab == tab_id:
                if payload.get("mutationTarget") is not None:
                    return "mutation_target_unexpected"
            else:
                binding = payload.get("mutationTarget")
                if not isinstance(binding, dict) or set(binding) != {
                    "kind", "profile_id", "tab_id", "opener_tab_id",
                }:
                    return "mutation_child_authority_missing"
                if binding.get("kind") != "opener_child":
                    return "mutation_child_authority_invalid"
                binding_profile = str(binding.get("profile_id") or "")
                binding_tab = binding.get("tab_id")
                binding_opener = binding.get("opener_tab_id")
                if binding_profile != actual_profile or binding_tab != actual_tab:
                    return "mutation_target_identity_mismatch"
                if actual_profile != profile_id or binding_opener != tab_id \
                        or target_snapshot.get("opener_tab_id") != tab_id:
                    return "mutation_opener_identity_mismatch"
                if isinstance(actual_tab, bool) or not isinstance(actual_tab, int):
                    return "mutation_target_identity_missing"
                target_key = (actual_profile, actual_tab)
        candidate = (owner_key[0], owner_key[1], generation, token)
        with self._lock:
            current_owner = self._mutation_owner_fences.get(owner_key)
            if current_owner is not None and (
                generation < current_owner[2]
                or generation == current_owner[2] and token < current_owner[3]
            ):
                return "mutation_fence_stale"
            current_target = self._mutation_target_fences.get(target_key)
            if current_target is not None and current_target[:2] != owner_key:
                return "mutation_child_owner_conflict"
            if current_owner is None or (
                generation > current_owner[2]
                or generation == current_owner[2] and token > current_owner[3]
            ):
                self._mutation_owner_fences[owner_key] = candidate
            if current_target != candidate:
                self._mutation_target_fences[target_key] = candidate
        return None

    @contextmanager
    def client_context(self, client_id: str | None) -> Iterator[None]:
        previous = getattr(self._thread_local, "client_id", None)
        self._thread_local.client_id = client_id
        try:
            yield
        finally:
            self._thread_local.client_id = previous

    @property
    def current_client_id(self) -> str | None:
        value = getattr(self._thread_local, "client_id", None)
        return str(value) if value else None

    def set_current_client_id(self, client_id: str | None) -> None:
        self._thread_local.client_id = str(client_id) if client_id else None

    def client_seen_recently(self, *, within_s: float = 5.0) -> bool:
        return (time.monotonic() - self._last_client_seen) <= within_s

    def client_snapshots(self, *, within_s: float = 15.0) -> list[dict[str, Any]]:
        now = time.monotonic()
        with self._lock:
            clients = []
            for client_id, data in self._clients.items():
                last_seen = float(data.get("last_seen", 0.0) or 0.0)
                clients.append(
                    {
                        "client_id": client_id,
                        "client_seen": (now - last_seen) <= within_s,
                        "client_version": data.get("version"),
                        "version_ok": data.get("version") == CURRENT_BRIDGE_VERSION,
                        "href": data.get("href") or "",
                        "title": data.get("title") or "",
                        "profile_id": data.get("profile_id") or "",
                        "tab_id": data.get("tab_id"),
                        "opener_tab_id": data.get("opener_tab_id"),
                        "last_seen_ago_s": max(0.0, now - last_seen),
                    }
                )
            return sorted(clients, key=lambda item: item["last_seen_ago_s"])

    def client_snapshot(self, client_id: str | None = None) -> dict[str, Any]:
        if client_id:
            for client in self.client_snapshots():
                if client.get("client_id") == client_id:
                    return client
            return {
                "client_id": client_id,
                "client_seen": False,
                "client_version": None,
                "version_ok": False,
                "href": "",
                "title": "",
            }
        return {
            "client_id": self.last_client_id,
            "client_seen": self.client_seen_recently(),
            "client_version": self.last_client_version,
            "version_ok": self.last_client_version == CURRENT_BRIDGE_VERSION,
            "href": "",
            "title": "",
        }

    def _record_client_locked(
        self,
        client_id: str,
        version: str | None,
        *,
        href: str = "",
        title: str = "",
        profile_id: object = "",
        tab_id: object = None,
        opener_tab_id: object = None,
    ) -> None:
        now = time.monotonic()
        self._prune_clients_locked(now)
        self._last_client_seen = now
        self._last_client_id = client_id
        if version:
            self._last_client_version = str(version)
        client = self._clients.setdefault(client_id, {})
        client["last_seen"] = now
        if version:
            client["version"] = str(version)
        if href:
            client["href"] = str(href)
        if title:
            client["title"] = str(title)
        client["profile_id"] = str(profile_id or "")
        client["tab_id"] = _optional_int(tab_id)
        client["opener_tab_id"] = _optional_int(opener_tab_id)

    def _prune_clients_locked(self, now: float) -> None:
        stale = [
            client_id for client_id, data in self._clients.items()
            if now - float(data.get("last_seen", 0.0) or 0.0) > self.CLIENT_TTL_S
        ]
        for client_id in stale:
            self._clients.pop(client_id, None)
        overflow = len(self._clients) - self.MAX_CLIENTS + 1
        if overflow > 0:
            oldest = sorted(
                self._clients,
                key=lambda client_id: float(self._clients[client_id].get("last_seen", 0.0) or 0.0),
            )[:overflow]
            for client_id in oldest:
                self._clients.pop(client_id, None)

    def _next_command_for_client_locked(self, client_id: str | None, client_version: str | None) -> dict[str, Any] | None:
        if not client_id:
            return None
        for pending in list(self._pending.values()):
            required_version = pending.get("required_version")
            target_client_id = pending.get("target_client_id")
            if target_client_id and target_client_id != client_id:
                continue
            if required_version is not None and client_version != required_version:
                continue
            if pending.get("mutating") is True and not self._pending_target_identity_current_locked(
                pending, client_id,
            ):
                continue
            if pending.get("mutating") is True and not self._pending_fence_is_current_locked(pending):
                continue
            # A mutating command is a one-shot physical delivery claim.  ACK
            # loss or a document reload must never make it executable again.
            if pending.get("mutating") is True and int(pending.get("delivered_count", 0) or 0) > 0:
                continue
            if target_client_id is None:
                pending["target_client_id"] = client_id
                pending["claimed_at"] = time.monotonic()
            pending["delivered_count"] = int(pending.get("delivered_count", 0) or 0) + 1
            pending["last_delivered_at"] = time.monotonic()
            return dict(pending["command"])
        return None

    def _pending_target_identity_current_locked(
        self, pending: dict[str, Any], client_id: str,
    ) -> bool:
        command = pending.get("command")
        payload = command.get("payload") if isinstance(command, dict) else None
        if not isinstance(payload, dict):
            return False
        fence = payload.get("mutationFence")
        if fence is None:
            return True
        if not isinstance(fence, dict):
            return False
        client = self._clients.get(client_id)
        if not isinstance(client, dict):
            return False
        actual_profile = str(client.get("profile_id") or "")
        actual_tab = client.get("tab_id")
        target = payload.get("mutationTarget")
        if target is None:
            return (
                actual_profile == str(fence.get("profile_id") or "")
                and actual_tab == fence.get("tab_id")
            )
        if not isinstance(target, dict) or target.get("kind") != "opener_child":
            return False
        return (
            actual_profile == str(target.get("profile_id") or "")
            and actual_tab == target.get("tab_id")
            and client.get("opener_tab_id") == target.get("opener_tab_id")
            and str(fence.get("profile_id") or "") == actual_profile
            and fence.get("tab_id") == target.get("opener_tab_id")
        )

    def _pending_fence_is_current_locked(self, pending: dict[str, Any]) -> bool:
        command = pending.get("command")
        payload = command.get("payload") if isinstance(command, dict) else None
        fence = payload.get("mutationFence") if isinstance(payload, dict) else None
        if fence is None:
            return True
        if not isinstance(fence, dict):
            return False
        try:
            owner_key = (str(fence["profile_id"]).strip(), int(fence["tab_id"]))
            target = payload.get("mutationTarget")
            key = (
                (str(target["profile_id"]).strip(), int(target["tab_id"]))
                if isinstance(target, dict) else owner_key
            )
            candidate = (
                owner_key[0], owner_key[1],
                int(fence["actor_generation"]), int(fence["fencing_token"]),
            )
        except (KeyError, TypeError, ValueError):
            return False
        return bool(key[0]) and (
            self._mutation_owner_fences.get(owner_key) == candidate
            and self._mutation_target_fences.get(key) == candidate
        )


    def _resolve_single_recent_client_locked(self, *, required_version: str | None) -> str | InjectorResult | None:
        now = time.monotonic()
        recent = []
        for client_id, data in self._clients.items():
            last_seen = float(data.get("last_seen", 0.0) or 0.0)
            if now - last_seen > 5.0:
                continue
            if required_version is not None and data.get("version") != required_version:
                continue
            recent.append(client_id)
        if len(recent) == 1:
            return recent[0]
        if len(recent) > 1:
            return InjectorResult(False, f"injector_ambiguous_clients:{','.join(sorted(recent))}")
        return None

    def _resolve_client_alias_locked(self, client_id: str, *, required_version: str | None) -> str:
        now = time.monotonic()
        current = self._clients.get(client_id)
        if isinstance(current, dict):
            last_seen = float(current.get("last_seen", 0.0) or 0.0)
            version_ok = required_version is None or current.get("version") == required_version
            if now - last_seen <= 5.0 and version_ok:
                return client_id
            profile_id = str(current.get("profile_id") or "")
            tab_id = current.get("tab_id")
        else:
            profile_id = ""
            tab_id = None
        if not profile_id or tab_id is None:
            return client_id
        replacements = []
        for candidate_id, data in self._clients.items():
            if candidate_id == client_id:
                continue
            last_seen = float(data.get("last_seen", 0.0) or 0.0)
            if now - last_seen > 5.0:
                continue
            if required_version is not None and data.get("version") != required_version:
                continue
            if str(data.get("profile_id") or "") != profile_id or data.get("tab_id") != tab_id:
                continue
            replacements.append((last_seen, candidate_id))
        return max(replacements)[1] if replacements else client_id

    @property
    def last_client_id(self) -> str | None:
        return self._last_client_id

    @property
    def last_client_version(self) -> str | None:
        return self._last_client_version

    def stop(self) -> None:
        server = self._server
        self._server = None
        if server is not None:
            server.shutdown()
            server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=1)
            self._thread = None


_GLOBAL_SERVER: BrowserInjectorServer | None = None


def global_browser_injector() -> BrowserInjectorServer:
    global _GLOBAL_SERVER
    if _GLOBAL_SERVER is None:
        _GLOBAL_SERVER = BrowserInjectorServer()
    return _GLOBAL_SERVER
