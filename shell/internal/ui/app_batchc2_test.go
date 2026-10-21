package ui

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/Brasth/Rig/shell/internal/view"
)

func TestTasksAreReadFromTheRepositoryAndRunInTheTerminal(t *testing.T) {
	m, src := codeRepo(t)
	withConversation(t, m, src)
	a := m.st.App
	for p, body := range map[string]string{
		"package.json": `{"scripts": {"build": "tsc", "test": "vitest"}}`,
		"Makefile":     "build:\n\tgo build ./...\n.PHONY: build\n\nVAR := 1\nlint: build\n\tgolint\n",
		"justfile":     "lint:\n  eslint .\n\n_hidden:\n  true\n",
		"go.mod":       "module x\n",
	} {
		_ = os.WriteFile(filepath.Join(a.Repo, p), []byte(body), 0o644)
	}
	got := []string{}
	for _, it := range repoTasks(a.Repo) {
		got = append(got, it.command)
	}
	if strings.Join(got, "|") != "npm run build|npm run test|make build|make lint|just lint|go test ./..." {
		t.Fatalf("the scripts, targets and suites: %v", got)
	}
	pane := newFakePane()
	m.spawn = func([]string, string, []string, int, int) (Pane, error) { return pane, nil }
	run(m.runPalette(paletteItem{Action: "tasks"}, ""), m)
	press(m, "just")
	items := paletteItems(m.st)
	if len(items) != 1 || items[0].Action != "run:just lint" {
		t.Fatalf("the tasks that match: %+v", items)
	}
	press(m, "enter") // the shell's wait is not run here: the fake pane never ends
	if !a.Term || !a.TermFocus || len(pane.writes) != 1 || string(pane.writes[0]) != "just lint\n" {
		t.Fatalf("⏎ runs it in the terminal split: %v %v %q", a.Term, a.TermFocus, pane.writes)
	}
}

func TestF9RunsTheTestAtTheCursor(t *testing.T) {
	repo := t.TempDir()
	_ = os.WriteFile(filepath.Join(repo, "package.json"), []byte(`{"devDependencies": {"vitest": "1"}}`), 0o644)
	goLines := []string{"package x", "", "func TestOne(t *testing.T) {", "\tx := 1", "}", "", "func TestTwo(t *testing.T) {}"}
	pyLines := []string{"class TestAuth:", "    def test_login(self):", "        pass", "", "def test_other():", "    pass"}
	jsLines := []string{"describe('auth', () => {", "  it('logs in', () => {", "    expect(1)", "  })", "})"}
	for _, c := range []struct {
		file  string
		lines []string
		line  int
		want  string
	}{
		{"pkg/x_test.go", goLines, 3, "go test ./pkg -run '^TestOne$'"},
		{"pkg/x_test.go", goLines, 6, "go test ./pkg -run '^TestTwo$'"},
		{"pkg/x_test.go", goLines, 0, "go test ./pkg"},
		{"pkg/x.go", goLines, 3, ""},
		{"tests/test_auth.py", pyLines, 2, "pytest tests/test_auth.py::TestAuth::test_login"},
		{"tests/test_auth.py", pyLines, 5, "pytest tests/test_auth.py::test_other"},
		{"src/auth.test.ts", jsLines, 2, "npx vitest run src/auth.test.ts -t 'logs in'"},
		{"src/auth.ts", jsLines, 2, ""},
	} {
		if got := testAtCursor(repo, c.file, c.lines, c.line); got != c.want {
			t.Fatalf("%s line %d: %q, want %q", c.file, c.line, got, c.want)
		}
	}
	m, _ := codeRepo(t)
	a := m.st.App
	pane := newFakePane()
	m.spawn = func([]string, string, []string, int, int) (Pane, error) { return pane, nil }
	a.File, a.FileLines, a.FileOff = "pkg/x_test.go", goLines, 0
	a.findAt = pos{3, 0}
	run(press(m, "e"), m)
	a.setCursor(pos{3, 0})
	m.Update(tea.KeyMsg{Type: tea.KeyF9})
	if len(pane.writes) != 1 || string(pane.writes[0]) != "go test ./pkg -run '^TestOne$'\n" {
		t.Fatalf("F9 runs the test the cursor is in: %q", pane.writes)
	}
}

