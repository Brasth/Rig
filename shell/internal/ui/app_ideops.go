package ui

import (
	"fmt"
	"regexp"
	"strconv"
	"strings"
	"unicode"

	tea "github.com/charmbracelet/bubbletea"
)

// More of an IDE's editing: lines moved and copied up and down (⌥↑ ⌥↓, ⇧⌥↑ ⇧⌥↓), a line selected (⌃l), the
// matching bracket lit and jumped to (⌃]), the word under the cursor lit where else it is, find and replace (⌃r),
// how many matches find has, and the file's symbols to go to (⌃o).

// canEdit is whether the open file can be edited here: a click in it starts editing, as in an IDE.
func canEdit(a *AppState) bool {
	return a.File != "" && a.FileLines != nil && a.FileErr == "" && !a.FileTruncated && !a.Selecting &&
		previewKind(a.File) != "binary" && !(a.Preview && previewKind(a.File) != "")
}

// shiftLines puts the selection's anchor and the cursor n lines further.
func (a *AppState) shiftLines(n int) {
	if a.EdSel {
		a.EdAnchor.l += n
	}
	a.CurLine += n
}

// moveLines moves the cursor's line, or the selected ones, one line up (dir -1) or down (1).
func (m *Model) moveLines(dir int) {
	a := m.st.App
	l0, l1 := a.lineSpan()
	if (dir < 0 && l0 == 0) || (dir > 0 && l1 >= len(a.FileLines)-1) {
		return
	}
	m.rememberEdit("move")
	lines := append([]string(nil), a.FileLines...)
	block := append([]string(nil), lines[l0:l1+1]...)
	if dir < 0 {
		copy(lines[l0-1:], block)
		lines[l1] = a.FileLines[l0-1]
	} else {
		lines[l0] = a.FileLines[l1+1]
		copy(lines[l0+1:], block)
	}
	a.FileLines = lines
	a.Dirty = linesID(lines) != a.SavedID
	a.shiftLines(dir)
}

// copyLines puts a copy of the cursor's line, or the selected ones, below them (dir 1, the cursor going with the
// copy) or above (dir -1, the cursor staying on the first).
func (m *Model) copyLines(dir int) {
	a := m.st.App
	l0, l1 := a.lineSpan()
	m.rememberEdit("copy")
	n := l1 - l0 + 1
	lines := make([]string, 0, len(a.FileLines)+n)
	lines = append(lines, a.FileLines[:l1+1]...)
	lines = append(lines, a.FileLines[l0:l1+1]...)
	lines = append(lines, a.FileLines[l1+1:]...)
	a.FileLines = lines
	a.Dirty = linesID(lines) != a.SavedID
	if dir > 0 {
		a.shiftLines(n)
	}
}

// selectLine selects the cursor's line (⌃l), and the next one on each ⌃l more.
func (m *Model) selectLine() {
	a := m.st.App
	if !a.EdSel {
		a.EdSel, a.EdAnchor = true, pos{a.CurLine, 0}
	}
	if a.CurLine+1 < len(a.FileLines) {
		a.setCursor(pos{a.CurLine + 1, 0})
	} else {
		a.setCursor(pos{a.CurLine, len([]rune(a.FileLines[a.CurLine]))})
	}
}

var closers = map[rune]rune{')': '(', ']': '[', '}': '{'}

