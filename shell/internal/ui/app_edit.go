package ui

import (
	"strings"
	"unicode"

	tea "github.com/charmbracelet/bubbletea"
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
// without, a bar. A draft taller than most rows shows the rows around the cursor.
func (st State) draftRows(room, most int) []string {
	room = max(2, room-1) // a cell for the cursor after a full row
	r, c := []rune(st.Prompt), st.caret()
	var rows []string
	at := 0 // the row the cursor is on
	for line := 0; line <= len(r); {
		end := line
		for end < len(r) && r[end] != '\n' {
			end++
		}
		for p := line; ; p += room { // a long line in pieces of room
			q := min(p+room, end)
			if c >= p && (c < q || (c == q && q == end)) {
				at = len(rows)
				rows = append(rows, cursorIn(st, r[p:q], c-p))
			} else {
				rows = append(rows, string(r[p:q]))
			}
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

// cursorIn draws the cursor at col in a row of the draft.
func cursorIn(st State, row []rune, col int) string {
	if !st.Styled {
		return string(row[:col]) + "▏" + string(row[col:])
	}
	under, rest := " ", ""
	if col < len(row) {
		under, rest = string(row[col]), string(row[col+1:])
	}
	return string(row[:col]) + "\x1b[7m" + under + "\x1b[27m" + rest
}

// boxRows is the reply box's content: the › and the draft's rows under it, or the placeholder.
func boxRows(st State, w int, placeholder string) []string {
	if st.Prompt == "" {
		return []string{" " + st.faint("›") + " ▏" + st.faint(placeholder)}
	}
	rows := st.draftRows(w-7, 8)
	for i := range rows {
		lead := "   "
		if i == 0 {
			lead = " " + st.faint("›") + " "
		}
		rows[i] = lead + rows[i]
	}
	return rows
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
		m.st.PromptBack, m.edited = 0, m.st.Prompt
	}
}

// setPrompt is the editor's own change to the box: the text, and the cursor's place in runes from the start.
func (m *Model) setPrompt(text string, caret int) {
	m.st.Prompt, m.edited, m.st.SlashSel = text, text, 0
	m.st.PromptBack = len([]rune(text)) - max(0, min(caret, len([]rune(text))))
}

// insert types s at the cursor.
func (m *Model) insert(s string) {
	r, c := []rune(m.st.Prompt), m.st.caret()
	m.histAt = -1 // an edited message is a draft again
	m.setPrompt(string(r[:c])+s+string(r[c:]), c+len([]rune(s)))
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

// editKey moves through or changes the box's text; false when k is not one of its keys.
func (m *Model) editKey(k tea.KeyMsg) bool {
	r, c := []rune(m.st.Prompt), m.st.caret()
	cut := func(from, to int) {
		m.histAt = -1
		m.setPrompt(string(r[:from])+string(r[to:]), from)
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
