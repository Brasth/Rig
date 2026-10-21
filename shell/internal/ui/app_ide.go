package ui

import (
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"unicode"

	tea "github.com/charmbracelet/bubbletea"
)

// The code tab's editor, as an IDE's: a cursor and a selection (Shift with the arrows, the mouse, ⌃a), copy, cut and
// paste, undo and redo, indent and outdent, comments, closing brackets and quotes, find in the file and go to line,
// and each open file keeping its own unsaved changes. Every change goes through replaceRange, so undo, the selection
// and the clipboard all see the same edits; FileLines is never changed in place, so a snapshot is just the old slice.

// pos is a place in the file: a line and a rune in it.
type pos struct{ l, c int }

func before(a, b pos) bool { return a.l < b.l || (a.l == b.l && a.c < b.c) }

// snapshot is the file and its cursor before an edit, for undo and redo.
type snapshot struct {
	lines []string
	cur   pos
	kind  string // what the edit was: typing runs into one undo
}

// codeBuf is an open file's unsaved state, kept while another tab is shown.
type codeBuf struct {
	lines       []string
	cur, anchor pos
	sel         bool
	off         int
	crlf        bool
	saved       string
	undo, redo  []snapshot
}

// codeAsk is a question the code tab asks on its last row: go to which line, find what, a new file's or a new name.
type codeAsk struct {
	kind, label, text string
	replace           bool   // ⌃r: what is found is then replaced
	find              string // what replace replaces, once it asks for the new text
}

// linesID names a version of the file: a new slice for every edit, the same one when nothing changed since.
func linesID(lines []string) string { return fmt.Sprintf("%p:%d", lines, len(lines)) }

func (a *AppState) cursor() pos { return pos{a.CurLine, a.CurCol} }

func (a *AppState) setCursor(p pos) {
	p.l = max(0, min(p.l, len(a.FileLines)-1))
	p.c = max(0, min(p.c, len([]rune(a.FileLines[p.l]))))
	a.CurLine, a.CurCol = p.l, p.c
}

// selRange is the selection in reading order; false when nothing is selected.
func (a *AppState) selRange() (pos, pos, bool) {
	if !a.EdSel {
		return pos{}, pos{}, false
	}
	from, to := a.EdAnchor, a.cursor()
	if before(to, from) {
		from, to = to, from
	}
	return from, to, from != to
}

// textOf is the file's text between two places.
func (a *AppState) textOf(from, to pos) string {
	if from.l == to.l {
		r := []rune(a.FileLines[from.l])
		return string(r[min(from.c, len(r)):min(to.c, len(r))])
	}
	var b strings.Builder
	b.WriteString(string([]rune(a.FileLines[from.l])[from.c:]))
	for l := from.l + 1; l < to.l; l++ {
		b.WriteString("\n" + a.FileLines[l])
	}
	b.WriteString("\n" + string([]rune(a.FileLines[to.l])[:min(to.c, len([]rune(a.FileLines[to.l])))]))
	return b.String()
}

// replaceRange puts text in place of the file from from to to, in a new slice, and returns where the text ends.
func (a *AppState) replaceRange(from, to pos, text string) pos {
	head := string([]rune(a.FileLines[from.l])[:from.c])
	tail := string([]rune(a.FileLines[to.l])[to.c:])
	parts := strings.Split(text, "\n")
	mid := make([]string, len(parts))
	copy(mid, parts)
	mid[0] = head + mid[0]
	end := pos{from.l + len(parts) - 1, len([]rune(mid[len(mid)-1]))}
	mid[len(mid)-1] += tail
	lines := make([]string, 0, len(a.FileLines)-(to.l-from.l)+len(mid)-1)
	lines = append(lines, a.FileLines[:from.l]...)
	lines = append(lines, mid...)
	lines = append(lines, a.FileLines[to.l+1:]...)
	a.FileLines = lines
	a.Dirty = linesID(lines) != a.SavedID
	return end
}

// edit records the file for undo and makes one change: text in place of the selection, or at the cursor.
func (m *Model) edit(kind, text string) {
	a := m.st.App
	m.rememberEdit(kind)
	from, to, ok := a.selRange()
	if !ok {
		from, to = a.cursor(), a.cursor()
	}
	a.EdSel = false
	a.setCursor(a.replaceRange(from, to, text))
}

