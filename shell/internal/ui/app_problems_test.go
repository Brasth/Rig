package ui

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/Brasth/Rig/shell/internal/view"
	tea "github.com/charmbracelet/bubbletea"
)

func TestProblemsFromTheHelpersChecksShowInTheStatusBarAndOpenOnTheLine(t *testing.T) {
	m, src := codeRepo(t)
	src.problems = &view.JobProblems{}
	_ = json.Unmarshal([]byte(`{"job_id": "j1", "errors": 1, "warnings": 1, "problems": [
		{"file": "src/routes/login.ts", "line": 4, "col": 9, "severity": "error", "message": "Type 'number' is not assignable", "check": "tsc"},
		{"file": "src/routes/login.ts", "line": 1, "col": 1, "severity": "warning", "message": "unused import", "check": "eslint"}]}`), src.problems)
	follow(m, m.fetchProblems(true), 0)
	out := appScreen(m)
	if !strings.Contains(out, "✕ 1") || !strings.Contains(out, "⚠ 1") {
		t.Fatalf("the status bar counts them:\n%s", out)
	}
	follow(m, press(m, "m"), 0)
	a := m.st.App
	if a.Palette == nil || !a.Palette.Problems {
		t.Fatal("m lists the problems")
	}
	items := paletteItems(m.st)
	if len(items) != 2 || !strings.Contains(items[0].Label, "✕ Type 'number'") || items[0].Detail != "src/routes/login.ts:4" || !strings.HasPrefix(items[1].Section, "problems · eslint") {
		t.Fatalf("each with its file and line: %+v", items)
	}
	follow(m, press(m, "enter"), 0)
	if a.File != "src/routes/login.ts" || !a.Editing || a.cursor() != (pos{3, 8}) {
		t.Fatalf("⏎ opens the file on the problem's line and column: %q %v %v", a.File, a.Editing, a.cursor())
	}
}

func TestAKeyMovedInSettingsIsTheOneThatWorks(t *testing.T) {
	m, src := appModel(t)
	withConversation(t, m, src)
	src.appSettings = sampleSettings()
	src.appSettings.Keys = map[string]string{"terminal": "alt+t", "find_in_chat": "alt+f"}
	run(m.fetchAppSettings(), m)
	pane := newFakePane()
	m.spawn = func([]string, string, []string, int, int) (Pane, error) { return pane, nil }
	a := m.st.App
	m.Update(tea.KeyMsg{Type: tea.KeyCtrlAt})
	if a.Term {
		t.Fatal("⌃` no longer opens the terminal once moved")
	}
	m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("t"), Alt: true})
	if !a.Term {
		t.Fatal("⌥t does")
	}
	m.Update(tea.KeyMsg{Type: tea.KeyCtrlCloseBracket})
	m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("f"), Alt: true})
	if a.ChatFind == nil {
		t.Fatal("⌥f finds in the conversation once moved there")
	}
	rows := settingRows(m.st, "keys")
	if len(rows) != 6 || rows[5].Value != "alt+t" || rows[5].Names[2] != "⌥t" {
		t.Fatalf("Settings › Keys shows the chords: %+v", rows[5])
	}
}
