package ui

import (
	"fmt"
	"strings"
	"unicode"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
)

// The reply box edits as codex's and grok's do: ←→ move through the text, ⌥←→ (⌥b ⌥f, ⌃←→) a word at a time, ⌃a or
// Home and ⌃e or End go to the line's ends, ⌥⏎ (or ⌃j) starts a new line, ⌫ and Delete (⌃d) remove a character, ⌃w and ⌥⌫ the word before the cursor, ⌥d
// the one after it, ⌃u the line before it (⌃k stays the palette). ↑↓ move between the draft's lines, and from an empty
// box bring back the messages you sent, newest first; ↓ past the newest brings back what you were typing.

// caret is where the cursor is in the box, in runes from the start.
func (st State) caret() int {
	n := len([]rune(st.Prompt))
	return n - min(max(0, st.PromptBack), n)
}

// draftRows is the box's text as rows of at most room cells: its lines, wrapped, with the cursor drawn in its row. With
// colour the cursor is a block on the character under it, as codex and grok draw theirs, so the text keeps its place;
// draftRow is one row of the box's text as drawn, and where it starts in the text, for a click on it.
type draftRow struct {
	text       string
	start, end int // its runes in the draft
}

// draftRows is the box's text as rows of at most room cells: its lines, wrapped, with the cursor drawn in its row and
// the selection lit. With colour the cursor is a block on the character under it, as codex and grok draw theirs, so
// the text keeps its place; without, a bar. A draft taller than most rows shows the rows around the cursor.
func (st State) draftRows(room, most int) []draftRow {
	room = max(2, room-1) // a cell for the cursor after a full row
	r, c := []rune(st.Prompt), st.caret()
	s0, s1 := st.selection()
	var rows []draftRow
	at := 0 // the row the cursor is on
	for line := 0; line <= len(r); {
		end := line
		for end < len(r) && r[end] != '\n' {
			end++
		}
		for p := line; ; p += room { // a long line in pieces of room
			q := min(p+room, end)
			col := -1
			if c >= p && (c < q || (c == q && q == end)) {
				at, col = len(rows), c-p
			}
			rows = append(rows, draftRow{drawRow(st, r[p:q], col, s0-p, s1-p), p, q})
			if q == end {
				break
			}
		}
		line = end + 1
	}
	if len(rows) > most {
		from := max(0, min(at-most/2, len(rows)-most))
		rows = rows[from : from+most]
	}
	return rows
}

// selection is the box's selected runes, [s0, s1); empty when nothing is selected.
func (st State) selection() (int, int) {
	if !st.PromptSel {
		return 0, 0
	}
	n := len([]rune(st.Prompt))
	a, c := min(max(0, st.PromptAnchor), n), st.caret()
	return min(a, c), max(a, c)
}

// drawRow draws a row of the draft: the selected runes in [s0, s1) lit, and the cursor at col (none when col < 0).
func drawRow(st State, row []rune, col, s0, s1 int) string {
	sel := "#c7d2fe"
	if !lightGround() {
		sel = "#3f3f8f"
	}
	var b strings.Builder
	for i := 0; i <= len(row); i++ {
		ch := " "
		if i < len(row) {
			ch = string(row[i])
		} else if i != col {
			break
		}
		switch {
		case i == col && !st.Styled:
			b.WriteString("▏")
			if i < len(row) {
				b.WriteString(ch)
			}
		case i == col:
			b.WriteString("\x1b[7m" + ch + "\x1b[27m")
		case i >= s0 && i < s1 && st.Styled:
			b.WriteString(lipgloss.NewStyle().Background(lipgloss.Color(sel)).Foreground(lipgloss.Color(colText)).Render(ch))

		default:
			b.WriteString(ch)
		}
	}
	return b.String()
}

// boxRows is the reply box's content: the › and the draft's rows under it, or the placeholder. Each row's text is a
// zone a click places the cursor in.
func boxRows(st State, w int, placeholder string) []string {
	if st.Prompt == "" {
		return []string{" " + st.faint("›") + " " + st.zone("text:box:0:0", "▏"+st.faint(placeholder))}
	}
	var out []string
	for i, r := range st.draftRows(w-7, 8) {
		lead := "   "
		if i == 0 {
			lead = " " + st.faint("›") + " "
		}
		out = append(out, lead+st.zone(fmt.Sprintf("text:box:%d:%d", r.start, r.end), r.text+" "))
	}
	return out
}

