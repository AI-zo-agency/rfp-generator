import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { createElement } from "react";
import {
  MarkdownReportBody,
  stripEvidenceCitations,
} from "../components/MarkdownReportBody";

function renderDoc(source: string): string {
  return renderToStaticMarkup(
    createElement(MarkdownReportBody, {
      body: stripEvidenceCitations(source),
      variant: "document",
    })
  );
}

function visibleText(html: string): string {
  return html
    .replace(/<[^>]+>/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

describe("markdown table rendering", () => {
  it("does not leak empty pipe rows as text", () => {
    const html = renderDoc(`### Business Information

| | | | | |
| Founded | August 21, 2013 |
| Years in Operation | 13 |
`);
    expect(html).not.toMatch(/\|\s*\|/);
    expect(html).toContain("<table");
    expect(html).not.toContain("<ul");
    expect(visibleText(html)).toContain("Founded");
    expect(visibleText(html)).toContain("August 21, 2013");
    expect(visibleText(html)).toContain("Years in Operation");
  });

  it("renders contact key-value rows as a real table, not a dotted list", () => {
    const html = renderDoc(`### Contact Information

| Field | Information |
| Name | Haley Neff |
| Title | Account Manager |
| Phone | (541) 350-2778 |
| Email | connect@zo.agency |
`);
    expect(html).toContain("<table");
    expect(html).not.toContain("<ul");
    expect(html).not.toContain(" · ");
    expect(visibleText(html)).toContain("Haley Neff");
    expect(visibleText(html)).toContain("Account Manager");
    expect(visibleText(html)).toContain("Field");
    expect(visibleText(html)).toContain("Information");
  });

  it("collapses spaced letter headers into a real table", () => {
    const html = renderDoc(`### Year 1 Timeline

| P | H | A | S | E | Key |
| --- | --- | --- | --- | --- | --- |
| Foundation | Research | Week 2 | Deliverable | Buffer |
`);
    expect(html).toContain("<table");
    expect(html).not.toContain("<pre");
    expect(html).not.toContain("<ul");
    expect(visibleText(html)).toContain("PHASE");
    expect(visibleText(html)).toContain("Foundation");
  });

  it("promotes data row when headers are ellipsis placeholders", () => {
    const html = renderDoc(`### Profile

| ... | ... |
| --- | --- |
| Founded | August 21, 2013 |
| Business Type | S-Corp/LLC |
`);
    expect(html).toContain("<table");
    expect(visibleText(html)).toContain("Founded");
    expect(visibleText(html)).toContain("S-Corp/LLC");
  });

  it("renders a normal rate card cleanly", () => {
    const html = renderDoc(`### Rate Card

| Role | Rate |
| --- | --- |
| Creative Director | $185 |
| Senior Designer | $140 |
`);
    expect(html).toContain("<table");
    expect(visibleText(html)).toContain("Creative Director");
    expect(visibleText(html)).toContain("$185");
  });

  it("rolls up stair-step Fee Detail tables into one row per phase", () => {
    const html = renderDoc(`## Fee Detail by Phase

| Phase | Deliverable / Service | Fee |
| --- | --- | ---: |
| Phase 1: Discovery | Stakeholder interviews with MCECS leadership | $6,640 |
| | Audience segmentation and persona development | $6,165 |
| | Competitive landscape analysis | $4,000 |
| Phase 2: Strategy | Messaging architecture | $8,000 |
`);
    expect(html).toContain("<table");
    expect(visibleText(html)).toContain("Phase 1: Discovery");
    expect(visibleText(html)).toContain("Phase 2: Strategy");
    expect(visibleText(html)).toContain("Scope");
    expect(visibleText(html)).toContain("$16,805");
    expect(visibleText(html)).toContain("$8,000");
    // Not one visible fee cell per original deliverable row for discovery
    const discoveryHits = visibleText(html).match(/Phase 1: Discovery/g) ?? [];
    expect(discoveryHits.length).toBe(1);
  });
});
