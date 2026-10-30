import { Fragment } from "react";

import { type Inline, parseMarkdown } from "./markdown.ts";

function Text({ parts }: { parts: Inline[] }) {
  return (
    <>
      {parts.map((part, index) => {
        if (part.kind === "code") return <code key={index}>{part.text}</code>;
        if (part.kind === "bold") return <strong key={index}>{part.text}</strong>;
        if (part.kind === "italic") return <em key={index}>{part.text}</em>;
        return <Fragment key={index}>{part.text}</Fragment>;
      })}
    </>
  );
}

/** Renders NightWatch's markdown as React elements: no HTML is ever injected. */
export function Markdown({ source }: { source: string }) {
  return (
    <div className="markdown">
      {parseMarkdown(source).map((block, index) => {
        if (block.kind === "heading") {
          const Tag = block.level <= 1 ? "h2" : block.level === 2 ? "h3" : "h4";
          return (
            <Tag key={index}>
              <Text parts={block.text} />
            </Tag>
          );
        }
        if (block.kind === "list") {
          return (
            <ul key={index} className="plain">
              {block.items.map((item, at) => (
                <li key={at}>
                  {item.checkbox ? <span className="box">☐ </span> : "• "}
                  <Text parts={item.text} />
                </li>
              ))}
            </ul>
          );
        }
        if (block.kind === "table") {
          return (
            <table key={index}>
              {block.header.some((cell) => cell.length) && (
                <thead>
                  <tr>
                    {block.header.map((cell, at) => (
                      <th key={at}>
                        <Text parts={cell} />
                      </th>
                    ))}
                  </tr>
                </thead>
              )}
              <tbody>
                {block.rows.map((row, at) => (
                  <tr key={at}>
                    {row.map((cell, col) => (
                      <td key={col}>
                        <Text parts={cell} />
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          );
        }
        return (
          <p key={index}>
            <Text parts={block.text} />
          </p>
        );
      })}
    </div>
  );
}