// rememberEdit keeps the file as it is for undo; a run of typing on one line is one undo.
func (m *Model) rememberEdit(kind string) {
	a := m.st.App
	if n := len(a.Undo); n > 0 && kind == "type" && a.Undo[n-1].kind == "type" && a.Undo[n-1].cur.l == a.CurLine && !a.EdSel {
		return
	}
	a.Undo = append(a.Undo, snapshot{lines: a.FileLines, cur: a.cursor(), kind: kind})
	if len(a.Undo) > 300 {
		a.Undo = a.Undo[len(a.Undo)-300:]
	}
	a.Redo = nil
}

// undo and redo step back and forth through the edits.
func (m *Model) undo(redo bool) {
	a := m.st.App
	from, to := &a.Undo, &a.Redo
	if redo {
		from, to = to, from
	}
	if len(*from) == 0 {
		m.st.Message = "nothing to undo"
		if redo {
			m.st.Message = "nothing to redo"
		}
		return
	}
	last := (*from)[len(*from)-1]
	*from = (*from)[:len(*from)-1]
	*to = append(*to, snapshot{lines: a.FileLines, cur: a.cursor(), kind: "undo"})
	a.FileLines, a.EdSel = last.lines, false
	a.setCursor(last.cur)
	a.Dirty = linesID(a.FileLines) != a.SavedID
}

// lineSpan is the lines the selection covers, else the cursor's line.
func (a *AppState) lineSpan() (int, int) {
	if from, to, ok := a.selRange(); ok {
		last := to.l
		if to.c == 0 && to.l > from.l {
			last-- // a selection ending at a line's start does not take that line
		}
		return from.l, last
	}
	return a.CurLine, a.CurLine
}

// eachLine changes each line from l0 to l1 as one edit, keeping the selection over them.
func (m *Model) eachLine(kind string, l0, l1 int, change func(string) string) {
	a := m.st.App
	m.rememberEdit(kind)
	lines := append([]string(nil), a.FileLines...)
	for l := l0; l <= l1; l++ {
		lines[l] = change(lines[l])
	}
	a.FileLines = lines
	a.Dirty = linesID(lines) != a.SavedID
	if l1 > l0 {
		a.EdSel, a.EdAnchor = true, pos{l0, 0}
		a.setCursor(pos{l1, len([]rune(lines[l1]))})
	} else {
		a.setCursor(a.cursor())
	}
}

// commentPrefix is the language's line comment, from the file's extension.
func commentPrefix(file string) string {
	switch strings.ToLower(filepath.Ext(file)) {
	case ".py", ".sh", ".bash", ".zsh", ".rb", ".yml", ".yaml", ".toml", ".r", ".ex", ".exs", ".pl", ".conf":
		return "#"
	case ".sql", ".lua", ".hs":
		return "--"
	case ".html", ".md", ".xml", ".svg":
		return ""
	}
	return "//"
}

// toggleComment comments the lines out, or back in when all of them already are.
func (m *Model) toggleComment() {
	a := m.st.App
	prefix := commentPrefix(a.File)
	if prefix == "" {
		m.st.Message = "no line comments in " + filepath.Ext(a.File) + " files"
		return
	}
	l0, l1 := a.lineSpan()
	all, indent := true, -1
	for l := l0; l <= l1; l++ {
		t := strings.TrimLeft(a.FileLines[l], " \t")
		if t == "" {
			continue
		}
		if !strings.HasPrefix(t, prefix) {
			all = false
		}
		if n := len(a.FileLines[l]) - len(t); indent < 0 || n < indent {
			indent = n
		}
	}
	indent = max(0, indent)
	m.eachLine("comment", l0, l1, func(s string) string {
		t := strings.TrimLeft(s, " \t")
		switch {
		case t == "":
			return s
		case all:
			lead := s[:len(s)-len(t)]
			return lead + strings.TrimPrefix(strings.TrimPrefix(t, prefix), " ")
		}
		return s[:indent] + prefix + " " + s[indent:]
	})
}

// outdent takes one indent off the start of each line.
func outdent(unit string) func(string) string {
	return func(s string) string {
		if strings.HasPrefix(s, unit) {
			return s[len(unit):]
		}
		return strings.TrimLeft(s[:min(len(s), len(unit))], " \t") + s[min(len(s), len(unit)):]
	}
}

var pairs = map[rune]rune{'(': ')', '[': ']', '{': '}', '"': '"', '\'': '\'', '`': '`'}

