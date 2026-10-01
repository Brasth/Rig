"""Fixed, advisory recovery steps; recorded data never supplies instructions."""
from __future__ import annotations


def step(operation, instruction, *prerequisites):
    return {"operation": operation, "instruction": instruction,
            "prerequisites": list(prerequisites), "automatic": False}


def explain(state, reservation, verification, *, evidence_complete=True):
    """Suggest existing operations without establishing their eligibility."""
    owner = "Saved credentials_path or authenticated owner credentials"
    inspect = step("rig_job_show", "Inspect current scoped content and verification evidence.")
    if not evidence_complete:
        return ["incomplete-evidence"], [inspect, step("rig_job_reconcile",
            "Inspect ownership once; preserve protection until new evidence is available.")]
    if state == "ask":
        return ["pending-ask"], [step("rig_job_allow / rig_job_deny",
            "Inspect and answer the pending ASK on this same attempt; do not replace it.",
            "Parent permission decision")]
    operation = reservation.get("operation") or reservation.get("pending_operation")
    if operation:
        return ["verification-operation"], [inspect, step("rig_job_reconcile",
            "Inspect the interrupted operation before using supported scoped recovery.",
            owner, "Confirm the check/evidence executor has stopped", "Recovery rationale")]
    if not reservation.get("stopped"):
        if state in {"cancelled", "cancel_requested", "unconfirmed", "stop-unconfirmed",
                     "stop-requested", "native-cancel-required"} or reservation.get("needs_reconciliation"):
            return ["stop-unconfirmed"], [step("owning-host",
                "Obtain actual terminal completion evidence. Keep files and execution slot protected.",
                "Exact attempt and executor identity"), step("rig_job_reconcile",
                "Inspect supported completion/recovery options; reporting alone does not release scope.", owner)]
        return [], [step("rig_job_wait", "Observe the existing running attempt.",
                         "No explicit Stop or cancellation intent")]
    if state in {"fail", "failed", "timeout", "cancelled"}:
        return ["execution-unverified"], [inspect, step("rig_job_close",
            "Release confirmed-stopped scope without acceptance, or prepare explicitly authorized new work.",
            owner, "Confirmed stopped execution", "Close rationale")]
    if verification.get("state") == "failed" or verification.get("acceptance") == "rejected":
        return ["failed-verification"], [inspect, step("rig_job_check",
            "Address failed criteria through authorized work; run the complete declared checks again.",
            owner, "Confirmed stopped execution", "Exact declared check arguments")]
    if verification.get("acceptance") == "accepted":
        if verification.get("next") == "review":
            return ["independent-review-required"], [inspect, step("rig_pick",
                "Request independent review of the current accepted writer snapshot; report unavailable review explicitly.",
                owner, "Current accepted snapshot", "Different known actual provider")]
        return ["acceptance-freshness-unchecked"], [inspect, step("rig_job_accept",
            "Reassess current content if recorded acceptance is stale; historical acceptance is not current verification.",
            owner, "Current snapshot", "All declared checks and criteria")]
    return ["parent-acceptance-required"], [inspect, step("rig_job_requirements",
        "Declare the complete acceptance manifest.", owner), step("rig_job_check",
        "Run every declared check deliberately.", owner, "Exact declared check arguments"),
        step("rig_job_accept", "Accept or reject the current scoped snapshot.", owner,
             "Current snapshot", "All required evidence")]


def format_lines(guide):
    lines = ["Recovery guidance (advisory; does not execute actions)",
             "Recorded state: " + guide.get("state", "unknown"),
             "Coverage: " + guide.get("coverage", "unknown")]
    for blocker in guide.get("blockers", []):
        lines.append("Blocker: " + blocker)
    for held in guide.get("held", []):
        lines.append("Held files: " + (", ".join(held.get("files", [])) or "unknown"))
        lines.append("Execution slot: " + str(held.get("slot", "unknown")))
        for resource in held.get("resources", []):
            lines.append("Held resource: " + resource)
    for gap in guide.get("evidence_gaps", []):
        lines.append("Evidence gap: " + gap)
    for index, item in enumerate(guide.get("steps", []), 1):
        lines.append(f"{index}. {item['operation']}: {item['instruction']}")
        if item.get("prerequisites"):
            lines.append("Requires: " + "; ".join(item["prerequisites"]))
    return lines
