import { Extension } from "@tiptap/core";

/** Tiptap's schema sanitiser strips inline `style=""`, `bgcolor=""`,
 *  `width=""`, `height=""` and `align=""` attributes by default. That
 *  matches a clean WYSIWYG flow, but the moment the operator pastes a
 *  Gmail/Outlook compose with custom backgrounds, gradients or table
 *  cell widths, every visual cue collapses.
 *
 *  Surface those attributes as first-class on the node types email
 *  templates lean on. We don't try to be exhaustive — the bar is "the
 *  paste preview reads roughly like the source", not pixel-perfect.
 *
 *  Limit the parsed `style` to a sanity cap: HTML emails routinely
 *  ship 2 kB+ inline styles per cell and we don't want a single bad
 *  paste to balloon the editor doc.
 */
const STYLE_MAX_LENGTH = 4096;

const NODES_WITH_INLINE_ATTRS = [
  "paragraph",
  "heading",
  "tableCell",
  "tableHeader",
  "tableRow",
  "table",
  "image",
  "blockquote",
  "listItem",
];

function clampStyle(value: string | null): string | null {
  if (!value) return null;
  return value.length > STYLE_MAX_LENGTH
    ? value.slice(0, STYLE_MAX_LENGTH)
    : value;
}

export const PreserveStyles = Extension.create({
  name: "preserveStyles",
  addGlobalAttributes() {
    return [
      {
        types: NODES_WITH_INLINE_ATTRS,
        attributes: {
          style: {
            default: null,
            parseHTML: (el) => clampStyle(el.getAttribute("style")),
            renderHTML: (attrs) =>
              attrs.style ? { style: attrs.style as string } : {},
          },
          bgcolor: {
            default: null,
            parseHTML: (el) => el.getAttribute("bgcolor"),
            renderHTML: (attrs) =>
              attrs.bgcolor ? { bgcolor: attrs.bgcolor as string } : {},
          },
          width: {
            default: null,
            parseHTML: (el) => el.getAttribute("width"),
            renderHTML: (attrs) =>
              attrs.width ? { width: attrs.width as string } : {},
          },
          height: {
            default: null,
            parseHTML: (el) => el.getAttribute("height"),
            renderHTML: (attrs) =>
              attrs.height ? { height: attrs.height as string } : {},
          },
          align: {
            default: null,
            parseHTML: (el) => el.getAttribute("align"),
            renderHTML: (attrs) =>
              attrs.align ? { align: attrs.align as string } : {},
          },
        },
      },
    ];
  },
});
