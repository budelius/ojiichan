"""Tests for the project-scoped provisioning package."""

from __future__ import annotations

import httpx

from ojiichan.provision import (
    ElkProvisionConfig,
    ElkProvisioner,
    OtelProvisionConfig,
    OtelProvisioner,
    RustrakProvisionConfig,
    RustrakProvisioner,
    provision_all,
    vault_write,
)
from ojiichan.provision.base import ProvisionStep, run_steps


# --- engine -----------------------------------------------------------------
def test_run_steps_created_and_skipped():
    calls: list[str] = []
    steps = [
        ProvisionStep("a", "make a", check=lambda: False, apply=lambda: calls.append("a")),
        ProvisionStep("b", "b exists", check=lambda: True, apply=lambda: calls.append("b")),
    ]
    result = run_steps("t", steps, dry_run=False)
    assert [s.status for s in result.steps] == ["created", "exists"]
    assert calls == ["a"]  # b was skipped
    assert result.ok and result.created


def test_run_steps_dry_run_applies_nothing():
    applied: list[str] = []
    steps = [ProvisionStep("a", "make a", check=lambda: False, apply=lambda: applied.append("a"))]
    result = run_steps("t", steps, dry_run=True)
    assert result.steps[0].status == "would-create"
    assert applied == []
    assert result.ok and not result.created


def test_run_steps_stops_on_failure():
    reached: list[str] = []

    def boom() -> None:
        raise RuntimeError("nope")

    steps = [
        ProvisionStep("a", "a", check=lambda: False, apply=boom),
        ProvisionStep("b", "b", check=lambda: False, apply=lambda: reached.append("b")),
    ]
    result = run_steps("t", steps, dry_run=False)
    assert result.steps[0].status == "failed"
    assert result.steps[0].error == "nope"
    assert len(result.steps) == 1  # stopped before b
    assert reached == [] and not result.ok


# --- vault write-path -------------------------------------------------------
def test_secret_path_uses_secret_map(monkeypatch):
    monkeypatch.delenv("OJIICHAN_VAULT_FOLDER", raising=False)
    assert vault_write.secret_path("ELASTICSEARCH_PASSWORD") == "ojiichan/elk/password"
    # unknown env var falls back to a literal sub-path
    assert vault_write.secret_path("SOMETHING_NEW") == "ojiichan/SOMETHING_NEW"


def test_store_secrets_unavailable_when_no_client(monkeypatch):
    monkeypatch.setattr(vault_write, "connect", lambda: None)
    out = vault_write.store_secrets({"ELASTICSEARCH_HOST": "https://os"})
    assert out == {"ELASTICSEARCH_HOST": "unavailable"}


def test_store_secrets_writes_via_client(monkeypatch):
    written: dict[str, str] = {}

    class FakeClient:
        def set_secret(self, path, value, *, username=None, uri=None, notes=None):
            written[path] = value
            return "created"

    monkeypatch.setattr(vault_write, "connect", lambda: FakeClient())
    monkeypatch.delenv("OJIICHAN_VAULT_FOLDER", raising=False)
    out = vault_write.store_secrets({"ELASTICSEARCH_PASSWORD": "pw", "RUSTRAK_API_TOKEN": None})
    assert out == {"ELASTICSEARCH_PASSWORD": "created", "RUSTRAK_API_TOKEN": "skipped"}
    assert written == {"ojiichan/elk/password": "pw"}


# --- ELK provisioner --------------------------------------------------------
def _elk(monkeypatch, *, exists: bool, captured: dict | None = None) -> ElkProvisioner:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200 if exists else 404)
        if captured is not None:
            captured[request.url.path] = request.method
        return httpx.Response(200, json={"ok": True})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://os:9200")
    config = ElkProvisionConfig(
        admin_url="https://os:9200",
        admin_user="admin",
        admin_password="x",
        project="m3rp-api",
        index_prefix="m3rp-api",
        scoped_password="fixed-pw",
    )
    return ElkProvisioner(config, client=client)


def test_elk_dry_run_plans_all_steps(monkeypatch):
    result = _elk(monkeypatch, exists=False).provision(dry_run=True)
    assert [s.name for s in result.steps] == [
        "index_template",
        "ism_policy",
        "role",
        "internal_user",
        "role_mapping",
    ]
    assert all(s.status == "would-create" for s in result.steps)
    assert result.secrets_written == []