// typeRunes types what was keyed: a selection is replaced (or wrapped, by a bracket or a quote), an opening bracket
// or a quote gets its closing one, and a closing one already there is stepped over.
func (m *Model) typeRunes(text string) {
	a := m.st.App
	r := []rune(text)
	line := []rune(a.FileLines[a.CurLine])
	if len(r) == 1 {
		ch := r[0]
		next, prev := rune(0), rune(0)
		if a.CurCol < len(line) {
			next = line[a.CurCol]
		}
		if a.CurCol > 0 {
			prev = line[a.CurCol-1]
		}
		if from, to, ok := a.selRange(); ok && pairs[ch] != 0 && from.l == to.l {
			// a bracket or quote typed over a selection wraps it
			m.edit("wrap", string(ch)+a.textOf(from, to)+string(pairs[ch]))
			return
		}
		if !a.EdSel && next == ch && strings.ContainsRune(")]}\"'`", ch) {
			a.CurCol++ // the closing one is already there
			return
		}
		closing, quote := pairs[ch], strings.ContainsRune("\"'`", ch)
		alnum := func(x rune) bool { return unicode.IsLetter(x) || unicode.IsDigit(x) || x == '_' }
		if closing != 0 && !a.EdSel && !alnum(next) && (!quote || !alnum(prev)) {
			m.edit("type", string(ch)+string(closing))
			a.CurCol--
			return
		}
	}
	m.edit("type", text)
}

// newline breaks the line at the cursor with its indent, one more after an opening bracket or a colon, and between a
// pair of brackets puts the closing one on a line of its own.
func (m *Model) newline() {
	a := m.st.App
	line := []rune(a.FileLines[a.CurLine])
	var lead []rune
	for _, r := range line {
		if r != ' ' && r != '\t' {
			break
		}
		lead = append(lead, r)
	}
	unit := indentUnit(m.st, a.FileLines)
	col := min(a.CurCol, len(line))
	if _, _, ok := a.selRange(); ok {
		col = -1
	}
	prev, next := rune(0), rune(0)
	if col > 0 {
		prev = line[col-1]
	}
	if col >= 0 && col < len(line) {
		next = line[col]
	}
	switch {
	case col >= 0 && pairs[prev] != 0 && pairs[prev] == next && strings.ContainsRune("([{", prev):
		m.edit("newline", "\n"+string(lead)+unit+"\n"+string(lead))
		a.setCursor(pos{a.CurLine - 1, len(lead) + len([]rune(unit))})
	case col >= 0 && strings.ContainsRune("([{:", prev):
		m.edit("newline", "\n"+string(lead)+unit)
	default:
		m.edit("newline", "\n"+string(lead))
	}
}

// copySel puts the selection, or the cursor's line, on the clipboard: Rig's own and the terminal's.
func (m *Model) copySel(cut bool) {
	a := m.st.App
	from, to, ok := a.selRange()
	if !ok {
		// nothing selected: the whole line, as an IDE does
		from, to = pos{a.CurLine, 0}, pos{a.CurLine, len([]rune(a.FileLines[a.CurLine]))}
		m.clip = a.FileLines[a.CurLine] + "\n"
		if cut {
			if a.CurLine+1 < len(a.FileLines) {
				to = pos{a.CurLine + 1, 0}
			} else if a.CurLine > 0 {
				from = pos{a.CurLine - 1, len([]rune(a.FileLines[a.CurLine-1]))}
			}
		}
	} else {
		m.clip = a.textOf(from, to)
	}
	Copy(m.clip)
	m.st.Message = fmt.Sprintf("copied %d characters", len([]rune(m.clip)))
	if cut {
		m.rememberEdit("cut")
		a.EdSel = false
		a.setCursor(a.replaceRange(from, to, ""))
		m.st.Message = fmt.Sprintf("cut %d characters", len([]rune(m.clip)))
	}
}

// moveTo puts the cursor at p; with Shift held the selection follows it, else the selection goes.
func (m *Model) moveTo(p pos, shift bool) {
	a := m.st.App
	if shift && !a.EdSel {
		a.EdSel, a.EdAnchor = true, a.cursor()
	}
	if !shift {
		a.EdSel = false
	}
	a.setCursor(p)
	if a.EdSel && a.EdAnchor == a.cursor() {
		a.EdSel = false
	}
}

