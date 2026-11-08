# ADR 0003: Separate fixture provenance from the current imaging runtime

- Status: Accepted
- Date: 2026-11-08

## Context

The imaging runtime moves from Pillow 12.2.0 to 13.0.0 on Python 3.12.
The new Linux wheel bundles libjpeg-turbo 3.2.0 instead of 3.1.4.1.
The committed fixture manifests describe the original provisioning environment,
not the version that must decode those immutable files forever. Rewriting that
record would misrepresent how the fixtures were made.

## Decision

Retain every committed fixture image, provisioning manifest, canonical normalized
hash, annotation and split unchanged. Keep the provisioning tool's strict check
against the historical environment; it must reject a new runtime before downloading
or producing an output. Do not provision or regenerate fixtures during CI.

The offline integrity validator checks the historical processing record and source
hashes unchanged, while separately pinning the current runtime to Pillow 13.0.0,
libjpeg-turbo 3.2.0, JPEG compatibility 6.2 and Python 3.12. The canonical fixture
loader continues to compare the current normalization against existing golden
hashes. Compiler tests rebuild both fixture domains at 320 and 640 twice, compare
all output bytes, and assert the new toolchain version in the derived manifests.
A normalized hash change remains a failure, not permission to update a golden.

The derivative manifest already records actual Pillow, JPEG, NumPy and OpenCV
versions plus encoder settings. Its toolchain record changes with this refresh;
its schema and geometry conventions do not. No existing release is rewritten.

Use uv 0.13.6's native three-day artifact-age filter for the refreshed Pillow
package and disable third-party source builds. Other dependency versions are
retained. A global age filter is not adopted by this bounded refresh: it would
reject an unrelated existing SDK pin. Reviewed workspace Hatchling builds remain
necessary; `no-build` is not a universal script-execution sandbox. Container builds
use `uv sync --frozen --no-editable` to build only the selected group's first-party
workspace members while refusing third-party source builds. Unlike workspace sync,
`uv pip install --no-editable` does not exempt first-party paths from `no-build`.

## Consequences

GitHub Actions is the verification authority for canonical hash preservation,
byte-identical repeat builds, geometry, fixture integrity, import contracts and
CPU-only ML smoke. Compatibility is not established merely by wheel availability.
No changed hash may be hidden by regenerating images or manifests. Provisioning
remains intentionally unavailable in the refreshed environment; a future fixture
release needs its own explicit processing record and output directory.

REA angle: reuse Pillow's release notes and wheel metadata, existing immutable
fixture hashes, and the compiler's versioned toolchain records rather than inventing
a second encoder or changing canonical geometry.
