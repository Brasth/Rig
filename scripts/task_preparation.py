"""Parent-selected task drafts. No discovery, execution, package creation or admission.

Returns a readable structured brief plus an inert, fingerprint-bound preparation object.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex

import context_packages
import harness
import preparation_binding as binding
import preparation_inputs as inputs
import workflow_recipes


def prepare(repo, selection):
    if os.environ.get("RIG_JOB_ID") or os.environ.get("RIG_JOB_DIR"):
        raise ValueError("task preparation is parent-only")
    draft = inputs.normalize(repo, selection)
    state = harness.project_state(repo)
    preparation, details = binding.build(repo, draft)
    gaps = list(details["unresolved"])
    contract = details["contract"]
    selection = {"files": draft["references"], "constraints": draft["constraints"] + draft["exclusions"],
                 "decisions": [row["choice"] for row in draft.get("decisions", [])],
                 "test_commands": [shlex.join(check["argv"]) for check in draft["checks"]]}
    preview = context_packages.preview(repo, selection) if state["enabled"] else None
    parameters, recipe_preview = None, None
    if draft["recipe"]:
        parameters = {"task": draft["task"], "files": draft["files"]}
        if draft["recipe"] == "research-implement":
            parameters["research_sources"] = draft["research_sources"]
        if contract:
            # Writer and parent final verifier use parent criteria; independent review remains separate.
            node = "implement" if draft["recipe"] != "ui-validation" else "verify"
            declared = workflow_recipes.show(draft["recipe"])["recipe"]["nodes"]
            if node not in {item["id"] for item in declared}:
                node = declared[-1]["id"]
            parameters["acceptance_contracts"] = {node: contract}
            if node in {item["id"] for item in declared if item["role"] in workflow_recipes.wf.WRITE_ROLES}:
                # Only the writer node carries the prepared brief; reviewers and final verifiers do not.
                parameters["preparations"] = {node: preparation}
        if draft["files"] and (draft["recipe"] != "research-implement" or draft["research_sources"]):
            recipe_preview = workflow_recipes.preview(repo, draft["recipe"], parameters)
    operations = ["Parent reviews draft scope, brief, checks and manual criteria."]
    if draft["references"]:
        operations.append("Explicitly call rig_context_build with context_selection; this preview is not a built package.")
    if draft["recipe"]:
        operations.extend(["Bind any built context package to selected node(s) in recipe_parameters.context_packages.",
                           "Call rig_workflow_recipe_preview again, review the spec, then explicitly create and advance."])
    else:
        operations.append("Pick with preparation, then explicitly launch/start with the exact brief, files, "
                          "acceptance_contract, preparation and any built context package.")
    operations.append("Run all checks and record manual criterion evidence before current parent acceptance; readiness grants no authority.")
    return {"schema_version": 1, "preview_only": True, "ready": not gaps,
            "execution_ready": preparation["readiness"]["execution_ready"],
            "readiness_gaps": details["gaps"],
            "files": draft["files"], "brief": details["brief"], "acceptance_contract": contract,
            "preparation": preparation,
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
        gaps = [row["message"] for row in result["readiness_gaps"]]
        print(json.dumps(result, indent=2) if args.json else result["brief"] + "\n\n" +
              "\n".join(result["unresolved"] + gaps + result["next_operations"]))
        return 0
    except (ValueError, OSError) as error:
        parser.exit(2, str(error) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
