package ui

import (
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
)

func TestCtrlBacktickOpensAShellSplitAndCtrlBracketGivesTheKeysBack(t *testing.T) {
	m, src := appModel(t)
	withConversation(t, m, src)
	pane := newFakePane()
	pane.lines = []string{"$ echo hi", "hi"}
	var argv []string
	var dir string
	m.spawn = func(a []string, d string, env []string, cols, rows int) (Pane, error) {
		argv, dir = a, d
		return pane, nil
	}
	tick := tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("`"), Alt: false}
	tick = tea.KeyMsg{Type: tea.KeyCtrlAt} // what most terminals send for ⌃`
	a := m.st.App
	m.Update(tick)
	if !a.Term || !a.TermFocus || len(argv) == 0 || dir != a.Repo {
		t.Fatalf("⌃` opens the shell in the repository with the keys: %v %v %q", a.Term, argv, dir)
	}
	out := appScreen(m)
	if !strings.Contains(out, "terminal") || !strings.Contains(out, "$ echo hi") || !strings.Contains(out, "⌃] rig shell") {
		t.Fatalf("the split shows the shell's screen:\n%s", out)
	}
	typeText(m, "ls")
	if len(pane.writes) != 2 || string(pane.writes[0]) != "l" {
		t.Fatalf("keys go to the shell: %q", pane.writes)
	}
	m.Update(tea.KeyMsg{Type: tea.KeyCtrlCloseBracket})
	if a.TermFocus || !a.Term {
		t.Fatal("⌃] gives the keys back, the split stays")
	}
	typeText(m, "x")
	if len(pane.writes) != 2 || m.st.Prompt != "x" {
		t.Fatalf("now the keys are rig shell's: %q %q", pane.writes, m.st.Prompt)
	}
	m.Update(tick)
	if !a.TermFocus {
		t.Fatal("⌃` focuses it again")
	}
	m.Update(tick)
	if a.Term || pane.closed != 0 {
		t.Fatal("⌃` with the keys hides the split; the shell keeps running")
	}
	m.Update(tick)
	if !a.Term || !a.TermFocus || len(pane.resizes) == 0 {
		t.Fatal("⌃` shows it again, sized to the split")
	}
}
