package ui

import (
	"regexp"
	"sort"
	"strings"
	"unicode"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
)

// Word completion while editing, as an IDE's suggest widget: after two letters of a word, the words of the file that
// start the same way appear in a small list under the cursor, the likeliest first; ⇥ or ⏎ takes the chosen one, ↑↓
// choose, esc dismisses it for this word, and any other key (a space, a move) closes it. There is no language
// server: the candidates are the words already in the file.

type completion struct {
	start  pos      // where the word being typed begins
	prefix string   // what is typed of it so far
	items  []string // the words that start with it, the likeliest first
	sel    int
}

const completeMax = 8

var wordRe = regexp.MustCompile(`[\p{L}_$][\p{L}\p{N}_$]*`)

// wordBefore is the word the cursor ends, and where it begins on the line.
func wordBefore(line []rune, c int) (start int, prefix string) {
	c = min(c, len(line))
	start = c
	for start > 0 && (unicode.IsLetter(line[start-1]) || unicode.IsDigit(line[start-1]) || line[start-1] == '_' || line[start-1] == '$') {
		start--
	}
	return start, string(line[start:c])
}

// candidates are the file's words that start with prefix, except the one being typed: the commonest first, then by
// name; the ones in the prefix's own case before the others.
func candidates(lines []string, prefix string, typing pos) []string {
	count := map[string]int{}
	lower := strings.ToLower(prefix)
	for i, l := range lines {
		for _, loc := range wordRe.FindAllStringIndex(l, -1) {
			w := l[loc[0]:loc[1]]
			if i == typing.l && len([]rune(l[:loc[0]])) == typing.c {
				continue // the word under the cursor
			}
			if len(w) <= len(prefix) || !strings.HasPrefix(strings.ToLower(w), lower) {
				continue
			}
			count[w]++
		}
	}
	out := make([]string, 0, len(count))
	for w := range count {
		out = append(out, w)
	}
	sort.Slice(out, func(i, j int) bool {
		a, b := out[i], out[j]
		ca, cb := strings.HasPrefix(a, prefix), strings.HasPrefix(b, prefix)
		if ca != cb {
			return ca
		}
		if count[a] != count[b] {
			return count[a] > count[b]
		}
		return a < b
	})
	if len(out) > completeMax {
		out = out[:completeMax]
	}
	return out
}

// refreshCompletion shows the list for the word the cursor ends, or hides it.
func (m *Model) refreshCompletion() {
	a := m.st.App
	a.Complete = nil
	if len(a.Extra) > 0 || a.EdSel || a.CurLine >= len(a.FileLines) {
		return
	}
	start, prefix := wordBefore([]rune(a.FileLines[a.CurLine]), a.CurCol)
	at := pos{a.CurLine, start}
	if len([]rune(prefix)) < 2 || (at == a.noComplete && strings.HasPrefix(prefix, a.noCompleteWord)) {
		return
	}
	items := candidates(a.FileLines, prefix, at)
	if len(items) == 0 {
		return
	}
	a.Complete = &completion{start: at, prefix: prefix, items: items}
}

// completeKey is a key while the list shows; false when the key is not the list's (the list closes and the key goes on).
func (m *Model) completeKey(k tea.KeyMsg) bool {
	a := m.st.App
	c := a.Complete
	switch k.String() {
	case "tab", "enter":
		word := c.items[clampSel(c.sel, len(c.items))]
		a.Complete = nil
		m.edit("type", strings.TrimPrefix(word, c.prefix))
		if !strings.HasPrefix(word, c.prefix) {
			// another case: the whole word in place of what was typed
			m.rememberEdit("type")
			a.setCursor(a.replaceRange(c.start, a.cursor(), word))
		}
		return true
	case "down":
		c.sel = clampSel(c.sel+1, len(c.items))
		return true
	case "up":
		c.sel = clampSel(c.sel-1, len(c.items))
		return true
	case "esc":
		a.noComplete, a.noCompleteWord = c.start, c.prefix
		a.Complete = nil
		return true
	}
	a.Complete = nil
	return false
}

// completionBox is the list as drawn under the cursor: the rows, and where they go in the editor's column.
func completionBox(st State, cursorRow, cursorCol, room, w int) (box []string, x, y int) {
	c := st.App.Complete
	if c == nil {
		return nil, 0, 0
	}
	bw := 8
	for _, it := range c.items {
		bw = max(bw, len([]rune(it))+2)
	}
	bw = min(bw, max(8, w-cursorCol-1))
	sel := clampSel(c.sel, len(c.items))
	for i, it := range c.items {
		row := fit(" "+it, bw)
		switch {
		case i == sel && st.Styled:
			row = tint(lipgloss.NewStyle().Bold(true).Render(row), colSelBg)
		case i == sel:
			row = ">" + row[1:]
		case st.Styled:
			row = tint(row, colRaised)
		}
		box = append(box, row)
	}
	x = max(0, cursorCol-len([]rune(c.prefix)))
	y = cursorRow + 1
	if y+len(box) > room && cursorRow-len(box) >= 2 {
		y = cursorRow - len(box) // no room below: above the line
	}
	return box, x, y
}
