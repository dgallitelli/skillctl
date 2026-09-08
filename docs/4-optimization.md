# Optimization

The LLM-driven optimizer is an authoring aid distributed separately as
`skillsops-optimize`. It is not part of the deterministic `skillctl`
governance or registry path.

## Install and run

```bash
pip install skillsops-optimize

skillsops-optimize ./my-skill --variants 3 --budget 10
skillsops-optimize history
skillsops-optimize diff <run-id>
```

The package depends on SkillsOps for the fail-closed deterministic evaluation:
80% security audit and 20% schema contract. LiteLLM calls are used only to
analyze weaknesses and generate candidate `SKILL.md` rewrites.

## Loop

Each optimization cycle:

1. Evaluates the current skill with the SkillsOps deterministic report.
2. Uses an LLM to identify weaknesses from the report.
3. Generates candidate instruction variants.
4. Re-evaluates every variant through the same report.
5. Promotes a variant only when it clears the configured improvement threshold
   and any required human approval.

The loop stops on its iteration cap, budget limit, or plateau threshold.
`--dry-run` records decisions without replacing the source `SKILL.md`.

## Key flags

| Flag | Default | Purpose |
|------|---------|---------|
| `--variants` | `3` | Candidate rewrites per cycle |
| `--threshold` | `0.05` | Minimum score improvement for promotion |
| `--max-iterations` | `50` | Hard cycle limit |
| `--plateau` | `3` | Stop after this many non-improving cycles |
| `--budget` | `10.0` | Maximum tracked LLM spend in USD |
| `--model` | configured default | LiteLLM model identifier |
| `--approve` | off | Require/record promotion approval |
| `--dry-run` | off | Do not replace the source instructions |

## Provenance

Runs are stored under `~/.skillctl/optimize/<run-id>/` with the original and
promoted instructions, cycle evaluations, analyses, variants, token costs, and
promotion decisions. This is optimization provenance, not registry compliance
evidence or authorization.

## Source and tests

The independent package lives at `packages/skillsops-optimize/`:

```text
skillctl_optimize/
  loop.py
  llm_client.py
  failure_analyzer.py
  variant_generator.py
  promotion_gate.py
  eval_runner.py
  budget.py
  provenance.py
```

Run its suite from the package directory:

```bash
pip install -e packages/skillsops-optimize
pytest packages/skillsops-optimize/tests -m "not integration"
```
