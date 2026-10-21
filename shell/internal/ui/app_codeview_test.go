package ui

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
)

// codeRepo opens the code tab on a small repository: src/routes/login.ts and two more files.
func codeRepo(t *testing.T) (*Model, *fakeSource) {
	t.Helper()
	m, src := appModel(t)
	withConversation(t, m, src)
	repo := m.st.App.Repo
	files := map[string]string{"src/routes/login.ts": "import { Router } from 'express'\n\nrouter.post('/login', async (req, res) => {\n  const user = 1\n})\n",
		"src/routes/logout.ts": "export {}\n", "README.md": "# repo\n"}
	for p, body := range files {
		_ = os.MkdirAll(filepath.Join(repo, filepath.Dir(p)), 0o755)
		_ = os.WriteFile(filepath.Join(repo, p), []byte(body), 0o644)
	}
	m.st.App.Files = []string{"README.md", "src/routes/login.ts", "src/routes/logout.ts"}
	run(m.runPalette(paletteItem{Action: "file:src/routes/login.ts"}, ""), m)
	return m, src
}

func TestTheExplorerOpensFoldersAndFiles(t *testing.T) {
	m, _ := codeRepo(t)
	screen := appScreen(m)
	for _, want := range []string{"EXPLORER", "/ filter files", "▾ src", "▾ routes", "login.ts", "logout.ts", "README.md", "src › routes › login.ts"} {
		if !strings.Contains(screen, want) {
			t.Fatalf("missing %q:\n%s", want, screen)
		}
	}
	m.Update(tea.KeyMsg{Type: tea.KeyCtrlB})
	if !m.st.App.TreeFocus {
		t.Fatal("⌃b moves into the explorer")
	}
	key(m, tea.KeyDown, false) // login.ts -> logout.ts
	run(press(m, "enter"), m)
	if m.st.App.File != "src/routes/logout.ts" || m.st.App.TreeFocus || len(m.st.App.Tabs) != 2 {
		t.Fatalf("⏎ opens the file in a tab of its own: %q %v", m.st.App.File, m.st.App.Tabs)
	}
	m.Update(tea.KeyMsg{Type: tea.KeyCtrlB})
	for i, r := range treeRows(m.st.App) {
		if r.path == "src" {
			m.st.App.TreeSel = i
		}
	}
	key(m, tea.KeyLeft, false)
	if strings.Contains(appScreen(m), "logout.ts  ") && dirOpen(m.st.App, "src") {
		t.Fatal("← folds the folder")
	}
	m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{'/'}})
	typeText(m, "read")
	if rows := treeRows(m.st.App); len(rows) != 1 || rows[0].path != "README.md" {
		t.Fatalf("/ filters the files: %v", rows)
	}
	press(m, "esc") // the filter
	press(m, "esc")
	if m.st.App.TreeFocus || m.st.App.Tab != 3 {
		t.Fatal("esc leaves the explorer for the code")
	}
	m.Update(tea.KeyMsg{Type: tea.KeyCtrlW})
	if m.st.App.File != "src/routes/login.ts" || len(m.st.App.Tabs) != 1 {
		t.Fatalf("⌃w closes the tab and opens the one beside it: %q %v", m.st.App.File, m.st.App.Tabs)
	}
}

func TestAFileIsEditedAndSavedHere(t *testing.T) {
	m, _ := codeRepo(t)
	press(m, "e")
	if !m.st.App.Editing {
		t.Fatal("e edits the file here")
	}
	for i := 0; i < 3; i++ {
		key(m, tea.KeyDown, false)
	}
	key(m, tea.KeyEnd, false)
	run(press(m, "enter"), m)
	typeText(m, "audit()")
	if got := m.st.App.FileLines[4]; got != "  audit()" {
		t.Fatalf("⏎ keeps the indent of the line: %q", got)
	}
	if !strings.Contains(appScreen(m), "● unsaved") {
		t.Fatal("unsaved changes show")
	}
	m.Update(tea.KeyMsg{Type: tea.KeyCtrlS})
	data, _ := os.ReadFile(filepath.Join(m.st.App.Repo, "src/routes/login.ts"))
	if !strings.Contains(string(data), "=> {\n  const user = 1\n  audit()\n})\n") || m.st.App.Dirty {
		t.Fatalf("⌃s saves: %q", data)
	}
	typeText(m, "x")
	if _, cmd := m.Update(tea.KeyMsg{Type: tea.KeyCtrlC}); cmd != nil || !strings.HasPrefix(m.st.Message, "copied") {
		t.Fatalf("in the editor ⌃c copies the line, it never quits: %q", m.st.Message)
	}
	press(m, "esc")
	if m.st.App.Editing || !m.st.App.Dirty || !strings.Contains(m.st.Message, "not saved") {
		t.Fatal("esc stops editing; the changes stay, not saved")
	}
	m.Update(tea.KeyMsg{Type: tea.KeyCtrlW})
	if m.st.Mode != ModeConfirm {
		t.Fatal("closing the tab asks first")
	}
	run(press(m, "y"), m)
	if m.st.App.Dirty || len(m.st.App.Tabs) != 0 {
		t.Fatal("yes throws the changes away")
	}
}

func TestExplainSendsTheSelectedLines(t *testing.T) {
	m, src := codeRepo(t)
	press(m, "v")
	press(m, "down")
	run(press(m, "x"), m)
	if n := len(src.chatSent); n == 0 || src.chatSent[n-1][0] != "Explain src/routes/login.ts:1-2: what it does and why." {
		t.Fatalf("x asks the parent to explain them: %v", src.chatSent)
	}
}

func TestTheLinesAnAgentTouchesAreMarked(t *testing.T) {
	added, lines := map[string]bool{}, map[string]map[int]bool{}
	touchedLines("diff --git a/x.ts b/x.ts\n--- a/x.ts\n+++ b/x.ts\n@@ -8,3 +8,4 @@\n a\n-b\n+B\n+C\n c\n"+
		"diff --git a/new.ts b/new.ts\nnew file mode 100644\n--- /dev/null\n+++ b/new.ts\n@@ -0,0 +1 @@\n+x\n", added, lines)
	if !added["new.ts"] || added["x.ts"] || !lines["x.ts"][9] || lines["x.ts"][8] || lines["new.ts"] != nil {
		t.Fatalf("x.ts line 9 rewritten, new.ts added: %v %v", added, lines)
	}
}

func TestThePanelDocksInAWideTerminal(t *testing.T) {
	m, src := appModel(t)
	withConversation(t, m, src)
	m.Update(tea.WindowSizeMsg{Width: 172, Height: 43})
	wide := func() string { st := m.st; st.Styled = false; return strings.Join(AppFrame(m.view, st, 172, 43), "\n") }
	screen := wide()
	if !strings.Contains(screen, "⇥ hide panel") || !strings.Contains(screen, "Done today") || !strings.Contains(screen, "⇥ hide") {
		t.Fatalf("160 columns and up: the panel stays docked:\n%s", screen)
	}
	press(m, "tab")
	if screen := wide(); strings.Contains(screen, "Done today") || !strings.Contains(screen, "⇥ panel") {
		t.Fatal("⇥ hides it")
	}
	press(m, "tab")
	m.Update(tea.WindowSizeMsg{Width: 120, Height: 36})
	press(m, "tab")
	if !m.st.App.Panel || !strings.Contains(appScreen(m), "╭") {
		t.Fatal("narrower, ⇥ opens it as a floating card")
	}
}
