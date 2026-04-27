"""Shared pytest configuration and markers."""


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: marks tests that call real external services (Bedrock, GitHub). Run with: pytest -m integration",
    )
