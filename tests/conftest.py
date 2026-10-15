"""Unit tests must not resolve an operator's live Vault credentials."""

import sys

import pytest

from ojiichan import credentials


@pytest.fixture(autouse=True)
def isolate_operator_vault(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "locke.vault", None)
    monkeypatch.setenv(credentials.VAULT_CACHE_PATH_ENV, str(tmp_path / "unit-vault.json"))
    credentials.reset_cache()
    yield
    credentials.reset_cache()
