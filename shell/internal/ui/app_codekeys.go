package ui

import (
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"unicode"

	tea "github.com/charmbracelet/bubbletea"
)

// addTab keeps a file among the code tab's open files.
func addTab(tabs []string, file string) []string {
	for _, t := range tabs {
		if t == file {
			return tabs
		}
	}
	return append(tabs, file)
}

var (
	diffFileHead = regexp.MustCompile(`^\+\+\+ (?:b/)?(.+)$`)
	diffHunkHead = regexp.MustCompile(`^@@ -(\d+)`)
)

// touchedLines reads a helper's diff for the files it adds and, for the others, the lines of the file here its change
// touches: the lines it removes or rewrites, and the line it adds after.
func touchedLines(diff string, added map[string]bool, lines map[string]map[int]bool) {
	file, old, isNew := "", 0, false
	for _, l := range strings.Split(diff, "\n") {
		switch {
		case strings.HasPrefix(l, "diff --git"):
			file, isNew = "", false
		case strings.HasPrefix(l, "new file mode"), l == "--- /dev/null":
			isNew = true
		case strings.HasPrefix(l, "+++ "):
			if m := diffFileHead.FindStringSubmatch(l); m != nil && m[1] != "/dev/null" {
				file = m[1]
				if isNew {
					added[file] = true
				}
			}
		case strings.HasPrefix(l, "@@"):
			if m := diffHunkHead.FindStringSubmatch(l); m != nil {
				old, _ = strconv.Atoi(m[1])
			}
		case file == "" || isNew:
		case strings.HasPrefix(l, "-"):
			touch(lines, file, old)
			old++
		case strings.HasPrefix(l, "+"):
			touch(lines, file, max(1, old-1))
		case strings.HasPrefix(l, " "):
			old++
		}
	}
}

func touch(lines map[string]map[int]bool, file string, n int) {
	if lines[file] == nil {
		lines[file] = map[int]bool{}
	}
	lines[file][n] = true
}

// openTree opens the explorer's row: a file in the code tab, or a folder opened or closed.
func (m *Model) openTree(path string, dir bool) tea.Cmd {
	a := m.st.App
	if dir {
		if a.TreeOpen == nil {
			a.TreeOpen = map[string]bool{}
		}
		a.TreeOpen[path] = !dirOpen(a, path)
		return nil
	}
	return m.runPalette(paletteItem{Action: "file:" + path}, "")
}

// treeKey is the explorer's keys after ⌃b: ↑↓ move, ⏎ opens, ←→ fold, / filters, esc goes back to the code.
func (m *Model) treeKey(k tea.KeyMsg) (tea.Model, tea.Cmd) {
	a := m.st.App
	rows := treeRows(a)
	if a.TreeFiltering {
		switch k.Type {
		case tea.KeyEnter, tea.KeyDown:
			a.TreeFiltering, a.TreeSel = false, 0
		case tea.KeyEsc:
			a.TreeFiltering, a.TreeFilter = false, ""
		case tea.KeyBackspace:
			if r := []rune(a.TreeFilter); len(r) > 0 {
				a.TreeFilter = string(r[:len(r)-1])
			}
		case tea.KeyRunes, tea.KeySpace:
			a.TreeFilter += string(k.Runes)
		}
		return m, nil
	}
	sel := clampSel(a.TreeSel, len(rows))
	switch k.String() {
	case "up", "k":
		a.TreeSel = clampSel(sel-1, len(rows))
	case "down", "j":
		a.TreeSel = clampSel(sel+1, len(rows))
	case "pgup":
		a.TreeSel = clampSel(sel-15, len(rows))
	case "pgdown":
		a.TreeSel = clampSel(sel+15, len(rows))
	case "/":
		a.TreeFiltering = true
	case "left", "h", "right", "l", "enter", " ":
		if len(rows) == 0 {
			return m, nil
		}
		r := rows[sel]
		if r.dir {
			want := k.String() == "right" || k.String() == "l" || ((k.String() == "enter" || k.String() == " ") && !r.open)
			if a.TreeOpen == nil {
				a.TreeOpen = map[string]bool{}
			}
			a.TreeOpen[r.path] = want
			return m, nil
		}
		if k.String() == "left" || k.String() == "h" {
			if parent := filepath.Dir(r.path); parent != "." {
				for i, row := range rows {
					if row.path == parent {
						a.TreeSel = i
					}
				}
			}
			return m, nil
		}
		cmd := m.openTree(r.path, false)
		a.TreeFocus = false
		return m, cmd
	case "esc", "ctrl+b":
		a.TreeFocus = false
		if a.TreeFilter != "" && k.String() == "esc" {
			a.TreeFilter, a.TreeFocus = "", true
		}
	}
	return m, nil
}