// lineOf is where rune c of r is: its line's start and end, and its column.
func lineOf(r []rune, c int) (start, end int) {
	start, end = c, c
	for start > 0 && r[start-1] != '\n' {
		start--
	}
	for end < len(r) && r[end] != '\n' {
		end++
	}
	return start, end
}

// moveLine puts the cursor on the line above (-1) or below (+1) at the same column; false on the first or last line.
func (m *Model) moveLine(step int) bool {
	r, c := []rune(m.st.Prompt), m.st.caret()
	start, end := lineOf(r, c)
	col := c - start
	switch {
	case step < 0 && start > 0:
		ps, _ := lineOf(r, start-1)
		m.setPrompt(m.st.Prompt, ps+min(col, start-1-ps))
	case step > 0 && end < len(r):
		_, ne := lineOf(r, end+1)
		m.setPrompt(m.st.Prompt, end+1+min(col, ne-end-1))
	default:
		return false
	}
	return true
}

// syncCaret puts the cursor at the end of a box that something other than the editor filled or emptied.
func (m *Model) syncCaret() {
	if m.st.Prompt != m.edited {
		m.st.PromptBack, m.edited, m.st.PromptSel = 0, m.st.Prompt, false
	}
}

// setPrompt is the editor's own change to the box: the text, and the cursor's place in runes from the start.
func (m *Model) setPrompt(text string, caret int) {
	m.st.Prompt, m.edited, m.st.SlashSel = text, text, 0
	m.st.PromptBack = len([]rune(text)) - max(0, min(caret, len([]rune(text))))
}

// insert types s at the cursor, in place of the selection when there is one.
func (m *Model) insert(s string) {
	r, c := []rune(m.st.Prompt), m.st.caret()
	from, to := c, c
	if s0, s1 := m.st.selection(); s1 > s0 {
		from, to = s0, s1
	}
	m.histAt, m.st.PromptSel = -1, false // an edited message is a draft again
	m.setPrompt(string(r[:from])+s+string(r[to:]), from+len([]rune(s)))
}

// selectTo moves the cursor to c with the selection following it, from where it was when the selection began.
func (m *Model) selectTo(c int) {
	if !m.st.PromptSel {
		m.st.PromptSel, m.st.PromptAnchor = true, m.st.caret()
	}
	m.setPrompt(m.st.Prompt, c)
	if m.st.PromptAnchor == m.st.caret() {
		m.st.PromptSel = false
	}
}

// wordLeft and wordRight are where ⌥← and ⌥→ go from c: the start of this or the previous word, the end of the next.
func wordLeft(r []rune, c int) int {
	for c > 0 && unicode.IsSpace(r[c-1]) {
		c--
	}
	for c > 0 && !unicode.IsSpace(r[c-1]) {
		c--
	}
	return c
}

func wordRight(r []rune, c int) int {
	for c < len(r) && unicode.IsSpace(r[c]) {
		c++
	}
	for c < len(r) && !unicode.IsSpace(r[c]) {
		c++
	}
	return c
}