// findNext moves to the next place the text is, after the cursor (or before it, back), round the end of the file.
func (m *Model) findNext(text string, back bool) bool {
	a := m.st.App
	if text == "" || len(a.FileLines) == 0 {
		return false
	}
	low := strings.ToLower(text)
	start := a.cursor()
	minC, maxC := start.c, start.c // on the starting line: forward from the cursor, back from before it
	if from, _, ok := a.selRange(); ok && a.Editing {
		maxC = from.c
	}
	if !a.Editing {
		start = pos{a.FileOff, 0}
		minC, maxC = 0, 0
		if a.findAt != (pos{}) {
			start, minC, maxC = a.findAt, a.findAt.c+1, a.findAt.c
		}
	}
	n := len(a.FileLines)
	for i := 0; i <= n; i++ {
		l := (start.l + i) % n
		if back {
			l = ((start.l-i)%n + n) % n
		}
		r := []rune(strings.ToLower(a.FileLines[l]))
		hit := -1
		for c := 0; c+len([]rune(low)) <= len(r); c++ {
			if string(r[c:c+len([]rune(low))]) != low {
				continue
			}
			if i == 0 && !back && c < minC {
				continue // before the cursor: the next one is wanted
			}
			if i == 0 && back && c >= maxC {
				continue
			}
			hit = c
			if !back {
				break
			}
		}
		if hit < 0 {
			continue
		}
		at := pos{l, hit}
		a.findAt = at
		if a.Editing {
			a.EdSel, a.EdAnchor = true, at
			a.setCursor(pos{l, hit + len([]rune(low))})
			m.keepCursorShown()
		} else {
			a.FileOff = max(0, l-3)
		}
		return true
	}
	m.st.Message = "no " + text + " in " + filepath.Base(a.File)
	return false
}

// askKey answers the code tab's question: ⏎ goes to the line, finds the next match (again on each ⏎), makes the file
// or renames it; esc lets it go.
func (m *Model) askKey(k tea.KeyMsg) (tea.Model, tea.Cmd) {
	a := m.st.App
	q := a.CodeAsk
	switch k.Type {
	case tea.KeyEsc:
		a.CodeAsk = nil
		if q.kind == "find" || q.kind == "replace" {
			a.Find = ""
		}
	case tea.KeyBackspace:
		if r := []rune(q.text); len(r) > 0 {
			q.text = string(r[:len(r)-1])
		}
	case tea.KeyRunes, tea.KeySpace:
		q.text += string(k.Runes)
	case tea.KeyCtrlA:
		if q.kind == "replace" {
			a.CodeAsk, a.Find = nil, ""
			m.replaceOne(q.find, q.text, true) // ⌃a: every one at once
		}
	case tea.KeyEnter:
		switch q.kind {
		case "replace":
			if k.Alt {
				a.CodeAsk, a.Find = nil, ""
				m.replaceOne(q.find, q.text, true)
				return m, nil
			}
			m.replaceOne(q.find, q.text, false)
		case "find":
			a.Find = q.text
			if q.replace && q.text != "" {
				// what to find is known: now what to put in its place
				m.findNext(q.text, false)
				q.kind, q.find, q.label, q.text = "replace", q.text, "replace "+q.text+" with", ""
				return m, nil
			}
			m.findNext(q.text, false)
		case "goto":
			a.CodeAsk = nil
			n, err := strconv.Atoi(strings.TrimSpace(q.text))
			if err != nil || n < 1 {
				m.st.Message = "a line number, from 1"
				return m, nil
			}
			n = min(n, len(a.FileLines)) - 1
			if a.Editing {
				m.moveTo(pos{n, 0}, false)
				m.keepCursorShown()
			} else {
				a.FileOff = max(0, n-3)
				a.findAt = pos{n, 0}
			}
		case "new", "rename":
			a.CodeAsk = nil
			return m, m.fileOp(q.kind, q.text)
		}
	}
	return m, nil
}

// switchTab shows the open file step tabs along (⌃PgUp, ⌃PgDn).
func (m *Model) switchTab(step int) tea.Cmd {
	a := m.st.App
	if len(a.Tabs) < 2 {
		return nil
	}
	at := 0
	for i, t := range a.Tabs {
		if t == a.File {
			at = i
		}
	}
	return m.runPalette(paletteItem{Action: "file:" + a.Tabs[(at+step+len(a.Tabs))%len(a.Tabs)]}, "")
}

