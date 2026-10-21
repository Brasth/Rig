package ui

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
)

func TestADroppedFilesPathsAreReadAsTerminalsPasteThem(t *testing.T) {
	dir := t.TempDir()
	spaced, plain := filepath.Join(dir, "My Shot.png"), filepath.Join(dir, "notes.md")
	for _, p := range []string{spaced, plain} {
		_ = os.WriteFile(p, []byte("x"), 0o644)
	}
	escaped := strings.ReplaceAll(spaced, " ", `\ `)
	for paste, want := range map[string][]string{
		escaped:                     {spaced},
		"'" + spaced + "' " + plain: {spaced, plain},
		`"` + spaced + `"`:          {spaced},
		"file://" + plain + "\n":    {plain},
		"file://" + strings.ReplaceAll(spaced, " ", "%20"): {spaced},
		plain + " " + dir + "/missing":                     nil,
		"fix the bug in " + plain:                          nil,
		"notes.md":                                         nil,
		"'" + plain:                                        nil,
		"file://elsewhere" + plain:                         nil,
	} {
		got := droppedFiles(paste)
		if strings.Join(got, "|") != strings.Join(want, "|") {
			t.Errorf("%q: %q, want %q", paste, got, want)
		}
	}
}

func TestDroppingFilesOnTheWindowAddsThemToTheBox(t *testing.T) {
	m, src := appModel(t)
	withConversation(t, m, src)
	repo := m.st.App.Repo
	_ = os.MkdirAll(filepath.Join(repo, "src"), 0o755)
	inside := filepath.Join(repo, "src", "login.ts")
	outside := filepath.Join(t.TempDir(), "Screen Shot.png")
	_ = os.WriteFile(inside, []byte("x"), 0o644)
	_ = os.WriteFile(outside, []byte("x"), 0o644)
	typeText(m, "look at")
	m.st.App.Tab = 3
	m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune(inside + " " + strings.ReplaceAll(outside, " ", `\ `)), Paste: true})
	want := `look at @src/login.ts @"` + outside + `" `
	if m.st.Prompt != want || m.st.App.Tab != 1 {
		t.Fatalf("the box has the two files, inside the repo by its own path: %q (tab %d)", m.st.Prompt, m.st.App.Tab)
	}
	m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("plain words"), Paste: true})
	if !strings.HasSuffix(m.st.Prompt, " plain words") {
		t.Fatalf("a paste that is not files is text: %q", m.st.Prompt)
	}
}