// focusTree moves the keys into the explorer, on the open file's row.
func (m *Model) focusTree() {
	a := m.st.App
	a.TreeFocus, a.TreeSel = true, 0
	for i, r := range treeRows(a) {
		if r.path == a.File {
			a.TreeSel = i
		}
	}
}

// closeTab closes the open file's tab (⌃w) and opens the one beside it.
func (m *Model) closeTab() tea.Cmd {
	a := m.st.App
	if a.Dirty {
		m.st.Message = filepath.Base(a.File) + " has unsaved changes: ⌃s saves them, esc in it discards them"
		return nil
	}
	at := -1
	for i, t := range a.Tabs {
		if t == a.File {
			at = i
		}
	}
	if at < 0 {
		return nil
	}
	a.Tabs = append(a.Tabs[:at:at], a.Tabs[at+1:]...)
	a.Editing, a.Selecting = false, false
	if len(a.Tabs) == 0 {
		a.File, a.FileLines, a.FileErr = "", nil, ""
		return nil
	}
	return m.runPalette(paletteItem{Action: "file:" + a.Tabs[min(at, len(a.Tabs)-1)]}, "")
}

// startEdit begins editing the open file here (e), with the cursor on the first line shown.
func (m *Model) startEdit() {
	a := m.st.App
	switch {
	case a.File == "" || a.FileLines == nil || a.FileErr != "":
		return
	case a.FileTruncated:
		m.st.Message = filepath.Base(a.File) + " is too long to edit here: E opens it in $EDITOR"
		return
	case previewKind(a.File) == "binary":
		return
	}
	a.Editing, a.Selecting, a.Finding = true, false, false
	a.CurLine, a.CurCol = min(max(a.FileOff, 0), len(a.FileLines)-1), 0
	a.editEsc = false
}

// keepCursorShown scrolls the file so the editing cursor is on screen.
func (m *Model) keepCursorShown() {
	a := m.st.App
	room := max(5, m.winH-12)
	if a.CurLine < a.FileOff {
		a.FileOff = a.CurLine
	}
	if a.CurLine >= a.FileOff+room {
		a.FileOff = a.CurLine - room + 1
	}
}

// saveFile writes the edited file back, with the line endings it had.
func (m *Model) saveFile() {
	a := m.st.App
	eol := "\n"
	if a.FileCRLF {
		eol = "\r\n"
	}
	path := filepath.Join(a.Repo, filepath.Clean("/"+a.File))
	mode := os.FileMode(0o644)
	if info, err := os.Stat(path); err == nil {
		mode = info.Mode().Perm()
	}
	if err := os.WriteFile(path, []byte(strings.Join(a.FileLines, eol)), mode); err != nil {
		m.st.Message = "Could not save " + a.File + ": " + err.Error()
		return
	}
	a.Dirty = false
	m.st.Message = "saved " + a.File
}

// indentUnit is what ⇥ types while editing: a tab in a file indented with tabs, else its spaces.
func indentUnit(st State, lines []string) string {
	in := indentName(st, lines)
	if strings.HasPrefix(in, "Tab") {
		return "\t"
	}
	n := 2
	fmt.Sscanf(in, "Spaces: %d", &n)
	return strings.Repeat(" ", max(1, n))
}

