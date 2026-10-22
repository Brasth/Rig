package ui

import (
	"encoding/hex"
	"fmt"
	"regexp"
	"strconv"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"
)

// The mouse, as in codex and grok: the screens mark what can be clicked with zero-width zones, the frame takes the
// marks out, draws the zone under the pointer underlined, and a click on a zone does what its key or row would.

// wheelStep is the lines a wheel event scrolls; a trackpad sends many small ones, so it stays small.
const wheelStep = 2

const (
	zoneOpen  = "\x1b_rz=" // an APC string: no width, kept whole by the ansi helpers, never reaches the terminal
	zoneClose = "\x1b_rz\x1b\\"
	zoneEnd   = "\x1b\\"
)

// zone marks s as a clickable place named id: "key:⏎" presses that key, "tab:2" opens a tab, "row:palette:3" picks a
// row, and "layer:panel" is an overlay a click outside of closes.
func (st State) zone(id, s string) string {
	if !st.Mouse || id == "" {
		return s
	}
	return zoneOpen + hex.EncodeToString([]byte(id)) + zoneEnd + s + zoneClose // hex: the parser ends an APC at non-ASCII
}

// hint is a footer's "key meaning", clickable as its key.
func (st State) hint(p [2]string) string {
	return st.zone("key:"+p[0], st.dim(p[0])+" "+st.faint(p[1]))
}

type hitZone struct {
	y, x0, x1, depth int
	id               string
}

type zoneMark struct {
	at   int // the byte in the plain line
	zone int
	open bool
}

// zonesOf takes the marks out of a frame and says where each zone is; the zone under (hx, hy) is drawn underlined.
func zonesOf(lines []string, hx, hy int) ([]string, []hitZone) {
	var zones []hitZone
	out := make([]string, len(lines))
	for y, line := range lines {
		if !strings.Contains(line, "\x1b_rz") {
			out[y] = line
			continue
		}
		var b strings.Builder
		var marks []zoneMark
		var stack []int
		x, rest := 0, line
		for {
			at := strings.Index(rest, "\x1b_rz")
			if at < 0 {
				b.WriteString(rest)
				break
			}
			b.WriteString(rest[:at])
			x += ansi.StringWidth(rest[:at])
			rest = rest[at:]
			if strings.HasPrefix(rest, zoneClose) {
				rest = rest[len(zoneClose):]
				if n := len(stack); n > 0 {
					zones[stack[n-1]].x1 = x
					marks = append(marks, zoneMark{b.Len(), stack[n-1], false})
					stack = stack[:n-1]
				}
				continue
			}
			end := strings.Index(rest, zoneEnd)
			if end < 0 {
				break
			}
			raw, _ := hex.DecodeString(strings.TrimPrefix(rest[:end], zoneOpen))
			id := string(raw)
			rest = rest[end+len(zoneEnd):]
			zones = append(zones, hitZone{y: y, x0: x, x1: -1, depth: len(stack), id: id})
			stack = append(stack, len(zones)-1)
			marks = append(marks, zoneMark{b.Len(), len(zones) - 1, true})
		}
		width := ansi.StringWidth(b.String())
		for _, z := range stack {
			zones[z].x1 = width
		}
		plain := b.String()
		if y == hy {
			plain = underlineHovered(plain, marks, zones, hx, hy)
		}
		out[y] = plain
	}
	return out, zones
}

// underlineHovered draws the clickable zone under the pointer underlined, through the resets inside it.
// wideRow is the widest zone a hover underlines: a button, a tab, a file name; a list's rows are wider.
const wideRow = 30

func underlineHovered(line string, marks []zoneMark, zones []hitZone, hx, hy int) string {
	z := innermost(zones, hx, hy)
	if z < 0 || strings.HasPrefix(zones[z].id, "layer:") || strings.HasPrefix(zones[z].id, "text:") {
		return line // an overlay or text to place the cursor in is not underlined
	}
	if strings.HasPrefix(zones[z].id, "row:") && zones[z].x1-zones[z].x0 > wideRow {
		return line // a whole row of a list: a line under all of it, its padding too, reads as a rule, not a hover
	}
	from, to := -1, len(line)
	for _, mk := range marks {
		if mk.zone == z && mk.open {
			from = mk.at
		}
		if mk.zone == z && !mk.open {
			to = mk.at
		}
	}
	if from < 0 {
		return line
	}
	// only the text is underlined, as a link is: not the padding around it, which would draw a rule across the row
	from, to = textSpan(line, from, to)
	if from >= to {
		return line
	}
	inner := sgrSeq.ReplaceAllStringFunc(line[from:to], func(s string) string { return s + "\x1b[4m" })
	return line[:from] + "\x1b[4m" + inner + "\x1b[24m" + line[to:]
}

