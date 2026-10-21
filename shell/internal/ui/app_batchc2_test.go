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

const twoHunkDiff = `diff --git a/src/a.ts b/src/a.ts
index 1111111..2222222 100644
--- a/src/a.ts
+++ b/src/a.ts
@@ -1,3 +1,3 @@
 const a = 1
-const b = 2
+const b = 3
 const c = 4
@@ -20,3 +20,4 @@
 function f() {
-  return 1
+  return 2
+  // changed
 }
diff --git a/src/b.ts b/src/b.ts
--- a/src/b.ts
+++ b/src/b.ts
@@ -1,1 +1,1 @@
-x
+y
`

func TestHunksAreReviewedOneByOneAndSentBackWithAReason(t *testing.T) {
	m, src := appModel(t)
	withConversation(t, m, src)
	for i := range m.view.Jobs {
		m.view.Jobs[i].Ask = false
	}
	src.diffs = map[string]*view.JobDiff{"job-7f2": {JobID: "job-7f2", Source: "git", Diff: twoHunkDiff}}
	run(press(m, "2"), m)
	a := m.st.App
	files := ParseDiff(twoHunkDiff)
	spans := hunkSpans(files[0])
	if len(spans) != 2 || spans[0].start != 1 || spans[0].end != 3 || spans[1].start != 20 || spans[1].end != 23 {
		t.Fatalf("each hunk knows the lines it covers: %+v", spans)
	}
	out := appScreen(m)
	for _, want := range []string{"hunk 1/2 · lines 1–3", "hunk 2/2 · lines 20–23", "n N hunk", "v reviewed", "x redo"} {
		if !strings.Contains(out, want) {
			t.Fatalf("missing %q:\n%s", want, out)
		}
	}
	press(m, "v") // hunk 1 is fine: marked, and on to the next
	if a.Reviewed[hunkKey("job-7f2", "src/a.ts", 0)] != "ok" || a.DiffHunk != 1 {
		t.Fatalf("v marks the hunk reviewed and moves on: %v %d", a.Reviewed, a.DiffHunk)
	}
	if out := appScreen(m); !strings.Contains(out, "✓ reviewed") || !strings.Contains(out, "1/3 hunks reviewed") {
		t.Fatalf("the mark and the count show:\n%s", out)
	}
	press(m, "N")
	press(m, "v")
	if a.Reviewed[hunkKey("job-7f2", "src/a.ts", 0)] != "" {
		t.Fatal("v on a reviewed hunk takes the mark off")
	}
	press(m, "n", "x")
	if a.RedoHunk == nil || !a.Commenting {
		t.Fatal("x starts a request to redo the chosen hunk")
	}
	if out := appScreen(m); !strings.Contains(out, "redo a.ts:20-23, why ›") {
		t.Fatalf("the prompt names the hunk:\n%s", out)
	}
	typeText(m, "keep the comment out")
	run(press(m, "enter"), m)
	last := src.calls[len(src.calls)-1]
	text, _ := last.params["text"].(string)
	if last.method != "job.message" || last.params["id"] != "job-7f2" {
		t.Fatalf("the helper's inbox gets it: %v", src.calls)
	}
	for _, want := range []string{"Please redo this change in src/a.ts (lines 20-23)", "```diff", "-  return 1", "+  return 2", "Why: keep the comment out"} {
		if !strings.Contains(text, want) {
			t.Fatalf("the request names the file, lines and hunk; missing %q:\n%s", want, text)
		}
	}
	if a.Reviewed[hunkKey("job-7f2", "src/a.ts", 1)] != "redo" || a.RedoHunk != nil || a.Commenting {
		t.Fatalf("the hunk is marked sent back: %v", a.Reviewed)
	}
	if out := appScreen(m); !strings.Contains(out, "↺ redo asked") || !strings.Contains(out, "redo src/a.ts:20-23 — keep the comment out") {
		t.Fatalf("it shows in the hunk and in what you said:\n%s", out)
	}
	press(m, "]")
	if a.DiffHunk != 0 {
		t.Fatal("another file starts at its first hunk")
	}
	press(m, "x", "esc")
	if a.RedoHunk != nil || a.Commenting {
		t.Fatal("esc drops the request")
	}
}

