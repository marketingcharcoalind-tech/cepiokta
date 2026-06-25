"""Pytest configuration and shared fixtures."""

import pytest


@pytest.fixture
def sample_fixture() -> str:
    """Placeholder fixture for testing setup."""
    return "fixture_works"