// ansiSeq is an escape sequence in a drawn line: a CSI one (colours, styles) or an OSC or APC one.
var ansiSeq = regexp.MustCompile(`\x1b(?:\[[0-9;?]*[ -/]*[@-~]|[\]_][^\x1b\x07]*(?:\x07|\x1b\\))`)

// textSpan narrows line[from:to] to its visible text: from its first character that is not a space to the end of its
// last one, stepping over escape sequences.
func textSpan(line string, from, to int) (int, int) {
	first, last := -1, -1
	for i := from; i < to; {
		if line[i] == '\x1b' {
			if loc := ansiSeq.FindStringIndex(line[i:to]); loc != nil && loc[0] == 0 {
				i += loc[1]
				continue
			}
		}
		r, size := utf8.DecodeRuneInString(line[i:])
		if r != ' ' {
			if first < 0 {
				first = i
			}
			last = i + size
		}
		i += size
	}
	if first < 0 {
		return from, from
	}
	return first, last
}

// innermost is the deepest zone holding the cell, or -1.
func innermost(zones []hitZone, x, y int) int {
	best := -1
	for i, z := range zones {
		if z.y == y && x >= z.x0 && x < z.x1 && (best < 0 || z.depth >= zones[best].depth) {
			best = i
		}
	}
	return best
}

// hitAt is the zone a click at (x, y) lands on and the overlays it is inside.
func hitAt(zones []hitZone, x, y int) (string, []string) {
	var layers []string
	for _, z := range zones {
		if z.y == y && x >= z.x0 && x < z.x1 && strings.HasPrefix(z.id, "layer:") {
			layers = append(layers, z.id)
		}
	}
	if i := innermost(zones, x, y); i >= 0 && !strings.HasPrefix(zones[i].id, "layer:") {
		return zones[i].id, layers
	}
	return "", layers
}

// keyFor is the key a footer shows as a glyph: ⏎, ⇥, ⌃k, ⌥c, esc, space or one character.
func keyFor(glyph string) (tea.KeyMsg, bool) {
	named := map[string]tea.KeyType{"⏎": tea.KeyEnter, "enter": tea.KeyEnter, "esc": tea.KeyEsc, "⇥": tea.KeyTab,
		"tab": tea.KeyTab, "⇧⇥": tea.KeyShiftTab, "shift+tab": tea.KeyShiftTab, "pgup": tea.KeyPgUp, "pgdown": tea.KeyPgDown,
		"up": tea.KeyUp, "down": tea.KeyDown, "left": tea.KeyLeft, "right": tea.KeyRight, "backspace": tea.KeyBackspace}
	if glyph == "⌥⏎" {
		return tea.KeyMsg{Type: tea.KeyEnter, Alt: true}, true
	}
	if t, ok := named[glyph]; ok {
		return tea.KeyMsg{Type: t}, true
	}
	if glyph == "space" || glyph == "␣" || glyph == " " {
		return tea.KeyMsg{Type: tea.KeySpace, Runes: []rune{' '}}, true
	}
	r := []rune(glyph)
	switch {
	case len(r) == 2 && (r[0] == '⌃') && r[1] >= 'a' && r[1] <= 'z':
		return tea.KeyMsg{Type: tea.KeyCtrlA + tea.KeyType(r[1]-'a')}, true
	case strings.HasPrefix(glyph, "ctrl+") && len(glyph) == 6 && glyph[5] >= 'a' && glyph[5] <= 'z':
		return tea.KeyMsg{Type: tea.KeyCtrlA + tea.KeyType(glyph[5]-'a')}, true
	case len(r) == 2 && r[0] == '⌥':
		return tea.KeyMsg{Type: tea.KeyRunes, Runes: r[1:], Alt: true}, true
	case strings.HasPrefix(glyph, "alt+") && len([]rune(glyph)) == 5:
		return tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune(glyph)[4:], Alt: true}, true
	case len(r) == 1:
		return tea.KeyMsg{Type: tea.KeyRunes, Runes: r}, true
	}
	return tea.KeyMsg{}, false
}