// stashBuffer keeps the open file's unsaved changes while another tab is shown.
func (m *Model) stashBuffer() {
	a := m.st.App
	if !a.Dirty || a.File == "" {
		return
	}
	if a.Bufs == nil {
		a.Bufs = map[string]*codeBuf{}
	}
	a.Bufs[a.File] = &codeBuf{lines: a.FileLines, cur: a.cursor(), anchor: a.EdAnchor, sel: a.EdSel, off: a.FileOff,
		crlf: a.FileCRLF, saved: a.SavedID, undo: a.Undo, redo: a.Redo}
}

// restoreBuffer shows a tab's unsaved changes again; false when it has none.
func (m *Model) restoreBuffer(file string) bool {
	a := m.st.App
	b := a.Bufs[file]
	if b == nil {
		return false
	}
	delete(a.Bufs, file)
	a.FileLines, a.FileCRLF, a.SavedID, a.Undo, a.Redo = b.lines, b.crlf, b.saved, b.undo, b.redo
	a.Editing, a.Dirty, a.EdSel, a.EdAnchor, a.FileOff, a.FileErr = true, true, b.sel, b.anchor, b.off, ""
	a.setCursor(b.cur)
	return true
}

// unsaved is the files with changes not saved, the open one first.
func (a *AppState) unsaved() []string {
	var out []string
	if a.Dirty && a.File != "" {
		out = append(out, a.File)
	}
	for f := range a.Bufs {
		out = append(out, f)
	}
	return out
}

