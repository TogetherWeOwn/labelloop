# ADR 0002: Use attributed Commons photographs for the M0 office fixture

## Status

Accepted, 2026-11-07.

## Context

The M0 plan illustrates a small two-domain fixture using a COCO-derived office subset and Wikimedia Commons interface images. The COCO annotation archive identifies licences but does not supply verifiable creator names or stable public creator attribution pages for the selected photographs. The bootstrap requires licence-clean inputs with evidence, not merely a dataset-level licence claim.

## Decision

Use 16 Commons keyboard photographs, each explicitly declared CC BY 4.0, alongside the existing 16 CC0 interface screenshots. Store the exact source page, original URL, reproduction thumbnail URL, title, creator, licence URL, and output SHA-256 for every image. The selected non-game office domain remains distinct from the software-interface domain and satisfies the fixture count and size budget.

Do not treat the source `keyboard` object hints as completed detection boxes. Canonical annotations remain the responsibility of the next milestone.

## Consequences

The fixture no longer depends on the large COCO annotation archive or on non-commercial Flickr licences. Reproduction uses only public Commons API metadata and served thumbnails, with no credentials or paid calls. Source terms can change upstream; the committed manifest retains the evidence observed on 2026-11-07 and the fixture validator rejects unlisted licences and hosts.