// mouse is a click, the wheel, or the pointer moving over the app.
func (m *Model) mouse(e tea.MouseMsg) (tea.Model, tea.Cmd) {
	if !m.appOn() {
		return m, nil
	}
	switch {
	case e.Button == tea.MouseButtonWheelUp || e.Button == tea.MouseButtonWheelDown:
		m.hoverX, m.hoverY = e.X, e.Y // the wheel acts on what is under the pointer
		return m.wheel(e.Button == tea.MouseButtonWheelUp)
	case e.Action == tea.MouseActionMotion && e.Button == tea.MouseButtonNone:
		m.hoverX, m.hoverY = e.X, e.Y
		if a := m.st.App; a.Hover != nil && (e.X != a.Hover.x || e.Y != a.Hover.y) {
			if at, ok := m.codeAt(e.X, e.Y); !ok || at.l >= len(a.FileLines) || identAt(a.FileLines[at.l], at.c) != a.Hover.name {
				a.Hover = nil // off the name
			}
		}
		return m, m.hoverTick(e.X, e.Y)
	case e.Action == tea.MouseActionPress && e.Button == tea.MouseButtonMiddle:
		// a middle click on a tab closes it, as in a browser or an editor
		if id, _ := hitAt(m.zones, e.X, e.Y); strings.HasPrefix(id, "row:tabfile:") {
			return m, m.closeTabFile(strings.TrimPrefix(id, "row:tabfile:"))
		}
		return m, nil
	case e.Action == tea.MouseActionPress && e.Button == tea.MouseButtonLeft && (e.Ctrl || e.Alt):
		// ⌃ or ⌥ click on a name: go to where it is defined
		if _, ok := m.codeAt(e.X, e.Y); ok || m.codeZone(e.X, e.Y) {
			m.textPress(e.X, e.Y, false)
			m.codeDrag = false
			if m.st.App.Editing {
				return m, m.goToDefinition()
			}
		}
		return m, nil
	case e.Action == tea.MouseActionPress && e.Button == tea.MouseButtonLeft:
		if m.textPress(e.X, e.Y, e.Shift) {
			return m, nil // in the reply box or a file being edited: the cursor goes there, as in an editor
		}
		// a press only marks where: lifting the button there clicks, dragging away selects text
		cx0, cx1 := m.clipFor(e.X)
		m.press, m.sel = &textSel{ax: e.X, ay: e.Y, bx: e.X, by: e.Y, cx0: cx0, cx1: cx1}, nil
		if strings.HasPrefix(m.st.Message, "copied ") {
			m.st.Message = ""
		}
		return m, nil
	case e.Action == tea.MouseActionMotion && e.Button == tea.MouseButtonLeft && m.codeDrag:
		if at, ok := m.codeAt(e.X, e.Y); ok {
			a := m.st.App
			a.EdSel = true
			a.setCursor(at)
			if a.EdAnchor == a.cursor() {
				a.EdSel = false
			}
		}
		return m, nil
	case e.Action == tea.MouseActionRelease && m.codeDrag:
		m.codeDrag = false
		return m, nil
	case e.Action == tea.MouseActionMotion && e.Button == tea.MouseButtonLeft && m.boxDrag:
		if at, ok := m.boxAt(e.X, e.Y); ok {
			if at >= m.boxFrom {
				at = min(at+1, len([]rune(m.st.Prompt))) // dragging forward takes in the character under the pointer
			}
			m.st.PromptSel, m.st.PromptAnchor = true, m.boxFrom
			m.setPrompt(m.st.Prompt, at)
		}
		return m, nil
	case e.Action == tea.MouseActionMotion && e.Button == tea.MouseButtonLeft:
		if m.press != nil && (e.X != m.press.ax || e.Y != m.press.ay || m.sel != nil) {
			m.press.bx, m.press.by = e.X, e.Y
			m.sel, m.hoverX, m.hoverY = m.press, -1, -1
		}
		return m, nil
	case e.Action == tea.MouseActionRelease && m.boxDrag:
		m.boxDrag = false
		if s0, s1 := m.st.selection(); s1 > s0 {
			// what was selected in the box is also copied, as a selection in the screen is
			text := string([]rune(m.st.Prompt)[s0:s1])
			if Copy(text) {
				m.st.Message = fmt.Sprintf("copied %d characters", len([]rune(text)))
			}
		}
		return m, nil
	case e.Action == tea.MouseActionRelease:
		press := m.press
		m.press = nil
		switch {
		case press == nil:
		case m.sel != nil:
			// as in opencode and grok: what was dragged over is copied as the button comes up
			text := selectedText(m.plain, *m.sel)
			m.sel = nil // the footer says what was copied; the new frame carries the copy to the terminal
			if text != "" && Copy(text) {
				m.st.Message = fmt.Sprintf("copied %d characters", len([]rune(text)))
			}
		default:
			if m.clicks >= 2 {
				if _, onZone := m.zoneAt(press.ax, press.ay); !onZone {
					if text := m.wordOrLineAt(press, m.clicks); text != "" && Copy(text) {
						m.st.Message = fmt.Sprintf("copied %d characters", len([]rune(text)))
						return m, nil
					}
				}
			}
			m.hoverX, m.hoverY = press.ax, press.ay
			return m.click(press.ax, press.ay)
		}
	}
	return m, nil
}

