// A small parser for the markdown NightWatch writes (postmortems): headings, tables, lists,
// checklists and paragraphs, with `code`, **bold** and *italic* inline. The page renders the
// result as React elements, never as HTML, so text from an incident can't inject markup.

export type Inline = { kind: "text" | "code" | "bold" | "italic"; text: string };

export type Block =
  | { kind: "heading"; level: number; text: Inline[] }
  | { kind: "paragraph"; text: Inline[] }
  | { kind: "list"; items: { checkbox: boolean; text: Inline[] }[] }
  | { kind: "table"; header: Inline[][]; rows: Inline[][][] };

export function parseInline(text: string): Inline[] {
  const parts: Inline[] = [];
  const pattern = /`([^`]+)`|\*\*([^*]+)\*\*|\*([^*]+)\*/g;
  let last = 0;
  for (const match of text.matchAll(pattern)) {
    const at = match.index ?? 0;
    if (at > last) parts.push({ kind: "text", text: text.slice(last, at) });
    if (match[1] !== undefined) parts.push({ kind: "code", text: match[1] });
    else if (match[2] !== undefined) parts.push({ kind: "bold", text: match[2] });
    else if (match[3] !== undefined) parts.push({ kind: "italic", text: match[3] });
    last = at + match[0].length;
  }
  if (last < text.length) parts.push({ kind: "text", text: text.slice(last) });
  return parts;
}

function cells(line: string): string[] {
  return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((cell) => cell.trim());
}

export function parseMarkdown(source: string): Block[] {
  const blocks: Block[] = [];
  const lines = source.split(/\r?\n/);
  let i = 0;
  while (i < lines.length) {
    const line = lines[i] ?? "";
    const heading = /^(#{1,4})\s+(.*)$/.exec(line);
    if (!line.trim()) {
      i += 1;
    } else if (heading) {
      blocks.push({ kind: "heading", level: heading[1]?.length ?? 1, text: parseInline(heading[2] ?? "") });
      i += 1;
    } else if (line.trimStart().startsWith("|")) {
      const rows: string[][] = [];
      while (i < lines.length && (lines[i] ?? "").trimStart().startsWith("|")) {
        rows.push(cells(lines[i] ?? ""));
        i += 1;
      }
      const [head = [], divider, ...body] = rows;
      const isDivider = divider?.every((cell) => /^:?-{3,}:?$/.test(cell)) ?? false;
      blocks.push({
        kind: "table",
        header: head.map(parseInline),
        rows: (isDivider ? body : rows.slice(1)).map((row) => row.map(parseInline)),
      });
    } else if (/^\s*[-*]\s+/.test(line)) {
      const items: { checkbox: boolean; text: Inline[] }[] = [];
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i] ?? "")) {
        const item = (lines[i] ?? "").replace(/^\s*[-*]\s+/, "");
        const checkbox = /^\[[ xX]\]\s+/.test(item);
        items.push({ checkbox, text: parseInline(item.replace(/^\[[ xX]\]\s+/, "")) });
        i += 1;
      }
      blocks.push({ kind: "list", items });
    } else {
      const paragraph: string[] = [];
      while (i < lines.length && (lines[i] ?? "").trim() && !/^(#|\||\s*[-*]\s)/.test(lines[i] ?? "")) {
        paragraph.push((lines[i] ?? "").trim());
        i += 1;
      }
      blocks.push({ kind: "paragraph", text: parseInline(paragraph.join(" ")) });
    }
  }
  return blocks;
}
