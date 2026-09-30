# Generated agent guidance

`docs/agent-protocol.md` is the maintained source for Rig's managed agent
protocol. Its named sections generate:

- the managed block in `AGENTS.md` and `templates/agents-protocol.md`
- `skills/delegate-harness/SKILL.md`
- `skills/delegate-harness/references/*.md`

Edit the source, then run:

```sh
python3 scripts/generate_protocol.py
python3 scripts/generate_protocol.py --check
python3 tests/run_tests.py
```

The generator is deterministic, standard-library-only and offline. `--check`
fails without writing if an output is missing or differs. It refuses duplicate,
unknown, out-of-order, nested or incomplete section markers and ambiguous
managed AGENTS markers. It preserves bytes outside the repository's existing
managed AGENTS block. Generated notices identify the source; do not independently
edit generated files.

`rig init` reads the generated template rather than carrying another protocol
literal in its Bash implementation. It keeps its existing managed-block upsert,
project-disable and optional-backend consent behavior. Missing templates or
failed upserts fail closed rather than overwriting user instructions. Existing
skill-copy paths install every generated topic reference, including the global
skill links and project-local copies. Generation is a maintainer operation in a
source checkout, not a requirement to initialize an end-user project.

## Read contract

AGENTS first applies the initialized/enabled-project gate, distinguishes parent
from child, and requires the delegation bootstrap before any Rig action. Missing
required guidance is a blocker. The delegation bootstrap retains hard safety
summaries and explicitly requires every applicable reference before the named
action. References are not optional background. Child-only authority always
wins over parent examples contained in a reference.

The discovery surfaces are short, while detailed lifecycle, provider and
permission rules remain available and mandatory when they apply. This changes
where the same rules are loaded, not the ownership or acceptance protocol.

## Migration and invariant map

The previous delegation skill's complete named sections are preserved in these
topics. AGENTS-only precision is retained too: BrowserSkill's real session CLI,
queue acknowledgement monotonicity, native reviewer provenance, request-scoped
check progress, native edit-capable-agent vs read-only explorer selection, JSON
file-list fidelity, and rollout preservation requirements.

| Previous source section / contract | Generated section |
| --- | --- |
| AGENTS activation; Hard gate | activation, orchestration |
| MCP first; Parent vs worker (parent duties); PR33 task-aware diagnostics | orchestration |
| Task-domain routing; Effective workers; Route | routing |
| Stage-gated parallel; Ownership and native completion; Parent verification and close | ownership |
| Fail classes; wait transport/ASK/Stop contract | waiting |
| Adaptive workflows | workflows |
| Queue drain | queue |
| Parent vs worker (backend selection, opt-in, permissions, grants, freshness, isolation) | computer-use |
| Call another CLI | launching |
| Child commands | providers |
| After a run; rollout/rollback | maintenance |

`tests/fixtures/protocol-migration.json` accounts for all 96 original delegation
skill paragraphs using literal fragment coverage, with explicitly recorded
section splits and the expanded BrowserSkill/Stop details. This is frozen
migration evidence, not another maintained policy source.

`tests/fixtures/protocol-invariants.json` maps 23 safety/behavior families to
source sections and required phrases. Tests verify those rules in both the
source and actual generated outputs. Existing protocol tests now read the
mandatory reference closure instead of demanding duplicate long prose in every
bootstrap. Assertions for cancellation, handshake, routing, backend isolation,
workflow acceptance and real MCP tool names remain. New tests independently
check that bootstrap gates and required-reference links cannot disappear, that
all referenced tools exist, and that init/setup copy complete usable bundles.
Installation tests use temporary projects and homes only.

## UTF-8 byte measurements

Before this migration, at main `03db845`, the readily loaded files were:

| Surface | Before | After |
| --- | ---: | ---: |
| AGENTS managed protocol | 26,215 | 2,045 |
| Delegation SKILL.md | 45,660 | 5,920 |
| Both discovery files | 71,875 | 7,965 |
| Embedded bin/rig protocol literal | 26,214 | 0 |

The ten generated topic references total 49,856 bytes. A parent session/pick
reads AGENTS + delegation bootstrap + orchestration + routing: 28,728 bytes.
A write additionally requires ownership and launching (40,109 bytes total),
and waiting additionally requires waiting/recovery (42,601 bytes total).
Other operations load their applicable mandatory topics as listed in the
bootstrap. These are explicit UTF-8 file-byte sums, not tokenizer measurements,
context-cache behavior, runtime latency, savings or performance claims. Reading
all references plus both discovery files totals 57,821 bytes.
