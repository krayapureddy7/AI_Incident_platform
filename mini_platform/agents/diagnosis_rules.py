"""
Symptom/evidence corroboration rules.

Pure mapping between the Planner's symptom vocabulary and the Investigator's
root-cause vocabulary. This module is advisory only: nothing here selects or
overrides a root cause. Telemetry (evidence) remains the only signal this
platform trusts to decide *what is wrong* -- the corpus's own runbooks warn
against "correcting" a real memory leak by scaling instead of restarting, so
free-text incident descriptions are not allowed to move that decision. What
this module adds is visibility: whether the incident's reported symptoms
agree with the evidence-derived conclusion, surfaced in the diagnosis summary
and the audit trace, so a mismatch is legible instead of silent.
"""
from typing import List

#: Version of the corroboration rule set.
__version__ = "1.0.0"

#: Symptoms that, if reported, corroborate a given root cause.
#:
#: ``UNKNOWN_DEGRADATION`` is deliberately absent from every set: it is the
#: Planner's "no keyword matched" fallback, not a specific claim, so it can
#: neither corroborate nor conflict with anything.
ROOT_CAUSE_CORROBORATING_SYMPTOMS = {
    "MEMORY_LEAK_HEAP_EXHAUSTION": {"MEMORY_EXHAUSTION_OR_LEAK"},
    "CPU_AND_CONNECTION_SATURATION": {
        "RESOURCE_OR_CONNECTION_STARVATION",
        "LATENCY_SPIKE_OR_TIMEOUT",
    },
    "SERVICE_UNRESPONSIVE": {"HIGH_ERROR_RATE", "LATENCY_SPIKE_OR_TIMEOUT"},
    # EVIDENCE_UNAVAILABLE intentionally has no entry: see corroboration_status().
}


def corroboration_status(root_cause: str, symptoms: List[str]) -> str:
    """
    Classify agreement between reported symptoms and an evidence-derived root
    cause.

    Returns one of ``"N/A"``, ``"CORROBORATED"``, ``"CONFLICTING"``,
    ``"UNCORROBORATED"``:

    - ``N/A`` -- the root cause is ``EVIDENCE_UNAVAILABLE``; there is no
      diagnosis here to corroborate against.
    - ``CORROBORATED`` -- at least one reported symptom is in the
      corroborating set for this root cause.
    - ``CONFLICTING`` -- the incident was classified into one or more
      *specific* symptoms, none of which corroborate this root cause.
    - ``UNCORROBORATED`` -- no specific symptom was reported at all (only
      ``UNKNOWN_DEGRADATION``, or an empty list): the incident text simply
      contained no signal, as opposed to actively pointing elsewhere.
    """
    if root_cause == "EVIDENCE_UNAVAILABLE":
        return "N/A"

    specific = [s for s in symptoms if s != "UNKNOWN_DEGRADATION"]
    corroborating = ROOT_CAUSE_CORROBORATING_SYMPTOMS.get(root_cause, set())

    if any(s in corroborating for s in specific):
        return "CORROBORATED"
    if specific:
        return "CONFLICTING"
    return "UNCORROBORATED"


def corroboration_note(root_cause: str, symptoms: List[str]) -> str:
    """
    Human-readable clause to append to a diagnosis summary.

    Empty string when the status is ``CORROBORATED`` or ``N/A``, so a
    well-supported diagnosis reads exactly as it did before this module
    existed.
    """
    status = corroboration_status(root_cause, symptoms)
    if status == "CONFLICTING":
        return (
            f" Symptom/evidence corroboration: CONFLICTING -- reported symptom(s) "
            f"{symptoms} suggest a different condition than the telemetry shows; "
            "this classification is based on directly observed metrics/logs, not "
            "the incident description."
        )
    if status == "UNCORROBORATED":
        return (
            f" Symptom/evidence corroboration: UNCORROBORATED -- the incident "
            f"description did not contain keywords indicating this condition "
            f"(reported symptom(s): {symptoms}); classification is based solely "
            "on directly observed, per-service telemetry, not the incident text."
        )
    return ""
