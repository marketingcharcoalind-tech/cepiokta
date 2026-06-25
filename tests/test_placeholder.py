"""Placeholder test to verify test infrastructure."""


def test_placeholder(sample_fixture: str) -> None:
    """Verify test infrastructure is working."""
    assert sample_fixture == "fixture_works"


def test_basic_math() -> None:
    """Sanity check."""
    assert 1 + 1 == 2
