from labelloop_llm import reviewer_backend


def test_reviewer_defaults_to_fixtures() -> None:
    assert reviewer_backend() == "fixture"
