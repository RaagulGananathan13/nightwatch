# 0014. Incident memory, postmortems and ChatOps

- Status: accepted
- Date: 2026-10-30
- Spec sections: F6, F8, F9 (Phase 6)

## Context

The spec asks for three things in this phase:
- **Memory.** NightWatch should recognise a failure it has seen before.
- **Postmortems.** Every incident should get a blameless postmortem.
- **ChatOps.** Engineers should be able to ask questions in chat.

The spec suggests embeddings for memory. The only LLM provider configured here (Groq) has no
embeddings API, and Gemini's free embeddings would need a key that this project chose not to
add. Everything must stay at $0.

## Decision

1. **A lexical embedding, computed in code.** Each resolved incident becomes a sparse vector of
   hashed words and word pairs. It covers the incident's title, alarms, diagnosis, cause and
   fix. A search weights the vectors by rarity (IDF) and compares them with cosine similarity.
   Incidents are mostly told apart by exact identifiers, such as `KeyError`, `error_rate`,
   `TABLE_NAME`, an alarm name or a runbook, and matching words captures those well. It costs
   nothing, gives the same answer every time, and is easy to explain. The vectors sit in
   DynamoDB next to the incident (`MEM#<service>#<incident>`), so a neural embedding can replace
   them later without changing the search interface.
2. **Search with what the evidence shows.** `search_similar_incidents` now runs last in the
   evidence plan. Its query is the incident plus the "what changed" summary and the error types
   from the logs, so a repeat of an earlier failure finds that failure first. Matches need a
   similarity of at least 0.2. Between near-identical matches, a small bonus favours the most
   recent.
3. **Shown by code; the model only adds to it.** The matches go on the Discord card ("Looks
   like: INC-015, 25m ago: ...") straight from the evidence. Prompt `investigate_v2` also asks
   the model to name an earlier incident with the same cause, but only as a hint. The current
   evidence must still show the cause.
4. **Postmortems from records.** A postmortem Lambda runs when an incident resolves, and again
   after a fix finishes. It writes markdown to a private, versioned, SSE-S3 bucket.
   - **Facts:** the timeline, impact, root cause and resolution come from the audit log, the
     evidence, the checked diagnosis and the action record.
   - **Lessons:** what went well, what didn't and the action items come from one LLM call, or
     from fixed rules when no LLM is available. The postmortem says which wrote them.
   - **Memory:** the same run writes the incident's memory and adds a "Postmortem" line to its
     Discord card.
5. **Slash commands on the existing endpoint.** `/nw status`, `/nw ack` and `/nw ask` arrive at
   the same Ed25519-checked function URL as the approval buttons.
   - Only a service's approvers may `ack` its incidents or `ask` about it, because asking spends
     the LLM budget.
   - `ask` replies "thinking..." at once, which is Discord's 3-second rule. It then starts a
     Lambda that runs the same read-only investigation with no incident, from the service's
     main metrics, and edits the reply with the answer.
   - The question reaches the model inside `<question>` tags, as data.

## Consequences

- A repeated failure is recognised from its second occurrence, with no model, API key or cost.
- Word matching misses paraphrases: "requests took too long" won't match "timeout". The
  diagnosis still works without a match, because memory is a hint, never the evidence.
- Each search reads every memory of the service. That is fine for hundreds of incidents; a
  bigger history would need a vector index.
- S3 stores a few kilobytes per incident: fractions of a cent a month, inside the budget alert.
  Incidents that resolved before this phase get their postmortem and memory from
  `make backfill-memory`.
- `/nw ask` uses 1 to 3 LLM calls per question, counted against the same daily cap as
  incidents.
