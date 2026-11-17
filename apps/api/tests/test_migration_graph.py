"""Keep independently delivered migrations reachable through one deployable head."""

import ast
from pathlib import Path

from alembic.script import ScriptDirectory


def test_auth_jobs_merge_is_the_single_head():
    scripts = ScriptDirectory(str(Path(__file__).parents[1] / "migrations"))
    assert scripts.get_heads() == ["management_graph_0001"]
    merged = scripts.get_revision("head")
    assert merged is not None
    assert merged.down_revision == ("auth_jobs_0001", "auth_proposal_0001")
    revisions = {revision.revision: revision for revision in scripts.walk_revisions()}
    for parent in merged.down_revision:
        assert revisions[parent].down_revision == (
            "auth_budget_0001",
            "0006_proposal_jobs",
        )
    assert revisions["0006_proposal_jobs"].down_revision == "budget_0002"
    assert {"0003_authentication", "budget_0001", "budget_0002"} <= revisions.keys()


def test_auth_jobs_merge_has_no_schema_operations():
    scripts = ScriptDirectory(str(Path(__file__).parents[1] / "migrations"))
    for revision in ("management_graph_0001", "auth_jobs_0001", "auth_proposal_0001"):
        merged = scripts.get_revision(revision)
        assert merged is not None
        assert merged.module.branch_labels is None
        assert merged.module.depends_on is None
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