// Update2 is Update's command alone.
func (m *Model) Update2(msg tea.Msg) tea.Cmd {
	_, cmd := m.Update(msg)
	return cmd
}

func TestACommitsDiffAndAFilesHistory(t *testing.T) {
	m := gitRepo(t)
	a := m.st.App
	if out, err := exec.Command("git", "-C", a.Repo, "commit", "-qam", "Add the role").CombinedOutput(); err != nil {
		t.Fatalf("commit: %v %s", err, out)
	}
	run(m.openGit(), m)
	rows := gitRows(a)
	commit := -1
	for i, r := range rows {
		if r.kind == "commit" && r.c.subject == "Add the role" {
			commit = i
		}
	}
	if commit < 0 {
		t.Fatalf("the recent commits: %+v", rows)
	}
	a.GitSel = commit
	run(press(m, "enter"), m)
	d := a.GitDiff
	if d == nil || d.commit == "" || d.meta.subject != "Add the role" || len(d.files) != 1 || d.files[0].Path != "src/routes/login.ts" {
		t.Fatalf("⏎ on a commit shows what it changed: %+v", d)
	}
	out := appScreen(m)
	for _, want := range []string{"Add the role", "Huy", "src/routes/login.ts", "const user = 2"} {
		if !strings.Contains(out, want) {
			t.Fatalf("missing %q:\n%s", want, out)
		}
	}
	press(m, "esc")
	if a.GitDiff != nil {
		t.Fatal("esc closes it")
	}
	press(m, "g") // the git view gives way to the files
	run(press(m, "l"), m)
	if a.Palette == nil || !a.Palette.Log {
		t.Fatal("l lists the file's history")
	}
	items := paletteItems(m.st)
	if len(items) != 2 || items[0].Label != "Add the role" || items[1].Label != "first" || !strings.HasSuffix(items[0].Action, ":src/routes/login.ts") {
		t.Fatalf("the commits that touched the file, newest first: %+v", items)
	}
	run(press(m, "enter"), m)
	if d := a.GitDiff; d == nil || d.path != "src/routes/login.ts" || d.meta.subject != "Add the role" {
		t.Fatalf("⏎ shows that commit's change to the file: %+v", d)
	}
}

func TestFormatDocumentAndFormatOnSave(t *testing.T) {
	if _, err := exec.LookPath("gofmt"); err != nil {
		t.Skip("no gofmt here")
	}
	m, _ := codeRepo(t)
	a := m.st.App
	_ = os.WriteFile(filepath.Join(a.Repo, "main.go"), []byte("package main\nfunc  main( ) {\n}\n"), 0o644)
	a.Files = append(a.Files, "main.go")
	run(m.runPalette(paletteItem{Action: "file:main.go"}, ""), m)
	press(m, "e")
	run(m.Update2(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("F"), Alt: true}), m)
	if strings.Join(a.FileLines, "\n") != "package main\n\nfunc main() {\n}\n" || !a.Dirty {
		t.Fatalf("⌥⇧f formats the file in the editor: %q", a.FileLines)
	}
	m.Update(tea.KeyMsg{Type: tea.KeyCtrlZ})
	if a.FileLines[1] != "func  main( ) {" {
		t.Fatalf("one undo takes it back: %q", a.FileLines)
	}
	m.st.AppSettings = &view.AppSettings{FormatOnSave: true}
	a.setCursor(pos{1, 0})
	keyMsg(m, tea.KeyEnd)
	typeText(m, " ")
	run(m.Update2(tea.KeyMsg{Type: tea.KeyCtrlS}), m)
	if a.Dirty {
		t.Fatal("saved")
	}
	if b, _ := os.ReadFile(filepath.Join(a.Repo, "main.go")); string(b) != "package main\n\nfunc main() {\n}\n" {
		t.Fatalf("⌃s formats first when Settings › Editor says so: %q", b)
	}
	a.File = "notes.unknown"
	a.FileLines = []string{"x"}
	if cmd := m.formatDocument(false); cmd != nil || !strings.Contains(m.st.Message, "no formatter") {
		t.Fatalf("a language with no formatter here says so: %q", m.st.Message)
	}
}