// zoneAt is the zone under (x, y) and whether there is one.
func (m *Model) zoneAt(x, y int) (hitZone, bool) {
	if i := innermost(m.zones, x, y); i >= 0 {
		return m.zones[i], true
	}
	return hitZone{}, false
}

// boxAt is the place in the box's text under (x, y): text:box:<start>:<end> marks a row of it.
func (m *Model) boxAt(x, y int) (int, bool) {
	z, ok := m.zoneAt(x, y)
	if !ok || !strings.HasPrefix(z.id, "text:box:") {
		return 0, false
	}
	var start, end int
	if _, err := fmt.Sscanf(strings.TrimPrefix(z.id, "text:box:"), "%d:%d", &start, &end); err != nil {
		return 0, false
	}
	return start + min(max(0, x-z.x0), end-start), true
}

// textPress puts the cursor where the reply box or a file being edited was pressed; a second press there soon after
// selects the word under it. False when the press was elsewhere.
func (m *Model) textPress(x, y int, shift bool) bool {
	a := m.st.App
	if a.Palette != nil || a.Help || a.NewJob != nil || (a.Panel && !panelDocks(m.st, m.winW)) || m.st.Mode == ModeConfirm {
		return false
	}
	double := m.now().Sub(m.clickAt) < 400*time.Millisecond && m.clickX == x && m.clickY == y
	m.clicks = 1
	if double {
		m.clicks = m.clickN + 1
	}
	m.clickAt, m.clickX, m.clickY, m.clickN = m.now(), x, y, m.clicks
	if at, ok := m.boxAt(x, y); ok {
		m.sel = nil
		m.st.PromptSel = false
		m.setPrompt(m.st.Prompt, at)
		if double {
			r := []rune(m.st.Prompt)
			from, to := at, at
			for from > 0 && !unicode.IsSpace(r[from-1]) {
				from--
			}
			for to < len(r) && !unicode.IsSpace(r[to]) {
				to++
			}
			if to > from {
				m.st.PromptSel, m.st.PromptAnchor = true, from
				m.setPrompt(m.st.Prompt, to)
			}
			return true
		}
		m.boxDrag, m.boxFrom = true, at
		return true
	}
	z, ok := m.zoneAt(x, y)
	if !ok || !strings.HasPrefix(z.id, "text:code:") || !(a.Editing || canEdit(a)) {
		return false
	}
	line, err := strconv.Atoi(strings.TrimPrefix(z.id, "text:code:"))
	if err != nil || line < 0 || line >= len(a.FileLines) {
		return false
	}
	if !a.Editing {
		m.startEdit() // a click in the file edits it there, as in an IDE
		if !a.Editing {
			return false
		}
	}
	at := pos{line, runeAtCell(a.FileLines[line], x-z.x0+a.ColOff, tabSize(m.st))}
	m.moveTo(at, shift) // with Shift, the selection runs from where the cursor was
	if m.clicks >= 3 {
		// a third click selects the line
		a.EdSel, a.EdAnchor = true, pos{line, 0}
		if line+1 < len(a.FileLines) {
			a.setCursor(pos{line + 1, 0})
		} else {
			a.setCursor(pos{line, len([]rune(a.FileLines[line]))})
		}
		return true
	}
	if shift {
		m.codeDrag = true
		return true
	}
	if double {
		r := []rune(a.FileLines[line])
		from, to := at.c, at.c
		word := func(c rune) bool { return unicode.IsLetter(c) || unicode.IsDigit(c) || c == '_' }
		for from > 0 && word(r[from-1]) {
			from--
		}
		for to < len(r) && word(r[to]) {
			to++
		}
		if to > from {
			a.EdSel, a.EdAnchor = true, pos{line, from}
			a.setCursor(pos{line, to})
		}
		return true
	}
	m.codeDrag, a.EdAnchor = true, at
	return true
}