func TestToolsTheParentCallsAreShownWithWhatTheyAnswered(t *testing.T) {
	m, src := appModel(t)
	src.chatView = transcript("idle", ce("user", "what is in a.ts?"), ce("assistant", "Let me look."), ce("tool", "Bash ls -la src"),
		ce("result", "total 8  (+2 lines)"), ce("tool", "Read src/a.ts"), ce("result-error", "ENOENT: no such file"), ce("assistant", "It is not there."))
	m.chatID, m.chBusy = "c-1a2b3c4d", false
	run(m.fetchChat(true), m)
	out := appScreen(m)
	for _, want := range []string{"● Bash ls -la src", "⎿ total 8 (+2 lines)", "● Read src/a.ts", "⎿ ENOENT: no such file", "It is not there."} {
		if !strings.Contains(out, want) {
			t.Fatalf("a tool is a bullet with its answer under it; missing %q:\n%s", want, out)
		}
	}
	a := m.st.App
	a.chatKey = ""
	lines := entryLines(m.st, m.st.Chat.Entries, 80)
	joined := strings.Join(lines, "\n")
	if strings.Index(joined, "ENOENT") < strings.Index(joined, "Read src/a.ts") {
		t.Fatal("the answer comes after its tool")
	}
}

func TestPlanModeShowsInTheConversationAndTheCommandsReachTheParent(t *testing.T) {
	m, src := appModel(t)
	view := transcript("idle", ce("user", "/plan"), ce("system", "plan mode on: claude plans and changes nothing until /plan off or /plan go"))
	view.Plan = true
	src.chatView = view
	m.chatID, m.chBusy = "c-1a2b3c4d", false
	run(m.fetchChat(true), m)
	out := appScreen(m)
	if !strings.Contains(out, "claude · ") || !strings.Contains(out, "plan mode") {
		t.Fatalf("the conversation says it is in plan mode:\n%s", out)
	}
	if !strings.Contains(out, "plan mode on: claude plans and changes nothing") {
		t.Fatalf("and shows what the command answered:\n%s", out)
	}
	for _, cmd := range []string{"/plan add a throttle", "/cost", "/context", "/mcp", "/init", "/review"} {
		src.chatSent = nil
		run(m.appSend(cmd), m)
		if len(src.chatSent) != 1 || src.chatSent[0][0] != cmd {
			t.Fatalf("%s goes to the parent's chat as typed: %v", cmd, src.chatSent)
		}
	}
	var names []string
	for _, c := range SlashCommands {
		names = append(names, c.Name)
	}
	for _, want := range []string{"/plan", "/cost", "/context", "/mcp", "/memory", "/init", "/review", "/security-review", "/clear"} {
		if !strings.Contains(strings.Join(names, " "), want) {
			t.Fatalf("the slash menu lists %s", want)
		}
	}
}

func TestBangRunsACommandInTheTerminalAndMemoryOpensTheInstructionsFile(t *testing.T) {
	m, src := appModel(t)
	withConversation(t, m, src)
	pane := newFakePane()
	m.spawn = func([]string, string, []string, int, int) (Pane, error) { return pane, nil }
	typeText(m, "!git status")
	m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	if len(pane.writes) != 1 || string(pane.writes[0]) != "git status\n" || !m.st.App.Term || len(src.chatSent) != 0 {
		t.Fatalf("!command runs in the terminal split, not in the parent: %q %v", pane.writes, src.chatSent)
	}
	a := m.st.App
	run(m.appSend("/memory"), m)
	if !strings.Contains(m.st.Message, "no CLAUDE.md") || !strings.Contains(m.st.Message, "/init") {
		t.Fatalf("with no instructions file it says how to get one: %q", m.st.Message)
	}
	_ = os.WriteFile(filepath.Join(a.Repo, "CLAUDE.md"), []byte("# rules\n"), 0o644)
	run(m.appSend("/memory"), m)
	if a.File != "CLAUDE.md" || a.Tab != 3 {
		t.Fatalf("/memory opens it in the code tab: %q tab %d", a.File, a.Tab)
	}
	m.chatID, m.st.Chat = "c-x", transcript("idle")
	run(m.appSend("/clear"), m)
	if m.chatID != "" {
		t.Fatal("/clear starts a new conversation")
	}
}

func TestTheFilesATurnChangedShowAsADiffUnderTheAnswer(t *testing.T) {
	m, src := appModel(t)
	src.chatView = transcript("idle", ce("user", "change things"), ce("assistant", "Done."),
		ce("diff", "a.py +2 −1\n@@ -1,3 +1,4 @@\n one\n-two\n+TWO\n three\n+four"), ce("diff", "new.txt +1 −0\n@@ -0,0 +1 @@\n+hello"),
		ce("system", "and 3 more files changed: x.go, y.go, z.go"))
	m.chatID, m.chBusy = "c-1a2b3c4d", false
	run(m.fetchChat(true), m)
	out := appScreen(m)
	for _, want := range []string{"● a.py  +2 −1", "− two", "+ TWO", "+ four", "      one", "● new.txt  +1 −0", "+ hello", "and 3 more files changed"} {
		if !strings.Contains(out, want) {
			t.Fatalf("each changed file is a header and its lines; missing %q:\n%s", want, out)
		}
	}
	if strings.Index(out, "Done.") > strings.Index(out, "● a.py") {
		t.Fatal("the diff comes after the answer it belongs to")
	}
}
