/** Viewport coordinates for a caret index inside a textarea (for floating toolbars). */
export function getTextareaCaretViewportRect(
  textarea: HTMLTextAreaElement,
  position: number
): { top: number; left: number } {
  const style = window.getComputedStyle(textarea);
  const rect = textarea.getBoundingClientRect();
  const mirror = document.createElement("div");
  const props = [
    "fontFamily",
    "fontSize",
    "fontWeight",
    "fontStyle",
    "letterSpacing",
    "textTransform",
    "wordSpacing",
    "textIndent",
    "lineHeight",
    "paddingTop",
    "paddingRight",
    "paddingBottom",
    "paddingLeft",
    "borderTopWidth",
    "borderRightWidth",
    "borderBottomWidth",
    "borderLeftWidth",
    "boxSizing",
  ] as const;

  mirror.style.position = "fixed";
  mirror.style.top = `${rect.top}px`;
  mirror.style.left = `${rect.left}px`;
  mirror.style.width = `${textarea.clientWidth}px`;
  mirror.style.height = `${textarea.clientHeight}px`;
  mirror.style.overflow = "hidden";
  mirror.style.visibility = "hidden";
  mirror.style.pointerEvents = "none";
  mirror.style.whiteSpace = "pre-wrap";
  mirror.style.wordWrap = "break-word";
  mirror.style.zIndex = "-1";

  for (const prop of props) {
    mirror.style[prop] = style[prop];
  }

  const inner = document.createElement("div");
  inner.style.transform = `translateY(-${textarea.scrollTop}px)`;

  const textBefore = textarea.value.substring(0, position);
  inner.appendChild(document.createTextNode(textBefore));
  const marker = document.createElement("span");
  marker.textContent = textarea.value.substring(position, position + 1) || ".";
  inner.appendChild(marker);
  mirror.appendChild(inner);

  document.body.appendChild(mirror);
  const markerRect = marker.getBoundingClientRect();
  document.body.removeChild(mirror);

  return {
    top: markerRect.top,
    left: markerRect.left + markerRect.width / 2,
  };
}

/** Scroll a textarea so the given range is visible and select it. */
export function scrollTextareaToRange(
  textarea: HTMLTextAreaElement,
  start: number,
  end: number
): void {
  const topCaret = getTextareaCaretViewportRect(textarea, start);
  const taRect = textarea.getBoundingClientRect();
  const relativeTop = topCaret.top - taRect.top + textarea.scrollTop;
  textarea.scrollTop = Math.max(0, relativeTop - textarea.clientHeight / 3);
  textarea.focus({ preventScroll: true });
  textarea.setSelectionRange(start, end);
}
