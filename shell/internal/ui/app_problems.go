package ui

import (
	"fmt"
	"os"
	"path/filepath"
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
	} else if q == "" {
		// last, so ⏎ on the first problem still opens it
		out = append(out, paletteItem{"ask " + appParent(st), "fix all " + strconv.Itoa(len(p.Problems)) + " problems", "⇥ on one asks for just that one", "fixall"})
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

// lineProblem is a problem of the open file on a line (from 0): the error before a warning.
func lineProblem(a *AppState, line int) *view.JobProblem {
	if a.Problems == nil || a.Dirty || a.File == "" {
		return nil // an edit moves the lines the problems were read for
	}
	var found *view.JobProblem
	for i := range a.Problems.Problems {
		r := &a.Problems.Problems[i]
		if r.File == a.File && r.Line == line+1 && (found == nil || (found.Severity != "error" && r.Severity == "error")) {
			found = r
		}
	}
	return found
}

// problemMark is the gutter's mark for a line with a problem: red for an error, amber for a warning.
func problemMark(st State, line int) string {
	r := lineProblem(st.App, line)
	if r == nil {
		return ""
	}
	if r.Severity == "error" {
		return st.paint(colRed, "●")
	}
	return st.paint(colAmber, "●")
}

// inlineProblem is a problem's message at the end of its line, as an editor's error lens shows it.
func inlineProblem(st State, line, room int) string {
	r := lineProblem(st.App, line)
	if r == nil || room < 16 {
		return ""
	}
	colour := colRed
	if r.Severity != "error" {
		colour = colAmber
	}
	return "     " + st.paint(colour, fit(clean(r.Message), room-5))
}

// codeContext is a few lines of a file around a line (from 1), numbered, for a prompt.
func codeContext(repo, file string, line int) string {
	data, err := os.ReadFile(filepath.Join(repo, filepath.Clean("/"+file)))
	if err != nil || line < 1 {
		return ""
	}
	lines := strings.Split(string(data), "\n")
	var out []string
	for i := max(0, line-3); i < min(len(lines), line+2); i++ {
		mark := " "
		if i == line-1 {
			mark = ">"
		}
		out = append(out, fmt.Sprintf("%s%4d| %s", mark, i+1, strings.TrimRight(lines[i], "\r")))
	}
	return strings.Join(out, "\n")
}

// fixPrompt asks the parent to fix problems the helpers' checks found, each with its place and the code around it.
func fixPrompt(repo string, probs []view.JobProblem) string {
	var b strings.Builder
	if len(probs) == 1 {
		b.WriteString("Fix this problem from a failing check:\n")
	} else {
		b.WriteString("Fix these problems from failing checks:\n")
	}
	for i, r := range probs {
		if i == 10 {
			fmt.Fprintf(&b, "\n…and %d more; start with these.\n", len(probs)-10)
			break
		}
		where := r.File
		if r.Line > 0 {
			where += ":" + strconv.Itoa(r.Line)
			if r.Col > 0 {
				where += ":" + strconv.Itoa(r.Col)
			}
		}
		fmt.Fprintf(&b, "\n- %s %s (%s): %s\n", r.Severity, where, r.Check, clean(r.Message))
		if ctx := codeContext(repo, r.File, r.Line); ctx != "" {
			b.WriteString("```\n" + ctx + "\n```\n")
		}
	}
	return strings.TrimSpace(b.String())
}

// askFix asks the parent to fix the chosen problem (an Action "problem:file:line:col"), or all of them.
func (m *Model) askFix(action string) tea.Cmd {
	p := m.st.App.Problems
	if p == nil || len(p.Problems) == 0 {
		return nil
	}
	probs := p.Problems
	if ref, ok := strings.CutPrefix(action, "problem:"); ok {
		parts := strings.Split(ref, ":")
		if len(parts) < 3 {
			return nil
		}
		col, _ := strconv.Atoi(parts[len(parts)-1])
		line, _ := strconv.Atoi(parts[len(parts)-2])
		file := strings.Join(parts[:len(parts)-2], ":")
		probs = nil
		for _, r := range p.Problems {
			if r.File == file && r.Line == line && r.Col == col {
				probs = []view.JobProblem{r}
				break
			}
		}
		if probs == nil {
			return nil
		}
	}
	return m.appSend(fixPrompt(m.st.App.Repo, probs))
}
