"""Source-bound anchor preparation; neither metadata approval nor pack publication."""

from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from unittest import TestCase

from pydantic import ValidationError

from labelloop_api.management_details import ReferenceRead
from labelloop_api.management_ports import ManagementFailure
from labelloop_api.management_reference_pack import (
    AnchorPackPlan,
    AnchorPackPublish,
    anchor_pack_summary,
    assemble_anchor_pack,
    pack_snapshot,
    verify_anchor_receipt,
)
from labelloop_api.management_references import reference_context_sha256
from labelloop_core.domain import (
    ClassDefinition,
    DatasetObject,
    OntologyRelease,
    SourceAsset,
    SourceSplitAssignment,
)
from labelloop_core.evidence import EvidenceScope, SourceAnnotationRevision
from labelloop_core.geometry import Box


def inputs():
    scope = EvidenceScope(
        ontology=OntologyRelease(
            "ontology-1",
            "project-1",
            1,
            (ClassDefinition("package", "Package", "A visible package"),),
            {"package": 0},
        ),
        assignment=SourceSplitAssignment(
            "assignment-1",
            {"source-1": "train"},
            {"group-1": "train"},
            {"source-1": "group-1"},
            7,
            "fixture",
            {"source-1": "fixture"},
        ),
        sources=(SourceAsset("source-1", "project-1", "a" * 64, "sha256/aa/" + "a" * 64, 12, 10),),
    )
    ref = ReferenceRead(
        id="reference-1",
        asset_id="source-1",
        class_id="package",
        source="Fixture",
        permitted_use="CC0",
        view="Side",
        lighting="Diffuse",
        scale="Full frame",
        confused_with=[],
        validity_scope="Visible packages",
        approval="admin_approved",
    )
    revision = SourceAnnotationRevision(
        asset_id="source-1",
        project_id="project-1",
        revision=2,
        parent_revision=1,
        actor_kind="human",
        approval_state="human_accepted",
        review_tier="human_reviewed",
        scope_completeness="exhaustive_for_scope",
        training_eligible=True,
        objects=(DatasetObject("object-1", "package", Box(1, 1, 11, 9)),),
    )
    plan = AnchorPackPlan.model_validate(
        {
            "pack_id": "pack-1",
            "version": "v1",
            "ontology_id": "ontology-1",
            "assignment_version": "assignment-1",
            "entries": {
                "reference-1": {
                    "annotation_revision": 2,
                    "scene": "studio",
                    "cluster_id": "cluster-1",
                    "validity_tags": ["side", "diffuse"],
                    "utility": 0.8,
                }
            },
        }
    )
    return scope, [ref], {"source-1": revision}, plan


