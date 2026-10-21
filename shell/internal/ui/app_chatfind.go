package ui

import (
	"fmt"
	"strings"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"
)

// Find in the conversation (⌃/): a question on the reply box's row; ⏎ goes to the next match (newest first, then
// older), esc closes it. The matches are lit; the chosen one is scrolled into view.

// chatFind is the find in the conversation: what is looked for and which match is chosen.
type chatFind struct {
	text string
	at   int // the chosen match, from 0, among the matching lines newest first
}

// chatMatches is which lines of the drawn conversation hold the text, newest first.
func chatMatches(lines []string, text string) []int {
	if text == "" {
		return nil
	}
	low := strings.ToLower(text)
	var out []int
	for i := len(lines) - 1; i >= 0; i-- {
		if strings.Contains(strings.ToLower(ansi.Strip(lines[i])), low) {
			out = append(out, i)
		}
	}
	return out
}

// chatFindKey is the keys while finding in the conversation.
func (m *Model) chatFindKey(k tea.KeyMsg) (tea.Model, tea.Cmd) {
	a := m.st.App
	f := a.ChatFind
	switch k.Type {
	case tea.KeyEsc:
		a.ChatFind = nil
	case tea.KeyBackspace:
		if r := []rune(f.text); len(r) > 0 {
			f.text, f.at = string(r[:len(r)-1]), 0
		}
	case tea.KeyRunes, tea.KeySpace:
		f.text, f.at = f.text+string(k.Runes), 0
		m.scrollToMatch()
	case tea.KeyEnter, tea.KeyDown:
		if n := len(chatMatches(a.chatLines, f.text)); n > 0 {
			f.at = (f.at + 1) % n
		}
		m.scrollToMatch()
	case tea.KeyUp:
		if n := len(chatMatches(a.chatLines, f.text)); n > 0 {
			f.at = (f.at + n - 1) % n
		}
		m.scrollToMatch()
	}
	return m, nil
}

// scrollToMatch puts the chosen match in view: ChatUp counts lines from the foot, so a match at line i of the
// conversation's lines is shown when the lines after it fit under it.
func (m *Model) scrollToMatch() {
	a := m.st.App
	f := a.ChatFind
	hits := chatMatches(a.chatLines, f.text)
	if len(hits) == 0 {
		return
	}
	f.at = clampSel(f.at, len(hits))
	below := len(a.chatLines) - hits[f.at] - 1
	m.st.ChatUp = max(0, min(a.ChatMax, below-3))
}

// chatFindRow is the find's row in place of the reply box, with which match of how many.
func chatFindRow(st State, cw int) []string {
	a := st.App
	f := a.ChatFind
	hits := chatMatches(a.chatLines, f.text)
	count := ""
	switch {
	case f.text == "":
	case len(hits) == 0:
		count = st.paint(colAmber, "no results")
	default:
		count = st.faint(fmt.Sprintf("%d of %d", clampSel(f.at, len(hits))+1, len(hits)))
	}
	line := " " + st.dim("find in conversation ›") + " " + f.text + "▏   " + count + "   " + st.faint("⏎ next · ↑ previous · esc close")
	return roundBox(st, []string{fit(line, cw-4)}, cw, true)
}

// markMatches lights the found text in the conversation's lines, the chosen match in a stronger tint.
func markMatches(st State, lines []string, f *chatFind) []string {
	if f == nil || f.text == "" {
		return lines
	}
	hits := chatMatches(lines, f.text)
	chosen := -1
	if len(hits) > 0 {
		chosen = hits[clampSel(f.at, len(hits))]
	}
	out := make([]string, len(lines))
	for i, l := range lines {
		plain := ansi.Strip(l)
		if !strings.Contains(strings.ToLower(plain), strings.ToLower(f.text)) {
			out[i] = l
			continue
		}
		marked := mark(st, l, f.text)
		if i == chosen && st.Styled {
			marked = "\x1b[1m" + marked + "\x1b[22m"
		}
		out[i] = marked
	}
	return out
}
