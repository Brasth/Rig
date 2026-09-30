<!-- rig:start -->
<!-- Generated from docs/agent-protocol.md; run scripts/generate_protocol.py. Do not edit. -->
This managed protocol applies only in an initialized, enabled Rig project. If `.rig/harness.toml` is missing or `[project] enabled=false`, use the host instructions instead; do not initialize or enable Rig implicitly. Existing legacy harnesses without `[project]` remain enabled. Global skill installation is not project or backend opt-in.

MUST use Rig only after that activation gate. Before any Rig action, read `.agents/skills/delegate-harness/SKILL.md` and the references it requires for that action. If a required file is missing or unreadable, stop and report the incomplete install; do not improvise or silently bypass it.

A live parent with parent Rig MCP owns orchestration, briefs, and acceptance; `RIG_JOB_ID` identifies a restricted child. Children first call `rig_job_inbox`, never spawn or message children, and never receive browser/computer-use tools. A child must read the child instructions in the delegation bootstrap before acting.

Orchestrate with MCP when available. Follow `rig_session` → `rig_pick` evidence; model/effort suggestions are not authority. No write before authenticated `rig_job_start` or `rig_job_launch`. Parent writes only when pick `parent_writes` is true. Never guess ownership tokens or bypass worker flags, permissions, file/resource isolation, or parent acceptance. Explicit cancellation must not trigger re-wait, re-pick, or queue drain. Execution success alone is unverified. Full mandatory action rules are in the delegation bootstrap and its topic references.

Generic computer-use requests do not select Rig. Check enabled project, parent Rig MCP, and backend opt-in/availability first; use host-native capabilities under their own instructions when Rig was not selected. Do not initialize, enable, install, unlock, or repair Rig implicitly. If explicitly requested Rig is blocked, ask before setup. Once selected, a denial is never a reason to switch tools.
<!-- rig:end -->