def test_elk_apply_creates_and_writes_vault(monkeypatch):
    stored: dict[str, str] = {}

    class FakeClient:
        def set_secret(self, path, value, *, username=None, uri=None, notes=None):
            stored[path] = value
            return "created"

    monkeypatch.setattr(vault_write, "connect", lambda: FakeClient())
    monkeypatch.delenv("OJIICHAN_VAULT_FOLDER", raising=False)
    captured: dict[str, str] = {}
    result = _elk(monkeypatch, exists=False, captured=captured).provision(dry_run=False)
    assert result.ok and result.created
    # all five resources PUT
    assert any("_index_template/m3rp-api-logs" in p for p in captured)
    assert any("internalusers/m3rp-api-writer" in p for p in captured)
    # explicit scoped password is persisted alongside host/user/prefix
    assert stored["ojiichan/elk/password"] == "fixed-pw"
    assert set(result.secrets_written) == {
        "ELASTICSEARCH_HOST",
        "ELASTICSEARCH_USERNAME",
        "ELASTICSEARCH_PASSWORD",
        "ELASTICSEARCH_INDEX_PREFIX",
    }


def test_elk_existing_user_does_not_rotate_password(monkeypatch):
    # When nothing is explicit and resources already exist, the password is not
    # stored (we can't know the live value), and a note explains why.
    config = ElkProvisionConfig(
        admin_url="https://os:9200",
        admin_user="admin",
        admin_password="x",
        project="m3rp-api",
        index_prefix="m3rp-api",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})  # everything exists

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://os:9200")
    monkeypatch.setattr(vault_write, "connect", lambda: None)
    result = ElkProvisioner(config, client=client).provision(dry_run=False)
    assert all(s.status == "exists" for s in result.steps)
    assert any("left unchanged" in note for note in result.notes)


# --- Rustrak provisioner ----------------------------------------------------
def test_rustrak_nothing_to_do():
    result = RustrakProvisioner(RustrakProvisionConfig(project="p")).provision(dry_run=True)
    assert result.steps == []
    assert any("nothing to do" in note for note in result.notes)


def test_rustrak_register_only_dry_run():
    config = RustrakProvisionConfig(project="p", dsn="https://k@ingest/1")
    result = RustrakProvisioner(config).provision(dry_run=True)
    assert result.steps[0].name == "register_credentials"
    assert result.steps[0].status == "would-create"


# --- OTel provisioner -------------------------------------------------------
def test_otel_requires_endpoint():
    result = OtelProvisioner(OtelProvisionConfig(endpoint="")).provision(dry_run=True)
    assert result.steps == []
    assert any("nothing to do" in note for note in result.notes)


def test_otel_dry_run_plan():
    result = OtelProvisioner(OtelProvisionConfig(endpoint="https://otel")).provision(dry_run=True)
    assert result.steps[0].name == "endpoint_resolved"
    # endpoint is already set, so the precondition reports satisfied
    assert result.steps[0].status == "exists"
    assert result.ok and result.secrets_written == []


# --- one-shot orchestrator --------------------------------------------------
def test_provision_all_returns_one_result_per_backend():
    # No config supplied at all: every backend is skipped (not failed), so an
    # agent gets a clear "what's missing" report rather than a crash.
    results = provision_all(
        project="demo",
        elk_admin_url=None,
        elk_admin_password=None,
        dry_run=True,
    )
    assert [r.target for r in results] == ["elk", "otel", "rustrak"]
    assert all(any("skipped" in n for n in r.notes) for r in results)


def test_provision_all_runs_elk_when_admin_present(monkeypatch):
    monkeypatch.setattr(vault_write, "connect", lambda: None)
    # avoid real network in the existence probe
    monkeypatch.setattr(
        "ojiichan.provision.elk.ElkProvisioner._exists", lambda self, path: False
    )
    results = provision_all(
        project="demo",
        elk_admin_url="https://os:9200",
        elk_admin_password="x",
        otlp_endpoint="https://otel",
        dry_run=True,
    )
    by_target = {r.target: r for r in results}
    # elk planned all five steps; otel planned; rustrak skipped (no DSN/admin)
    assert [s.name for s in by_target["elk"].steps][0] == "index_template"
    assert by_target["otel"].steps  # has at least the endpoint_resolved step
    assert any("skipped" in n for n in by_target["rustrak"].notes)