// editFileKey edits the open file as an IDE does.
func (m *Model) editFileKey(k tea.KeyMsg) (tea.Model, tea.Cmd) {
	a := m.st.App
	if a.CodeAsk != nil {
		return m.askKey(k)
	}
	if len(a.FileLines) == 0 {
		a.FileLines = []string{""}
	}
	a.setCursor(a.cursor())
	key := k.String()
	line := []rune(a.FileLines[a.CurLine])
	cur := a.cursor()
	shift := strings.HasPrefix(key, "shift+") || strings.HasPrefix(key, "ctrl+shift+") || strings.HasPrefix(key, "alt+shift+")
	bare := strings.NewReplacer("ctrl+shift+", "ctrl+", "alt+shift+", "alt+", "shift+", "").Replace(key)
	if !strings.HasPrefix(key, "shift+tab") {
		switch bare {
		case "up":
			m.moveTo(pos{cur.l - 1, cur.c}, shift)
		case "down":
			m.moveTo(pos{cur.l + 1, cur.c}, shift)
		case "left":
			if from, _, ok := a.selRange(); ok && !shift {
				m.moveTo(from, false)
			} else if cur.c > 0 {
				m.moveTo(pos{cur.l, cur.c - 1}, shift)
			} else if cur.l > 0 {
				m.moveTo(pos{cur.l - 1, len([]rune(a.FileLines[cur.l-1]))}, shift)
			}
		case "right":
			if _, to, ok := a.selRange(); ok && !shift {
				m.moveTo(to, false)
			} else if cur.c < len(line) {
				m.moveTo(pos{cur.l, cur.c + 1}, shift)
			} else if cur.l < len(a.FileLines)-1 {
				m.moveTo(pos{cur.l + 1, 0}, shift)
			}
		case "home":
			m.moveTo(pos{cur.l, 0}, shift)
		case "end":
			m.moveTo(pos{cur.l, len(line)}, shift)
		case "ctrl+home":
			m.moveTo(pos{0, 0}, shift)
		case "ctrl+end":
			m.moveTo(pos{len(a.FileLines) - 1, len([]rune(a.FileLines[len(a.FileLines)-1]))}, shift)
		case "pgup":
			m.moveTo(pos{cur.l - 20, cur.c}, shift)
		case "pgdown":
			m.moveTo(pos{cur.l + 20, cur.c}, shift)
		case "alt+left", "ctrl+left", "alt+b":
			m.moveTo(pos{cur.l, wordLeft(line, cur.c)}, shift)
		case "alt+right", "ctrl+right", "alt+f":
			m.moveTo(pos{cur.l, wordRight(line, cur.c)}, shift)
		default:
			goto change
		}
		m.keepCursorShown()
		return m, nil
	}
change:
	if m.ideKey(k) {
		return m, nil
	}
	switch key {
	case "ctrl+s":
		m.saveFile()
	case "esc":
		if a.EdSel {
			a.EdSel = false
			return m, nil
		}
		// editing stops; changes not saved stay, marked in the tab, until ⌃s saves them or ⌃w throws them away
		a.Editing = false
		if a.Dirty {
			m.st.Message = filepath.Base(a.File) + " is not saved: ⌃s saves it, e edits again"
		}
		return m, nil
	case "ctrl+a":
		a.EdSel, a.EdAnchor = true, pos{0, 0}
		a.setCursor(pos{len(a.FileLines) - 1, len([]rune(a.FileLines[len(a.FileLines)-1]))})
	case "ctrl+e":
		m.moveTo(pos{cur.l, len(line)}, false)
	case "ctrl+c":
		m.copySel(false)
	case "ctrl+x":
		m.copySel(true)
	case "ctrl+v":
		if m.clip != "" {
			m.edit("paste", m.clip)
		}
	case "ctrl+z":
		m.undo(false)
	case "ctrl+y":
		m.undo(true)
	case "ctrl+_", "ctrl+/":
		m.toggleComment()
	case "ctrl+g":
		a.CodeAsk = &codeAsk{kind: "goto", label: "go to line"}
	case "ctrl+f":
		a.CodeAsk = &codeAsk{kind: "find", label: "find"}
		if from, to, ok := a.selRange(); ok && from.l == to.l {
			a.CodeAsk.text = a.textOf(from, to)
		}
	case "ctrl+pgup":
		return m, m.switchTab(-1)
	case "ctrl+pgdown":
		return m, m.switchTab(1)
	case "ctrl+w":
		return m, m.closeTab()
	case "enter":
		m.newline()
	case "tab":
		unit := indentUnit(m.st, a.FileLines)
		if from, to, ok := a.selRange(); ok && from.l != to.l {
			l0, l1 := a.lineSpan()
			m.eachLine("indent", l0, l1, func(s string) string {
				if strings.TrimSpace(s) == "" {
					return s
				}
				return unit + s
			})
		} else {
			m.edit("type", unit)
		}
	case "shift+tab":
		l0, l1 := a.lineSpan()
		m.eachLine("outdent", l0, l1, outdent(indentUnit(m.st, a.FileLines)))
	case "backspace":
		switch {
		case a.EdSel:
			m.edit("delete", "")
		case cur.c > 0 && cur.c < len(line) && pairs[line[cur.c-1]] == line[cur.c]:
			m.rememberEdit("delete")
			a.setCursor(a.replaceRange(pos{cur.l, cur.c - 1}, pos{cur.l, cur.c + 1}, ""))
		case cur.c > 0:
			m.rememberEdit("delete")
			a.setCursor(a.replaceRange(pos{cur.l, cur.c - 1}, cur, ""))
		case cur.l > 0:
			m.rememberEdit("delete")
			a.setCursor(a.replaceRange(pos{cur.l - 1, len([]rune(a.FileLines[cur.l-1]))}, cur, ""))
		}
	case "delete", "ctrl+d":
		switch {
		case a.EdSel:
			m.edit("delete", "")
		case cur.c < len(line):
			m.rememberEdit("delete")
			a.replaceRange(cur, pos{cur.l, cur.c + 1}, "")
		case cur.l < len(a.FileLines)-1:
			m.rememberEdit("delete")
			a.replaceRange(cur, pos{cur.l + 1, 0}, "")
		}
	case "alt+backspace":
		if a.EdSel {
			m.edit("delete", "")
		} else {
			m.rememberEdit("delete")
			a.setCursor(a.replaceRange(pos{cur.l, wordLeft(line, cur.c)}, cur, ""))
		}
	default:
		if k.Type != tea.KeyRunes && k.Type != tea.KeySpace {
			return m, nil
		}
		text := strings.ReplaceAll(string(k.Runes), "\r\n", "\n")
		text = strings.ReplaceAll(text, "\r", "\n")
		for _, r := range text {
			if !unicode.IsPrint(r) && r != '\t' && r != '\n' {
				return m, nil
			}
		}
		if k.Paste || len([]rune(text)) > 1 {
			m.edit("paste", text)
		} else {
			m.typeRunes(text)
		}
	}
	m.keepCursorShown()
	return m, nil
}

// editKeys is the footer while editing.
func editKeys(a *AppState) [][2]string {
	if a.CodeAsk != nil {
		if a.CodeAsk.kind == "find" {
			return [][2]string{{"⏎", "next"}, {"esc", "close"}}
		}
		if a.CodeAsk.kind == "replace" {
			return [][2]string{{"⏎", "replace"}, {"⌃a", "all"}, {"esc", "close"}}
		}
		return [][2]string{{"⏎", "ok"}, {"esc", "cancel"}}
	}
	return [][2]string{{"⌃s", "save"}, {"⌃z", "undo"}, {"⌃f", "find"}, {"⌃r", "replace"}, {"⌃o", "symbol"}, {"⌃/", "comment"}, {"esc", "done"}}
}

