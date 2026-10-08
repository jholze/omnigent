// Clickable http(s) URLs in terminal output. Ported from @xterm/addon-web-links
// (MIT), which only rejoins rows xterm soft-wrapped; this provider also follows
// a URL across the hard rows a program leaves when it wraps at the pane width.

import type { IBufferLine, ILink, ILinkProvider, Terminal } from "@xterm/xterm";

// Everything from http:// or https:// up to the first whitespace or quote,
// excluding RFC 3986/1738 unsafe characters, and ending before trailing
// punctuation or the brackets that usually enclose a URL in prose.
const URL_PATTERN = /(https?|HTTPS?):[/]{2}[^\s"'!*(){}|\\^<>`]*[^\s"':,.!?{}|\\^~[\]`()<>]/;

// Rows joined for one URL search are capped at this many characters.
const MAX_JOINED_LENGTH = 2048;

export type TerminalLinkActivate = (event: MouseEvent, uri: string) => void;

export class TerminalLinkProvider implements ILinkProvider {
  private readonly term: Terminal;
  private readonly activate: TerminalLinkActivate;

  constructor(term: Terminal, activate: TerminalLinkActivate) {
    this.term = term;
    this.activate = activate;
  }

  provideLinks(bufferLineNumber: number, callback: (links: ILink[] | undefined) => void): void {
    callback(computeLinks(this.term, bufferLineNumber - 1, this.activate));
  }
}

function computeLinks(term: Terminal, lineIndex: number, activate: TerminalLinkActivate): ILink[] {
  const [lines, startLineIndex] = logicalLine(term, lineIndex);
  const text = lines.join("");
  const pattern = new RegExp(URL_PATTERN.source, "g");
  const links: ILink[] = [];
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(text))) {
    const uri = match[0];
    if (!isUrl(uri)) continue;
    const [startY, startX] = bufferPosition(term, startLineIndex, 0, match.index);
    const [endY, endX] = bufferPosition(term, startY, startX, uri.length);
    if (startY === -1 || startX === -1 || endY === -1 || endX === -1) continue;
    // Ranges are 1-based with an inclusive end, so only endX keeps its
    // 0-based exclusive value.
    links.push({
      range: { start: { x: startX + 1, y: startY + 1 }, end: { x: endX, y: endY + 1 } },
      text: uri,
      activate,
    });
  }
  return links;
}

/**
 * Rows forming the logical line around ``lineIndex`` and the index of its first row.
 * Expansion stops at whitespace, which ends any URL, or at the length cap. Rows are
 * right-trimmed so a URL wrapped early before a wide char still matches; see bufferPosition.
 */
function logicalLine(term: Terminal, lineIndex: number): [string[], number] {
  const buffer = term.buffer.active;
  const current = buffer.getLine(lineIndex);
  if (!current) return [[], lineIndex];
  const currentText = current.translateToString(true);
  const lines = [currentText];
  let topIndex = lineIndex;
  let length = 0;
  if (currentText[0] !== " ") {
    while (length < MAX_JOINED_LENGTH && continuesRowAbove(term, topIndex)) {
      const above = buffer.getLine(--topIndex);
      if (!above) break;
      const text = above.translateToString(true);
      length += text.length;
      lines.unshift(text);
      if (text.includes(" ")) break;
    }
  }
  let bottomIndex = lineIndex;
  length = 0;
  while (length < MAX_JOINED_LENGTH && continuesRowAbove(term, bottomIndex + 1)) {
    const below = buffer.getLine(++bottomIndex);
    if (!below) break;
    const text = below.translateToString(true);
    length += text.length;
    lines.push(text);
    if (text.includes(" ")) break;
  }
  return [lines, topIndex];
}

/** Whether the row at ``rowIndex`` continues the logical line of the row above it. */
function continuesRowAbove(term: Terminal, rowIndex: number): boolean {
  const buffer = term.buffer.active;
  const row = buffer.getLine(rowIndex);
  const above = buffer.getLine(rowIndex - 1);
  if (!row || !above) return false;
  return row.isWrapped || splitTokenSpans(above, row, term.cols);
}

/**
 * Whether ``row`` holds the tail of a token a width-aware program split at the pane
 * edge: ``above`` is filled to its last column, ``row`` starts with a non-blank, and
 * the fragments together are longer than the pane. Such a program only splits a token
 * that cannot fit on one row, so a shorter pair is two words that met at the edge.
 */
function splitTokenSpans(above: IBufferLine, row: IBufferLine, cols: number): boolean {
  const tail = /\S+$/.exec(above.translateToString(false, 0, cols));
  const head = /^\S+/.exec(row.translateToString(true));
  return tail !== null && head !== null && tail[0].length + head[0].length > cols;
}

/**
 * Map an offset into the joined text back to a 0-based buffer position, walking cells
 * from ``column`` of line ``lineIndex``; ``[-1, -1]`` when the walk leaves the buffer.
 */
function bufferPosition(
  term: Terminal,
  lineIndex: number,
  column: number,
  offset: number,
): [number, number] {
  const buffer = term.buffer.active;
  const cell = buffer.getNullCell();
  let line = lineIndex;
  let start = column;
  let remaining = offset;
  while (remaining) {
    const row = buffer.getLine(line);
    if (!row) return [-1, -1];
    for (let i = start; i < row.length; ++i) {
      row.getCell(i, cell);
      const chars = cell.getChars();
      if (cell.getWidth()) {
        remaining -= chars.length || 1;
        // A wide character that did not fit in the last cell wrapped early,
        // leaving an empty cell that the trimmed row text skipped.
        if (i === row.length - 1 && chars === "") {
          const next = buffer.getLine(line + 1);
          if (next?.isWrapped) {
            next.getCell(0, cell);
            if (cell.getWidth() === 2) remaining += 1;
          }
        }
      }
      if (remaining < 0) return [line, i];
    }
    line++;
    start = 0;
  }
  return [line, start];
}

/** Whether the matched text parses as a URL whose origin is spelled as written. */
function isUrl(text: string): boolean {
  try {
    const url = new URL(text);
    const credentials = url.username
      ? `${url.username}${url.password ? `:${url.password}` : ""}@`
      : "";
    const origin = `${url.protocol}//${credentials}${url.host}`;
    return text.toLocaleLowerCase().startsWith(origin.toLocaleLowerCase());
  } catch {
    return false;
  }
}
