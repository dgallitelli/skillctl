# Progressive Deployment (Milestone 3, Part B)

Roll out skill versions gradually to limit blast radius, with health-driven
automatic rollback. Deployments are tracked in SQLite and every transition is
audited.

## Strategies

| Strategy | Behaviour |
|----------|-----------|
| `canary` | Ramp traffic through stages (e.g. 1% → 5% → 25% → 50% → 100%) |
| `blue-green` | Old (blue) serves while new (green) warms; promote switches atomically |
| `staged` | Move through named stages (dev → staging → prod) with approval gates |
| `immediate` | Legacy: 100% at once |

## Traffic routing

`TrafficRouter.resolve_version(skill, namespace, actor_id, current_version)`
returns the version to serve:

- **Consistent hashing** of `actor_id` (SHA-256) against the current traffic
  percentage — the same actor always gets the same version during a rollout, and
  the split is approximately correct across many actors. No state required.
- A `COMPLETED` deployment serves the new version to everyone; a `ROLLED_BACK`
  one serves the previous version; blue-green serves blue until the switch.

## Health & rollback

`HealthMonitor.evaluate_health` computes error rate, policy-denial rate, success
rate, and p99 latency from recorded invocation metrics over the evaluation
window, comparing against a `HealthThreshold`. Health checks are **caller-driven**
(`engine.check_health` / `engine.evaluate_and_maybe_rollback`) for deterministic,
testable behaviour. With `auto_rollback`, a breach reverts traffic to the
previous version and records the reason in the audit chain. A deployment below
`min_sample_size` is reported healthy (not enough data to judge).

## CLI

```bash
skillctl deploy canary my-org/skill --version 2.0.0 --namespace org/acme \
    --from 1.0.0 --stages "1,5,25,50,100" --auto-rollback
skillctl deploy blue-green my-org/skill --version 2.0.0 --namespace org/acme --from 1.0.0
skillctl deploy staged my-org/skill --version 2.0.0 --namespace org/acme \
    --stages "dev,staging,prod" --require-approval staging,prod
skillctl deploy status [--skill my-org/skill]
skillctl deploy promote <deployment-id>
skillctl deploy rollback <deployment-id> --reason "elevated error rate"
skillctl deploy history [--skill my-org/skill]
```

Deployments are recorded in `~/.skillctl/deployments.db`. The `deployment`
evidence type lets a compliance report demonstrate rollback capability
(EU AI Act Art 14-1-b, intervention mechanism).