// matchBracket is the bracket at the cursor (or just before it) and the one that pairs with it.
func matchBracket(lines []string, at pos) (pos, pos, bool) {
	if at.l < 0 || at.l >= len(lines) {
		return pos{}, pos{}, false
	}
	line := []rune(lines[at.l])
	for _, c := range []int{at.c, at.c - 1} {
		if c < 0 || c >= len(line) {
			continue
		}
		ch := line[c]
		open, isOpen := pairs[ch], strings.ContainsRune("([{", ch)
		shut, isShut := closers[ch]
		if !isOpen && !isShut {
			continue
		}
		depth, here := 0, pos{at.l, c}
		for l, n := at.l, 0; l >= 0 && l < len(lines) && n < 5000; n++ {
			r := []rune(lines[l])
			from, to, step := 0, len(r), 1
			if l == at.l {
				from = c
			}
			if isShut {
				from, to, step = len(r)-1, -1, -1
				if l == at.l {
					from = c
				}
			}
			for i := from; i != to; i += step {
				switch {
				case isOpen && r[i] == ch, isShut && r[i] == ch:
					depth++
				case isOpen && r[i] == open, isShut && r[i] == shut:
					depth--
				}
				if depth == 0 {
					return here, pos{l, i}, true
				}
			}
			l += step
		}
		return pos{}, pos{}, false
	}
	return pos{}, pos{}, false
}

// wordAt is the word the cursor is in, for lighting it where else it is in the file.
func wordAt(line string, c int) string {
	r := []rune(line)
	word := func(x rune) bool { return unicode.IsLetter(x) || unicode.IsDigit(x) || x == '_' }
	from, to := min(c, len(r)), min(c, len(r))
	for from > 0 && word(r[from-1]) {
		from--
	}
	for to < len(r) && word(r[to]) {
		to++
	}
	if to-from < 2 {
		return ""
	}
	return string(r[from:to])
}

// wordSpans are where a whole word is in a line, as rune spans.
func wordSpans(line, w string) [][2]int {
	if w == "" {
		return nil
	}
	r, wr := []rune(line), []rune(w)
	word := func(x rune) bool { return unicode.IsLetter(x) || unicode.IsDigit(x) || x == '_' }
	var out [][2]int
	for i := 0; i+len(wr) <= len(r); i++ {
		if string(r[i:i+len(wr)]) != w || (i > 0 && word(r[i-1])) || (i+len(wr) < len(r) && word(r[i+len(wr)])) {
			continue
		}
		out = append(out, [2]int{i, i + len(wr)})
		i += len(wr) - 1
	}
	return out
}

// matchCount is how many times find's text is in the file, and which of them the cursor is at (0: none).
func matchCount(a *AppState, text string) (int, int) {
	if text == "" {
		return 0, 0
	}
	low, n, at := []rune(strings.ToLower(text)), 0, 0
	here := a.findAt
	if a.Editing {
		if from, _, ok := a.selRange(); ok {
			here = from
		}
	}
	for l, line := range a.FileLines {
		r := []rune(strings.ToLower(line))
		for c := 0; c+len(low) <= len(r); c++ {
			if string(r[c:c+len(low)]) == string(low) {
				n++
				if (pos{l, c}) == here {
					at = n
				}
				c += len(low) - 1
			}
		}
	}
	return n, at
}

// replaceOne replaces the match that is selected, if one is, and goes to the next; all replaces every match.
func (m *Model) replaceOne(find, with string, all bool) {
	a := m.st.App
	if find == "" {
		return
	}
	if all {
		m.rememberEdit("replace")
		low, count := strings.ToLower(find), 0
		lines := make([]string, len(a.FileLines))
		for i, line := range a.FileLines {
			var b strings.Builder
			r, lr, fr := []rune(line), []rune(strings.ToLower(line)), []rune(low)
			for c := 0; c < len(r); {
				if c+len(fr) <= len(r) && string(lr[c:c+len(fr)]) == low {
					b.WriteString(with)
					c += len(fr)
					count++
					continue
				}
				b.WriteRune(r[c])
				c++
			}
			lines[i] = b.String()
		}
		if count == 0 {
			a.Undo = a.Undo[:len(a.Undo)-1]
			m.st.Message = "no " + find + " in the file"
			return
		}
		a.FileLines, a.EdSel = lines, false
		a.Dirty = linesID(lines) != a.SavedID
		a.setCursor(a.cursor())
		m.st.Message = fmt.Sprintf("replaced %d", count)
		return
	}
	if from, to, ok := a.selRange(); ok && strings.EqualFold(a.textOf(from, to), find) {
		m.edit("replace", with)
	}
	m.findNext(find, false)
}

