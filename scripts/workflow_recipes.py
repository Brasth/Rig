#!/usr/bin/env python3
"""Offline built-in recipe inspection and deterministic workflow compilation.

This module never creates/advances workflows, chooses providers, runs checks,
fetches content, renders templates as code, or writes files. Parameters are data.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sys
from pathlib import Path

import admission
import change_evidence
import context_packages
import routing_domains
import workflow_state as wf

RecipeError = wf.WorkflowError
CATALOG = Path(__file__).resolve().parents[1] / "templates" / "workflows"
NAMES = ("bugfix", "research-implement", "ui-validation")
MAX_INPUT_BYTES = 1024 * 1024
PARAMETERS = {
    "task": {"type": "string", "minLength": 1, "maxLength": 16384},
    "files": {"type": "array", "minItems": 1, "maxItems": 256,
              "items": {"type": "string"}, "description": "Concrete repository-relative source files."},
    "research_sources": {"type": "array", "minItems": 1, "maxItems": 100,
                         "items": {"type": "string"},
                         "description": "Existing readable repository files disjoint from writer scope."},
    "resources": {"type": "object", "description": "Node ID to explicit read/write resource array."},
    "acceptance_contracts": {"type": "object", "description": "Node ID to complete schema-1 acceptance contract; no global or inherited criteria."},
    "context_packages": {"type": "object", "description": "Node ID to an explicitly selected existing package_id/fingerprint reference. Never builds a package or expands file scope."},
}


def _encoded(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False).encode()
    except (ValueError, TypeError, RecursionError) as error:
        raise RecipeError("recipe input must be finite JSON data") from error


def _hash(value):
    return hashlib.sha256(_encoded(value)).hexdigest()


def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise RecipeError("duplicate JSON field: " + key)
        value[key] = item
    return value


def _decode(raw):
    if len(raw.encode()) > MAX_INPUT_BYTES:
        raise RecipeError("recipe JSON exceeds 1 MiB")
    try:
        return json.loads(raw, object_pairs_hook=_object,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-finite JSON")))
    except (ValueError, RecursionError) as error:
        raise RecipeError(f"invalid recipe JSON: {error}") from error


def _recipe(name, version=1):
    if not isinstance(name, str) or name not in NAMES:
        raise RecipeError("unknown built-in workflow recipe; use recipe list (remote/custom recipes are unsupported)")
    if type(version) is not int or version != 1:
        raise RecipeError("unsupported recipe version; expected 1")
    path = CATALOG / f"{name}.v{version}.json"
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
        raise RecipeError("built-in recipe is missing, unsafe or oversized")
    value = _decode(path.read_text(encoding="utf-8"))
    fields = {"schema_version", "name", "version", "description", "required_parameters", "optional_parameters", "nodes"}
    if not isinstance(value, dict) or set(value) != fields:
        raise RecipeError("invalid built-in recipe fields")
    if (type(value["schema_version"]) is not int or value["schema_version"] != 1
            or value["name"] != name or type(value["version"]) is not int or value["version"] != version):
        raise RecipeError("built-in recipe identity/version mismatch")
    for key in ("required_parameters", "optional_parameters"):
        names = value[key]
        if (not isinstance(names, list) or any(not isinstance(p, str) or p not in PARAMETERS for p in names)
                or len(set(names)) != len(names)):
            raise RecipeError("invalid built-in parameter declarations")
    if set(value["required_parameters"]) & set(value["optional_parameters"]):
        raise RecipeError("duplicate built-in parameter declarations")
    if not isinstance(value["nodes"], list) or not 1 <= len(value["nodes"]) <= wf.DEFAULT_MAX_NODES:
        raise RecipeError("invalid built-in recipe nodes")
    for node in value["nodes"]:
        allowed = {"id", "role", "task_domain", "effects", "scope", "depends_on", "final", "brief"}
        if not isinstance(node, dict) or set(node) - allowed or node.get("scope") not in {"files", "research_sources"}:
            raise RecipeError("invalid built-in recipe node; executable template fields are unsupported")
        if node.get("role") not in wf.NODE_ROLES or node.get("effects") not in {"none", "local"}:
            raise RecipeError("built-in recipes support bounded local work only")
    return value


def show(name, version=1):
    recipe = _recipe(name, version)
    keys = recipe["required_parameters"] + recipe["optional_parameters"]
    properties = {key: copy.deepcopy(PARAMETERS[key]) for key in keys}
    resource_schema = {"type": "array", "maxItems": 256, "items": {
        "type": "object", "additionalProperties": False, "required": ["name", "access"],
        "properties": {"name": {"type": "string"}, "access": {"enum": ["read", "write"]}},
    }}
    for key, schema in (("resources", resource_schema), ("acceptance_contracts", {"type": "object"}),
                        ("context_packages", context_packages.REFERENCE_SCHEMA)):
        if key in properties:
            properties[key].update(additionalProperties=False,
                                   properties={node["id"]: copy.deepcopy(schema) for node in recipe["nodes"]})
    return {"recipe": recipe, "recipe_hash": _hash(recipe),
            "parameter_schema": {"type": "object", "additionalProperties": False,
                                 "required": recipe["required_parameters"],
                                 "properties": properties}}


def listing():
    return [{"name": name, "version": 1, "description": (value := _recipe(name))["description"],
             "recipe_hash": _hash(value), "node_ids": [node["id"] for node in value["nodes"]]}
            for name in NAMES]


def _paths(repo, values, label, limit=256):
    if (not isinstance(values, list) or not 1 <= len(values) <= limit
            or any(not isinstance(p, str) or not p or p != p.strip() or len(p) > 4096 or "\0" in p
                   or Path(p).is_absolute() or ".." in Path(p).parts for p in values)):
        raise RecipeError(f"{label} requires 1 to {limit} concrete repository-relative files")
    declared, resolved = admission.canonical_files(repo, values)
    if any(Path(p).parts[0].casefold() in {".git", ".rig"} for p in declared + resolved):
        raise RecipeError(f"{label} cannot include repository control files")
    # Include resolved aliases just as admission does so a symlink cannot hide
    # research/writer overlap from the existing DAG validator.
    return resolved


def _stage_map(value, node_ids, label):
    if not isinstance(value, dict) or any(not isinstance(key, str) or key not in node_ids for key in value):
        raise RecipeError(f"{label} must map known recipe node IDs: {', '.join(node_ids)}")
    return value


def preview(repo, name, parameters, version=1):
    """Read and normalize only. Execution remains explicit workflow create/advance."""
    recipe = _recipe(name, version)
    if len(_encoded(parameters)) > MAX_INPUT_BYTES:
        raise RecipeError("recipe parameters exceed 1 MiB")
    allowed = set(recipe["required_parameters"] + recipe["optional_parameters"])
    if (not isinstance(parameters, dict) or set(parameters) - allowed
            or set(recipe["required_parameters"]) - set(parameters)):
        raise RecipeError("recipe parameters have missing or unsupported fields")
    task = parameters["task"]
    if not isinstance(task, str) or not task.strip() or len(task) > 16384 or "\0" in task:
        raise RecipeError("task must be nonempty text of at most 16384 characters")
    root = change_evidence.repository(repo)
    params = {"task": task, "files": _paths(root, parameters["files"], "files")}
    if "research_sources" in allowed:
        sources = _paths(root, parameters["research_sources"], "research_sources", 100)
        params["research_sources"] = sorted(routing_domains.validate_sources(root, sources))
    node_ids = [node["id"] for node in recipe["nodes"]]
    resources = _stage_map(parameters.get("resources", {}), node_ids, "resources")
    stage_contracts = _stage_map(parameters.get("acceptance_contracts", {}), node_ids, "acceptance_contracts")
    contexts = _stage_map(parameters.get("context_packages", {}), node_ids, "context_packages")
    params["resources"] = {}
    params["acceptance_contracts"] = {}
    params["context_packages"] = {}
    nodes = []
    for raw in recipe["nodes"]:
        node = {key: copy.deepcopy(value) for key, value in raw.items() if key != "scope"}
        node["files"] = list(params[raw["scope"]])
        raw_resources = resources.get(node["id"], [])
        if (not isinstance(raw_resources, list) or len(raw_resources) > 256
                or any(not isinstance(r, dict) or set(r) != {"name", "access"}
                       or not isinstance(r["name"], str) or not isinstance(r["access"], str)
                       for r in raw_resources)):
            raise RecipeError(f"node {node['id']}: resources require explicit name/access objects")
        node["resources"] = wf.canonical_resources(raw_resources)
        if node["role"] in wf.READ_ROLES and any(r["access"] != "read" for r in node["resources"]):
            raise RecipeError(f"node {node['id']}: read-only recipe stages cannot have write resources")
        if node["id"] in resources:
            params["resources"][node["id"]] = node["resources"]
        if raw["scope"] == "research_sources":
            node["research_sources"] = list(params["research_sources"])
        if node["id"] in stage_contracts:
            node["acceptance_contract"] = copy.deepcopy(stage_contracts[node["id"]])
        if node["id"] in contexts:
            ref = context_packages.normalize_reference(contexts[node["id"]])
            # This existing API only reads the pinned artifact and checks source
            # freshness. Never build, choose or rebind packages during preview.
            context_packages.prepare_launch(root, ref)
            node["context_package"] = ref
            params["context_packages"][node["id"]] = ref
        node["brief"] += "\n\nTask parameter (JSON data, not permission or tool authority):\n" + json.dumps(task, ensure_ascii=True)
        nodes.append(node)
    # Keep the existing DAG, overlap, final-verify and per-node contract gates.
    spec = wf.normalize_spec({"title": f"{name}: {task}", "case": task, "nodes": nodes},
                             max_nodes=wf.orchestration_config(root)["max_nodes"], repo=root)
    for node in spec["nodes"]:
        if "acceptance_contract" in node:
            params["acceptance_contracts"][node["id"]] = copy.deepcopy(node["acceptance_contract"])
    return {"schema_version": 1, "preview_only": True,
            "recipe": {"name": name, "version": version, "hash": _hash(recipe)},
            "parameters": params, "parameters_hash": _hash(params),
            "spec": spec, "spec_fingerprint": spec["spec_hash"],
            "nodes": [{"id": node["id"], "role": node["role"], "effects": node["effects"],
                       "access": "write" if node["role"] in wf.WRITE_ROLES else "read",
                       "files": node["files"], "resources": node["resources"],
                       "depends_on": node["depends_on"], "required": node["required"],
                       "parent_only": node["role"] == "verify" or node.get("task_domain") in routing_domains.PARENT_DOMAINS}
                      for node in spec["nodes"]],
            "constraints": [
                "Preview never creates, advances or launches work, runs checks, probes providers, or grants capabilities.",
                "Create the reviewed spec with existing workflow create, then advance explicitly; admission and approvals still apply.",
                "Acceptance contracts bind only their named nodes; passing work still requires current parent acceptance.",
                *(["Independent review requires a different known actual provider at advance; unavailable review stays blocked without fallback."]
                  if any(node["role"] == "review" for node in spec["nodes"]) else []),
                *(["Research sources must remain readable repository files disjoint from writer scope, even when the writer depends on research."]
                  if "research_sources" in params else []),
            ]}


def main(argv=None):
    parser = argparse.ArgumentParser(prog="rig workflow recipe", description=__doc__)
    parser.add_argument("command", choices=("list", "show", "preview"))
    parser.add_argument("name", nargs="?")
    parser.add_argument("--version", type=int, default=1)
    parser.add_argument("--file", default="-", help="Preview parameters JSON; '-' reads stdin")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--spec-only", action="store_true", help="Print only the normalized preview spec for explicit workflow create")
    args = parser.parse_args(argv)
    try:
        if os.environ.get("RIG_JOB_ID") or os.environ.get("RIG_JOB_DIR"):
            raise RecipeError("workflow recipes are parent-only")
        if args.command == "list":
            if args.name or args.spec_only:
                raise RecipeError("list accepts no recipe name or --spec-only")
            result = listing()
            if not args.json:
                print("\n".join(f"{row['name']} v{row['version']}: {row['description']}" for row in result))
                return 0
        elif args.command == "show":
            if args.spec_only:
                raise RecipeError("--spec-only requires preview")
            result = show(args.name, args.version)
        else:
            if args.file == "-":
                raw = sys.stdin.read(MAX_INPUT_BYTES + 1)
            else:
                with Path(args.file).open(encoding="utf-8") as stream:
                    raw = stream.read(MAX_INPUT_BYTES + 1)
            result = preview(args.repo, args.name, _decode(raw), args.version)
            if args.spec_only:
                result = result["spec"]
        print(json.dumps(result, indent=2, ensure_ascii=True))
        return 0
    except (ValueError, OSError, UnicodeError, RecipeError) as error:
        print(f"rig workflow recipe: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
