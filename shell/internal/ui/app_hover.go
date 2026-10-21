package ui

import (
	"os"
	"path"
	"path/filepath"
	"strings"
	"time"

	"github.com/charmbracelet/x/ansi"

	tea "github.com/charmbracelet/bubbletea"
)

// Hover info in the code tab: when the pointer rests on a name for a moment, a small box shows the line that
// defines it and where, found the way go to definition finds it (this file, its imports, then the repository). It
// goes when the pointer moves off the name.

// hoverInfo is what the box shows.
type hoverInfo struct {
	name, file string
	line       int    // from 0
	text       string // the defining line
	x, y       int    // where the pointer was
}

type hoverMsg struct {
	info *hoverInfo
	key  string // name@x,y: only shown while the pointer is still there
}

const hoverDelay = 400 * time.Millisecond

// hoverTick fires after the pointer rests, to look the name up.
func (m *Model) hoverTick(x, y int) tea.Cmd {
	a := m.st.App
	if a.Tab != 3 || a.File == "" || a.FileLines == nil || a.Palette != nil || a.Help {
		return nil
	}
	at, ok := m.codeAt(x, y)
	if !ok || at.l >= len(a.FileLines) {
		return nil
	}
	name := identAt(a.FileLines[at.l], at.c)
	if name == "" || notMethod[name] || len(name) < 2 {
		a.Hover = nil
		return nil
	}
	if a.Hover != nil && a.Hover.name == name {
		return nil // already shown
	}
	key := name + "@" + itoa2(x, y)
	m.hoverKey = key
	repo, file, lines, files := a.Repo, a.File, a.FileLines, a.Files
	return tea.Tick(hoverDelay, func(time.Time) tea.Msg {
		return hoverMsg{key: key, info: lookupHover(repo, file, lines, files, name, at.l, x, y)}
	})
}

func itoa2(x, y int) string {
	return strings.TrimSpace(strings.Join([]string{itoaS(x), itoaS(y)}, ","))
}

func itoaS(n int) string {
	if n == 0 {
		return "0"
	}
	neg := n < 0
	if neg {
		n = -n
	}
	var b []byte
	for n > 0 {
		b = append([]byte{byte('0' + n%10)}, b...)
		n /= 10
	}
	if neg {
		b = append([]byte{'-'}, b...)
	}
	return string(b)
}

// lookupHover finds where name is defined, as goToDefinition does, and returns the defining line.
func lookupHover(repo, file string, lines, files []string, name string, here, x, y int) *hoverInfo {
	re := definitionRe(name)
	read := func(f string) []string {
		if f == file {
			return lines
		}
		data, err := os.ReadFile(filepath.Join(repo, filepath.FromSlash(f)))
		if err != nil || len(data) > 2<<20 {
			return nil
		}
		return strings.Split(string(data), "\n")
	}
	found := func(f string, ls []string) *hoverInfo {
		for i, l := range ls {
			if f == file && i == here {
				continue
			}
			if at := definedAt(l, name, re); at >= 0 {
				return &hoverInfo{name: name, file: f, line: i, text: strings.TrimSpace(l), x: x, y: y}
			}
		}
		return nil
	}
	if spec := importedFrom(lines, name); spec != "" {
		if target := resolveImport(repo, file, spec); target != "" {
			if h := found(target, read(target)); h != nil {
				return h
			}
			if j := throughReExports(repo, target, name, re, 0); j != nil {
				ls := read(j.file)
				if j.at.l < len(ls) {
					return &hoverInfo{name: name, file: j.file, line: j.at.l, text: strings.TrimSpace(ls[j.at.l]), x: x, y: y}
				}
			}
			return &hoverInfo{name: name, file: target, line: -1, text: "from " + spec, x: x, y: y}
		}
		return &hoverInfo{name: name, file: "", line: -1, text: "from " + spec + " (a package)", x: x, y: y}
	}
	if h := found(file, lines); h != nil {
		return h
	}
	dir := path.Dir(file)
	var order []string
	for _, f := range files {
		if path.Dir(f) == dir && f != file {
			order = append(order, f)
		}
	}
	for _, f := range files {
		if path.Dir(f) != dir {
			order = append(order, f)
		}
	}
	for _, f := range order {
		if languages[strings.ToLower(filepath.Ext(f))] == "" || strings.Contains(f, "node_modules/") {
			continue
		}
		if h := found(f, read(f)); h != nil {
			return h
		}
	}
	return nil
}

// hoverBox draws the box under the pointer: the defining line, and the file and line it is in.
func hoverBox(st State, w int) (lines []string, x, y int) {
	h := st.App.Hover
	if h == nil {
		return nil, 0, 0
	}
	where := ""
	switch {
	case h.file == "":
	case h.line < 0:
		where = h.file
	default:
		where = h.file + ":" + itoaS(h.line+1)
	}
	text := sanitize(h.text)
	if len([]rune(text)) > 70 {
		text = string([]rune(text)[:69]) + "…"
	}
	row := " " + highlight(st, text) + " "
	if where != "" {
		row += st.faint(where) + " "
	}
	bw := min(w-4, lipglossWidth(row)+2)
	box := roundBox(st, []string{fit(row, bw-2)}, bw, false)
	x = max(0, min(h.x, w-bw))
	y = h.y - len(box)
	if y < 1 {
		y = h.y + 1
	}
	return box, x, y
}

func lipglossWidth(s string) int { return ansi.StringWidth(s) }
