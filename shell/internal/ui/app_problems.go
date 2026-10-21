package ui

import (
	"fmt"
	"strconv"
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/Brasth/Rig/shell/internal/view"
)

// Problems, as the design's status bar counts them: what the helpers' failed checks found (tsc, eslint, pytest,
// go vet…), read from their output. The status bar shows ✕ errors and ⚠ warnings; m (or a click on the counts)
// lists them in the palette and ⏎ opens the file on the line. Nothing runs here: the counts come from checks the
// helpers already ran (job.problems).

type problemsMsg struct {
	p   *view.JobProblems
	err error
}

const problemsEvery = 20 * time.Second

// fetchProblems asks for the newest job's problems, now and then, or now when forced.
func (m *Model) fetchProblems(force bool) tea.Cmd {
	if !m.appOn() || (!force && m.now().Sub(m.probLast) < problemsEvery) {
		return nil
	}
	m.probLast = m.now()
	src := m.src
	return func() tea.Msg {
		p, err := src.JobProblems("", callTimeout)
		return problemsMsg{p: p, err: err}
	}
}

// bound is the chord a movable key is set to in Settings › Keys, or its default.
func bound(st State, name, def string) string {
	if s := st.AppSettings; s != nil {
		if k := s.Keys[name]; k != "" {
			return k
		}
	}
	return def
}

// isKey is whether a key press is the chord: "ctrl+`" arrives as ctrl+@ or ctrl+` by terminal, ctrl+/ as ctrl+_.
func isKey(k tea.KeyMsg, chord string) bool {
	key := k.String()
	switch chord {
	case "ctrl+`":
		return key == "ctrl+`" || key == "ctrl+@"
	case "ctrl+/":
		return key == "ctrl+/" || key == "ctrl+_"
	}
	return key == chord
}

// problemCounts is the status bar's part: ✕ n ⚠ n, a zone that opens the list.
func problemCounts(st State) string {
	p := st.App.Problems
	if p == nil || (p.Errors == 0 && p.Warnings == 0) {
		return ""
	}
	text := st.paint(colRed, fmt.Sprintf("✕ %d", p.Errors)) + "  " + st.paint(colAmber, fmt.Sprintf("⚠ %d", p.Warnings))
	return st.zone("row:open:problems", text)
}

// problemItems is the palette in the problems list: each with its file and line, filtered by the query.
func problemItems(st State, q string) []paletteItem {
	p := st.App.Problems
	var out []paletteItem
	if p == nil {
		return []paletteItem{{"problems", "no problems", "no helper's check has failed", ""}}
	}
	for _, r := range p.Problems {
		mark := "✕"
		if r.Severity == "warning" {
			mark = "⚠"
		} else if r.Severity == "info" {
			mark = "i"
		}
		where := r.File
		if r.Line > 0 {
			where += ":" + strconv.Itoa(r.Line)
		}
		label := mark + " " + clean(r.Message)
		if q != "" && !strings.Contains(strings.ToLower(label+" "+where), q) {
			continue
		}
		action := ""
		if r.File != "" {
			action = "problem:" + r.File + ":" + strconv.Itoa(r.Line) + ":" + strconv.Itoa(r.Col)
		}
		out = append(out, paletteItem{"problems · " + r.Check, label, where, action})
	}
	if len(out) == 0 {
		out = append(out, paletteItem{"problems", "no problems", "every check passed", ""})
	}
	return out
}

// openProblem opens the file on the problem's line, editing, the cursor on its column.
func (m *Model) openProblem(ref string) tea.Cmd {
	parts := strings.Split(ref, ":")
	if len(parts) < 3 {
		return nil
	}
	col, _ := strconv.Atoi(parts[len(parts)-1])
	line, _ := strconv.Atoi(parts[len(parts)-2])
	file := strings.Join(parts[:len(parts)-2], ":")
	m.st.App.pendingAt = &jumpTo{file: file, at: pos{max(0, line-1), max(0, col-1)}}
	return m.runPalette(paletteItem{Action: "file:" + file}, "")
}