// editFileKey edits the open file: the arrows, Home and End move; typing, ⏎, ⇥, ⌫ and Delete change it; ⌃s saves;
// esc stops editing (a second esc throws away what is not saved).
func (m *Model) editFileKey(k tea.KeyMsg) (tea.Model, tea.Cmd) {
	a := m.st.App
	lines := a.FileLines
	line := []rune(lines[a.CurLine])
	a.CurCol = min(a.CurCol, len(line))
	set := func(l int, text []rune) {
		lines[l] = string(text)
		a.Dirty = true
	}
	key := k.String()
	if key != "esc" {
		a.editEsc = false
	}
	switch key {
	case "ctrl+s":
		m.saveFile()
	case "esc":
		if a.Dirty && !a.editEsc {
			a.editEsc = true
			m.st.Message = "unsaved changes: ⌃s saves them, esc again throws them away"
			return m, nil
		}
		a.Editing = false
		if a.Dirty {
			a.Dirty = false
			return m, m.openFile(a.File) // thrown away: the file as it is on disk
		}
	case "up":
		a.CurLine = max(0, a.CurLine-1)
	case "down":
		a.CurLine = min(len(lines)-1, a.CurLine+1)
	case "pgup":
		a.CurLine = max(0, a.CurLine-20)
	case "pgdown":
		a.CurLine = min(len(lines)-1, a.CurLine+20)
	case "left":
		if a.CurCol > 0 {
			a.CurCol--
		} else if a.CurLine > 0 {
			a.CurLine--
			a.CurCol = len([]rune(lines[a.CurLine]))
		}
	case "right":
		if a.CurCol < len(line) {
			a.CurCol++
		} else if a.CurLine < len(lines)-1 {
			a.CurLine, a.CurCol = a.CurLine+1, 0
		}
	case "home", "ctrl+a":
		a.CurCol = 0
	case "end", "ctrl+e":
		a.CurCol = len(line)
	case "alt+left", "ctrl+left":
		a.CurCol = wordLeft(line, a.CurCol)
	case "alt+right", "ctrl+right":
		a.CurCol = wordRight(line, a.CurCol)
	case "enter":
		var lead []rune
		for _, r := range line {
			if r != ' ' && r != '\t' {
				break
			}
			lead = append(lead, r)
		}
		rest := append(append([]rune{}, lead...), line[a.CurCol:]...)
		set(a.CurLine, line[:a.CurCol])
		a.FileLines = append(lines[:a.CurLine+1], append([]string{string(rest)}, lines[a.CurLine+1:]...)...)
		a.CurLine, a.CurCol = a.CurLine+1, len(lead)
	case "backspace":
		switch {
		case a.CurCol > 0:
			set(a.CurLine, append(line[:a.CurCol-1:a.CurCol-1], line[a.CurCol:]...))
			a.CurCol--
		case a.CurLine > 0:
			prev := []rune(lines[a.CurLine-1])
			set(a.CurLine-1, append(prev, line...))
			a.FileLines = append(lines[:a.CurLine], lines[a.CurLine+1:]...)
			a.CurLine, a.CurCol = a.CurLine-1, len(prev)
		}
	case "delete":
		switch {
		case a.CurCol < len(line):
			set(a.CurLine, append(line[:a.CurCol:a.CurCol], line[a.CurCol+1:]...))
		case a.CurLine < len(lines)-1:
			set(a.CurLine, append(line, []rune(lines[a.CurLine+1])...))
			a.FileLines = append(lines[:a.CurLine+1], lines[a.CurLine+2:]...)
		}
	case "tab":
		unit := []rune(indentUnit(m.st, lines))
		set(a.CurLine, append(line[:a.CurCol:a.CurCol], append(unit, line[a.CurCol:]...)...))
		a.CurCol += len(unit)
	default:
		if k.Type == tea.KeyRunes || k.Type == tea.KeySpace {
			text := []rune(strings.ReplaceAll(string(k.Runes), "\r", ""))
			if strings.ContainsRune(string(text), '\n') {
				// a paste of several lines: each becomes a line of the file
				parts := strings.Split(string(text), "\n")
				head, tail := string(line[:a.CurCol])+parts[0], parts[len(parts)-1]+string(line[a.CurCol:])
				add := append([]string{head}, parts[1:len(parts)-1]...)
				add = append(add, tail)
				a.FileLines = append(lines[:a.CurLine], append(add, lines[a.CurLine+1:]...)...)
				a.CurLine += len(parts) - 1
				a.CurCol = len([]rune(parts[len(parts)-1]))
				a.Dirty = true
				break
			}
			for _, r := range text {
				if !unicode.IsPrint(r) && r != '\t' {
					return m, nil
				}
			}
			set(a.CurLine, append(line[:a.CurCol:a.CurCol], append(text, line[a.CurCol:]...)...))
			a.CurCol += len(text)
		}
	}
	m.keepCursorShown()
	return m, nil
}
