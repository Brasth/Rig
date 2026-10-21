package ui

import (
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"
)

// Finding things across the repository, as an IDE does: F7 lists every use of the name at the cursor (find
// references), F12 on a definition does the same, # in the palette lists the symbols of the whole repository, and
// ⌃p lists the files opened lately first.

// findReferences opens the search tab on the name at the cursor, whole word, case as written: every use of it.
func (m *Model) findReferences() tea.Cmd {
	a := m.st.App
	if a.File == "" || a.CurLine >= len(a.FileLines) {
		return nil
	}
	name := identAt(a.FileLines[a.CurLine], a.CurCol)
	if name == "" {
		m.st.Message = "no name at the cursor"
		return nil
	}
	m.st.Message = "references to " + name
	a.Search = &SearchState{Query: name, Word: true, Case: true}
	return tea.Batch(m.fetchChanged(), m.runSearch())
}

// wsSymbol is one symbol of the repository: its name and where it is.
type wsSymbol struct {
	name, file string
	line, col  int
}

// symIndex is every symbol of the repository, read once and kept a while.
type symIndex struct {
	at       time.Time
	items    []wsSymbol
	building bool
}

type symIndexMsg struct{ items []wsSymbol }

const (
	symIndexFor   = 2 * time.Minute
	symIndexFiles = 4000
	symIndexMax   = 20000
)

// indexSymbols reads the repository's source files for their symbols, the ⌃o lookup over each.
func indexSymbols(repo string, files []string) []wsSymbol {
	var out []wsSymbol
	seen := 0
	for _, f := range files {
		if languages[strings.ToLower(filepath.Ext(f))] == "" || strings.Contains(f, "node_modules/") || strings.Contains(f, "vendor/") {
			continue
		}
		if seen++; seen > symIndexFiles || len(out) >= symIndexMax {
			break
		}
		data, err := os.ReadFile(filepath.Join(repo, filepath.FromSlash(f)))
		if err != nil || len(data) > 1<<20 {
			continue
		}
		lines := strings.Split(string(data), "\n")
		for _, it := range symbols(lines) {
			if strings.Contains(it.Label, "(") {
				continue // a call block (describe('…')), not a definition
			}
			n, _ := strconv.Atoi(strings.TrimPrefix(it.Action, "line:"))
			if n < 1 || n > len(lines) {
				continue
			}
			col := 0
			if j := strings.Index(lines[n-1], it.Label); j >= 0 {
				col = len([]rune(lines[n-1][:j]))
			}
			out = append(out, wsSymbol{name: it.Label, file: f, line: n, col: col})
		}
	}
	return out
}

// indexCmd reads the symbols when the index is missing or old; nil while one is fresh or being read.
func (m *Model) indexCmd() tea.Cmd {
	a := m.st.App
	if a.SymIndex != nil && (a.SymIndex.building || m.now().Sub(a.SymIndex.at) < symIndexFor) {
		return nil
	}
	if a.SymIndex == nil {
		a.SymIndex = &symIndex{}
	}
	a.SymIndex.building = true
	repo, files := a.Repo, a.Files
	return func() tea.Msg { return symIndexMsg{items: indexSymbols(repo, files)} }
}

// workspaceSymbolItems are the palette's rows for #query: the repository's symbols that match, the ones that start
// with it first.
func workspaceSymbolItems(st State, q string) []paletteItem {
	idx := st.App.SymIndex
	if idx == nil || (idx.building && idx.items == nil) {
		return []paletteItem{{"symbols in the repository", "reading the repository…", "", ""}}
	}
	q = strings.ToLower(strings.TrimSpace(q))
	var hits []wsSymbol
	for _, s := range idx.items {
		if q == "" || strings.Contains(strings.ToLower(s.name), q) {
			hits = append(hits, s)
		}
	}
	sort.SliceStable(hits, func(i, j int) bool {
		a, b := strings.HasPrefix(strings.ToLower(hits[i].name), q), strings.HasPrefix(strings.ToLower(hits[j].name), q)
		if a != b {
			return a
		}
		return len(hits[i].name) < len(hits[j].name)
	})
	var out []paletteItem
	for _, s := range hits {
		out = append(out, paletteItem{"symbols in the repository", s.name, s.file + ":" + strconv.Itoa(s.line),
			"at:" + s.file + ":" + strconv.Itoa(s.line) + ":" + strconv.Itoa(s.col+1)})
		if len(out) == 12 {
			break
		}
	}
	if len(out) == 0 {
		out = append(out, paletteItem{"symbols in the repository", "no symbol matches " + q, "", ""})
	}
	return out
}

// openRef opens a file on a line and column: the "at:file:line:col" palette action.
func (m *Model) openRef(ref string) tea.Cmd {
	a := m.st.App
	parts := strings.Split(ref, ":")
	if len(parts) < 3 {
		return nil
	}
	col, _ := strconv.Atoi(parts[len(parts)-1])
	line, _ := strconv.Atoi(parts[len(parts)-2])
	file := strings.Join(parts[:len(parts)-2], ":")
	if a.File != "" {
		m.jumpBack = append(m.jumpBack, jumpTo{file: a.File, at: a.cursor()}) // ⌃t comes back
	}
	return m.openAt(jumpTo{file: file, at: pos{max(0, line-1), max(0, col-1)}})
}

// rememberOpened keeps a file at the head of the files opened lately.
func (a *AppState) rememberOpened(file string) {
	out := []string{file}
	for _, f := range a.Recent {
		if f != file && len(out) < 10 {
			out = append(out, f)
		}
	}
	a.Recent = out
}

// recentItems are the files opened lately, newest first, but the open one: the palette's first rows with nothing typed.
func recentItems(st State, attach bool) []paletteItem {
	var out []paletteItem
	for _, f := range st.App.Recent {
		if f == st.App.File && !attach {
			continue
		}
		dir, name := "", f
		if i := strings.LastIndex(f, "/"); i >= 0 {
			dir, name = f[:i], f[i+1:]
		}
		action := "file:" + f
		if attach {
			action = "attach:" + f
		}
		if out = append(out, paletteItem{"recent files", name, dir, action}); len(out) == 6 {
			break
		}
	}
	return out
}
