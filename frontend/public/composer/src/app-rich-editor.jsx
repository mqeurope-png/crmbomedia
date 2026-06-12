/* ───────────── RICH TEXT EDITOR ─────────────
   contentEditable-based editor with a formatting toolbar. Uses document.execCommand
   for formatting (still the simplest path for in-browser WYSIWYG without deps). */

function RichTextEditor({ value, onChange, placeholder, minHeight = 120, fontSize }) {
  const editorRef = React.useRef(null)
  const isInternalUpdate = React.useRef(false)
  const savedRangeRef = React.useRef(null)

  // Sync external value into DOM without wiping caret during typing
  React.useEffect(() => {
    const el = editorRef.current
    if (!el) return
    if (isInternalUpdate.current) { isInternalUpdate.current = false; return }
    const current = el.innerHTML
    const incoming = value || ''
    if (current !== incoming) el.innerHTML = incoming
  }, [value])

  const emit = () => {
    const el = editorRef.current
    if (!el) return
    isInternalUpdate.current = true
    onChange(el.innerHTML)
  }

  // Save the current selection (caret position) — toolbar buttons take focus
  // away from the editor on click, so we restore the saved range before
  // running any execCommand.
  const saveSelection = () => {
    const sel = window.getSelection && window.getSelection()
    if (!sel || sel.rangeCount === 0) return
    const range = sel.getRangeAt(0)
    if (editorRef.current && editorRef.current.contains(range.commonAncestorContainer)) {
      savedRangeRef.current = range
    }
  }
  const restoreSelection = () => {
    const range = savedRangeRef.current
    if (!range) {
      // No saved selection — focus + place caret at end
      const el = editorRef.current
      if (!el) return
      el.focus()
      const r = document.createRange()
      r.selectNodeContents(el)
      r.collapse(false)
      const sel = window.getSelection()
      sel.removeAllRanges()
      sel.addRange(r)
      return
    }
    editorRef.current?.focus()
    const sel = window.getSelection()
    sel.removeAllRanges()
    sel.addRange(range)
  }

  const exec = (cmd, arg) => {
    restoreSelection()
    document.execCommand(cmd, false, arg)
    emit()
  }

  // formatBlock needs the tag wrapped in angle brackets in some browsers
  // (notably WebKit-based ones), and a saved selection — otherwise the caret
  // ends up outside the editor and the block change silently no-ops.
  const format = (tag) => {
    restoreSelection()
    document.execCommand('formatBlock', false, '<' + tag + '>')
    emit()
  }

  const insertLink = () => {
    const url = window.prompt('URL del enlace:', 'https://')
    if (url) exec('createLink', url)
  }

  const removeLink = () => exec('unlink')

  // removeFormat doesn't strip <a> tags — chain unlink after it so a single
  // click cleans everything (bold, italic, color, AND the link).
  const clearFormat = () => {
    restoreSelection()
    document.execCommand('removeFormat', false, null)
    document.execCommand('unlink', false, null)
    emit()
  }

  // mousedown on the toolbar button shouldn't move the caret out of the
  // editor — preventDefault keeps the contentEditable selection intact.
  const btn = (label, action, title) => (
    <button type="button" className="rte-btn" title={title || (typeof label === 'string' ? label : '')} onMouseDown={e => { e.preventDefault(); saveSelection(); }} onClick={action}>
      {label}
    </button>
  )

  return (
    <div className="rte">
      <div className="rte-toolbar">
        {/* Block-format buttons (P / H1-H3) — direct buttons rather than a
           dropdown because <select>'s open behaviour steals the caret in
           Chrome/Firefox and the format silently no-ops. */}
        {btn('P', () => format('p'), 'Párrafo')}
        {btn(<span style={{fontWeight:700}}>H1</span>, () => format('h1'), 'Encabezado 1')}
        {btn(<span style={{fontWeight:700}}>H2</span>, () => format('h2'), 'Encabezado 2')}
        {btn(<span style={{fontWeight:700}}>H3</span>, () => format('h3'), 'Encabezado 3')}
        <span className="rte-sep" />
        {btn(<b>B</b>, () => exec('bold'), 'Negrita')}
        {btn(<i>I</i>, () => exec('italic'), 'Cursiva')}
        {btn(<u>U</u>, () => exec('underline'), 'Subrayado')}
        {btn(<s>S</s>, () => exec('strikeThrough'), 'Tachado')}
        <span className="rte-sep" />
        {btn('• Lista', () => exec('insertUnorderedList'), 'Lista')}
        {btn('1. Lista', () => exec('insertOrderedList'), 'Lista numerada')}
        <span className="rte-sep" />
        {btn('←', () => exec('justifyLeft'), 'Izquierda')}
        {btn('↔', () => exec('justifyCenter'), 'Centrar')}
        {btn('→', () => exec('justifyRight'), 'Derecha')}
        <span className="rte-sep" />
        {btn('🔗', insertLink, 'Insertar enlace')}
        {btn('🔗✕', removeLink, 'Quitar enlace')}
        {btn('⨯', clearFormat, 'Limpiar formato (también quita enlaces)')}
      </div>
      <div
        ref={editorRef}
        className="rte-editor"
        contentEditable
        suppressContentEditableWarning
        onInput={emit}
        onBlur={() => { saveSelection(); emit(); }}
        onKeyUp={saveSelection}
        onMouseUp={saveSelection}
        data-placeholder={placeholder || 'Escribe aquí…'}
        style={{ minHeight, fontSize: fontSize ? fontSize + 'px' : undefined }}
      />
      <style>{`
        .rte { border:1.5px solid var(--border); border-radius:var(--r-sm); background:var(--bg-panel); overflow:hidden; }
        .rte-toolbar { display:flex; flex-wrap:wrap; gap:2px; align-items:center; padding:6px; border-bottom:1px solid var(--border); background:var(--bg-sunken); }
        .rte-btn { border:1px solid transparent; background:transparent; color:var(--text); padding:4px 8px; font-size:12px; cursor:pointer; border-radius:var(--r-xs); }
        .rte-btn:hover { background:var(--bg-hover); border-color:var(--border); }
        .rte-select { border:1px solid var(--border); background:var(--bg-panel); color:var(--text); padding:3px 6px; font-size:11px; border-radius:var(--r-xs); cursor:pointer; }
        .rte-sep { width:1px; height:16px; background:var(--border); margin:0 4px; }
        .rte-editor { padding:10px 12px; font-size:14px; color:var(--text); line-height:1.6; outline:none; min-height:120px; }
        .rte-editor:empty::before { content:attr(data-placeholder); color:var(--text-subtle); }
        .rte-editor h1 { font-size:20px; font-weight:700; margin:8px 0; }
        .rte-editor h2 { font-size:17px; font-weight:700; margin:8px 0; }
        .rte-editor h3 { font-size:15px; font-weight:700; margin:8px 0; }
        .rte-editor p { margin:0 0 8px; }
        .rte-editor ul, .rte-editor ol { margin:8px 0; padding-left:22px; }
        .rte-editor a { color:var(--artisjet); text-decoration:underline; }
      `}</style>
    </div>
  )
}

Object.assign(window, { RichTextEditor })
