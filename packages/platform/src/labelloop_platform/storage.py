"""Signed storage has its own unauthenticated transport and trusted host policy."""

import ipaddress
import re

import httpx

from .ports import Outcome, StorageResult, StorageTarget
from .transport import _OwnedClient


class SignedStorage:
    def __init__(
        self,
        allowed_hosts: frozenset[str],
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        # Exact deployment-owned storage host inventory, never wildcard/model input.
        if not allowed_hosts or any(not self._public_host(host) for host in allowed_hosts):
            raise ValueError("Invalid trusted storage host policy")
        self._hosts = allowed_hosts
        self._http = _OwnedClient(transport or httpx.HTTPTransport(retries=0, trust_env=False))

    @staticmethod
    def _public_host(host: str) -> bool:
        if (
            host != host.lower()
            or host == "platform.ultralytics.com"
            or len(host) > 253
            or not re.fullmatch(
                r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
                r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+",
                host,
            )
            or host.endswith((".localhost", ".local", ".internal"))
        ):
            return False
        try:
            return ipaddress.ip_address(host).is_global
        except ValueError:
            # Strict-IP parse failure is not proof of DNS: reject legacy numeric
            # address spellings (short, octal and hex components) without resolving.
            return (
                re.fullmatch(r"(?:0x[0-9a-f]+|[0-9]+)(?:\.(?:0x[0-9a-f]+|[0-9]+))*", host) is None
            )

    def _transfer(self, method: str, target: StorageTarget, content: bytes | None) -> StorageResult:
        try:
            url = httpx.URL(target.url)
            if (
                url.scheme != "https"
                or url.host not in self._hosts
                or url.port not in (None, 443)
                or url.userinfo
                or url.fragment
            ):
                return StorageResult(Outcome.REJECTED)
            headers = {}
            for name, value in target.headers.items():
                lower = name.lower()
                # Returned storage instructions are untrusted; no auth/cookies/host override.
                if lower not in ("content-type", "content-md5", "content-length") and not (
                    lower.startswith("x-amz-") or lower.startswith("x-goog-")
                ):
                    return StorageResult(Outcome.REJECTED)
                headers[name] = value
            response = self._http.request(method, url, headers=headers, content=content)
        except (httpx.HTTPError, httpx.InvalidURL, ValueError, TypeError):
            return StorageResult(
                Outcome.RECONCILE_REQUIRED if method == "PUT" else Outcome.TRANSPORT_ERROR
            )
        status = response.status_code
        if 200 <= status < 300:
            return StorageResult(Outcome.SUCCEEDED, status, response.content)
        if status == 412 or (method == "PUT" and (status == 408 or status >= 500)):
            return StorageResult(Outcome.RECONCILE_REQUIRED, status)
        if status == 408 or status >= 500:
            return StorageResult(Outcome.UNAVAILABLE, status)
        return StorageResult(Outcome.REJECTED, status)

    def put(self, target: StorageTarget, content: bytes) -> StorageResult:
        # A failed/uncertain create-only PUT requires a fresh signed URL/session, not a repeat.
        return self._transfer("PUT", target, content)

    def get(self, target: StorageTarget) -> StorageResult:
        return self._transfer("GET", target, None)

    def close(self) -> None:
        self._http.close()
