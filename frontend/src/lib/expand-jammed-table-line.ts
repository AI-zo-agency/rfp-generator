/**
 * A model sometimes joins a whole markdown table onto one line, turning the
 * newline between rows into a space: `| Header | | --- | | cell |`.
 *
 * An empty cell is the same character pattern (`| |`). Splitting every `| |`
 * pulls a blank form cell out of its row, and the row then prints as raw pipes
 * under the header. Only a line that already contains both a separator cell
 * and a real cell is more than one row.
 */

const SEPARATOR_CELL = /^:?-{3,}:?$/;

export function pipeCells(line: string): string[] {
  return line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => cell.trim());
}

function formatRow(cells: string[]): string {
  return `| ${cells.join(" | ")} |`;
}

/** Rebuild a one-line table. Returns null when the line is already a single row. */
export function expandJammedTableLine(line: string): string | null {
  const trimmed = line.trim();
  if (!trimmed.includes("|")) return null;
  const cells = pipeCells(trimmed);
  const sepAt = cells.findIndex((cell) => SEPARATOR_CELL.test(cell));
  if (sepAt < 0) return null;
  const hasContent = cells.some((cell) => cell.length > 0 && !SEPARATOR_CELL.test(cell));
  if (!hasContent) return null;

  const header = cells.slice(0, sepAt);
  if (header.length > 0 && header[header.length - 1] === "") header.pop();
  if (header.length === 0 || header.every((cell) => cell.length === 0)) return null;
  const width = header.length;

  let index = sepAt;
  while (index < cells.length && SEPARATOR_CELL.test(cells[index] || "")) index += 1;
  if (index < cells.length && cells[index] === "") index += 1;

  const dataRows: string[][] = [];
  let rest = cells.slice(index);
  while (rest.length > 0) {
    let row = rest.slice(0, width);
    rest = rest.slice(width);
    if (rest.length > 1 && rest[0] === "") rest = rest.slice(1);
    if (row.length < width) row = [...row, ...Array(width - row.length).fill("")];
    if (row.some((cell) => cell.length > 0)) dataRows.push(row);
  }

  const separator = formatRow(Array.from({ length: width }, () => "---"));
  return [formatRow(header), separator, ...dataRows.map(formatRow)].join("\n");
}
