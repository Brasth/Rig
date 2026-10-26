# deepseek: DeepSeek Harness as a worker

Runs a Rig job with [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) (`dsh`). Rig's command here is
`rig dsh-worker` (`scripts/rig_dsh.py`), which turns the job's brief and MCP config into `dsh --profile headless --patch
JOB/dsh.patch.yml TASK` with telemetry off.

Install and enable:

```sh
npm install -g @deepseek-ai/dsh          # and export DEEPSEEK_API_KEY
rig dsh-worker install                   # writes ~/.config/rig/adapters/deepseek/adapter.json
# .rig/harness.toml
# [workers]
# deepseek = true
```

Launch with `worker = "deepseek"`; smart routing never picks an adapter worker. Limits: no resume, no model flag (the model
is `dsh`'s profile setting), host mode only for now. The MCP patch row shape and headless approval behaviour are not yet
checked against a real `dsh`: run `scripts/dsh-probe.sh` and see
`docs/journals/261026-deepseek-harness-worker-plan.md`.
