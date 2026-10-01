"""Parent-selected task drafts. No discovery, execution, package creation or admission."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex

import acceptance_contract as contracts
import context_packages
import harness
import preparation_inputs as inputs
import workflow_recipes


def _contract(repo, draft):
    rows = []
    for check in draft["checks"]:
        rows.append({"id": check["id"], "description": check["description"],
                     "scope": draft["files"], "evidence_type": "check", "verifier_role": "parent",
                     "check": {key: check[key] for key in ("id", "argv", "cwd")}})
    for index, description in enumerate(draft["manual_criteria"], 1):
        identifier = f"manual-{index}"
        while identifier in {item["id"] for item in rows}:
            identifier = "m-" + identifier
        rows.append({"id": identifier, "description": description, "scope": draft["files"],
                     "evidence_type": "review_assertion", "verifier_role": "parent",
                     "artifact_kind": "parent-review"})
    if not rows or not draft["files"]:
        return None
    return contracts.normalize(repo, {"schema_version": 1, "contract_id": "prepared-task",
                                     "revision": 1, "criteria": rows}, draft["files"])


def prepare(repo, selection):
    if os.environ.get("RIG_JOB_ID") or os.environ.get("RIG_JOB_DIR"):
        raise ValueError("task preparation is parent-only")
    draft = inputs.normalize(repo, selection)
    state = harness.project_state(repo)
    gaps = []
    if not state["enabled"]:
        gaps.append("Project must be initialized and enabled before context preview or admission.")
    if not draft["files"]:
        gaps.append("Parent must select concrete writer files.")
    if not draft["checks"] and not draft["manual_criteria"]:
        gaps.append("Parent must supply exact checks or explicit manual acceptance criteria.")
    contract = _contract(repo, draft)
    selection = {"files": draft["references"], "constraints": draft["constraints"] + draft["exclusions"],
                 "decisions": [], "test_commands": [shlex.join(check["argv"]) for check in draft["checks"]]}
    preview = context_packages.preview(repo, selection) if state["enabled"] else None
    parameters, recipe_preview = None, None
    if draft["recipe"]:
        parameters = {"task": draft["task"], "files": draft["files"]}
        if draft["recipe"] == "research-implement":
            parameters["research_sources"] = draft["research_sources"]
            if not draft["research_sources"]:
                gaps.append("Parent must supply existing, disjoint research_sources for this recipe.")
        if contract:
            # Writer and parent final verifier use parent criteria; independent review remains separate.
            node = "implement" if draft["recipe"] != "ui-validation" else "verify"
            declared = workflow_recipes.show(draft["recipe"])["recipe"]["nodes"]
            if node not in {item["id"] for item in declared}:
                node = declared[-1]["id"]
            parameters["acceptance_contracts"] = {node: contract}
        if draft["files"] and (draft["recipe"] != "research-implement" or draft["research_sources"]):
            recipe_preview = workflow_recipes.preview(repo, draft["recipe"], parameters)
    brief = ["You are the scoped worker, not the orchestrator. First Rig call: rig_job_inbox.",
             "Do only the declared files and changes. Do not spawn or message workers.",
             "Task: " + draft["task"], "Files to modify: " + ", ".join(draft["files"])]
    for label, key in (("Constraints", "constraints"), ("Do not change", "exclusions"),
                       ("Manual acceptance", "manual_criteria")):
        brief.extend(label + ": " + item for item in draft[key])
    brief.extend("Required check: " + check["id"] + " (cwd " + check["cwd"] + "): " +
                 shlex.join(check["argv"]) for check in draft["checks"])
    operations = ["Parent reviews draft scope, brief, checks and manual criteria."]
    if draft["references"]:
        operations.append("Explicitly call rig_context_build with context_selection; this preview is not a built package.")
    if draft["recipe"]:
        operations.extend(["Bind any built context package to selected node(s) in recipe_parameters.context_packages.",
                           "Call rig_workflow_recipe_preview again, review the spec, then explicitly create and advance."])
    else:
        operations.append("Pick and explicitly launch/start with files, brief, acceptance_contract and any built context package.")
    operations.append("Run all checks and record manual criterion evidence before current parent acceptance; readiness grants no authority.")
    return {"schema_version": 1, "preview_only": True, "ready": not gaps,
            "files": draft["files"], "brief": "\n".join(brief), "acceptance_contract": contract,
            "context_selection": selection, "context_preview": preview, "recipe": draft["recipe"],
            "recipe_parameters": parameters, "recipe_preview": recipe_preview,
            "unresolved": gaps, "next_operations": operations}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare"])
    parser.add_argument("--file", required=True)
    parser.add_argument("--repo", default=".")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        with Path(args.file).open("rb") as stream:
            raw = stream.read(context_packages.MAX_SPEC_BYTES + 1)
        if len(raw) > context_packages.MAX_SPEC_BYTES:
            raise ValueError("task preparation exceeds 32 KiB")
        result = prepare(args.repo, json.loads(raw, object_pairs_hook=inputs.json_object))
        print(json.dumps(result, indent=2) if args.json else result["brief"] + "\n" +
              "\n".join(result["unresolved"] + result["next_operations"]))
        return 0
    except (ValueError, OSError) as error:
        parser.exit(2, str(error) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
