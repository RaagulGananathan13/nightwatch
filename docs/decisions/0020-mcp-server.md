# 0020. The MCP server: NightWatch's evidence in your own AI assistant

- Status: accepted
- Date: 2026-10-30
- Spec sections: F15 (Phase 8)

## Context

Engineers already ask their own AI assistants (Claude Code, Claude Desktop) about their
systems. Those assistants know nothing about production unless something tells them, and
what they are told should be the evidence NightWatch collected, not guesses. The Model Context
Protocol (MCP) is the standard way to give an assistant tools. The spec requires the same IAM
permissions and audit logging as NightWatch itself.

## Decision

1. **A local server over stdio, not a hosted one.** `python -m nightwatch.mcp_server` runs on
   the engineer's machine; their MCP client starts it. A hosted MCP endpoint would need its
   own login (OAuth) and a public URL; stdio needs neither and costs nothing.
2. **The protocol is written out, not imported.** A tools-only server needs four JSON-RPC
   methods: `initialize`, `ping`, `tools/list` and `tools/call`. They take about a hundred
   lines (`protocol.py`). That avoids a framework and its dependencies, and the whole surface
   can be read in a minute.
   - The server answers in any of the protocol versions 2024-11-05 to 2025-11-25.
   - A tool that fails returns an error result instead of ending the session.
   - Only protocol messages go to stdout; logs go to stderr.
3. **Two kinds of tools, all read-only.**
   - Incident data:
     - `whats_going_on`: open incidents with their checked diagnoses, plus today's forecast
       warnings;
     - `list_incidents`;
     - `get_incident`: every hypothesis, with the evidence it cites and whether the guard
       verified it;
     - `get_evidence`: the exact query and its result.
   - The investigator's own seven tools, through the same registry and argument checks. They
     run against a service now, or around an incident's time.

   Every tool is marked `readOnlyHint`.
4. **The same IAM reach as the investigator.** The server starts with the engineer's AWS
   profile and immediately switches to the `nightwatch-mcp` role.
   - That role and the investigator share one policy document (`read_only_tools`), so they
     can't drift apart.
   - It gets no LLM keys, since the assistant brings its own model.
   - Its credentials are renewed before the one-hour session ends.
   - Only the IAM users named in `mcp_users` may switch to it: today, the deployer user.
5. **The same audit log and redaction.** Every call writes an `mcp_tool_called` audit record
   under `mcp:<caller>`. Answers pass through the same redaction as evidence bound for an
   external LLM, because the assistant on the other end is one. Log lines are wrapped and
   labelled as data, so text in a log can't pose as an instruction.

## Consequences

- Nothing new runs in AWS: one IAM role. The server runs only while a client uses it.
- The role can add items to NightWatch's own table, like the investigator, because audit
  records need it. IAM can't limit writes to audit items alone, since they share the
  incidents' partition key.
- Each engineer who should use it needs their IAM identity in `mcp_users` and a NightWatch
  checkout to run the server from.
- `make mcp-check` tests the setup (role switch, table reads, audit write) without a client.
