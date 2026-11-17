"""Explicit pause acknowledgement using the existing immutable journal and head lock.

No dispatch, billing reconciliation, queue work or provider I/O. Resume metadata
and tokens are concurrency evidence, never spend authority. No schema migration.
"""

import hmac
from dataclasses import asdict, dataclass

import sqlalchemy as sa

from labelloop_api import budget_schema as db
from labelloop_api.budget_store import BudgetStore, Principal, decode_entry, digest, sha256
from labelloop_core.budget import AcknowledgedPause, BudgetGate, BudgetRefused, identity


def load_acknowledgement(connection, head) -> AcknowledgedPause | None:
    """Only explicit versioned admin events count; never infer legacy acknowledgements."""
    events = (
        connection.execute(
            sa.select(db.event)
            .where(db.event.c.project_id == head["project_id"], db.event.c.action == "resume")
            .order_by(db.event.c.policy_revision)
        )
        .mappings()
        .all()
    )
    acknowledged = None
    previous_revision = 0
    for event in events:
        record = event["record"]
        if (
            set(record) != {"version", "expected_revision", "state_token", "acknowledgement"}
            or type(record["version"]) is not int
            or record["version"] != 1
            or type(record["expected_revision"]) is not int
            or record["expected_revision"] < 1
            or event["policy_revision"] != record["expected_revision"] + 1
            or not previous_revision < event["policy_revision"] <= head["revision"]
            or event["principal_kind"] != "human"
            or event["principal_role"] != "admin"
            or event["operation_id"] is not None
            or event["principal_version"]
            != digest({"id": event["principal_id"], "kind": "human", "role": "admin"})
        ):
            raise ValueError("unsupported resume evidence")
        identity(event["principal_id"])
        validate_token(record["state_token"])
        value = record["acknowledgement"]
        if set(value) != {"project_id", "pause_reason", "entries"}:
            raise ValueError("unsupported acknowledgement evidence")
        if value["project_id"] != head["project_id"] or type(value["entries"]) is not list:
            raise ValueError("foreign acknowledgement evidence")
        # Strict entry keys prevent silently dropping substituted/unsupported evidence.
        keys = {"request", "approval", "state", "actual", "reserved_autonomously", "exposure_floor"}
        if any(set(entry) != keys for entry in value["entries"]):
            raise ValueError("unsupported acknowledgement entry")
        acknowledged = AcknowledgedPause(
            value["project_id"],
            value["pause_reason"],
            tuple(decode_entry(entry) for entry in value["entries"]),
        )
        previous_revision = event["policy_revision"]
    return acknowledged


def validate_token(value: str) -> None:
    if not isinstance(value, str) or not value.startswith("v1."):
        raise ValueError("unsupported paused-state token")
    sha256(value[3:])


@dataclass(frozen=True)
class PausedState:
    project_id: str
    policy_revision: int
    pause_reason: str | None
    state_token: str | None


@dataclass(frozen=True)
class ResumeReceipt:
    project_id: str
    policy_revision: int


class BudgetResumeStore(BudgetStore):
    @staticmethod
    def _children(connection, project_id):
        return (
            connection.execute(
                sa.select(db.review_request).where(db.review_request.c.project_id == project_id)
            )
            .mappings()
            .all()
        )

    @staticmethod
    def _admit(gate: BudgetGate, children) -> BudgetGate:
        if any(child["state"] in {"dispatched", "unconfirmed"} for child in children):
            raise BudgetRefused("resume_in_flight")
        return gate.acknowledge_pause()

    def _state_token(self, connection, head) -> str:
        # All writers already serialize on this head. Include immutable event IDs
        # as generation nonces, including outcomes at an unchanged policy revision.
        tables = (db.operation, db.dry_run, db.review_request, db.event)
        state = {"head": dict(head)}
        for table in tables:
            rows = (
                connection.execute(
                    sa.select(table)
                    .where(table.c.project_id == head["project_id"])
                    .order_by(*table.primary_key.columns)
                )
                .mappings()
                .all()
            )
            state[table.name] = [
                {
                    key: value.isoformat() if key == "occurred_at" else value
                    for key, value in row.items()
                }
                for row in rows
            ]
        return "v1." + digest(state)

    def paused_state(self, project_id: str, *, actor: Principal) -> PausedState:
        self._admin(actor)
        identity(project_id)
        with self.engine.begin() as connection:
            head = self._head(connection, project_id)
            gate = self._gate(connection, head)
            token = None
            if gate.pause_reason is not None:
                try:
                    self._admit(gate, self._children(connection, project_id))
                except BudgetRefused:
                    pass
                else:
                    token = self._state_token(connection, head)
            return PausedState(project_id, head["revision"], gate.pause_reason, token)

    def resume(
        self, project_id: str, *, actor: Principal, expected_revision: int, state_token: str
    ) -> ResumeReceipt:
        self._admin(actor)
        identity(project_id)
        if type(expected_revision) is not int or expected_revision < 1:
            raise ValueError("expected revision must be a positive integer")
        validate_token(state_token)
        with self.engine.begin() as connection:
            head = self._head(connection, project_id)
            if head["revision"] != expected_revision:
                raise BudgetRefused("policy_conflict")
            gate = self._gate(connection, head)
            changed = self._admit(gate, self._children(connection, project_id))
            if not hmac.compare_digest(state_token, self._state_token(connection, head)):
                raise BudgetRefused("paused_state_conflict")
            revision = head["revision"] + 1
            connection.execute(
                sa.update(db.head)
                .where(db.head.c.project_id == project_id)
                .values(revision=revision, pause_reason=None)
            )
            self._event(
                connection,
                actor,
                dict(head) | {"revision": revision},
                "resume",
                {
                    "version": 1,
                    "expected_revision": expected_revision,
                    "state_token": state_token,
                    "acknowledgement": asdict(changed.acknowledged_pause),
                },
            )
        return ResumeReceipt(project_id, revision)
