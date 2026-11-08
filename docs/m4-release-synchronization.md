# M4 release synchronization and correction ports

This implements the synchronization state machines in plan §§4.4, 8.2, 8.4–8.6 and 12.
It is offline domain logic, not a live Platform qualification or permission to upload,
create credentials, run prediction, spend credits or start training.

## Immutable compiler inputs and reconciliation

`ReleaseIntent.from_compiler` consumes the delivered compiler manifest bytes, an archive,
the approved stable-ID/name catalog and a compiled-file reader. It freezes manifest/archive
SHA-256, release/recipe IDs, the release-specific stable class map, exact image filenames,
source IDs/revisions, object IDs, normalized labels and recorded transforms. The bridge
checks file hashes, labels against object lineage, class-index identity and explicit negatives.
The archive is a trusted compiler/package output; its bytes are hash-bound for transport,
not independently recompiled or decoded by this module. The transport must verify that hash
before dispatch. Canonical sources and generated datasets are never modified here.

Preflight refuses empty validation membership, ambiguous class names, missing membership,
unknown task/class labels and image short sides below 28 pixels. Ingest always includes
`classMapping` from archive class names to approved numeric indices and `conflictPolicy: skip`.
It never sends `targetSplit`, `sourceUrl`, or a connected-source reference. A release gets
one stable private detection dataset slug, never an editable shared incoming dataset.

`reconcile_release` consumes a `CapturedVersion` capsule from the trusted version-export
reader, not a live image listing accompanied by independently supplied version/hash fields.
The reader must request `GET export?v=N`, verify the response version and dataset's recorded
version/content hash, download and hash the saved NDJSON, and derive complete image/label
snapshots from that exact export. `export_sha256` is retained in the frozen mapping. Processing
completion and complete export membership remain required. A live listing cannot claim the
`saved_version_export` provenance. Final implementation/testing of this reader is part of the
explicit foundation integration gate below; the typed capsule is not proof of that wiring.

List summaries are insufficient. Reconciliation checks class order at dataset and image level,
unique IDs/names, exact membership, splits, label multisets/classes, truncation and geometry.
Every image also requires trusted `ContentEvidence`: measured original content SHA-256 and
raster dimensions matching the compiler's expected file. The remote CAS `properties.hash`
is retained as opaque storage identity, never assumed to be SHA-256. If only a resized image
can be retrieved and original content cannot be verified, fail closed rather than bless its
filename as content proof. This may require original-asset access for >4096-pixel uploads;
dimension tolerance alone does not qualify those uploads.

The compiler bridge also reads hash-frozen `data.yaml` names and rejects independently renamed
catalogs (recompile first). Each parsed row's class index is checked directly against its
stable class ID, not only against a redundant manifest field. A moved train image, missing
negative image, remapped class, wrong-pixel image or incomplete response prevents freezing.

For dimensions up to 4096 pixels, exact dimensions are mandatory. Above 4096, the expected
long side is exactly 4096; the short side may be floor or ceiling of the proportional resize
(one pixel apart at most), because the accepted contract does not specify integer rounding.
No other resize tolerance applies. Normalized labels are not rescaled again. Label-coordinate
readback tolerance is half of one unit at five decimals (0.000005 plus floating-point epsilon).
An independently rounded center/extent may move an edge by 0.0000075; this is not permission
to clamp or accept arbitrary out-of-bounds geometry. Matching is order-independent and preserves
multiplicity, using iterative augmenting paths rather than greedy matching.

`FrozenReleaseMapping` retains owner/slug/dataset ID, captured version/content hash,
manifest/archive hashes, stable class map and every source revision/object/transform-to-image
mapping. Link its release/recipe/source IDs into the existing core lineage graph alongside
training and generated-annotation records; a bad source revision remains traceable through
those immutable parent edges. `verify_model_capture` checks the actual model record's
`datasetId`, `datasetVersion.version` and `datasetVersion.contentHash`, not a fabricated
version-qualified dataset URI or a training-start response.

## Durable mutation protocol

`ReleaseSync` separates new, dataset-created, signed, uploaded, processing, reconciliation,
in-flight, uncertain, frozen and failed states. `dispatch_once` writes an in-flight intent
with an atomic journal compare-and-set **before** invoking an injected transport. Every
returned dataset/session/job ID and sanitized outcome is persisted before advancement.
The journal port requires shared durable transactions and unique stable operation IDs.
The test journals are deliberately in-memory fakes, not production persistence.

- A create timeout or restart after intent cannot dispatch create again. Recover only by
  looking up the authorized exact owner/stable slug and checking `DatasetEvidence`: private
  visibility, detection task, exact class catalog and operation/manifest/archive metadata
  supplied in the original create. Do not adopt a public or unrelated same-name dataset.
  An empty lookup is not proof that a previously submitted create cannot still arrive.
