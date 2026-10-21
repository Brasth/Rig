package ui

import (
	"encoding/hex"
	"strconv"
	"strings"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"
)

// The mouse, as in codex and grok: the screens mark what can be clicked with zero-width zones, the frame takes the
// marks out, draws the zone under the pointer underlined, and a click on a zone does what its key or row would.

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
func underlineHovered(line string, marks []zoneMark, zones []hitZone, hx, hy int) string {
	z := innermost(zones, hx, hy)
	if z < 0 || strings.HasPrefix(zones[z].id, "layer:") {
		return line
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
	inner := sgrSeq.ReplaceAllStringFunc(line[from:to], func(s string) string { return s + "\x1b[4m" })
	return line[:from] + "\x1b[4m" + inner + "\x1b[24m" + line[to:]
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
		return m.wheel(e.Button == tea.MouseButtonWheelUp)
	case e.Action == tea.MouseActionMotion && e.Button == tea.MouseButtonNone:
		m.hoverX, m.hoverY = e.X, e.Y
		return m, nil
	case e.Action == tea.MouseActionPress && e.Button == tea.MouseButtonLeft:
		m.hoverX, m.hoverY = e.X, e.Y
		return m.click(e.X, e.Y)
	}
	return m, nil
}

// wheel scrolls what the screen shows: the conversation, the diff and code, or the selection of a list.
func (m *Model) wheel(up bool) (tea.Model, tea.Cmd) {
	a := m.st.App
	if a.Splash > 0 || m.st.Mode == ModeConfirm {
		return m, nil
	}
	list := a.Help || a.Welcome || a.Onboard != nil || a.Settings != nil || a.History != nil || a.Palette != nil ||
		a.Panel || a.Search != nil || problemScreen(m.st) != "" || a.NewJob != nil
	if !list && a.Tab == 1 {
		if up {
			m.st.ChatUp += 3
		} else {
			m.st.ChatUp = max(0, m.st.ChatUp-3)
		}
		return m, nil
	}
	if !list && a.Tab == 3 && a.Preview && previewKind(a.File) != "" {
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
	return tea.Batch(m.fetchDock(true), m.fetchDiff(true), m.fetchChanged())
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
		if a.Panel {
			a.PanelSel = i
			return m.appKey(enter)
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
		a.DiffFile, a.DiffOff = i, 0
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
	case "file":
		return m, m.runPalette(paletteItem{Action: "file:" + at}, "")
	}
	return m, nil
}
