"""Trusted development-protocol bounds; no database, queue or ML execution."""

from dataclasses import FrozenInstanceError, replace
from uuid import uuid4

import pytest

from labelloop_api.management_evaluation import (
    ApprovedDevelopmentProtocol,
    CanonicalManagementEvaluation,
)


@pytest.fixture
def protocol():
    return ApprovedDevelopmentProtocol(uuid4(), "development", 1, uuid4())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("project_id", "not-a-uuid"),
        ("variant_id", "not-a-uuid"),
        ("id", ""),
        ("id", "a" * 201),
        ("id", "../protocol"),
        ("revision", 0),
        ("revision", -1),
        ("revision", True),
        ("revision", 1.0),
    ],
)
def test_development_protocol_refuses_invalid_configuration(protocol, field, value):
    with pytest.raises(ValueError):
        replace(protocol, **{field: value})


def test_development_protocol_is_immutable_and_has_no_client_role_or_threshold(protocol):
    with pytest.raises(FrozenInstanceError):
        protocol.revision = 2
    with pytest.raises(TypeError):
        ApprovedDevelopmentProtocol(
            protocol.project_id, protocol.id, 1, protocol.variant_id, role="locked_final_test"
        )
    assert set(protocol.__dataclass_fields__) == {"project_id", "id", "revision", "variant_id"}


def test_development_protocols_are_bounded_and_uniquely_project_scoped(protocol):
    assert CanonicalManagementEvaluation(None, None, (protocol,)).protocols == (protocol,)
    for items in ([protocol], (protocol, protocol), (protocol,) * 1001):
        with pytest.raises(ValueError):
            CanonicalManagementEvaluation(None, None, items)
    other = replace(protocol, project_id=uuid4())
    assert len(CanonicalManagementEvaluation(None, None, (protocol, other)).protocols) == 2