- Signed PUT targets are create-only. Expiry, 412, a lost PUT result or loss of the ephemeral
  signed target after restart retires the old session. A new signed URL/session is required;
  never repeat PUT against the old target. Retired session IDs remain in recovery evidence.
- Ingest with `sessionId` also completes upload, so there is no separate completion mutation.
  `201 queued` stores a job ID and enters processing, not ready/frozen.
- An uncertain ingest must reconcile its remote dataset membership/version. It cannot sign,
  reingest or replace the dataset to make uncertainty disappear. A failed ingest stops.
- A frozen state is immutable to the application writer. A changed release uses a new intent,
  canonical release and slug; do not reset the old state or delete recovery evidence.

The `ReleaseTransport` port owns approved archive bytes and ephemeral signed-target tickets.
Only returned storage headers go to storage. It must bind archive hash, derive `totalBytes`,
use an operation-specific ID for each mutation (including each fresh signed session), and
translate foundation outcomes without automatic mutation retries. Signed URLs and credentials
are never persistent identities or included in the frozen mapping. Provider exception text
is not copied into durable records.

**Integration still required before merge:** wire this port and `LabelTransport` to the
Platform foundation's `PlatformPort`, `PlatformRequest`, `Operation`, `Outcome`, `StoragePort`
and storage target validation; run exact-head fixture integration through those adapters.
The independent implementation and fixture tests do not wait on that producer. The worker
must supply the real durable journal and coordinator ports; these modules do not create a
second queue, scheduler, lock service or canonical store.

## Complete correction replacement and external edits

`Replacement.prepare` pins an accepted/current canonical revision, source identity, full
remote recovery snapshot, complete intended label set and maintenance-window ID. Only an
editable canonical `source_working` dataset is a target. Frozen/derived datasets, truncated
snapshots, stale source revisions and labels outside the declared detection/class scope fail.
The intended set is the entire target detection set (including an intentional empty set),
plus every non-target label. Mixed-task records with segments, pose or OBB remain indivisible,
including any associated bbox. PATCH contains only `labels`, never a simultaneous metadata
branch. Snapshots and write intent remain available after success or conflict.

`replace_once` requires a shared per-image writer reservation and a `WriterCoordinator` that
verifies current canonical revision, a valid writer lease and an externally coordinated
annotation-maintenance window. If external/UI writers cannot be quiesced, the coordinator
must refuse dispatch. It rechecks the window after recording intent. A local mutex, lease
alone, timestamp or read-before-write hash **does not** exclude a simultaneous Platform UI
edit. The API has no annotation revision/If-Match guard; this code claims no remote atomic CAS.
An external edit between a read and PATCH could otherwise be overwritten without any evidence
left in readback. Do not enable this writer while allowing uncoordinated UI edits.

Empty `segments`/`keypoints` arrays are not mixed-task geometry and do not retain stale target
boxes. Meaningful task geometry is preserved and numerically compared within the same rounding
tolerance; array shape and skeleton identity remain exact. Already-satisfied replacements are
verified without dispatching PATCH, including matches within saved-coordinate tolerance.
An old uncertain no-op intent cannot use the unchanged baseline as completion proof.

Before PATCH, observed divergence stops without writing. After PATCH, complete readback must
match intended labels (bounded rounding) and unchanged image identity/classes/split/dimensions.
Divergence stops for adjudication; it never replaces canonical truth. A lost response or
restart after intent permits only readback reconciliation. If readback differs—even if it
still equals the baseline—do not replay PATCH: an old request may still be outstanding.
Keep the reservation and maintenance window until verified/adjudicated. Never blindly restore
the baseline over an external edit. Later divergence requires a new proposed canonical revision
and adjudication, not an in-place overwrite of source history.

`inverse_edit` produces only proposed retained-source geometry from the recorded raster
inverse. A previously clipped object, new intersection with padding, or a box reaching an
internal source-crop edge requires source-image review. Lost extent is never reconstructed, and this proposal cannot assert acceptance or
change canonical state. `require_complete_prediction` rejects both `partial` and nonempty
`partialImages`, as well as truncated label results; a nonpartial result is still only proposal
evidence, not an image-completeness assessment.

## Offline evidence

The three new test modules cover reconciliation, intent/outcome/restart/412 transitions and
complete replacement/external divergence. `tests/fixtures/sync-http.json` is an original
synthetic recorded exchange shaped from SDK 0.1.107's OpenAPI at official commit `8af3921`,
not a live account capture. It contains no real images, credentials or usable signed URL.
Full tests, lint, types and licence/secret gates run in the existing GitHub Actions pipeline.
No live calls or local full-suite result is claimed.