// codeZone is whether (x, y) is on a line of the open file.
func (m *Model) codeZone(x, y int) bool {
	z, ok := m.zoneAt(x, y)
	return ok && strings.HasPrefix(z.id, "text:code:")
}

// codeAt is the place in the file being edited under (x, y): text:code:<line> marks a line of it.
func (m *Model) codeAt(x, y int) (pos, bool) {
	a := m.st.App
	z, ok := m.zoneAt(x, y)
	if !ok || !strings.HasPrefix(z.id, "text:code:") {
		return pos{}, false
	}
	line, err := strconv.Atoi(strings.TrimPrefix(z.id, "text:code:"))
	if err != nil || line < 0 || line >= len(a.FileLines) {
		return pos{}, false
	}
	off := 0
	if a.Editing {
		off = a.ColOff
	}
	return pos{line, runeAtCell(a.FileLines[line], x-z.x0+off, tabSize(m.st))}, true
}

// runeAtCell is the rune of a line drawn at cell col, with its tabs as spaces.
func runeAtCell(line string, col, tab int) int {
	cell := 0
	for i, r := range []rune(line) {
		w := 1
		if r == '\t' {
			w = tab
		}
		if col < cell+w {
			return i
		}
		cell += w
	}
	return len([]rune(line))
}

// textSel is a stretch of the screen dragged over, from where the button went down (a) to where it is (b).
type textSel struct {
	ax, ay, bx, by int
	cx0, cx1       int // when cx1 > 0, only the cells of [cx0, cx1) are selected: the column the drag began in
}

// span is the selection as reading order: its first cell and its last, inclusive.
func (s textSel) span() (x0, y0, x1, y1 int) {
	if s.ay < s.by || (s.ay == s.by && s.ax <= s.bx) {
		return s.ax, s.ay, s.bx, s.by
	}
	return s.bx, s.by, s.ax, s.ay
}

// cols is the cells of row y the selection covers, as [from, to).
func (s textSel) cols(y, w int) (int, int, bool) {
	x0, y0, x1, y1 := s.span()
	if y < y0 || y > y1 {
		return 0, 0, false
	}
	from, to := 0, w
	if y == y0 {
		from = x0
	}
	if y == y1 {
		to = x1 + 1
	}
	if s.cx1 > 0 { // a drag that began in the conversation takes nothing from the panels beside it
		from, to = max(from, s.cx0), min(to, s.cx1)
	}
	return from, min(to, w), from < to
}

// selectedText is the selection's text from the frame as drawn: each row's cells, without the spaces that pad it.
func selectedText(plain []string, s textSel) string {
	var rows []string
	for y, line := range plain {
		from, to, ok := s.cols(y, ansi.StringWidth(line))
		if !ok {
			continue
		}
		rows = append(rows, strings.TrimRight(ansi.Cut(line, from, to), " "))
	}
	rows = undecorate(rows)
	// the conversation sits in the middle of the window: the margin all its rows share is not part of the text
	// (a first row begun mid-line has no margin: the rest share theirs)
	first := 0
	if x0, _, _, _ := s.span(); x0 > 0 && len(rows) > 1 {
		first = 1
	}
	indent := -1
	for _, r := range rows[first:] {
		if strings.TrimSpace(r) != "" {
			if n := len(r) - len(strings.TrimLeft(r, " ")); indent < 0 || n < indent {
				indent = n
			}
		}
	}
	for i := first; i < len(rows); i++ {
		if indent > 0 && len(rows[i]) >= indent {
			rows[i] = rows[i][indent:]
		}
	}
	return strings.Trim(strings.Join(rows, "\n"), "\n")
}

