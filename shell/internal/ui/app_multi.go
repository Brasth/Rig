package ui

import (
	"fmt"
	"sort"
	"strings"

	tea "github.com/charmbracelet/bubbletea"
)

// Several selections at once, as an IDE's "add next occurrence": ⌃d with a selection selects the next place the same
// text is (or the word under the cursor when nothing is selected); the extra selections are kept as ranges and the
// cursor's own stays the primary one. Typing, a paste, ⌫ or Delete then go to every selection, from the last to the
// first so the earlier positions hold; a move of the cursor lets the extras go. F2 renames: every whole-word
// occurrence in the file is selected and the new name asked for.

// span is one extra selection.
type span struct{ from, to pos }

// selectedWordOrText is what ⌃d looks for: the selection, or the word under the cursor (which it selects; selected
// is then false, so the first ⌃d only selects the word, as an IDE's does).
func (m *Model) selectedWordOrText() (text string, selected bool) {
	a := m.st.App
	if from, to, ok := a.selRange(); ok && from.l == to.l {
		return a.textOf(from, to), true
	}
	w := wordAt(a.FileLines[a.CurLine], a.CurCol)
	if w == "" {
		return "", false
	}
	for _, s := range wordSpans(a.FileLines[a.CurLine], w) {
		if a.CurCol >= s[0] && a.CurCol <= s[1] {
			a.EdSel, a.EdAnchor = true, pos{a.CurLine, s[0]}
			a.setCursor(pos{a.CurLine, s[1]})
		}
	}
	return w, false
}

// addNextOccurrence selects the next place the selected text is, after the last selection, round the end of the file.
func (m *Model) addNextOccurrence() {
	a := m.st.App
	text, selected := m.selectedWordOrText()
	if text == "" {
		m.st.Message = "select some text, or put the cursor on a word"
		return
	}
	if !selected {
		return // the word is selected now; ⌃d again adds the next one
	}
	from, to, _ := a.selRange()
	taken := map[span]bool{{from, to}: true}
	for _, s := range a.Extra {
		taken[s] = true
	}
	// search after the last selection
	last := to
	for _, s := range a.Extra {
		if before(last, s.to) {
			last = s.to
		}
	}
	n := len(a.FileLines)
	tr := []rune(text)
	for i := 0; i <= n; i++ {
		l := (last.l + i) % n
		r := []rune(a.FileLines[l])
		start := 0
		if i == 0 {
			start = last.c
		}
		for c := start; c+len(tr) <= len(r); c++ {
			if string(r[c:c+len(tr)]) != text {
				continue
			}
			s := span{pos{l, c}, pos{l, c + len(tr)}}
			if taken[s] {
				continue
			}
			a.Extra = append(a.Extra, span{from, to}) // the old primary becomes an extra
			a.EdSel, a.EdAnchor = true, s.from
			a.setCursor(s.to)
			m.keepCursorShown()
			m.st.Message = fmt.Sprintf("%d selections · typing changes them all · esc keeps one", len(a.Extra)+1)
			return
		}
	}
	m.st.Message = "no more occurrences of " + text
}

// allSelections is every selection in reading order: the extras and the primary.
func (a *AppState) allSelections() []span {
	from, to, ok := a.selRange()
	out := append([]span(nil), a.Extra...)
	if ok {
		out = append(out, span{from, to})
	} else {
		out = append(out, span{a.cursor(), a.cursor()})
	}
	sort.Slice(out, func(i, j int) bool { return before(out[i].from, out[j].from) })
	return out
}

