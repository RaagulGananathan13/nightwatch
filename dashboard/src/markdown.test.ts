import assert from "node:assert/strict";
import { test } from "node:test";

import { duration } from "./format.ts";
import { parseInline, parseMarkdown } from "./markdown.ts";
import { parseRoute } from "./router.ts";

const POSTMORTEM = `# INC-015: shoplite-5xx-rate

*A blameless postmortem written by NightWatch.*

| | |
|---|---|
| Service | shoplite |

## Action items

- [ ] Add a test for the code path that failed
- Nothing else
`;

test("postmortem markdown becomes headings, tables, lists and paragraphs", () => {
  const blocks = parseMarkdown(POSTMORTEM);
  assert.deepEqual(
    blocks.map((block) => block.kind),
    ["heading", "paragraph", "table", "heading", "list"],
  );
  const table = blocks[2];
  assert.ok(table?.kind === "table");
  assert.deepEqual(table.rows[0]?.map((cell) => cell[0]?.text), ["Service", "shoplite"]);
  const list = blocks[4];
  assert.ok(list?.kind === "list");
  assert.deepEqual(list.items.map((item) => item.checkbox), [true, false]);
});

test("inline code, bold and italic; anything else stays plain text", () => {
  assert.deepEqual(parseInline("a `b` **c** *d* <img src=x>"), [
    { kind: "text", text: "a " },
    { kind: "code", text: "b" },
    { kind: "text", text: " " },
    { kind: "bold", text: "c" },
    { kind: "text", text: " " },
    { kind: "italic", text: "d" },
    { kind: "text", text: " <img src=x>" },
  ]);
});

test("durations and routes", () => {
  assert.equal(duration(44), "44 s");
  assert.equal(duration(130), "2 min 10 s");
  assert.equal(duration(3 * 86400 + 4 * 3600), "3 d 4 h");
  assert.deepEqual(parseRoute("#/incidents/01JB"), ["incidents", "01JB"]);
  assert.deepEqual(parseRoute(""), []);
});
