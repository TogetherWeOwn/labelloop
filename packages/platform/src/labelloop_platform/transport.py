"""Owned SDK transport: no wake hooks, redirects, environment auth, or automatic retries."""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import math
import threading
import time
from collections import deque
from collections.abc import Callable
from contextvars import ContextVar
from email.utils import parsedate_to_datetime
from importlib.metadata import version
from typing import Any

import httpx
from ultralytics_platform import Platform
from ultralytics_platform._exceptions import APIConnectionError, APIError

from .contract import WireError, prepare, validate_shape
from .ports import Outcome, PlatformRequest, PlatformResult

ORIGIN = "https://platform.ultralytics.com"
TIMEOUT = httpx.Timeout(connect=10, read=60, write=60, pool=10)
LIMITS = {"default": 100, "training": 10, "upload": 10, "predict": 20, "export": 20, "download": 30}
_LOG = logging.getLogger(__name__)
_WIRE_ACTIVE: ContextVar[bool] = ContextVar("platform_wire_active", default=False)


class _WireLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # httpcore DEBUG can contain raw response headers. Suppress only this
        # execution context, never unrelated requests or application diagnostics.
        return not _WIRE_ACTIVE.get()


for _logger_name in (
    "httpx",
    "httpcore.connection",
    "httpcore.proxy",
    "httpcore.http11",
    "httpcore.http2",
    "httpcore.socks",
):
    logging.getLogger(_logger_name).addFilter(_WireLogFilter())