var (
	symType   = regexp.MustCompile(`^\s*(?:export\s+)?(?:pub(?:\([^)]*\))?\s+)?(?:type|interface|struct|enum|trait|impl|class)\s+([A-Za-z_]\w*)`)
	symMethod = regexp.MustCompile(`^\s+(?:(?:public|private|protected|static|async|override)\s+)*([A-Za-z_]\w*)\s*\([^)]*\)\s*(?::\s*[\w<>\[\]|, ]+)?\s*\{\s*$`)
	notMethod = map[string]bool{"if": true, "for": true, "while": true, "switch": true, "catch": true, "function": true, "return": true}
)

// symbols are the file's functions, types and blocks, for ⌃o: a name and the line it starts on.
func symbols(lines []string) []paletteItem {
	var out []paletteItem
	for i, l := range lines {
		name := ""
		for _, re := range []*regexp.Regexp{symGo, symFunc, symType, symConst} {
			if m := re.FindStringSubmatch(l); m != nil {
				name = m[1]
				break
			}
		}
		if name == "" {
			if m := symCall.FindStringSubmatch(l); m != nil && opensBlock(l) {
				name = m[1] + "(" + m[2] + ")"
			} else if m := symMethod.FindStringSubmatch(l); m != nil && !notMethod[m[1]] {
				name = m[1]
			}
		}
		if name != "" {
			out = append(out, paletteItem{"symbols", name, "line " + strconv.Itoa(i+1), "line:" + strconv.Itoa(i+1)})
		}
	}
	return out
}

// symbolItems are the palette in ⌃o: the file's symbols that fit the query.
func symbolItems(st State, q string) []paletteItem {
	var out []paletteItem
	for _, it := range symbols(st.App.FileLines) {
		if q == "" || strings.Contains(strings.ToLower(it.Label), q) {
			out = append(out, it)
		}
	}
	if len(out) == 0 {
		out = append(out, paletteItem{"symbols", "no symbols", "in " + st.App.File, ""})
	}
	return out
}

// goToLine shows line n (from 1) of the open file: the cursor goes there while editing.
func (m *Model) goToLine(n int) {
	a := m.st.App
	if len(a.FileLines) == 0 {
		return
	}
	n = max(0, min(n, len(a.FileLines))-1)
	if a.Editing {
		m.moveTo(pos{n, 0}, false)
		m.keepCursorShown()
		return
	}
	a.FileOff = max(0, n-3)
	a.findAt = pos{n, 0}
}

// ideKey is the editing keys this file adds; false when the key is not one of them.
func (m *Model) ideKey(k tea.KeyMsg) bool {
	a := m.st.App
	switch k.String() {
	case "alt+up":
		m.moveLines(-1)
	case "alt+down":
		m.moveLines(1)
	case "alt+shift+up", "shift+alt+up":
		m.copyLines(-1)
	case "alt+shift+down", "shift+alt+down":
		m.copyLines(1)
	case "ctrl+l":
		m.selectLine()
	case "ctrl+]":
		if _, to, ok := matchBracket(a.FileLines, a.cursor()); ok {
			m.moveTo(to, false)
		} else {
			m.st.Message = "no bracket at the cursor"
		}
	case "alt+delete", "alt+d":
		if a.EdSel {
			m.edit("delete", "")
		} else {
			m.rememberEdit("delete")
			a.replaceRange(a.cursor(), pos{a.CurLine, wordRight([]rune(a.FileLines[a.CurLine]), a.CurCol)}, "")
		}
	case "ctrl+r":
		a.CodeAsk = &codeAsk{kind: "find", label: "replace", text: a.Find}
		if from, to, ok := a.selRange(); ok && from.l == to.l {
			a.CodeAsk.text = a.textOf(from, to)
		}
		a.CodeAsk.replace = true
	case "ctrl+o":
		a.Palette = &Palette{Symbols: true}
	default:
		return false
	}
	m.keepCursorShown()
	return true
}
