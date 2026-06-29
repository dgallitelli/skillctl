# skillsops-optimize

Authoring-time skill optimizer, **extracted from the SkillsOps governance core**
in Milestone 0.

## Why it lives here

The optimizer iteratively rewrites a skill with an LLM to improve its evaluation
score. That is an **authoring aid**, not a **governance control**: governance
gatekeeps (validate, audit, version, publish) and must be deterministic, while
optimization explores and is inherently non-deterministic.

Keeping the two in one package conflated those concerns and pulled a heavy
`litellm` dependency into the governance CLI. The optimizer now ships as a
separate, optional tool that *depends on* the governance core (`skillsops`) but
is **not importable from it** (`import skillctl.optimize` no longer resolves).

## Install

```bash
pip install skillsops-optimize   # pulls in skillsops + litellm
```

## Use

```bash
skillsops-optimize <skill-path> --variants 3 --budget 10
skillsops-optimize history
skillsops-optimize diff <run-id>
```

## Status

This package is a direct relocation of the former `skillctl.optimize` module.
It targets the governance core's evaluator, which after Milestone 0 produces a
deterministic `80% security audit + 20% schema contract` score. Treat the
optimizer's loop semantics as authoring-experimental.
