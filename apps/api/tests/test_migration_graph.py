"""Keep independently delivered migrations reachable through one deployable head."""

import ast
from pathlib import Path

import pytest
from alembic.script import ScriptDirectory


def test_qualification_auth_merge_is_the_single_head():
    scripts = ScriptDirectory(str(Path(__file__).parents[1] / "migrations"))
    assert scripts.get_heads() == ["m5_auth_0001"]
    merged = scripts.get_revision("head")
    assert merged is not None
    assert merged.down_revision == ("qualification_0007", "auth_jobs_0001")
    revisions = {revision.revision for revision in scripts.walk_revisions()}
    assert {
        "qualification_0001",
        "qualification_0002",
        "qualification_0003",
        "qualification_0004",
        "qualification_0005",
        "qualification_0006",
        "qualification_0007",
        "m5_jobs_0001",
        "auth_jobs_0001",
        "auth_budget_0001",
    } <= revisions


def test_auth_jobs_merge_preserves_both_histories():
    scripts = ScriptDirectory(str(Path(__file__).parents[1] / "migrations"))
    merged = scripts.get_revision("auth_jobs_0001")
    assert merged is not None
    assert merged.down_revision == ("auth_budget_0001", "0006_proposal_jobs")
    revisions = {revision.revision: revision for revision in scripts.walk_revisions()}
    assert revisions["0006_proposal_jobs"].down_revision == "budget_0002"
    assert {"0003_authentication", "budget_0001", "budget_0002"} <= revisions.keys()


@pytest.mark.parametrize("revision", ["auth_jobs_0001", "m5_auth_0001"])
def test_merge_has_no_schema_operations(revision):
    scripts = ScriptDirectory(str(Path(__file__).parents[1] / "migrations"))
    merged = scripts.get_revision(revision)
    assert merged is not None
    tree = ast.parse(Path(merged.path).read_text())
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    assert set(functions) == {"upgrade", "downgrade"}
    for function in functions.values():
        assert len(function.body) == 1
        assert isinstance(function.body[0], ast.Pass)


def test_auth_budget_merge_preserves_both_histories():
    scripts = ScriptDirectory(str(Path(__file__).parents[1] / "migrations"))
    merged = scripts.get_revision("auth_budget_0001")
    assert merged is not None
    assert set(merged.down_revision) == {"0003_authentication", "budget_0002"}
    revisions = {revision.revision: revision for revision in scripts.walk_revisions()}
    assert revisions["0003_authentication"].down_revision == "0002_local_models"
    assert revisions["budget_0001"].down_revision == "0002_local_models"
    assert revisions["budget_0002"].down_revision == "budget_0001"
    assert "0002_local_models" in revisions