// inRepo is a path the explorer may make, rename or delete: relative, inside the repository, not in .git.
func inRepo(rel string) (string, bool) {
	rel = filepath.ToSlash(filepath.Clean(strings.TrimSpace(rel)))
	if rel == "." || rel == "" || filepath.IsAbs(rel) || rel == ".." || strings.HasPrefix(rel, "../") || rel == ".git" || strings.HasPrefix(rel, ".git/") {
		return "", false
	}
	return rel, true
}

// fileOp makes a new file (and its folders) and opens it to edit, or renames a file or folder, from the explorer.
func (m *Model) fileOp(kind, text string) tea.Cmd {
	a := m.st.App
	rel, ok := inRepo(text)
	if !ok {
		m.st.Message = "a path inside the repository"
		return nil
	}
	abs := filepath.Join(a.Repo, rel)
	switch kind {
	case "new":
		if _, err := os.Stat(abs); err == nil {
			m.st.Message = rel + " is already there"
			return nil
		}
		if err := os.MkdirAll(filepath.Dir(abs), 0o755); err != nil {
			m.st.Message = "Could not make " + rel + ": " + err.Error()
			return nil
		}
		if err := os.WriteFile(abs, nil, 0o644); err != nil {
			m.st.Message = "Could not make " + rel + ": " + err.Error()
			return nil
		}
		m.stashBuffer()
		a.Files = append(a.Files, rel)
		a.Tabs, a.Tab, a.File, a.FileErr, a.Preview, a.TreeFocus = addTab(a.Tabs, rel), 3, rel, "", false, false
		a.FileLines, a.FileCRLF, a.FileTruncated, a.FileOff = []string{""}, false, false, 0
		a.SavedID, a.Undo, a.Redo, a.Dirty, a.EdSel = linesID(a.FileLines), nil, nil, false, false
		a.Editing, a.CurLine, a.CurCol = true, 0, 0
		m.st.Message = "made " + rel
	case "rename":
		from := a.renameFrom
		if from == "" || from == rel {
			return nil
		}
		if _, err := os.Stat(abs); err == nil {
			m.st.Message = rel + " is already there"
			return nil
		}
		if err := os.MkdirAll(filepath.Dir(abs), 0o755); err == nil {
			err = os.Rename(filepath.Join(a.Repo, from), abs)
			if err != nil {
				m.st.Message = "Could not rename " + from + ": " + err.Error()
				return nil
			}
		}
		moved := func(p string) string {
			if p == from {
				return rel
			}
			if strings.HasPrefix(p, from+"/") {
				return rel + strings.TrimPrefix(p, from)
			}
			return p
		}
		for i := range a.Files {
			a.Files[i] = moved(a.Files[i])
		}
		for i := range a.Tabs {
			a.Tabs[i] = moved(a.Tabs[i])
		}
		for f, b := range a.Bufs {
			if n := moved(f); n != f {
				delete(a.Bufs, f)
				a.Bufs[n] = b
			}
		}
		a.File = moved(a.File)
		for i, r := range treeRows(a) {
			if r.path == rel {
				a.TreeSel = i // the explorer stays on it
			}
		}
		m.st.Message = "renamed " + from + " to " + rel
	}
	return nil
}

// deleteFile removes a file from the repository and from the explorer, after the question confirmKey asked.
func (m *Model) deleteFile(rel string) {
	a := m.st.App
	if err := os.Remove(filepath.Join(a.Repo, rel)); err != nil {
		m.st.Message = "Could not delete " + rel + ": " + err.Error()
		return
	}
	var keep []string
	for _, f := range a.Files {
		if f != rel {
			keep = append(keep, f)
		}
	}
	a.Files = keep
	delete(a.Bufs, rel)
	for i, t := range a.Tabs {
		if t == rel {
			a.Tabs = append(a.Tabs[:i:i], a.Tabs[i+1:]...)
			break
		}
	}
	if a.File == rel {
		a.File, a.FileLines, a.Editing, a.Dirty = "", nil, false, false
	}
	m.st.Message = "deleted " + rel
}
