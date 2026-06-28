"""Unit tests for the multi-system foundation layers (no GUI, no network).

Run from the project root with the venv Python:

    .\\venv\\Scripts\\python.exe -m unittest tests.test_foundation -v

These cover the seams added in the cleanup/prep pass: routing, the identity
scope map, the gateway (namespacing / allow-list / write-gate / untrusted
framing / audit), the audit record shape, and the adapter's vendor translation.
External calls (Entra, MCP servers) are monkeypatched, so the suite is offline.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

# Make the project root importable when run as `python tests/test_foundation.py`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import audit
import gateway
import identity
import llm_adapter
import mcp_client
import routing
from llm_adapter import ToolCall, ToolSpec


class RoutingTests(unittest.TestCase):
    def test_finance_query_routes_to_finance(self):
        self.assertEqual(routing.route_systems("show my expenses"), ["finance"])
        self.assertEqual(routing.route_systems("what's pending approval?"), ["finance"])

    def test_hr_policy_query_routes_to_no_system(self):
        self.assertEqual(routing.route_systems("what is the leave policy"), [])
        self.assertEqual(routing.route_systems("how many casual leaves do I get"), [])

    def test_empty_text(self):
        self.assertEqual(routing.route_systems(""), [])


class IdentityTests(unittest.TestCase):
    def test_scope_for_unknown_system_is_empty(self):
        self.assertEqual(identity.scope_for("nope"), "")

    def test_mint_without_scope_raises(self):
        with self.assertRaises(identity.IdentityError):
            identity.mint_downstream_token("nope")


class GatewayTests(unittest.TestCase):
    def setUp(self):
        # Pretend finance is fully configured, and stub the transport + identity.
        self._patches = []
        self._audit_records: list[dict] = []

        def patch(obj, name, value):
            original = getattr(obj, name)
            setattr(obj, name, value)
            self._patches.append((obj, name, original))

        patch(identity, "is_configured", lambda s: s == "finance")
        patch(mcp_client, "is_configured", lambda s: s == "finance")
        patch(identity, "mint_downstream_token",
              lambda s, **kw: "fake-token")
        patch(mcp_client, "list_tools", lambda s, t: [
            {"name": "get_expenses", "description": "list expenses",
             "parameters": {"type": "object", "properties": {}}},
            {"name": "create_expense", "description": "make one",
             "parameters": {"type": "object", "properties": {}}},
        ])
        patch(mcp_client, "call_tool",
              lambda s, t, name, args: f"result of {name}({args})")
        # Capture audit records instead of writing to disk.
        patch(gateway, "log_tool_call",
              lambda **rec: self._audit_records.append(rec))
        self.patch = patch

    def tearDown(self):
        for obj, name, original in reversed(self._patches):
            setattr(obj, name, original)

    def test_list_tools_namespaces_and_drops_writes(self):
        specs = gateway.list_tools(["finance"])
        names = [s.name for s in specs]
        self.assertIn("finance.get_expenses", names)
        # create_expense is a write → not exposed (not in the allow-list).
        self.assertNotIn("finance.create_expense", names)

    def test_list_tools_ignores_unavailable_system(self):
        self.assertEqual(gateway.list_tools(["hr"]), [])

    def test_call_tool_ok_is_framed_and_audited(self):
        out = gateway.call_tool("finance.get_expenses", {"limit": 5})
        self.assertIn("<<<TOOL_OUTPUT", out)
        self.assertIn("UNTRUSTED DATA", out)
        self.assertIn("result of get_expenses", out)
        self.assertEqual(self._audit_records[-1]["status"], "ok")
        self.assertEqual(self._audit_records[-1]["system"], "finance")
        self.assertEqual(self._audit_records[-1]["tool"], "get_expenses")

    def test_write_tool_is_blocked(self):
        out = gateway.call_tool("finance.create_expense", {})
        self.assertIn("not permitted", out)
        self.assertEqual(self._audit_records[-1]["status"], "blocked")

    def test_unnamespaced_name_is_blocked(self):
        out = gateway.call_tool("get_expenses", {})
        self.assertIn("ERROR", out)
        self.assertEqual(self._audit_records[-1]["status"], "blocked")

    def test_unavailable_system_call_blocked(self):
        out = gateway.call_tool("hr.get_leave", {})
        self.assertIn("not available", out)
        self.assertEqual(self._audit_records[-1]["status"], "blocked")


class AuditTests(unittest.TestCase):
    def test_record_is_written_as_jsonl(self):
        with tempfile.TemporaryDirectory() as d:
            log_path = Path(d) / "audit.jsonl"
            orig_dir, orig_path = audit._LOG_DIR, audit._LOG_PATH
            audit._LOG_DIR, audit._LOG_PATH = Path(d), log_path
            try:
                audit.log_tool_call(
                    user=None, system="finance", tool="get_expenses",
                    args={"limit": 5}, status="ok", result="[]",
                )
            finally:
                audit._LOG_DIR, audit._LOG_PATH = orig_dir, orig_path
            line = log_path.read_text(encoding="utf-8").strip()
            rec = json.loads(line)
            self.assertEqual(rec["system"], "finance")
            self.assertEqual(rec["tool"], "get_expenses")
            self.assertEqual(rec["status"], "ok")
            self.assertIn("ts", rec)


class AdapterTests(unittest.TestCase):
    def test_tool_spec_to_openai_schema(self):
        adapter = llm_adapter.OpenAIAdapter(provider="azure")
        spec = ToolSpec(name="finance.get_expenses", description="d",
                        parameters={"type": "object", "properties": {}})
        tools = adapter._to_openai_tools([spec])
        self.assertEqual(tools[0]["type"], "function")
        self.assertEqual(tools[0]["function"]["name"], "finance.get_expenses")

    def test_tool_call_message_roundtrip(self):
        adapter = llm_adapter.OpenAIAdapter(provider="azure")
        call = ToolCall(id="c1", name="finance.get_expenses", arguments={"limit": 5})
        amsg = adapter.assistant_tool_call_message("", [call])
        self.assertEqual(amsg["role"], "assistant")
        self.assertEqual(amsg["tool_calls"][0]["id"], "c1")
        self.assertEqual(amsg["tool_calls"][0]["function"]["name"], "finance.get_expenses")
        tmsg = adapter.tool_result_message(call, "framed result")
        self.assertEqual(tmsg, {"role": "tool", "tool_call_id": "c1", "content": "framed result"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
