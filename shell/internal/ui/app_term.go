package ui

import (
	"fmt"
	"os"
	"strings"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
)

// A terminal inside rig shell, as an IDE's: ⌃` opens the shell in a split at the foot of the screen and gives it the
// keys; ⌃] gives the keys back to rig shell with the split still there; ⌃` again hides the split (the shell keeps
// running) or shows it again. The pane machinery is the old board's: a pseudo-terminal and an emulator.

// termRows is how many rows the split takes of a screen h rows high.
func termRows(h int) int { return max(6, h/3) }

// toggleTerm opens the terminal, or focuses it, or hides it when it has the keys already.
func (m *Model) toggleTerm() tea.Cmd {
	a := m.st.App
	switch {
	case !a.Term:
		a.Term, a.TermFocus = true, true
		if m.pane == nil || m.paneDone() {
			return m.startTerm()
		}
		m.pane.Resize(m.termSize())
		return nil
	case !a.TermFocus:
		a.TermFocus = true
		return nil
	default:
		a.Term, a.TermFocus = false, false
		return nil
	}
}

// paneDone is whether the pane's program has exited.
func (m *Model) paneDone() bool {
	if m.pane == nil {
		return true
	}
	done, _ := m.pane.Exited()
	return done
}

// termSize is the split's screen in cells.
func (m *Model) termSize() (cols, rows int) {
	w, h := m.winW, m.winH
	if w == 0 {
		w, h = m.w, m.h
	}
	return max(20, w-2), max(2, termRows(h-2)-1)
}

// startTerm runs the user's shell in the repository.
func (m *Model) startTerm() tea.Cmd {
	a := m.st.App
	shell := os.Getenv("SHELL")
	if shell == "" {
		shell = "/bin/sh"
	}
	if m.pane != nil {
		m.pane.Close()
	}
	cols, rows := m.termSize()
	p, err := m.spawn([]string{shell}, a.Repo, []string{"RIG_SHELL_TERM=1", "TERM=xterm-256color"}, cols, rows)
	if err != nil {
		a.Term, a.TermFocus = false, false
		m.st.Message = "Could not start a shell: " + clean(err.Error())
		return nil
	}
	m.pane, m.paneName, m.paneFocus = p, "terminal", false
	return waitPane(p)
}

// termSplit draws the split: a rule with its name and keys, then the shell's screen.
func termSplit(st State, w, h int) []string {
	a := st.App
	name := " terminal "
	keys := st.faint("⌃] rig shell · PgUp scroll · ⌃` hide")
	if a.TermUp > 0 {
		keys = st.paint(colAmber, fmt.Sprintf("↑%d ", a.TermUp)) + keys
	}
	if !a.TermFocus {
		name = st.faint(" terminal ")
		keys = st.faint("⌃` type here")
	} else {
		name = st.bold(name)
	}
	rule := st.paint(colBorder, "─"+name+strings.Repeat("─", max(0, w-lipgloss.Width(name)-lipgloss.Width(keys)-4))+" "+keys+" ─")
	out := []string{fit(rule, w)}
	lines := st.PaneLines
	for i := 0; i < h-1; i++ {
		if i < len(lines) {
			out = append(out, fit(" "+lines[i], w))
		} else {
			out = append(out, fit("", w))
		}
	}
	return out
}
