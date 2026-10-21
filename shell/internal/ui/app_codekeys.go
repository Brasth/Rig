package ui

import (
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"

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
	if a.GitView {
		return m.gitKey(k)
	}
	if a.Outline {
		return m.outlineKey(k)
	}
	if a.CodeAsk != nil {
		return m.askKey(k)
	}
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
	case "n", "r", "d":
		// a new file where the explorer is, or the chosen one renamed or deleted
		path := ""
		if len(rows) > 0 {
			path = rows[sel].path
		}
		switch k.String() {
		case "n":
			dir := path
			if len(rows) > 0 && !rows[sel].dir {
				dir = filepath.Dir(path)
			}
			text := ""
			if dir != "" && dir != "." {
				text = dir + "/"
			}
			a.CodeAsk = &codeAsk{kind: "new", label: "new file", text: text}
		case "r":
			if path != "" {
				a.renameFrom, a.CodeAsk = path, &codeAsk{kind: "rename", label: "rename to", text: path}
			}
		case "d":
			if path != "" && !rows[sel].dir {
				m.pendingX, m.pendingI = "file.delete", path
				m.st.Mode, m.st.Prompt = ModeConfirm, "Delete "+path+"? It is removed from the repository folder."
			}
		}
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
	case "g":
		return m, m.openGit()
	case "o":
		m.toggleOutline()
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
	if a.Dirty && m.pendingX != "tab.discard" {
		m.pendingX, m.pendingI = "tab.discard", a.File
		m.st.Mode, m.st.Prompt = ModeConfirm, "Close "+filepath.Base(a.File)+" without saving? Its changes are thrown away."
		return nil
	}
	a.Dirty = false
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

// closeTabFile closes one tab by its × or a middle click: the open one as ⌃w does; another one is just taken off the
// row, or, with changes not saved, opened first so closing it asks.
func (m *Model) closeTabFile(file string) tea.Cmd {
	a := m.st.App
	if file == a.File {
		return m.closeTab()
	}
	if a.Bufs[file] != nil {
		cmd := m.runPalette(paletteItem{Action: "file:" + file}, "")
		if a.File == file {
			return tea.Batch(cmd, m.closeTab())
		}
		return cmd
	}
	for i, t := range a.Tabs {
		if t == file {
			a.Tabs = append(a.Tabs[:i:i], a.Tabs[i+1:]...)
			break
		}
	}
	return nil
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
	if a.Dirty {
		return // back to the changes not saved, the cursor where it was
	}
	a.CurLine, a.CurCol = min(max(a.FileOff, 0), len(a.FileLines)-1), 0
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
	// and across: the cells scrolled off the left keep the cursor's column in view
	if !a.Editing || a.CurLine >= len(a.FileLines) {
		a.ColOff = 0
		return
	}
	dc, tw := displayCol(a.FileLines[a.CurLine], a.CurCol, tabSize(m.st)), m.codeTextWidth()
	if dc < tw {
		a.ColOff = 0 // the line's start fits: back to it
	} else if dc < a.ColOff {
		a.ColOff = dc
	}
	if dc >= a.ColOff+tw {
		a.ColOff = dc - tw + 1
	}
}

// codeTextWidth is how many cells of a line the editor shows: the column less the gutter and the line numbers.
func (m *Model) codeTextWidth() int {
	a := m.st.App
	w := m.winW
	if w == 0 {
		w = m.w
	}
	if w >= 90 {
		w -= explorerWidth
	}
	gutter := 1
	if a.Blame && a.BlameFor == a.File {
		gutter += blameWidth
	}
	return max(10, w-gutter-6)
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
	a.Dirty, a.SavedID = false, linesID(a.FileLines)
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
