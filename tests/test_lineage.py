"""Unit tests for Milestone 4 data lineage + forensics."""

from __future__ import annotations

import pytest

from skillctl.forensics.query import ForensicQuery
from skillctl.lineage.store import LineageStore


@pytest.fixture
def store():
    s = LineageStore(":memory:")
    s.initialize()
    yield s
    s.close()


class TestLineage:
    def test_record_and_provenance(self, store):
        # skill reads source-a, writes derived-b
        store.record_access(
            invocation_id="inv1",
            skill="proj/etl",
            actor="alice",
            reads=["data:source-a"],
            writes=["data:derived-b"],
            ts=100,
        )
        # another skill reads derived-b, writes report-c
        store.record_access(
            invocation_id="inv2",
            skill="proj/report",
            actor="bob",
            reads=["data:derived-b"],
            writes=["data:report-c"],
            ts=200,
        )

        prov_b = store.trace_provenance("data:derived-b")
        assert prov_b == {"data:source-a"}

        prov_c = store.trace_provenance("data:report-c")
        assert prov_c == {"data:derived-b", "data:source-a"}  # transitive

    def test_downstream_consumers(self, store):
        store.record_access(invocation_id="inv1", skill="proj/etl", reads=["data:a"], writes=["data:b"], ts=10)
        downstream = store.downstream_consumers("data:a")
        assert any(d["data_ref"] == "data:b" for d in downstream)

    def test_who_accessed(self, store):
        store.record_access(invocation_id="i1", skill="s", actor="alice", reads=["data:cust-123"], ts=10)
        store.record_access(invocation_id="i2", skill="s", actor="bob", reads=["data:cust-123"], ts=20)
        assert store.who_accessed("data:cust-123") == ["alice", "bob"]
        assert store.who_accessed("data:cust-123", since=15) == ["bob"]

    def test_query_by_label(self, store):
        store.record_access(invocation_id="i1", skill="s", reads=[{"ref": "data:x", "label": "pii"}], ts=10)
        store.record_access(invocation_id="i2", skill="s", reads=[{"ref": "data:y", "label": "public"}], ts=20)
        pii = store.query(label="pii")
        assert len(pii) == 1 and pii[0]["data_ref"] == "data:x"


class TestForensics:
    def test_invocations_accessing_label(self, store):
        for i in range(5):
            store.record_access(
                invocation_id=f"pii-{i}", skill="proj/risky", reads=[{"ref": f"data:c{i}", "label": "pii"}], ts=100 + i
            )
        for i in range(3):
            store.record_access(
                invocation_id=f"clean-{i}",
                skill="proj/risky",
                reads=[{"ref": f"data:p{i}", "label": "public"}],
                ts=200 + i,
            )

        q = ForensicQuery(store)
        pii_invs = q.invocations_accessing(skill="proj/risky", label="pii")
        assert len(pii_invs) == 5

        windowed = q.invocations_accessing(skill="proj/risky", label="pii", since=102, until=104)
        assert len(windowed) == 3  # ts 102,103,104

    def test_provenance_query(self, store):
        store.record_access(invocation_id="i1", skill="s", reads=["data:a"], writes=["data:b"], ts=1)
        result = ForensicQuery(store).provenance("data:b")
        assert result["sources"] == ["data:a"]
