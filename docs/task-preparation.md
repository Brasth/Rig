# Parent-assisted task preparation

The parent reads the repository and selects concrete files, exact checks, constraints and references. `rig_task_prepare` assembles those selections into a worker brief, acceptance contract, context preview and optional recipe preview. It does not discover files, execute checks, build a package, pick a worker or launch work. `ready` means required draft inputs are present; it does not grant admission or acceptance.

CLI equivalent:

```sh
rig task prepare --file task.json --json
```

Example `task.json`:

```json
{
  "task": "Fix the save handler",
  "files": ["src/save.py", "tests/test_save.py"],
  "constraints": ["Preserve the public save API"],
  "exclusions": ["Do not change storage configuration"],
  "checks": [{"id": "save-tests", "argv": ["python3", "-m", "unittest", "discover", "-s", "tests", "-p", "test_save.py"], "cwd": "."}],
  "references": [{"path": "docs/storage.md", "reason": "Existing save contract", "provenance": "Parent selected repository documentation"}],
  "recipe": "bugfix"
}
```

MCP takes `repo` and `selection` containing the same JSON object. Omit `recipe` for a single-job draft. Supported recipes: `bugfix`, `research-implement`, `ui-validation`. Research requires existing readable `research_sources` disjoint from writer files. UI remains parent-only where its recipe requires it.

Inspect `unresolved`, the brief, scope, contract and previews. Missing files or acceptance criteria produce an incomplete draft. Manual-only tasks may supply `manual_criteria`; each becomes a parent review assertion requiring a `parent-review` evidence artifact before acceptance. No automatic manual approval occurs.

If references are selected, explicitly call `rig_context_build` with the returned `context_selection`. Bind its returned reference to the job or selected recipe nodes, preview the recipe again, then explicitly create/advance or pick/start/launch under the existing ownership rules. Preparation does not enable an uninitialized or disabled project.

Inputs are bounded to 32 KiB. Existing context, file-scope and acceptance limits apply; constraints and exclusions share the context preview's 32-statement budget. Literal repository-relative paths are required; private paths and scope-expanding aliases are refused. Reference content uses existing context screening, which cannot guarantee absence of secrets. Checks are exact argv/cwd data and remain inert until the parent deliberately runs them.