class AnchorPackTests(TestCase):
    def test_positive_anchor_is_source_bound_and_preserves_provenance(self):
        scope, refs, revisions, plan = inputs()
        pack = assemble_anchor_pack(scope, refs, revisions, plan)
        self.assertEqual((pack.pack_id, pack.version), ("pack-1", "v1"))
        (example,) = pack.examples
        self.assertEqual(example.revision, revisions["source-1"])
        self.assertEqual(example.reference.asset_id, "source-1")
        self.assertIn('"permitted_use": "CC0"', example.reference.evidence_provenance)
        self.assertEqual(example.collection, "anchor")
        self.assertEqual(example.kind, "positive")
        self.assertIsNone(example.teacher_generation)
        self.assertIsNone(example.gold_adjudication)
        self.assertEqual(example.validity_tags, frozenset({"side", "diffuse"}))

    def test_snapshot_is_deterministic_json_native_and_content_bound(self):
        import hashlib
        import json

        scope, refs, revisions, plan = inputs()
        pack = assemble_anchor_pack(scope, refs, revisions, plan)
        snapshot, digest = pack_snapshot(pack)
        raw = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), allow_nan=False)
        self.assertEqual(hashlib.sha256(raw.encode()).hexdigest(), digest)
        self.assertEqual(json.loads(raw), snapshot)
        self.assertEqual(pack_snapshot(pack), (snapshot, digest))
        self.assertEqual(snapshot["examples"][0]["validity_tags"], ["diffuse", "side"])
        self.assertEqual(snapshot["examples"][0]["revision"]["objects"][0]["box"]["x_min"], 1)
        changed = replace(pack, examples=(replace(pack.examples[0], utility=0.5),))
        self.assertNotEqual(pack_snapshot(changed)[1], digest)

    def test_summary_projects_only_identity_digest_and_class_counts(self):
        scope, refs, revisions, plan = inputs()
        pack = assemble_anchor_pack(scope, refs, revisions, plan)
        self.assertEqual(
            anchor_pack_summary(pack, 5).model_dump(mode="json"),
            {
                "project_id": "project-1",
                "project_revision": 5,
                "pack_id": "pack-1",
                "version": "v1",
                "snapshot_sha256": pack_snapshot(pack)[1],
                "ontology_id": "ontology-1",
                "assignment_version": "assignment-1",
                "examples_per_class": {"package": 1},
                "qualification": "not_assessed",
            },
        )

    def test_summary_counts_examples_not_boxes_or_quality(self):
        scope, refs, revisions, plan = inputs()
        revisions["source-1"] = replace(
            revisions["source-1"],
            objects=revisions["source-1"].objects
            + (DatasetObject("object-2", "package", Box(0, 0, 2, 2)),),
        )
        pack = assemble_anchor_pack(scope, refs, revisions, plan)
        first = pack.examples[0]
        second = replace(first, reference=replace(first.reference, reference_id="reference-2"))
        pack = replace(pack, examples=(first, second))
        summary = anchor_pack_summary(pack, 5)
        self.assertEqual(summary.examples_per_class, {"package": 2})
        self.assertEqual(summary.qualification, "not_assessed")

    def test_summary_reader_refuses_untrusted_identity_before_database_access(self):
        from uuid import uuid4

        from sqlalchemy import create_engine, event

        from labelloop_api.management_reads import CanonicalManagementReads

        engine = create_engine("postgresql+psycopg://")

        @event.listens_for(engine, "do_connect")
        def no_database(*args):
            self.fail("untrusted readers must be refused before database access")

        try:
            reads = CanonicalManagementReads(engine, uuid4())
            for changes in (
                {"id": ""},
                {"kind": "service"},
                {"role": "worker-core"},
                {"role": "worker-ml"},
                {"role": "worker-llm"},
                {"role": "worker-platform"},
                {"permits": lambda permission: False},
            ):
                actor = SimpleNamespace(
                    **{
                        **{
                            "id": "reader",
                            "kind": "human",
                            "role": "reviewer",
                            "permits": lambda permission: True,
                        },
                        **changes,
                    }
                )
                with (
                    self.subTest(changes=changes),
                    self.assertRaisesRegex(ManagementFailure, "permission_denied"),
                ):
                    reads.read_anchor_pack(actor, "project-1", "pack-1", "v1")
        finally:
            engine.dispose()

    def test_summary_schema_cannot_add_lineage_or_claim_qualification(self):
        scope, refs, revisions, plan = inputs()
        summary = anchor_pack_summary(assemble_anchor_pack(scope, refs, revisions, plan), 5)
        for changes in (
            {"snapshot": {}},
            {"object_key": "sha256/aa/" + "a" * 64},
            {"qualification": "qualified"},
            {"project_revision": True},
            {"examples_per_class": {"package": -1}},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                type(summary).model_validate({**summary.model_dump(), **changes})

    def test_snapshot_refuses_oversized_evidence(self):
        scope, refs, revisions, plan = inputs()
        pack = assemble_anchor_pack(scope, refs, revisions, plan)
        changed = replace(pack, examples=(replace(pack.examples[0], scene="x" * 2_000_000),))
        with self.assertRaisesRegex(ManagementFailure, "management_unavailable"):
            pack_snapshot(changed)

    def test_internal_publication_input_cannot_supply_snapshot_or_identity(self):
        raw = {"expected_revision": 4, "plan": inputs()[3].model_dump(), "reason": "Checked"}
        self.assertEqual(AnchorPackPublish.model_validate(raw).expected_revision, 4)
        for changes in (
            {"snapshot": {}},
            {"actor_id": "foreign"},
            {"expected_revision": True},
            {"reason": " "},
            {"reason": "x" * 4097},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                AnchorPackPublish.model_validate({**raw, **changes})

    def receipt_inputs(self):
        scope, refs, revisions, plan = inputs()
        pack = assemble_anchor_pack(scope, refs, revisions, plan)
        snapshot, digest = pack_snapshot(pack)
        project = SimpleNamespace(
            id="project-1",
            workspace_id="workspace-1",
            scope="Packages",
            domain="objects",
            access_policy={},
        )
        receipt = SimpleNamespace(
            event_type="management.publish_anchor_pack",
            aggregate_id=project.id,
            actor_kind="human",
            actor_id="admin-1",
            expected_revision=4,
            payload={
                "workspace_id": "workspace-1",
                "aggregate_revision": 5,
                "result": {
                    "id": project.id,
                    "project_id": project.id,
                    "kind": "project",
                    "revision": 5,
                    "state": "draft",
                },
                "evidence": {
                    "plan": plan.model_dump(mode="json"),
                    "snapshot": snapshot,
                    "snapshot_sha256": digest,
                    "context_sha256": reference_context_sha256(project),
                },
            },
        )
        return receipt, project, pack

    def test_publication_receipt_matches_fresh_pack_without_mutating_it(self):
        receipt, project, pack = self.receipt_inputs()
        before = deepcopy(receipt.payload)
        verify_anchor_receipt(receipt, project, "workspace-1", pack)
        self.assertEqual(receipt.payload, before)

    def test_receipt_requires_owned_human_attribution(self):
        receipt, project, pack = self.receipt_inputs()
        for field, value in (
            ("actor_kind", "worker"),
            ("actor_id", ""),
            ("aggregate_id", "foreign"),
            ("event_type", "other"),
        ):
            changed = SimpleNamespace(**{**vars(receipt), field: value})
            with self.subTest(field=field), self.assertRaises(ManagementFailure):
                verify_anchor_receipt(changed, project, "workspace-1", pack)
        with self.assertRaises(ManagementFailure):
            verify_anchor_receipt(receipt, project, "foreign", pack)

    def test_receipt_revision_and_result_claims_do_not_supply_authority(self):
        receipt, project, pack = self.receipt_inputs()
        for changes in (
            {"aggregate_revision": True},
            {"aggregate_revision": 6},
            {"workspace_id": "foreign"},
            {"result": {}},
            {"result": {**receipt.payload["result"], "revision": 6}},
            {"result": {**receipt.payload["result"], "state": "published"}},
        ):
            changed = deepcopy(receipt)
            changed.payload.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ManagementFailure):
                verify_anchor_receipt(changed, project, "workspace-1", pack)

    def test_receipt_rehashes_saved_snapshot_even_for_equal_python_values(self):
        receipt, project, pack = self.receipt_inputs()
        receipt.payload["evidence"]["snapshot"]["scope"]["ontology"]["version"] = True
        self.assertEqual(receipt.payload["evidence"]["snapshot"], pack_snapshot(pack)[0])
        with self.assertRaises(ManagementFailure):
            verify_anchor_receipt(receipt, project, "workspace-1", pack)

    def test_receipt_cannot_substitute_current_pack_or_digest(self):
        receipt, project, pack = self.receipt_inputs()
        changed = replace(pack, examples=(replace(pack.examples[0], utility=0.1),))
        with self.assertRaises(ManagementFailure):
            verify_anchor_receipt(receipt, project, "workspace-1", changed)
        receipt.payload["evidence"]["snapshot_sha256"] = "0" * 64
        with self.assertRaises(ManagementFailure):
            verify_anchor_receipt(receipt, project, "workspace-1", pack)

    def test_context_change_invalidates_historical_publication(self):
        receipt, project, pack = self.receipt_inputs()
        project.scope = "Other objects"
        with self.assertRaises(ManagementFailure):
            verify_anchor_receipt(receipt, project, "workspace-1", pack)

    def test_malformed_receipt_fails_closed(self):
        receipt, project, pack = self.receipt_inputs()
        for payload in ({}, {"evidence": None}, None):
            changed = SimpleNamespace(**{**vars(receipt), "payload": payload})
            with self.subTest(payload=payload), self.assertRaises(ManagementFailure):
                verify_anchor_receipt(changed, project, "workspace-1", pack)

    def test_metadata_approval_does_not_create_a_positive_instance(self):
        scope, refs, revisions, plan = inputs()
        revisions["source-1"] = replace(revisions["source-1"], objects=())
        with self.assertRaisesRegex(ManagementFailure, "missing_evidence"):
            assemble_anchor_pack(scope, refs, revisions, plan)

    def test_unapproved_or_withdrawn_metadata_cannot_enter_memory(self):
        scope, refs, revisions, plan = inputs()
        for state in ("proposed", "withdrawn"):
            with self.subTest(state=state), self.assertRaises(ManagementFailure):
                assemble_anchor_pack(
                    scope, [refs[0].model_copy(update={"approval": state})], revisions, plan
                )

    def test_unknown_reference_and_missing_revision_refuse(self):
        scope, refs, revisions, plan = inputs()
        for bank, evidence in (([], revisions), (refs, {})):
            with self.subTest(bank=bank), self.assertRaises(ManagementFailure):
                assemble_anchor_pack(scope, bank, evidence, plan)

    def test_stale_pins_do_not_substitute_newer_evidence(self):
        scope, refs, revisions, plan = inputs()
        for field in ("ontology_id", "assignment_version"):
            with (
                self.subTest(field=field),
                self.assertRaisesRegex(ManagementFailure, "revision_conflict"),
            ):
                assemble_anchor_pack(scope, refs, revisions, plan.model_copy(update={field: "old"}))
        revisions["source-1"] = replace(revisions["source-1"], revision=3, parent_revision=2)
        with self.assertRaisesRegex(ManagementFailure, "revision_conflict"):
            assemble_anchor_pack(scope, refs, revisions, plan)

    def test_source_and_project_substitution_refuse(self):
        scope, refs, revisions, plan = inputs()
        for field in ("asset_id", "project_id"):
            changed = replace(revisions["source-1"], **{field: "foreign"})
            with self.subTest(field=field), self.assertRaises(ManagementFailure):
                assemble_anchor_pack(scope, refs, {"source-1": changed}, plan)

    def test_holdout_and_explicit_evaluation_membership_refuse(self):
        scope, refs, revisions, plan = inputs()
        holdout = replace(
            scope.assignment, asset_splits={"source-1": "val"}, group_splits={"group-1": "val"}
        )
        for changed in (
            replace(scope, assignment=holdout),
            replace(scope, evaluation_asset_ids=frozenset({"source-1"})),
        ):
            with self.subTest(scope=changed), self.assertRaises(ManagementFailure):
                assemble_anchor_pack(changed, refs, revisions, plan)

    def test_automatic_acceptance_does_not_become_a_human_anchor(self):
        scope, refs, revisions, plan = inputs()
        revisions["source-1"] = replace(
            revisions["source-1"],
            actor_kind="system",
            approval_state="auto_accepted",
            review_tier="machine_verified_no_human_review",
        )
        with self.assertRaises(ManagementFailure):
            assemble_anchor_pack(scope, refs, revisions, plan)

    def test_nonaccepted_and_gold_evidence_refuse(self):
        scope, refs, revisions, plan = inputs()
        for changes in (
            {"training_eligible": False, "approval_state": "rejected"},
            {"gold_eligible": True, "review_tier": "adjudicated"},
        ):
            changed = replace(revisions["source-1"], **changes)
            with self.subTest(changes=changes), self.assertRaises(ManagementFailure):
                assemble_anchor_pack(scope, refs, {"source-1": changed}, plan)

    def test_out_of_frame_geometry_and_unsupported_class_refuse(self):
        scope, refs, revisions, plan = inputs()
        for obj in (
            DatasetObject("object-1", "package", Box(1, 1, 13, 9)),
            DatasetObject("object-1", "foreign", Box(1, 1, 11, 9)),
        ):
            changed = replace(revisions["source-1"], objects=(obj,))
            with self.subTest(obj=obj), self.assertRaises(ManagementFailure):
                assemble_anchor_pack(scope, refs, {"source-1": changed}, plan)

    def test_blank_or_oversized_tags_refuse(self):
        scope, refs, revisions, plan = inputs()
        for tags in ([" "], ["x" * 201]):
            entry = plan.entries["reference-1"].model_copy(update={"validity_tags": tags})
            with self.subTest(tags=tags), self.assertRaises(ManagementFailure):
                assemble_anchor_pack(
                    scope,
                    refs,
                    revisions,
                    plan.model_copy(update={"entries": {"reference-1": entry}}),
                )

    def test_plan_is_strict_bounded_and_cannot_request_gold_or_teacher_authority(self):
        raw = inputs()[3].model_dump()
        for changes in ({"entries": {}}, {"version": " "}, {"approved": True}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                AnchorPackPlan.model_validate({**raw, **changes})
        for changes in (
            {"annotation_revision": True},
            {"utility": float("nan")},
            {"collection": "gold"},
            {"teacher_generation": "teacher"},
            {"validity_tags": [1]},
            {"scene": " "},
        ):
            entry = {**raw["entries"]["reference-1"], **changes}
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                AnchorPackPlan.model_validate({**raw, "entries": {"reference-1": entry}})
