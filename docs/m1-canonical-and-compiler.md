# M1 canonical foundation and compiler

Canonical assets and revisions are the authority. `packages/core` contains the
coordinate/state contract and cannot call platform or model code. The compiler
never rewrites labels through a model and always derives from the original
orientation-normalized RGB object.

## Asset ingest

`process_image` reads up to 40 MiB/100 MP, applies EXIF orientation, converts
RGB, and stores canonical JPEG bytes under `sha256/<aa>/<hash>`. The hash is the
**stored normalized object's** digest; originals supplied in the licence
fixtures keep their recorded upstream provenance separately. `MinioStore` uses
the same key contract with the existing local MinIO service. No caller filename
becomes an object-storage path.

`apps/api/alembic.ini` contains no credentials. Compose supplies a local-only
connection to Alembic; Alembic creates the canonical tables and append-only,
expected-revision and frozen-release guards. The existing `0001_pgvector.sql`
keeps old local volumes compatible; Alembic also enables the extension on a
fresh database. `SourceAsset` validates the content-key format; PostgreSQL
retains its internal UUID separately.

Source annotations use append-only revisions. Human acceptance, review and
adjudication are denied for model/system actors. Model proposals cannot assert
release eligibility; a qualified system policy may auto-accept training-eligible
work only when its identity matches the project policy. Gold eligibility still
requires human adjudication. Object IDs, class membership and canonical image
bounds are validated before append. Review-only revisions may retain a valid
completeness assessment, while changed object sets require a new authoritative
completeness decision. The expected parent is checked under an asset revision
lock. Frozen split assignments cannot be changed when a release says they are
locked.

## Grouping and split

Group evidence merges only through exact hash, pHash distance ≤8, embedding
cosine ≥0.95 (when a model/versioned embedding exists), and equal declared
session/sequence/subject keys. Transitive closure joins related groups. pHash
9–12 or cosine 0.90–0.95 is retained in `BorderlineReview`, never silently
merged or declared independent. An empty embedding is missing evidence, not a
zero/same vector. Split assignment uses canonical sorted groups and a
SHA-256-derived seeded order with class-count balancing. The assignment version
binds the algorithm, seed, validation fraction and all grouping thresholds.
Both fixture releases use the same seed and grouping algorithm version.

Fixture coarse rectangles are intentionally machine/LLM-free preliminary labels
for compiler testing, **not** an adjudicated quality benchmark. Application-view
and keyboard definitions live in fixture configuration, not the engine.

## Compiler gates

The two full-frame recipes use `min(S/W,S/H)`, actual rounded raster gains,
integer centered padding (114,114,114), OpenCV area on downscale and linear on
upscale. The inverse matrix is equivalent subtract-padding/divide-gain. A crop
inverse recovers only the retained source intersection. The built-in full-frame
recipes use `min_target_px=4`: an annotated sample whose remaining targets are
all too small or otherwise policy-ineligible is explicitly excluded rather than
silently trained as a negative. A crop that removes every target likewise records
an exclusion. Partial crop absence alone does not fabricate class support; its
object is marked `absent_after_crop`. Targets at or below 6 pixels additionally
carry `near_min_target_px`. If any visible retained object fails crop-retention or
minimum-size policy while other objects remain trainable, compilation fails and
requires a recrop or explicit whole-sample exclusion instead of a partial label.

Every release has nonempty val. Hash-validated RGB source decoding, stable class
mapping, finite clipped boxes, five-field labels (8 decimals), exact source and
group membership, and actual dimensions are gate conditions. Duplicate rows,
zero-area geometry, out-of-bounds values, mismatched image/label counts and
incomplete samples fail compilation. Zero-box samples are emitted only after
`scope_completeness=exhaustive_for_scope`; their empty label file and
`verified_negative:true` are explicit.

Compilation is atomic into a new sibling directory. For each recipe and fixture
domain `python -m labelloop_compiler ...` builds metadata, image/label pairs,
transforms, exclusions and validation report with canonical JSON ordering.
Every sidecar and derivative is included in byte-identical recompilation tests.
The audit contact sheet is not a training input. Versioned image processing
records Pillow/libjpeg, OpenCV/NumPy, Python, interpolation and encoder options.

## Verification

GitHub Actions runs Hypothesis affine round trips, raster limits, failures,
both domains/two recipes double compiles, injected exact-duplicate/group split
leakage, source lineage, revision/human-state guards, ingest and strict gates.
The existing compose CI stack additionally checks Alembic migrations. No paid
training, provider, LLM, or live Platform call is used.

REA angle: none for M1, because the implementation contract is already defined
by the source plan and public interoperable raster formats; paid external
reverse engineering would not improve the offline tests.