// editKey moves through or changes the box's text; false when k is not one of its keys. With Shift the arrows, Home
// and End select; typing, ⌫ and Delete replace or remove what is selected.
func (m *Model) editKey(k tea.KeyMsg) bool {
	r, c := []rune(m.st.Prompt), m.st.caret()
	s0, s1 := m.st.selection()
	cut := func(from, to int) {
		m.histAt, m.st.PromptSel = -1, false
		m.setPrompt(string(r[:from])+string(r[to:]), from)
	}
	switch k.String() {
	case "shift+left":
		m.selectTo(max(0, c-1))
		return true
	case "shift+right":
		m.selectTo(min(len(r), c+1))
		return true
	case "ctrl+shift+left", "alt+shift+left":
		m.selectTo(wordLeft(r, c))
		return true
	case "ctrl+shift+right", "alt+shift+right":
		m.selectTo(wordRight(r, c))
		return true
	case "shift+home":
		start, _ := lineOf(r, c)
		m.selectTo(start)
		return true
	case "shift+end":
		_, end := lineOf(r, c)
		m.selectTo(end)
		return true
	case "shift+up", "shift+down":
		start, end := lineOf(r, c)
		switch {
		case k.String() == "shift+up" && start > 0:
			ps, _ := lineOf(r, start-1)
			m.selectTo(ps + min(c-start, start-1-ps))
		case k.String() == "shift+up":
			m.selectTo(0)
		case end < len(r):
			_, ne := lineOf(r, end+1)
			m.selectTo(end + 1 + min(c-start, ne-end-1))
		default:
			m.selectTo(len(r))
		}
		return true
	}
	if s1 > s0 {
		switch k.String() {
		case "backspace", "delete", "ctrl+d", "ctrl+w", "alt+backspace", "alt+d", "ctrl+u":
			cut(s0, s1) // what is selected goes
			return true
		case "left":
			m.st.PromptSel = false
			m.setPrompt(m.st.Prompt, s0)
			return true
		case "right":
			m.st.PromptSel = false
			m.setPrompt(m.st.Prompt, s1)
			return true
		}
	}
	switch k.String() {
	case "left", "right", "alt+left", "ctrl+left", "alt+b", "alt+right", "ctrl+right", "alt+f", "home", "ctrl+a", "end", "ctrl+e":
		m.st.PromptSel = false
	}
	switch k.String() {
	case "left", "ctrl+b":
		m.setPrompt(m.st.Prompt, c-1)
	case "right":
		m.setPrompt(m.st.Prompt, c+1)
	case "alt+left", "ctrl+left", "alt+b":
		m.setPrompt(m.st.Prompt, wordLeft(r, c))
	case "alt+right", "ctrl+right", "alt+f":
		m.setPrompt(m.st.Prompt, wordRight(r, c))
	case "home", "ctrl+a":
		start, _ := lineOf(r, c)
		m.setPrompt(m.st.Prompt, start)
	case "end", "ctrl+e":
		_, end := lineOf(r, c)
		m.setPrompt(m.st.Prompt, end)
	case "alt+enter", "ctrl+j":
		m.insert("\n") // a new line, as ⌥⏎ (and ⌃j) give in codex
	case "backspace":
		if c > 0 {
			cut(c-1, c)
		}
	case "delete", "ctrl+d":
		if c < len(r) {
			cut(c, c+1)
		}
	case "ctrl+w", "alt+backspace":
		cut(wordLeft(r, c), c)
	case "alt+d":
		cut(c, wordRight(r, c))
	case "ctrl+u":
		start, _ := lineOf(r, c)
		cut(start, c)
	default:
		return false
	}
	return true
}

// recall puts a message sent before in the box: step -1 an older one (↑), +1 a newer one (↓), and past the newest
// the draft that was there before ↑. False when there is nothing to go to.
func (m *Model) recall(step int) bool {
	if len(m.history) == 0 || (m.histAt < 0 && step > 0) {
		return false
	}
	at := m.histAt
	if at < 0 {
		m.histDraft, at = m.st.Prompt, len(m.history)
	}
	at += step
	switch {
	case at < 0:
		return true // the oldest stays
	case at >= len(m.history):
		m.histAt = -1
		m.setPrompt(m.histDraft, len([]rune(m.histDraft)))
		return true
	}
	m.histAt = at
	m.setPrompt(m.history[at], len([]rune(m.history[at])))
	return true
}

// rememberSent keeps a sent message for ↑, without repeating the one before it.
func (m *Model) rememberSent(text string) {
	text = strings.TrimSpace(text)
	if text != "" && (len(m.history) == 0 || m.history[len(m.history)-1] != text) {
		m.history = append(m.history, text)
	}
	m.histAt, m.histDraft = -1, ""
}

// rememberChat seeds ↑ with what you asked in a conversation, once, so it reaches back past this run of the shell.
func (m *Model) rememberChat() {
	if m.st.Chat == nil || m.histFor == m.st.Chat.Session {
		return
	}
	m.histFor = m.st.Chat.Session
	var asked []string
	for _, e := range m.st.Chat.Entries {
		if e.Role == "user" && strings.TrimSpace(e.Text) != "" {
			asked = append(asked, strings.TrimSpace(e.Text))
		}
	}
	m.history = append(asked, m.history...)
	m.histAt = -1
}
