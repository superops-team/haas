"""Admission Control tests (specs/admission-control/README.md)."""

from haas.admission import AdmissionControl, AdmissionInput
from haas.stores import MemoryStore


def test_admit_and_release_quota() -> None:
    admission = AdmissionControl(store=MemoryStore(), run_quota=1)
    req = AdmissionInput(principalHash="h1", appName="chrn_1", tenantId="t1")

    first = admission.admit_run(req)
    assert first.allowed is True
    assert first.leaseId is not None

    second = admission.admit_run(req)
    assert second.allowed is False
    assert second.code == "haas_quota_exceeded"

    admission.release_run(first.leaseId or "")
    third = admission.admit_run(req)
    assert third.allowed is True


def test_rate_limit() -> None:
    admission = AdmissionControl(store=MemoryStore(), rate_limit=1, run_quota=100)
    req = AdmissionInput(principalHash="h1", appName="chrn_1", tenantId="t1")
    assert admission.admit_run(req).allowed is True
    denied = admission.admit_run(req)
    assert denied.allowed is False
    assert denied.code == "haas_rate_limited"
