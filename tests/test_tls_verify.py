"""TLS CA-bundle support (ELASTICSEARCH_CA_CERT) across config, transports, provision.

obachan's OpenSearch presents a cert issued by the private ``obachan-internal-ca``,
which is not in the system trust store. Without a CA bundle every TLS call to the
internal cluster fails verification, so the only prior option was ``--insecure``.
These tests pin the behaviour of the ``ca_cert`` plumbing that fixes that.
"""

from __future__ import annotations

from typing import Any

import pytest

from ojiichan import transports
from ojiichan.config import ElkConfig, tls_verify
from ojiichan.provision import (
    ElkProvisionConfig,
    ElkProvisioner,
    OtelProvisionConfig,
    OtelProvisioner,
    RustrakProvisionConfig,
    RustrakProvisioner,
)

CA = "/etc/ojiichan/obachan-internal-ca.pem"


# --- tls_verify truth table -------------------------------------------------
def test_tls_verify_system_store_when_no_ca() -> None:
    assert tls_verify(True, None) is True


def test_tls_verify_pins_ca_bundle() -> None:
    assert tls_verify(True, CA) == CA


def test_tls_verify_insecure_ignores_ca() -> None:
    # --insecure must win: never silently verify against a CA the caller disabled.
    assert tls_verify(False, None) is False
    assert tls_verify(False, CA) is False


# --- ElkConfig --------------------------------------------------------------
def test_elk_config_verify_property_uses_ca() -> None:
    assert ElkConfig(host="os", ca_cert=CA).verify == CA
    assert ElkConfig(host="os").verify is True
    assert ElkConfig(host="os", verify_certs=False, ca_cert=CA).verify is False


def test_elk_config_from_env_reads_ca(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ELASTICSEARCH_CA_CERT", CA)
    assert ElkConfig.from_env().ca_cert == CA


# --- transports thread the CA into httpx.post -------------------------------
class _Capture:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] = {}

    def __call__(self, url: str, **kwargs: Any) -> Any:
        self.kwargs = kwargs

        class _Resp:
            status_code = 200

        return _Resp()


def test_logstash_transport_passes_ca(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _Capture()
    monkeypatch.setattr(transports.httpx, "post", cap)
    cfg = ElkConfig(host=None, transport="logstash", logstash_url="http://ls:8080", ca_cert=CA)
    assert transports.send_logstash(cfg, "app-auth", {"event_type": "x"}) is True
    assert cap.kwargs["verify"] == CA


def test_otlp_transport_passes_ca(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _Capture()
    monkeypatch.setattr(transports.httpx, "post", cap)
    cfg = ElkConfig(host=None, transport="otlp", otlp_endpoint="http://c:4318", ca_cert=CA)
    assert transports.send_otlp(cfg, {"message": "x", "service": "svc"}, level="info") is True
    assert cap.kwargs["verify"] == CA


# --- provision clients build with verify=<ca path> --------------------------
class _FakeClient:
    """Captures the ``verify=`` kwarg a provisioner hands to httpx.Client."""

    last_verify: Any = None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        type(self).last_verify = kwargs.get("verify")

    def post(self, *args: Any, **kwargs: Any) -> Any:
        class _Resp:
            status_code = 200
            content = b""

            @staticmethod
            def json() -> dict[str, Any]:
                return {}

            @staticmethod
            def raise_for_status() -> None:
                return None

        return _Resp()


def test_elk_provisioner_client_pins_ca(monkeypatch: pytest.MonkeyPatch) -> None:
    import ojiichan.provision.elk as elk_mod

    monkeypatch.setattr(elk_mod.httpx, "Client", _FakeClient)
    _FakeClient.last_verify = None
    ElkProvisioner(
        ElkProvisionConfig(
            admin_url="https://os:9200",
            admin_user="admin",
            admin_password="pw",
            project="m3rp-api",
            index_prefix="m3rp-api",
            ca_cert=CA,
        )
    )
    assert _FakeClient.last_verify == CA


def test_rustrak_provisioner_client_pins_ca(monkeypatch: pytest.MonkeyPatch) -> None:
    import ojiichan.provision.rustrak as rustrak_mod

    monkeypatch.setattr(rustrak_mod.httpx, "Client", _FakeClient)
    _FakeClient.last_verify = None
    RustrakProvisioner(
        RustrakProvisionConfig(
            project="m3rp-api",
            admin_url="https://rustrak:8080",
            admin_token="tok",
            ca_cert=CA,
        )
    )
    assert _FakeClient.last_verify == CA


def test_otel_provisioner_probe_pins_ca(monkeypatch: pytest.MonkeyPatch) -> None:
    import ojiichan.provision.otel as otel_mod

    monkeypatch.setattr(otel_mod.httpx, "Client", _FakeClient)
    _FakeClient.last_verify = None
    OtelProvisioner(
        OtelProvisionConfig(project="demo", endpoint="https://otel:4318", probe=True, ca_cert=CA)
    )._probe()
    assert _FakeClient.last_verify == CA
