# Evidence selection and memory ports

These pure-core modules implement plan §§10.2–10.4 and 11.4. They perform no I/O,
provider requests, embeddings, training, persistence or authorization. Persistence
consumers must supply authorized, complete, version-pinned records; model output
must not construct policies, approve packs/imports, select splits or claim human
review. No reference addition updates model weights.

## Source eligibility (`labelloop_core.evidence`)

`EvidenceScope` binds the existing `OntologyRelease`, `SourceSplitAssignment` and
complete canonical `SourceAsset` inventory. It validates assignment/group agreement,
project identity, exact-duplicate grouping, and declared derivative ancestry.
Validation/test groups and additional evaluation memberships quarantine their entire
source group from training selection and reference retrieval. An unrecognized ID
fails closed. Derivatives cannot masquerade as canonical sources; reference crops
are explicit rectangles on original source pixels. Related/near-duplicate grouping
must already have been computed by the delivered grouping module, not inferred by
these ports. An incomplete inventory cannot establish independence.

## Active selection (`labelloop_core.selection`)

`SelectionCandidate` binds an immutable annotation revision, source, teacher
generation, precomputed evidence ID, signal version, declared diversity cluster and
integer estimated work units. `SelectionSignals` are finite normalized priorities,
not calibrated correctness probabilities. All eight signals have explicit positive
weights: disagreement, low confidence, omission, confusion, novelty, rare class,
difficult size and distribution shift. No signal grants acceptance or completeness.
Zero predictions are not interpreted as verified negatives.

`SelectionPolicy` explicitly bounds input count, output count, work units, per-source
group and per-cluster counts. Three positive quotas reserve **distinct slots**, in
this order: zero detection, suspected missing objects (omission threshold), ordinary
accepted random audit (all signals below the ordinary threshold). Then weighted
scores plus a diminishing cluster-diversity bonus fill remaining affordable slots.
Seeded SHA-256 ordering breaks ties and samples the ordinary stratum without relying
on iteration order or process-global random state. Mandatory quotas never silently
shrink. This is a bounded deterministic greedy policy, not an optimal packing solver:
it may conservatively refuse a feasible alternative packing rather than overspend
or relax quotas. Callers may submit a newly versioned policy/evidence batch after
review, never catch an insufficiency and automatically reduce quotas.

`SelectionResult` records the input SHA-256 (complete scope, candidates, policy,
algorithm), policy/signal versions, exact revisions/evidence IDs, assigned stratum,
score and consumed units. Candidate and inventory ordering does not change it.

## Reference memory (`labelloop_core.references`)

`ReferencePack` is explicit versioned enrollment, not an automatic prediction sink.
`MemoryExample` keeps anchor, auto-expanded and gold membership separate. Curated
anchor/gold requires human review; auto-expansions require retained teacher identity
and system-qualified acceptance. All training references require accepted complete
non-gold revisions; positive crops must contain a complete labeled class instance.
Gold enrollment requires its source group to be quarantined in the authoritative
scope first, so another revision or pack cannot retrieve the same evaluation pixels.
Provenance,
validity scope/tags, scene, diversity cluster, utility and positive versus confusion
negative kind survive enrollment. Gold can remain in a pack for accounting but is
never retrieved. `collection_distributions` reports object class/size/occlusion and
example teacher/scene counts separately for all three collections, with explicit
source-pixel size thresholds.

`retrieve_references` only returns approved, query-compatible entries within explicit
item/class/cluster budgets. Before filling optional high-utility slots, a deterministic
class-to-cluster augmenting assignment reserves one reference per requested class;
flexible classes can move to an alternative cluster to preserve scarce coverage.
Empty or unsupported class coverage is insufficient
evidence, not a successful empty retrieval. Local packs must use the current pinned
scope. Cross-project reuse requires one approved `ReferenceImport` binding a named
pack/version, target ontology/version, complete explicit class mapping and import
version/provenance. Both source holdout eligibility and target holdout pixel-hash
collisions are checked. Access authorization and independently grouping imported
near-duplicate/related pixels remain persistence prerequisites; an import is not an
access grant. Returned references retain pack/import versions and confusion kind.

## Descendant invalidation (`labelloop_core.invalidation`)

`EvidenceArtifact` supplies typed metadata for existing `LineageGraph` IDs. Register
source revision/reference → canonical release → derived sample/variant → training
run → model → proposal run → generated revision edges when producing each artifact;
reference-conditioned proposals must include their reference/pack dependencies.
Every graph node needs typed metadata. Missing metadata and reconstructed cycles
fail closed. The graph must be the complete project dependency closure supplied by
persistence; the planner cannot discover omitted external records.

`plan_invalidation` traces all paths from bad source revisions/references and emits
immutable marks: review, rebuild, retrain, or adjudication-required. Human-reviewed
and adjudicated annotation revisions receive only adjudication-required marks with
expected revision numbers, never an automated replacement. Root causes are retained
for each mark, including multiple bad ancestors. Unrelated records remain untouched.
`append_invalidation` preserves all historical events and is idempotent only for an
identical event ID/payload; conflicting replays fail. Consumers append marks and
suspend affected evidence pending review/rebuild/retraining, preserving old releases,
weights and labels; they must enforce expected revisions and authenticated new
adjudication when applying later corrections. These ports do not mutate the registry
or make persistence-state changes on their own.

## Verification

`packages/core/tests/test_memory_policy.py` uses pixel-free synthetic office and
wildlife catalogs. GitHub Actions runs these fixtures with strict typing and the
existing full offline CI gates. No dependency additions, live Platform calls or paid
provider calls are needed. This slice does not claim the milestone's separate
end-to-end measured improvement experiment or its persistence integrations.
