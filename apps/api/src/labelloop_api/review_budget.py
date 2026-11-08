"""Bounded review accounting inputs from trusted application configuration only."""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from urllib.parse import urlsplit

from labelloop_core.budget import identity, nonnegative


def provider(value: str) -> None:
    if not isinstance(value, str) or re.fullmatch("[a-z][a-z0-9_-]{0,63}", value) is None:
        raise ValueError("provider requires a configured identifier, not a URL")


@dataclass(frozen=True)
class ReviewUsage:
    """Only explicitly supported input/output/image usage; no cached-rate fallback."""

    input_tokens: int
    output_tokens: int
    image_tokens: int = 0

    def __post_init__(self) -> None:
        for count in (self.input_tokens, self.output_tokens, self.image_tokens):
            nonnegative(count)


@dataclass(frozen=True)
class ReviewPriceTable:
    version: str
    provider_id: str
    model_id: str
    input_per_million: int
    output_per_million: int
    image_per_million: int
    effective_date: str
    source_url: str
    currency: str = "USD"

    def __post_init__(self) -> None:
        identity(self.version)
        provider(self.provider_id)
        identity(self.model_id)
        for amount in (self.input_per_million, self.output_per_million, self.image_per_million):
            nonnegative(amount)
        if self.currency != "USD":
            raise ValueError("ledger supports USD only")
        if date.fromisoformat(self.effective_date).isoformat() != self.effective_date:
            raise ValueError("effective date requires YYYY-MM-DD")
        url = urlsplit(self.source_url)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username is not None
            or url.password is not None
            or url.query
            or url.fragment
            or len(self.source_url) > 2048
        ):
            raise ValueError("price source requires a public HTTPS reference without credentials")

    def cost(self, usage: ReviewUsage) -> int:
        numerator = (
            usage.input_tokens * self.input_per_million
            + usage.output_tokens * self.output_per_million
            + usage.image_tokens * self.image_per_million
        )
        return (numerator + 999_999) // 1_000_000


@dataclass(frozen=True)
class ReviewBatch:
    """One full evidence-bundle request per image/pass, under one pinned release.

    The producer validates image membership in that release and passes the full
    sorted set. No provider output supplies these fields. Probe/configuration and
    token bounds remain pinned by the enclosing SpendRequest configuration hash.
    """

    annotation_job_id: str
    image_ids: tuple[str, ...]
    model_id: str
    release_id: str
    max_passes: int
    max_image_tokens: int = 0

    def __post_init__(self) -> None:
        # JSON storage round-trips tuples as arrays.
        if isinstance(self.image_ids, list):
            object.__setattr__(self, "image_ids", tuple(self.image_ids))
        for value in (self.annotation_job_id, self.model_id, self.release_id):
            identity(value)
        if type(self.image_ids) is not tuple or not self.image_ids:
            raise ValueError("review batch requires a fixed nonempty image set")
        for image_id in self.image_ids:
            identity(image_id)
        if tuple(sorted(set(self.image_ids))) != self.image_ids:
            raise ValueError("review images must be sorted and unique")
        nonnegative(self.max_passes)
        nonnegative(self.max_image_tokens)
        if self.max_passes == 0:
            raise ValueError("review batch requires a positive pass bound")

    @property
    def image_set_sha256(self) -> str:
        return hashlib.sha256(
            json.dumps(self.image_ids, separators=(",", ":")).encode()
        ).hexdigest()
