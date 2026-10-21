package ui

import (
	"strings"
	"unicode"

	tea "github.com/charmbracelet/bubbletea"
)

// The reply box edits as codex's and grok's do: ←→ move through the text, ⌥←→ (⌥b ⌥f, ⌃←→) a word at a time, ⌃a or
// Home and ⌃e or End go to its ends, ⌫ and Delete (⌃d) remove a character, ⌃w and ⌥⌫ the word before the cursor, ⌥d
// the one after it, ⌃u everything before it (⌃k stays the palette). ↑↓ bring back the messages you sent, newest first,
// and ↓ past the newest brings back what you were typing.

// caret is where the cursor is in the box, in runes from the start.
func (st State) caret() int {
	n := len([]rune(st.Prompt))
	return n - min(max(0, st.PromptBack), n)
}

// draft is the box's text with the cursor drawn in it, kept to room cells around the cursor.
func (st State) draft(room int) string {
	r := []rune(st.Prompt)
	c := st.caret()
	if len(r) < room {
		return string(r[:c]) + "▏" + string(r[c:])
	}
	from := max(0, min(c-room/2, len(r)-room+1))
	to := min(len(r), from+room-1)
	out := string(r[from:c]) + "▏" + string(r[c:to])
	if from > 0 {
		out = "…" + string([]rune(out)[1:])
	}
	if to < len(r) {
		out = string([]rune(out)[:len([]rune(out))-1]) + "…"
	}
	return out
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
		m.setPrompt(m.st.Prompt, 0)
	case "end", "ctrl+e":
		m.setPrompt(m.st.Prompt, len(r))
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
		cut(0, c)
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
