from __future__ import annotations

import json
import sys
import time
import types
from pathlib import Path

import pytest

from ojiichan import credentials


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(credentials.VAULT_CACHE_PATH_ENV, str(tmp_path / "vault.json"))
    monkeypatch.delenv(credentials.VAULT_CACHE_DISABLE_ENV, raising=False)
    monkeypatch.delenv(credentials.VAULT_CACHE_TTL_ENV, raising=False)
    monkeypatch.delenv(credentials.VAULT_FOLDER_ENV, raising=False)
    for env_var in credentials.SECRET_MAP:
        monkeypatch.delenv(env_var, raising=False)
    credentials._reset_for_tests()
    yield
    credentials._reset_for_tests()


class FakeVaultClient:
    def __init__(self, snapshot: dict[str, str]) -> None:
        self._snapshot = snapshot
        self.list_calls = 0

    def list_secrets(self, prefix: str = "") -> dict[str, str]:
        self.list_calls += 1
        if not prefix:
            return dict(self._snapshot)
        return {k: v for k, v in self._snapshot.items() if k.startswith(prefix)}


def _install_fake_locke(monkeypatch: pytest.MonkeyPatch, client: FakeVaultClient) -> None:
    module = types.ModuleType("locke.vault")

    class VaultClient:
        @classmethod
        def connect(cls) -> FakeVaultClient:
            return client

    module.VaultClient = VaultClient
    parent = types.ModuleType("locke")
    parent.vault = module
    monkeypatch.setitem(sys.modules, "locke", parent)
    monkeypatch.setitem(sys.modules, "locke.vault", module)


def test_env_var_wins_over_vault(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeVaultClient({"ojiichan/bugsink-host": "vault-host"})
    _install_fake_locke(monkeypatch, fake)
    monkeypatch.setenv("BUGSINK_HOST", "env-host")

    assert credentials.get_secret("BUGSINK_HOST") == "env-host"
    assert fake.list_calls == 0


def test_warm_populates_all_known_secrets_in_one_call(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeVaultClient(
        {
            "ojiichan/bugsink-host": "vault-host",
            "ojiichan/bugsink-api-token": "vault-token",
        }
    )
    _install_fake_locke(monkeypatch, fake)

    assert credentials.get_secret("BUGSINK_HOST") == "vault-host"
    assert credentials.get_secret("BUGSINK_API_TOKEN") == "vault-token"
    # Missing secret stays None but does not retrigger a lookup.
    assert credentials.get_secret("SENTRY_DSN") is None
    assert credentials.get_secret("SENTRY_DSN") is None

    assert fake.list_calls == 1


def test_disk_cache_is_reused_across_process_restarts(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeVaultClient({"ojiichan/bugsink-host": "vault-host"})
    _install_fake_locke(monkeypatch, fake)

    assert credentials.get_secret("BUGSINK_HOST") == "vault-host"
    assert fake.list_calls == 1

    # Simulate a new process: fresh module-level state, but the on-disk cache survives.
    credentials._reset_for_tests()

    assert credentials.get_secret("BUGSINK_HOST") == "vault-host"
    # Disk cache served it — no second list_secrets call.
    assert fake.list_calls == 1


def test_expired_disk_cache_refetches(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeVaultClient({"ojiichan/bugsink-host": "vault-host"})
    _install_fake_locke(monkeypatch, fake)
    monkeypatch.setenv(credentials.VAULT_CACHE_TTL_ENV, "60")

    assert credentials.get_secret("BUGSINK_HOST") == "vault-host"
    cache_path = Path(credentials._disk_cache_path())
    payload = json.loads(cache_path.read_text())
    payload["timestamp"] = time.time() - 3600
    cache_path.write_text(json.dumps(payload))

    credentials._reset_for_tests()
    assert credentials.get_secret("BUGSINK_HOST") == "vault-host"
    assert fake.list_calls == 2


def test_disk_cache_file_is_user_only(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeVaultClient({"ojiichan/bugsink-host": "vault-host"})
    _install_fake_locke(monkeypatch, fake)

    assert credentials.get_secret("BUGSINK_HOST") == "vault-host"
    cache_path = Path(credentials._disk_cache_path())
    mode = cache_path.stat().st_mode & 0o777
    assert mode == 0o600


def test_disable_flag_skips_disk_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeVaultClient({"ojiichan/bugsink-host": "vault-host"})
    _install_fake_locke(monkeypatch, fake)
    monkeypatch.setenv(credentials.VAULT_CACHE_DISABLE_ENV, "1")

    assert credentials.get_secret("BUGSINK_HOST") == "vault-host"
    assert not Path(credentials._disk_cache_path()).exists()

    credentials._reset_for_tests()
    assert credentials.get_secret("BUGSINK_HOST") == "vault-host"
    assert fake.list_calls == 2


def test_locke_missing_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "locke", None)
    monkeypatch.setitem(sys.modules, "locke.vault", None)
    assert credentials.get_secret("BUGSINK_HOST") is None