// paintSelection draws the selection in reverse video, through the colours inside it.
func paintSelection(lines []string, s textSel, w int) {
	for y, line := range lines {
		from, to, ok := s.cols(y, w)
		if !ok {
			continue
		}
		inner := sgrSeq.ReplaceAllStringFunc(ansi.Cut(line, from, to), func(q string) string { return q + "\x1b[7m" })
		lines[y] = ansi.Truncate(line, from, "") + "\x1b[7m" + inner + "\x1b[27m" + ansi.TruncateLeft(line, to, "")
	}
}

// wheel scrolls what the screen shows: the conversation, the diff and code, or the selection of a list.
func (m *Model) wheel(up bool) (tea.Model, tea.Cmd) {
	if a := m.st.App; m.appOn() && a.Term && m.pane != nil && m.hoverY >= m.winH-termRows(m.winH)-1 {
		if up {
			a.TermUp += wheelStep
		} else {
			a.TermUp = max(0, a.TermUp-wheelStep)
		}
		return m, nil
	}
	a := m.st.App
	if a.Splash > 0 || m.st.Mode == ModeConfirm {
		return m, nil
	}
	if m.wheelBox(up) {
		return m, nil
	}
	m.sel = nil // the text under a selection moves away
	list := a.Help || a.Welcome || a.Onboard != nil || a.Settings != nil || a.History != nil || a.Palette != nil ||
		a.Panel || a.Search != nil || problemScreen(m.st) != "" || a.NewJob != nil
	if !list && (a.Tab == 1 || a.Thread != "") {
		if up {
			m.scrollBy(wheelStep)
		} else {
			m.scrollBy(-wheelStep)
		}
		return m, nil
	}
	if !list && a.Tab == 3 && a.Preview && previewKind(a.File) != "" {
		return m, nil
	}
	if !list && a.Tab == 3 && !a.TreeFocus && a.File != "" && a.FileLines != nil {
		// the wheel scrolls the file and leaves the cursor where it is, as in an IDE
		if up {
			a.FileOff = max(0, a.FileOff-3)
		} else {
			a.FileOff = min(max(0, len(a.FileLines)-5), a.FileOff+3)
		}
		return m, nil
	}
	k := tea.KeyMsg{Type: tea.KeyDown}
	if up {
		k.Type = tea.KeyUp
	}
	steps := 1
	if !list {
		steps = 3 // the diff and the code move three lines a notch, as a terminal scrolls
	}
	var cmds []tea.Cmd
	for i := 0; i < steps; i++ {
		_, cmd := m.appKey(k)
		cmds = append(cmds, cmd)
	}
	return m, tea.Batch(cmds...)
}

// wheelBox scrolls the reply box when the pointer is over a draft taller than the box shows; false when it is not.
func (m *Model) wheelBox(up bool) bool {
	a := m.st.App
	if a.BoxTotal <= boxMostRows || a.Palette != nil {
		return false
	}
	if z, ok := m.zoneAt(m.hoverX, m.hoverY); !ok || !strings.HasPrefix(z.id, "text:box:") {
		return false
	}
	top := a.BoxTop + 2
	if up {
		top = a.BoxTop - 2
	}
	m.st.PromptTop, m.st.PromptScrolled, m.st.PromptTopAt = max(0, min(top, a.BoxTotal-boxMostRows)), true, m.st.caret()
	return true
}

// click does what the zone under the pointer stands for; a click outside an open overlay closes it.
func (m *Model) click(x, y int) (tea.Model, tea.Cmd) {
	a := m.st.App
	if a.Splash > 0 {
		a.Splash = 0
		return m, nil
	}
	id, layers := hitAt(m.zones, x, y)
	in := func(layer string) bool {
		for _, l := range layers {
			if l == "layer:"+layer {
				return true
			}
		}
		return false
	}
	if m.st.Mode != ModeConfirm {
		for _, o := range []struct {
			open  bool
			layer string
			close func()
		}{
			{a.Help, "help", func() { a.Help = false }},
			{a.Palette != nil, "palette", func() { a.Palette = nil }},
			{a.NewJob != nil && a.Palette == nil, "newjob", func() { a.NewJob = nil }},
			{a.Panel, "panel", func() { a.Panel = false }},
		} {
			if o.open && !in(o.layer) {
				o.close()
				return m, nil
			}
		}
	}
	kind, arg, _ := strings.Cut(id, ":")
	switch kind {
	case "key":
		if k, ok := keyFor(arg); ok {
			return m.key(k)
		}
	case "tab":
		n, _ := strconv.Atoi(arg)
		return m, m.openTab(n)
	case "row":
		list, at, _ := strings.Cut(arg, ":")
		return m.clickRow(list, at)
	}
	return m, nil
}

