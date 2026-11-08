"""Keep independently delivered migrations reachable through one deployable head."""

from pathlib import Path

from alembic.script import ScriptDirectory


def test_auth_budget_merge_preserves_both_histories():
    scripts = ScriptDirectory(str(Path(__file__).parents[1] / "migrations"))
    assert scripts.get_heads() == ["auth_proposal_0001"]
    merged = scripts.get_revision("head")
    assert merged is not None
    assert set(merged.down_revision) == {"auth_budget_0001", "0006_proposal_jobs"}
    revisions = {revision.revision: revision for revision in scripts.walk_revisions()}
    assert set(revisions["auth_budget_0001"].down_revision) == {
        "0003_authentication",
        "budget_0002",
    }
    assert revisions["0006_proposal_jobs"].down_revision == "budget_0002"
    assert revisions["0003_authentication"].down_revision == "0002_local_models"
    assert revisions["budget_0001"].down_revision == "0002_local_models"
    assert revisions["budget_0002"].down_revision == "budget_0001"
    assert "0002_local_models" in revisions
    assert merged.module.branch_labels is None
    assert merged.module.depends_on is None
    assert merged.module.upgrade() is None
    assert merged.module.downgrade() is None
