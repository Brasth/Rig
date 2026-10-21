package ui

import (
	"fmt"
	"strconv"
	"strings"

	tea "github.com/charmbracelet/bubbletea"
)

// An outline in the side bar, as an IDE's: the open file's functions, types and blocks (what ⌃o lists), the one
// around the cursor lit and kept in view as the cursor moves; ⏎ or a click goes to one. o shows it in place of the
// files, o again (or a click on EXPLORER) brings the files back.

// outlineRows are the symbols with their lines, from the same lookup as ⌃o.
func outlineRows(a *AppState) []paletteItem {
	if a.File == "" || a.FileLines == nil {
		return nil
	}
	return symbols(a.FileLines)
}

// outlineAt is which row the cursor's line is in: the last symbol that starts on or before it.
func outlineAt(rows []paletteItem, line int) int {
	at := -1
	for i, r := range rows {
		n, _ := strconv.Atoi(strings.TrimPrefix(r.Action, "line:"))
		if n-1 <= line {
			at = i
		}
	}
	return at
}

// outlinePane draws the outline in the side bar.
func outlinePane(st State, w, h int) []string {
	a := st.App
	lines := []string{sideHeads(st, w)}
	rows := outlineRows(a)
	switch {
	case a.File == "":
		lines = append(lines, "", " "+st.faint("no file open"))
	case len(rows) == 0:
		lines = append(lines, "", " "+st.faint("no symbols in "+baseName(a.File)))
	}
	line, _ := cursorLine(a)
	at := outlineAt(rows, line)
	sel := at
	if a.TreeFocus {
		sel = clampSel(a.OutlineSel, len(rows))
	}
	from := 0
	if sel >= h-3 {
		from = sel - (h - 3) + 1
	}
	for i := from; i < len(rows) && len(lines) < h-1; i++ {
		r := rows[i]
		n := strings.TrimPrefix(r.Action, "line:")
		indent := "  "
		if strings.Contains(r.Label, "(") || strings.Contains(r.Label, ".") {
			indent = "    " // a call block or a method: under its holder
		}
		row := fit(" "+indent+r.Label, w-len(n)-3) + st.faint(n) + "  "
		switch {
		case i == at && st.Styled:
			row = tint(fit(row, w), colRaised)
		case i == at:
			row = "›" + row[1:]
		}
		if a.TreeFocus && i == sel && i != at {
			row = ">" + row[1:]
		}
		lines = append(lines, st.zone(fmt.Sprintf("row:outline:%d", i), fit(row, w)))
	}
	for len(lines) < h {
		lines = append(lines, "")
	}
	lines = lines[:h]
	for i := range lines {
		lines[i] = fit(lines[i], w)
		if st.Styled {
			lines[i] = tint(lines[i], sideColour())
		}
	}
	return lines
}

func baseName(p string) string {
	if i := strings.LastIndex(p, "/"); i >= 0 {
		return p[i+1:]
	}
	return p
}

// outlineKey is the outline's keys while it has them: ↑↓ move, ⏎ goes to the symbol, o or esc back.
func (m *Model) outlineKey(k tea.KeyMsg) (tea.Model, tea.Cmd) {
	a := m.st.App
	rows := outlineRows(a)
	switch k.String() {
	case "up", "k":
		a.OutlineSel = clampSel(clampSel(a.OutlineSel, len(rows))-1, len(rows))
	case "down", "j":
		a.OutlineSel = clampSel(clampSel(a.OutlineSel, len(rows))+1, len(rows))
	case "enter":
		if len(rows) > 0 {
			n, _ := strconv.Atoi(strings.TrimPrefix(rows[clampSel(a.OutlineSel, len(rows))].Action, "line:"))
			a.TreeFocus = false
			m.goToLine(n)
		}
	case "o":
		a.Outline, a.TreeFocus = false, false
	case "esc", "ctrl+b":
		a.TreeFocus = false
	}
	return m, nil
}

// toggleOutline shows the outline in the side bar with the keys, or hides it.
func (m *Model) toggleOutline() {
	a := m.st.App
	if a.Outline && a.TreeFocus {
		a.Outline, a.TreeFocus = false, false
		return
	}
	a.Outline, a.GitView, a.TreeFocus = true, false, true
	line, _ := cursorLine(a)
	a.OutlineSel = max(0, outlineAt(outlineRows(a), line))
}