// openTab is the top bar's conversation, changes and code.
func (m *Model) openTab(n int) tea.Cmd {
	a := m.st.App
	if n < 1 || n > 3 {
		return nil
	}
	a.Search, a.Thread, a.Selecting = nil, "", false
	if n == 3 && a.Tab == 3 && a.Preview {
		a.Preview = false
	}
	a.Tab = n
	if n == 1 {
		return nil
	}
	return tea.Batch(m.fetchDock(true), m.fetchDiff(true), m.fetchChanged(), m.loadFiles())
}

// clickRow picks a list's row as its arrows would, and opens it as ⏎ would.
func (m *Model) clickRow(list, at string) (tea.Model, tea.Cmd) {
	a := m.st.App
	i, _ := strconv.Atoi(at)
	enter := tea.KeyMsg{Type: tea.KeyEnter}
	switch list {
	case "palette":
		if a.Palette != nil {
			a.Palette.Sel = i
			return m.appKey(enter)
		}
	case "slash":
		m.st.SlashSel = i
		return m.appKey(enter)
	case "panel":
		// a helper in the panel, floating or docked: its thread
		if jobs := panelJobs(m.view); i < len(jobs) {
			a.PanelSel = i
			a.Thread, a.Panel, m.st.LogUp = jobs[i].ID, false, 0
			m.st.Log, m.st.LogE, m.lgLast = nil, "", time.Time{}
			return m, m.fetchLog(true)
		}
	case "welcome":
		a.WelcomeSel = i
		return m.appKey(enter)
	case "history":
		if a.History != nil {
			again := a.History.Sel == i
			a.History.Sel = i
			if again {
				return m.appKey(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{'d'}}) // a second click shows its changes
			}
		}
	case "search":
		if a.Search != nil {
			a.Search.Sel = i
			return m.appKey(enter)
		}
	case "section":
		if sc := a.Settings; sc != nil {
			if at == "adv" {
				toggleAdvanced(sc)
				return m, nil
			}
			sc.Section, sc.Row, sc.Pick = i, 0, ""
		}
	case "setting":
		if sc := a.Settings; sc != nil {
			again := sc.Row == i && sc.Pick == ""
			sc.Row, sc.Pick = i, ""
			rows := settingRows(m.st, SettingsSections[sc.Section])
			if again && i < len(rows) && rows[i].Toggle {
				return m.appKey(enter) // a second click on an on/off row turns it over
			}
		}
	case "choice":
		// row:choice:<row>=<value> picks the value and applies it, as ←→ then ⏎ would
		if sc := a.Settings; sc != nil {
			row, value, _ := strings.Cut(at, "=")
			n, _ := strconv.Atoi(row)
			rows := settingRows(m.st, SettingsSections[sc.Section])
			if n < len(rows) {
				sc.Row, sc.Pick = n, ""
				if value != rows[n].Value || rows[n].Toggle {
					if !rows[n].Toggle {
						sc.Pick = value
					}
					return m.appKey(enter)
				}
			}
		}
	case "ship":
		if a.ShipScreen != nil {
			a.ShipScreen.Push = at == "push"
		}
	case "difffile":
		a.DiffFile, a.DiffOff, a.DiffHunk = i, 0, 0
	case "diffhunk":
		a.DiffHunk = i
	case "helper":
		if o := a.Onboard; o != nil {
			o.HelperSel = i
			return m.appKey(tea.KeyMsg{Type: tea.KeySpace, Runes: []rune{' '}})
		}
	case "sandbox":
		if o := a.Onboard; o != nil {
			again := o.SandboxSel == i
			o.SandboxSel = i
			if again {
				return m.appKey(enter)
			}
		}
	case "installagent":
		a.InstallSel = i
	case "open":
		return m, m.runPalette(paletteItem{Action: at}, "") // the top bar's parent and settings
	case "answer":
		return m.answer(at == "allow")
	case "suggest":
		if i < len(Suggestions) {
			return m, m.appSend(Suggestions[i])
		}
	case "card":
		m.selectJob(at)
		a.Tab = 2
		return m, tea.Batch(m.fetchDock(true), m.fetchDiff(true))
	case "install":
		if sc := a.Settings; sc != nil {
			sc.Row, sc.Pick = i, ""
			return m.appKey(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{'i'}})
		}
	case "file", "tabfile":
		return m, m.runPalette(paletteItem{Action: "file:" + at}, "")
	case "tabclose":
		return m, m.closeTabFile(at)
	case "tree":
		for _, r := range treeRows(a) {
			if r.path == at {
				a.TreeFocus = false
				return m, m.openTree(r.path, r.dir)
			}
		}
	case "next":
		return m, m.nextAction(at) // the bar under a turn that changed files: review, commit or undo
	case "gitbtn":
		// the git view's buttons and its branch and message box: each does what its key does
		key := map[string]string{"commit": "c", "pull": "P", "push": "p", "branch": "b"}[at]
		if key == "" {
			return m, nil
		}
		a.GitView, a.TreeFocus = true, true
		return m.gitKey(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune(key)})
	case "side":
		// the side bar's heads: the files, git, or the outline
		switch at {
		case "git":
			return m, m.openGit()
		case "outline":
			a.GitView, a.GitDiff = false, nil
			m.toggleOutline()
			return m, nil
		}
		a.GitView, a.GitDiff, a.Outline = false, nil, false
	case "outline":
		if rows := outlineRows(a); i < len(rows) {
			n, _ := strconv.Atoi(strings.TrimPrefix(rows[i].Action, "line:"))
			a.OutlineSel = i
			m.goToLine(n)
		}
	case "git":
		// a changed file shows its diff; a click on the chosen one again stages or unstages it
		rows := gitRows(a)
		if i >= len(rows) {
			return m, nil
		}
		again := a.TreeFocus && a.GitView && a.GitSel == i
		a.GitView, a.TreeFocus, a.GitSel = true, true, i
		if rows[i].kind == "commit" {
			return m, m.fetchCommitDiff(rows[i].c.hash, "")
		}
		if rows[i].file.code == 'U' {
			a.TreeFocus = false
			return m, m.openConflicts(rows[i].file.path)
		}
		if again {
			return m, m.toggleStage(rows[i])
		}
		return m, m.fetchGitDiff(rows[i].file.path, rows[i].kind == "staged")
	}
	return m, nil
}