// editAll puts text in place of every selection (or at every cursor), from the last to the first.
func (m *Model) editAll(kind, text string) {
	a := m.st.App
	all := a.allSelections()
	m.rememberEdit(kind)
	var ends []pos
	for i := len(all) - 1; i >= 0; i-- {
		ends = append([]pos{a.replaceRange(all[i].from, all[i].to, text)}, ends...)
	}
	// the cursors stay at the end of each change: extras as empty spans, the primary last. Each change was applied
	// after the ones that follow it in the file, so an end on a line is moved by the earlier changes on that line,
	// which came after it.
	a.Extra = nil // a new slice: all still reads the old one
	grow := len([]rune(text))
	for i, e := range ends {
		for j := 0; j < i; j++ {
			if all[j].from.l == all[i].from.l && all[j].to.l == all[j].from.l {
				e.c += grow - (all[j].to.c - all[j].from.c)
			}
		}
		if i < len(ends)-1 {
			a.Extra = append(a.Extra, span{e, e})
		} else {
			a.EdSel = false
			a.setCursor(e)
		}
	}
}

// multiKey handles the keys that act on every selection while there are several; false otherwise.
func (m *Model) multiKey(k tea.KeyMsg) bool {
	a := m.st.App
	if len(a.Extra) == 0 {
		return false
	}
	switch k.String() {
	case "esc":
		a.Extra = nil
		return true
	case "backspace":
		if _, _, ok := a.selRange(); ok {
			m.editAll("delete", "")
			return true
		}
		// nothing selected at the cursors: the character before each
		all := a.allSelections()
		a.Extra = nil
		m.rememberEdit("delete")
		for i := len(all) - 1; i >= 0; i-- {
			p := all[i].from
			if p.c > 0 {
				a.replaceRange(pos{p.l, p.c - 1}, p, "")
				all[i].from.c--
			}
		}
		for _, s := range all[:len(all)-1] {
			a.Extra = append(a.Extra, span{s.from, s.from})
		}
		a.EdSel = false
		a.setCursor(all[len(all)-1].from)
		return true
	case "delete", "ctrl+d":
		if _, _, ok := a.selRange(); ok {
			m.editAll("delete", "")
		}
		return true
	case "enter":
		m.editAll("newline", "\n")
		return true
	case "up", "down", "left", "right", "home", "end", "pgup", "pgdown":
		a.Extra = nil // a move keeps the primary only
		return false
	}
	if k.Type == tea.KeyRunes || k.Type == tea.KeySpace {
		m.editAll("type", string(k.Runes))
		return true
	}
	return false
}

// rename (F2) selects every whole-word occurrence of the word at the cursor and asks for its new name.
func (m *Model) rename() {
	a := m.st.App
	word, _ := m.selectedWordOrText()
	if word == "" {
		m.st.Message = "put the cursor on a name to rename"
		return
	}
	a.Extra = nil
	var all []span
	for l, line := range a.FileLines {
		for _, s := range wordSpans(line, word) {
			all = append(all, span{pos{l, s[0]}, pos{l, s[1]}})
		}
	}
	if len(all) == 0 {
		return
	}
	a.Extra = all[:len(all)-1]
	a.EdSel, a.EdAnchor = true, all[len(all)-1].from
	a.setCursor(all[len(all)-1].to)
	a.CodeAsk = &codeAsk{kind: "rename-word", label: fmt.Sprintf("rename %s (%d places) to", word, len(all)), text: word}
}

// renameTo puts the new name in every selected place (⏎ in the rename question).
func (m *Model) renameTo(name string) {
	name = strings.TrimSpace(name)
	if name == "" {
		m.st.App.Extra = nil
		return
	}
	n := len(m.st.App.Extra) + 1
	m.editAll("rename", name)
	m.st.Message = fmt.Sprintf("renamed in %d places", n)
}

// extraSelected lights the extra selections on a drawn line of the file.
func extraSelected(st State, text string, i int) string {
	a := st.App
	for _, s := range a.Extra {
		if s.from.l != i {
			continue
		}
		d0 := displayCol(a.FileLines[i], s.from.c, tabSize(st))
		d1 := displayCol(a.FileLines[i], s.to.c, tabSize(st))
		if d1 == d0 {
			d1 = d0 + 1 // an empty extra: its cursor cell
		}
		text = selectCells(st, text, d0, d1)
	}
	return text
}
