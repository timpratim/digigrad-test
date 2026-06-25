"""Memory store + caller-ID identity tests, against a throwaway SQLite DB."""

import asyncio
import importlib

import pytest


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """Point the DB at a temp file and reload the modules that bind DB_PATH."""
    monkeypatch.setenv("GRADPHONE_DB", str(tmp_path / "test.db"))
    import gradphone.tenants as tenants
    import gradphone.memory as memory
    importlib.reload(tenants)
    importlib.reload(memory)
    asyncio.run(tenants.init_db())
    return tenants, memory


def test_phone_normalization_and_lookup(db):
    tenants, _ = db

    async def go():
        tid = await tenants.register_tenant(111, "alice")
        await tenants.set_tenant_phone(tid, "+1 (415) 555-0000")
        # Differently-formatted same number resolves to the same tenant.
        t = await tenants.get_tenant_by_phone("14155550000")
        assert t and t["id"] == tid
        assert await tenants.get_tenant_by_phone("+19999999999") is None
        assert await tenants.get_tenant_by_phone("") is None

    asyncio.run(go())


def test_memory_add_dedup_search_digest(db):
    tenants, memory = db

    async def go():
        tid = await tenants.register_tenant(222, "bob")
        assert await memory.add_memory(tid, "Dentist is Dr. Lemoine", source="remember_tool")
        assert await memory.add_memory(tid, "Prefers morning calls")
        # Exact duplicate is rejected.
        assert not await memory.add_memory(tid, "Prefers morning calls")
        assert await memory.search_memories(tid, "dentist") == ["Dentist is Dr. Lemoine"]
        digest = await memory.render_digest(tid)
        assert "Dr. Lemoine" in digest and digest.startswith("- ")

    asyncio.run(go())


def test_digest_empty_for_unknown_tenant(db):
    _, memory = db
    assert asyncio.run(memory.render_digest(999)) == ""


def test_extract_facts_no_endpoint(db, monkeypatch):
    _, memory = db
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    assert asyncio.run(memory.extract_facts([("caller", "hi there")])) == []


def test_parse_fact_list_tolerates_fences_and_prose(db):
    _, memory = db
    assert memory._parse_fact_list('```json\n["a", "b"]\n```') == ["a", "b"]
    assert memory._parse_fact_list('Here: ["likes tea"] ok') == ["likes tea"]
    assert memory._parse_fact_list("no json here") == []