// clipFor is the cells a selection that begins at column x may take: the conversation's column when x is inside it, else all.
func (m *Model) clipFor(x int) (int, int) {
	if a := m.st.App; a != nil && a.ChatW > 0 && x >= a.ChatX && x < a.ChatX+a.ChatW {
		return a.ChatX, a.ChatX + a.ChatW
	}
	return 0, 0
}

// wordOrLineAt is the text of the word (two clicks) or the whole line (three) under the press, within its column, as drawn.
func (m *Model) wordOrLineAt(p *textSel, clicks int) string {
	if p.ay < 0 || p.ay >= len(m.plain) {
		return ""
	}
	var cells []rune // one rune per cell: a wide rune fills the cells after its first with a blank
	for _, r := range m.plain[p.ay] {
		cells = append(cells, r)
		for i := 1; i < ansi.StringWidth(string(r)); i++ {
			cells = append(cells, ' ')
		}
	}
	lo, hi := 0, len(cells)
	if p.cx1 > 0 {
		lo, hi = max(0, p.cx0), min(len(cells), p.cx1)
	}
	if p.ax < lo || p.ax >= hi {
		return ""
	}
	from, to := lo, hi
	if clicks == 2 {
		if cells[p.ax] == ' ' {
			return ""
		}
		from, to = p.ax, p.ax+1
		for from > lo && cells[from-1] != ' ' {
			from--
		}
		for to < hi && cells[to] != ' ' {
			to++
		}
		// the marks a sentence leaves on a word are not part of it
		for to > from+1 && strings.ContainsRune(".,;:!?)\"'", cells[to-1]) {
			to--
		}
	}
	return selectedText([]string{string(cells[from:to])}, textSel{ax: 0, ay: 0, bx: to - from - 1, by: 0})
}
