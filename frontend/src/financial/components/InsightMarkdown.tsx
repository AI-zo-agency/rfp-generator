"use client";

import { Fragment, type HTMLAttributes, type ReactNode } from "react";

/**
 * Tiny markdown for AI Intelligence briefs/chat: paragraphs, bullets,
 * **bold**, *italic*. No headings/tables — keep the drawer light.
 */

const INLINE_RE = /(\*\*[^*]+?\*\*|\*[^*]+?\*)/g;
const BULLET_RE = /^\s*(?:[-*•]|\d+[.)])\s+(.*)$/;

function inlineNodes(text: string): ReactNode[] {
  const parts = text.split(INLINE_RE);
  return parts.map((part, i) => {
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
      return <strong key={i}>{part.slice(2, -2)}</strong>;
    }
    if (part.startsWith("*") && part.endsWith("*") && part.length > 2) {
      return <em key={i}>{part.slice(1, -1)}</em>;
    }
    return <Fragment key={i}>{part}</Fragment>;
  });
}

type Block =
  | { kind: "p"; text: string }
  | { kind: "ul"; items: string[] };

/** Exported for tests — blank-line paragraphs become bullets when the model skips `-`. */
export function parseBlocks(raw: string): Block[] {
  const lines = raw.replace(/\r\n/g, "\n").trim().split("\n");
  const blocks: Block[] = [];
  let para: string[] = [];
  let bullets: string[] = [];

  const flushPara = () => {
    const text = para.join(" ").trim();
    if (text) blocks.push({ kind: "p", text });
    para = [];
  };
  const flushBullets = () => {
    if (bullets.length) blocks.push({ kind: "ul", items: bullets });
    bullets = [];
  };

  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) {
      flushBullets();
      flushPara();
      continue;
    }
    const bullet = trimmed.match(BULLET_RE);
    if (bullet) {
      flushPara();
      bullets.push(bullet[1]);
      continue;
    }
    flushBullets();
    para.push(trimmed);
  }
  flushBullets();
  flushPara();

  // Models often emit labeled paragraphs with **bold** leads and no `-` markers.
  // Promote 2+ paragraph-only briefs to a list so the drawer always skims as bullets.
  if (blocks.length >= 2 && blocks.every((b) => b.kind === "p")) {
    return [{ kind: "ul", items: blocks.map((b) => b.text) }];
  }
  return blocks;
}

export function InsightMarkdown({
  text,
  className,
  ...rest
}: {
  text: string;
  className?: string;
} & HTMLAttributes<HTMLDivElement>) {
  if (!text.trim()) return null;
  const blocks = parseBlocks(text);
  return (
    <div className={className} {...rest}>
      {blocks.map((block, i) =>
        block.kind === "ul" ? (
          <ul key={i} className="qb-ai-md-list">
            {block.items.map((item, j) => (
              <li key={j}>{inlineNodes(item)}</li>
            ))}
          </ul>
        ) : (
          <p key={i} className="qb-ai-md-p">
            {inlineNodes(block.text)}
          </p>
        ),
      )}
    </div>
  );
}
