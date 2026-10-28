# 0003. Groq by default, evidence-first agent, strict LLM call budget

- Status: accepted
- Date: 2026-10-28
- Spec sections: 6, 10, 11

## Context

The spec makes Gemini Flash's free tier the default and allows up to 8 tool-calling steps
(about 9 LLM calls and about 30,000 tokens) per incident. In October 2026:

- Gemini's free tier for Flash models is reported at about **20 requests per day** per project.
  Google may also use free-tier prompts and responses to improve its products.
- Groq's free tier gives each model limits like 30 requests per minute, 1,000 requests per day,
  6,000 to 12,000 tokens per minute and 100,000 to 500,000 tokens per day.

Both providers change these limits often, so check the consoles for current numbers.

At 9 calls per incident, Gemini's free tier would handle about two incidents a day.

## Decision

1. **Evidence first, LLM second.** Code gathers the cheap, high-signal evidence
   deterministically: alarm context, metric summaries, recent changes, similar past incidents
   and an aggregated error-count query. The LLM then gets one compact context (about 4,000 to
   6,000 tokens). It either answers or requests a few more read-only tool calls. A typical
   incident takes 1 to 3 LLM calls.
2. **Providers:** Groq is the default, with the model set in config. Gemini is the fallback,
   Ollama runs locally and keeps all data on the machine, and `FakeProvider` serves tests. All
   are called over plain HTTP behind one interface.
3. **Budgets:** each provider has a token-bucket rate limiter, plus daily request and token
   counters stored in DynamoDB. When the budget runs out, NightWatch posts a **rule-based
   diagnosis**: the evidence summary without an LLM, clearly labelled. Running out never means
   no diagnosis at all.
4. **Privacy:** PII is redacted before every external call. The docs warn against sending real
   customer data to free tiers.

## Consequences

- NightWatch fits free quotas at tens of incidents per day, and diagnoses arrive faster.
- Free models may reason less well than large paid ones. The Phase 5 eval harness measures this
  and compares models and prompt versions with real numbers.