class Throttle:
    """Share one instance across clients in a worker. No credential values are retained."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self._lock = threading.Lock()
        self._calls: dict[tuple[str, str], deque[float]] = {}
        self._blocked: dict[tuple[str, str], float] = {}

    def reserve(self, key: str, category: str) -> float | None:
        with self._lock:
            now = self.clock()
            bucket = (key, category)
            calls = self._calls.setdefault(bucket, deque())
            while calls and calls[0] <= now - 60:
                calls.popleft()
            retry_at = self._blocked.get(bucket, now)
            if len(calls) >= LIMITS[category]:
                retry_at = max(retry_at, calls[0] + 60)
            if retry_at > now:
                return retry_at
            calls.append(now)
            return None

    def defer(self, key: str, category: str, delay: float) -> float:
        with self._lock:
            bucket = (key, category)
            until = max(self._blocked.get(bucket, 0), self.clock() + delay)
            self._blocked[bucket] = until
            return until


_DEFAULT_THROTTLE = Throttle()


def retry_delay(value: str | None, wall_time: float) -> float:
    try:
        delay = float(value or "")
    except ValueError:
        try:
            delay = parsedate_to_datetime(value or "").timestamp() - wall_time
        except (ValueError, TypeError, OverflowError):
            return 1.0
    return max(0.0, delay) if math.isfinite(delay) else 1.0


class _OwnedClient(httpx.Client):
    """Use httpx's transport directly: no URL/header/body logging or implicit client state.

    In particular, httpx.Client.send's INFO log would disclose signed URL queries.
    Transport injection is a trusted infrastructure seam, not a user-configurable endpoint.
    """

    def __init__(self, transport: httpx.BaseTransport) -> None:
        super().__init__(
            transport=transport, timeout=TIMEOUT, trust_env=False, follow_redirects=False
        )
        self._wire = transport
        self.last_status: int | None = None
        self.retry_after: str | None = None

    def request(self, method: str, url: Any, **kwargs: Any) -> httpx.Response:
        self.last_status = None
        self.retry_after = None
        request = httpx.Request(
            method,
            url,
            headers=kwargs.get("headers"),
            params=kwargs.get("params"),
            json=kwargs.get("json"),
            content=kwargs.get("content"),
            extensions={"timeout": TIMEOUT.as_dict()},
        )
        token = _WIRE_ACTIVE.set(True)
        try:
            response = self._wire.handle_request(request)
            response.request = request
            self.last_status = response.status_code
            self.retry_after = response.headers.get("retry-after")
            try:
                response.read()
            finally:
                response.close()
            return response
        finally:
            _WIRE_ACTIVE.reset(token)


class _OriginGuard(httpx.BaseTransport):
    def __init__(self, transport: httpx.BaseTransport) -> None:
        self.transport = transport
        self.expected: tuple[str, str] | None = None

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        if (
            url.scheme != "https"
            or url.host != "platform.ultralytics.com"
            or url.port not in (None, 443)
            or url.userinfo
            or self.expected != (request.method, url.path)
        ):
            raise WireError()
        return self.transport.handle_request(request)

    def close(self) -> None:
        self.transport.close()


class PlatformAdapter:
    """Single-worker SDK adapter; consumers persist write intents and results durably.

    The process-local write latch prevents repeated dispatch within this adapter, NOT
    cross-process idempotency. A restarted worker must reconcile uncertain persisted
    intents before calling this port again. No remote idempotency contract exists.
    """

    def __init__(
        self,
        api_key: str,
        *,
        transport: httpx.BaseTransport | None = None,
        throttle: Throttle | None = None,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        if version("ultralytics-platform") != "0.1.107":
            raise RuntimeError("Unsupported Platform SDK version")
        if not api_key or any(ord(c) < 33 or ord(c) > 126 for c in api_key):
            raise ValueError("Invalid Platform credential")
        self._key_id = hashlib.sha256(api_key.encode()).hexdigest()
        self._guard = _OriginGuard(transport or httpx.HTTPTransport(retries=0, trust_env=False))
        self._http = _OwnedClient(self._guard)
        self._sdk = Platform(
            api_key=api_key,
            base_url=ORIGIN,
            http_client=self._http,
            timeout=TIMEOUT,
            max_retries=0,
            retry_methods=(),
        )
        self._throttle = throttle or _DEFAULT_THROTTLE
        self._wall_clock = wall_clock
        self._writes: dict[str, tuple[str, PlatformResult]] = {}
        self._lock = threading.Lock()

    def execute(self, request: PlatformRequest) -> PlatformResult:
        # Serializes this client's capture state and write latch, not independent clients.
        with self._lock:
            result = self._execute(request)
        # Never log caller IDs, paths, response bodies, exception text or headers.
        _LOG.info("Platform result outcome=%s status=%s", result.outcome.value, result.status_code)
        return result

    def _execute(self, request: PlatformRequest) -> PlatformResult:
        try:
            request = copy.deepcopy(request)
            route, path = prepare(request)
            mutation = route["method"] != "GET"
            if mutation and (
                not request.operation_id
                or not request.operation_id.isascii()
                or not request.operation_id.replace("-", "").replace("_", "").isalnum()
                or len(request.operation_id) > 128
            ):
                raise WireError()
            digest = hashlib.sha256(
                json.dumps(
                    [request.operation, path, request.query, request.body],
                    sort_keys=True,
                    allow_nan=False,
                ).encode()
            ).hexdigest()
        except (ValueError, TypeError, KeyError, RecursionError):
            return PlatformResult(Outcome.REJECTED)
        if mutation and request.operation_id in self._writes:
            prior_digest, result = self._writes[request.operation_id]
            if prior_digest != digest:
                return PlatformResult(Outcome.REJECTED)
            return result
        category = route["category"]
        retry_at = self._throttle.reserve(self._key_id, category)
        if retry_at is not None:
            return PlatformResult(Outcome.RATE_LIMITED, retry_at=retry_at)
        self._guard.expected = (route["method"], path)
        try:
            # Pinned SDK request primitive intentionally bypasses generated resource
            # wrappers accepting arbitrary servers/extra headers. Fixture CI pins this seam.
            data = self._sdk._client.request(
                route["method"],
                path,
                auth=("Authorization", "Bearer "),
                json=request.body,
                params=request.query,
            )
            status = self._http.last_status
            response = route["responses"].get(str(status))
            if response is None:
                raise WireError()
            content = response.get("content", {})
            if "application/json" in content:
                validate_shape(data, content["application/json"]["schema"])
            elif content and not isinstance(data, str):
                raise WireError()
            result = PlatformResult(Outcome.SUCCEEDED, status, data)
        except APIError as error:
            status = error.status_code
            if status == 402:
                result = PlatformResult(Outcome.PAID_PAUSED, status)
            elif status == 429:
                retry_at = self._throttle.defer(
                    self._key_id,
                    category,
                    retry_delay(self._http.retry_after, self._wall_clock()),
                )
                result = PlatformResult(Outcome.RATE_LIMITED, status, retry_at=retry_at)
            elif status == 409 or (mutation and (status == 408 or status >= 500)):
                result = PlatformResult(Outcome.RECONCILE_REQUIRED, status)
            elif status == 503:
                result = PlatformResult(Outcome.UNAVAILABLE, status)
            else:
                result = PlatformResult(Outcome.REJECTED, status)
        except (APIConnectionError, httpx.HTTPError):
            result = PlatformResult(
                Outcome.RECONCILE_REQUIRED if mutation else Outcome.TRANSPORT_ERROR,
                self._http.last_status,
            )
        except (ValueError, TypeError, KeyError, RecursionError):
            result = PlatformResult(
                Outcome.RECONCILE_REQUIRED if mutation else Outcome.MALFORMED,
                self._http.last_status,
            )
        finally:
            self._guard.expected = None
        if mutation and request.operation_id and result.outcome != Outcome.RATE_LIMITED:
            self._writes[request.operation_id] = (digest, result)
        return result

    def close(self) -> None:
        self._sdk.close()
