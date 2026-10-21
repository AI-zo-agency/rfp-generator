"use client";

import { useEffect, useRef } from "react";
import { EditorContent, useEditor, useEditorState } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import Underline from "@tiptap/extension-underline";
import Placeholder from "@tiptap/extension-placeholder";
import {
  Table,
  TableCell,
  TableHeader,
  TableRow,
} from "@tiptap/extension-table";
import {
  htmlToMarkdown,
  markdownToHtml,
  normalizeMarkdownForCompare,
  repairLeakedHtmlInMarkdown,
} from "@/lib/markdown-rich-text";

interface ProposalSectionRichEditorProps {
  value: string;
  onChange: (markdown: string) => void;
  onUserEditStart?: () => void;
  disabled?: boolean;
  placeholder?: string;
}

/** Cells allow text blocks only — no nested tables (Insert table inside a cell). */
const FlatTableCell = TableCell.extend({
  content: "paragraph+",
});
const FlatTableHeader = TableHeader.extend({
  content: "paragraph+",
});

function ToolbarButton({
  label,
  active,
  disabled,
  onClick,
}: {
  label: string;
  active?: boolean;
  disabled?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      title={label}
      aria-label={label}
      disabled={disabled}
      onMouseDown={(e) => e.preventDefault()}
      onClick={onClick}
      className={`proposal-rich-toolbar-btn${active ? " is-active" : ""}`}
    >
      {label}
    </button>
  );
}

