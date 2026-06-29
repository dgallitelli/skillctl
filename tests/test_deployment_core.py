"""Unit tests for the deployment core: engine lifecycle, store, router."""

from __future__ import annotations

import pytest

from skillctl.deployment.engine import DeploymentEngine, DeploymentError
from skillctl.deployment.models import DeploymentState, DeploymentStrategy
from skillctl.deployment.router import TrafficRouter
from skillctl.deployment.store import DeploymentStore


@pytest.fixture
def engine_store():
    store = DeploymentStore(":memory:")
    store.initialize()
    yield DeploymentEngine(store), store
    store.close()


def _canary(engine, **kw):
    cfg = {"stages": [0.10, 0.50, 1.0]}
    cfg.update(kw.pop("config", {}))
    return engine.create(
        skill_name=kw.get("skill", "proj/x"),
        skill_namespace="org/acme",
        to_version="2.0.0",
        from_version="1.0.0",
        strategy=DeploymentStrategy.CANARY,
        config=cfg,
    )


class TestEngine:
    def test_immediate_completes(self, engine_store):
        engine, _ = engine_store
        dep = engine.create(
            skill_name="proj/i",
            skill_namespace="org/acme",
            to_version="2.0.0",
            strategy=DeploymentStrategy.IMMEDIATE,
        )
        assert dep.state == DeploymentState.COMPLETED
        assert dep.current_traffic_percent == 1.0

    def test_canary_stage_progression(self, engine_store):
        engine, _ = engine_store
        dep = _canary(engine)
        assert dep.current_traffic_percent == 0.10
        dep = engine.promote(dep.id)
        assert dep.current_traffic_percent == 0.50
        assert dep.state == DeploymentState.IN_PROGRESS
        dep = engine.promote(dep.id)
        assert dep.current_traffic_percent == 1.0
        assert dep.state == DeploymentState.COMPLETED

    def test_pause_resume(self, engine_store):
        engine, _ = engine_store
        dep = _canary(engine)
        assert engine.pause(dep.id).state == DeploymentState.PAUSED
        assert engine.resume(dep.id).state == DeploymentState.IN_PROGRESS

    def test_rollback(self, engine_store):
        engine, _ = engine_store
        dep = _canary(engine)
        rolled = engine.rollback(dep.id, reason="bad", rolled_back_by="alice")
        assert rolled.state == DeploymentState.ROLLED_BACK
        assert rolled.rollback_reason == "bad"

    def test_staged_progression(self, engine_store):
        engine, _ = engine_store
        dep = engine.create(
            skill_name="proj/s",
            skill_namespace="org/acme",
            to_version="2.0.0",
            from_version="1.0.0",
            strategy=DeploymentStrategy.STAGED,
            config={"stages": [{"name": "dev"}, {"name": "staging"}, {"name": "prod", "traffic_percent": 1.0}]},
        )
        assert dep.current_stage == 0
        dep = engine.promote(dep.id, approved_by="pub")
        assert dep.current_stage == 1 and dep.state == DeploymentState.IN_PROGRESS
        dep = engine.promote(dep.id, approved_by="admin")
        assert dep.current_stage == 2 and dep.state == DeploymentState.COMPLETED
        assert "pub" in dep.approved_by and "admin" in dep.approved_by

    def test_rbac_check_blocks_create(self, engine_store):
        _, store = engine_store
        engine = DeploymentEngine(store, rbac_check=lambda action, ns: False)
        with pytest.raises(DeploymentError):
            engine.create(
                skill_name="proj/x",
                skill_namespace="org/acme",
                to_version="2.0.0",
                strategy=DeploymentStrategy.CANARY,
                config={"stages": [1.0]},
            )

    def test_missing_deployment_raises(self, engine_store):
        engine, _ = engine_store
        with pytest.raises(DeploymentError):
            engine.promote("dep-nope")


class TestRouter:
    def test_no_deployment_returns_current(self, engine_store):
        _, store = engine_store
        router = TrafficRouter(store)
        assert router.resolve_version("unknown", "org/acme", "u1", "1.0.0") == "1.0.0"

    def test_full_traffic_routes_all_to_new(self, engine_store):
        engine, store = engine_store
        engine.create(
            skill_name="proj/f",
            skill_namespace="org/acme",
            to_version="2.0.0",
            from_version="1.0.0",
            strategy=DeploymentStrategy.CANARY,
            config={"stages": [1.0]},
        )
        router = TrafficRouter(store)
        assert router.resolve_version("proj/f", "org/acme", "anyone", "1.0.0") == "2.0.0"

    def test_hash_route_is_deterministic(self):
        assert TrafficRouter._hash_route("user-1", 0.5) == TrafficRouter._hash_route("user-1", 0.5)

    def test_hash_route_bounds(self):
        assert TrafficRouter._hash_route("anything", 1.0) is True
        assert TrafficRouter._hash_route("anything", 0.0) is False