export function ProposalSectionRichEditor({
  value,
  onChange,
  onUserEditStart,
  disabled = false,
  placeholder = "Start writing this section…",
}: ProposalSectionRichEditorProps) {
  const lastEmittedRef = useRef(normalizeMarkdownForCompare(value));
  const skipNextExternalRef = useRef(false);
  const repairedLeakRef = useRef(false);
  const onChangeRef = useRef(onChange);
  const onUserEditStartRef = useRef(onUserEditStart);
  onChangeRef.current = onChange;
  onUserEditStartRef.current = onUserEditStart;

  const editor = useEditor({
    immediatelyRender: false,
    editable: !disabled,
    extensions: [
      StarterKit.configure({
        heading: { levels: [2, 3, 4] },
      }),
      Underline,
      Placeholder.configure({ placeholder }),
      Table.configure({
        resizable: true,
        HTMLAttributes: { class: "proposal-rich-table" },
      }),
      TableRow,
      FlatTableHeader,
      FlatTableCell,
    ],
    content: markdownToHtml(value),
    onUpdate: ({ editor: ed }) => {
      const md = htmlToMarkdown(ed.getHTML());
      const norm = normalizeMarkdownForCompare(md);
      if (norm === lastEmittedRef.current) return;
      lastEmittedRef.current = norm;
      skipNextExternalRef.current = true;
      onUserEditStartRef.current?.();
      onChangeRef.current(md);
    },
  });

  const toolbar = useEditorState({
    editor,
    selector: ({ editor: ed }) => {
      // Prefer can().addRowAfter — isActive('table') is unreliable with cell selections.
      const inTable = Boolean(
        ed &&
          (ed.can().addRowAfter() ||
            ed.isActive("table") ||
            ed.isActive("tableCell") ||
            ed.isActive("tableHeader")),
      );
      return {
        bold: ed?.isActive("bold") ?? false,
        italic: ed?.isActive("italic") ?? false,
        underline: ed?.isActive("underline") ?? false,
        h2: ed?.isActive("heading", { level: 2 }) ?? false,
        h3: ed?.isActive("heading", { level: 3 }) ?? false,
        bullet: ed?.isActive("bulletList") ?? false,
        ordered: ed?.isActive("orderedList") ?? false,
        inTable,
      };
    },
  });

  useEffect(() => {
    if (!editor) return;
    editor.setEditable(!disabled);
  }, [disabled, editor]);

  // One-shot: repair content that already leaked TipTap HTML into markdown.
  useEffect(() => {
    if (repairedLeakRef.current) return;
    if (!/<table\b/i.test(value)) return;
    const repaired = repairLeakedHtmlInMarkdown(value);
    if (repaired === value) return;
    repairedLeakRef.current = true;
    lastEmittedRef.current = normalizeMarkdownForCompare(repaired);
    skipNextExternalRef.current = true;
    onUserEditStartRef.current?.();
    onChangeRef.current(repaired);
  }, [value]);

  // Sync external markdown (section switch / AI revise) into the editor.
  useEffect(() => {
    if (!editor) return;
    const incoming = normalizeMarkdownForCompare(value);
    if (skipNextExternalRef.current) {
      skipNextExternalRef.current = false;
      lastEmittedRef.current = incoming;
      return;
    }
    if (incoming === lastEmittedRef.current) return;
    lastEmittedRef.current = incoming;
    editor.commands.setContent(markdownToHtml(value), { emitUpdate: false });
  }, [editor, value]);

  if (!editor) {
    return (
      <div className="proposal-draft-preview-pane proposal-rich-editor-shell">
        <p className="text-sm text-zo-text-muted">Loading editor…</p>
      </div>
    );
  }

  const inTable = toolbar?.inTable ?? false;

  return (
    <div className="proposal-rich-editor-shell flex min-h-0 w-full flex-1 flex-col overflow-hidden">
      <div
        className="proposal-rich-toolbar"
        role="toolbar"
        aria-label="Formatting"
      >
        <ToolbarButton
          label="Bold"
          active={toolbar?.bold}
          disabled={disabled}
          onClick={() => editor.chain().focus().toggleBold().run()}
        />
        <ToolbarButton
          label="Italic"
          active={toolbar?.italic}
          disabled={disabled}
          onClick={() => editor.chain().focus().toggleItalic().run()}
        />
        <ToolbarButton
          label="Underline"
          active={toolbar?.underline}
          disabled={disabled}
          onClick={() => editor.chain().focus().toggleUnderline().run()}
        />
        <span className="proposal-rich-toolbar-sep" aria-hidden />
        <ToolbarButton
          label="Heading"
          active={toolbar?.h2}
          disabled={disabled}
          onClick={() =>
            editor.chain().focus().toggleHeading({ level: 2 }).run()
          }
        />
        <ToolbarButton
          label="Subhead"
          active={toolbar?.h3}
          disabled={disabled}
          onClick={() =>
            editor.chain().focus().toggleHeading({ level: 3 }).run()
          }
        />
        <span className="proposal-rich-toolbar-sep" aria-hidden />
        <ToolbarButton
          label="Bullets"
          active={toolbar?.bullet}
          disabled={disabled}
          onClick={() => editor.chain().focus().toggleBulletList().run()}
        />
        <ToolbarButton
          label="Numbers"
          active={toolbar?.ordered}
          disabled={disabled}
          onClick={() => editor.chain().focus().toggleOrderedList().run()}
        />
        <span className="proposal-rich-toolbar-sep" aria-hidden />
        <ToolbarButton
          label="Insert table"
          disabled={disabled || inTable}
          onClick={() =>
            editor
              .chain()
              .focus()
              .insertTable({ rows: 3, cols: 3, withHeaderRow: true })
              .run()
          }
        />
        <ToolbarButton
          label="Add row"
          disabled={disabled || !inTable}
          onClick={() => editor.chain().focus().addRowAfter().run()}
        />
        <ToolbarButton
          label="Add column"
          disabled={disabled || !inTable}
          onClick={() => editor.chain().focus().addColumnAfter().run()}
        />
        <ToolbarButton
          label="Delete row"
          disabled={disabled || !inTable}
          onClick={() => editor.chain().focus().deleteRow().run()}
        />
        <ToolbarButton
          label="Delete column"
          disabled={disabled || !inTable}
          onClick={() => editor.chain().focus().deleteColumn().run()}
        />
        <ToolbarButton
          label="Delete table"
          disabled={disabled || !inTable}
          onClick={() => editor.chain().focus().deleteTable().run()}
        />
      </div>
      <div className="proposal-draft-preview-pane proposal-rich-editor custom-scrollbar min-h-0 flex-1 overflow-y-auto">
        <EditorContent editor={editor} />
      </div>
    </div>
  );
}
