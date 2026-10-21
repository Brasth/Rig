package ui

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
)

func TestGitStatusBlameAndGutterAreRead(t *testing.T) {
	st := parseStatus("## main...origin/main [ahead 2, behind 1]\x00M  a.go\x00 M b.go\x00MM c.go\x00?? new.txt\x00R  d.go\x00old.go\x00UU e.go\x00")
	if st.branch != "main" || st.upstream != "origin/main" || st.ahead != 2 || st.behind != 1 {
		t.Fatalf("the branch: %+v", st)
	}
	var got []string
	for _, f := range st.files {
		got = append(got, string(f.code)+map[bool]string{true: "+", false: ""}[f.staged]+f.path)
	}
	if strings.Join(got, " ") != "M+a.go Mb.go M+c.go Mc.go ?new.txt R+d.go Ue.go" {
		t.Fatalf("the files, staged and not: %v", got)
	}
	blame := parseBlame("1111111111111111111111111111111111111111 1 1 2\nauthor Huy\nauthor-time 1700000000\nsummary first\nfilename a.go\n\tone\n" +
		"1111111111111111111111111111111111111111 2 2\n\ttwo\n0000000000000000000000000000000000000000 3 3 1\nauthor Not Committed Yet\nsummary x\n\tthree\n")
	if len(blame) != 3 || blame[1].author != "Huy" || blame[1].summary != "first" || !strings.HasPrefix(blame[2].hash, "0000") {
		t.Fatalf("each line's commit: %+v", blame)
	}
	marks := parseGutter("@@ -0,0 +1,2 @@\n+a\n+b\n@@ -5 +7 @@\n-x\n+y\n@@ -10,2 +11,0 @@\n-p\n-q\n")
	if marks[0] != 'A' || marks[1] != 'A' || marks[6] != 'M' || marks[10] != 'D' || len(marks) != 4 {
		t.Fatalf("added, changed and removed lines: %v", marks)
	}
}

// follow runs a command and the commands its messages lead to, as the program would (a few steps deep).
func follow(m *Model, cmd tea.Cmd, depth int) {
	if cmd == nil || depth > 4 {
		return
	}
	msg := cmd()
	if batch, ok := msg.(tea.BatchMsg); ok {
		for _, c := range batch {
			follow(m, c, depth)
		}
		return
	}
	if msg != nil {
		_, next := m.Update(msg)
		follow(m, next, depth+1)
	}
}

// gitRepo makes the code tab's repository a git repository with login.ts committed and then changed.
func gitRepo(t *testing.T) *Model {
	t.Helper()
	m, _ := codeRepo(t)
	repo := m.st.App.Repo
	for _, args := range [][]string{{"init", "-q", "-b", "main"}, {"config", "user.email", "t@example.com"}, {"config", "user.name", "Huy"},
		{"add", "-A"}, {"commit", "-qm", "first"}} {
		if out, err := exec.Command("git", append([]string{"-C", repo}, args...)...).CombinedOutput(); err != nil {
			t.Skipf("no git here: %v %s", err, out)
		}
	}
	path := filepath.Join(repo, "src/routes/login.ts")
	data, _ := os.ReadFile(path)
	_ = os.WriteFile(path, []byte(strings.Replace(string(data), "const user = 1", "const user = 2\n  const role = 'admin'", 1)), 0o644)
	_ = os.WriteFile(filepath.Join(repo, "notes.md"), []byte("# notes\n"), 0o644)
	follow(m, m.openFile("src/routes/login.ts"), 0)
	return m
}

func TestTheCodeTabMarksChangedLinesAndShowsBlame(t *testing.T) {
	m := gitRepo(t)
	a := m.st.App
	if a.GutterFor != a.File || a.Gutter[3] != 'M' || a.Gutter[4] != 'M' {
		t.Fatalf("the changed lines are marked in the gutter: %v", a.Gutter)
	}
	follow(m, press(m, "b"), 0)
	if !a.Blame || len(a.BlameLines) != len(a.FileLines)-1 {
		t.Fatalf("b reads who changed each line: %d lines for %d", len(a.BlameLines), len(a.FileLines))
	}
	out := appScreen(m)
	if !strings.Contains(out, "Huy · ") || !strings.Contains(out, "not committed") {
		t.Fatalf("the blame column names who and when:\n%s", out)
	}
	a.FileOff, a.CurLine = 0, 0
	if out := appScreen(m); !strings.Contains(out, "Huy, ") || !strings.Contains(out, "first") {
		t.Fatalf("the status bar says who changed the line and why:\n%s", out)
	}
	follow(m, press(m, "b"), 0)
	if strings.Contains(appScreen(m), "Huy · ") {
		t.Fatal("b again hides it")
	}
}

func TestTheGitViewStagesDiffsAndCommits(t *testing.T) {
	m := gitRepo(t)
	a := m.st.App
	follow(m, press(m, "g"), 0)
	if !a.GitView || !a.TreeFocus || a.Git == nil {
		t.Fatal("g opens the git view with the keys")
	}
	out := appScreen(m)
	for _, want := range []string{"GIT", "⎇ main", "CHANGES 2", "login.ts", "notes.md", "RECENT", "first"} {
		if !strings.Contains(out, want) {
			t.Fatalf("missing %q:\n%s", want, out)
		}
	}
	rows := gitRows(a)
	for i, r := range rows {
		if r.file.path == "src/routes/login.ts" {
			a.GitSel = i
		}
	}
	follow(m, press(m, "enter"), 0)
	if a.GitDiff == nil || !strings.Contains(appScreen(m), "+   const role = 'admin'") {
		t.Fatalf("⏎ shows the file's diff:\n%s", appScreen(m))
	}
	follow(m, press(m, " "), 0)
	if out := appScreen(m); !strings.Contains(out, "STAGED 1") {
		t.Fatalf("space stages it:\n%s", out)
	}
	follow(m, press(m, "c"), 0)
	if a.CodeAsk == nil || a.CodeAsk.kind != "commit" {
		t.Fatal("c asks for the commit message")
	}
	typeText(m, "Add the role")
	follow(m, press(m, "enter"), 0)
	log, _ := gitOut(a.Repo, "log", "-1", "--format=%s")
	if strings.TrimSpace(log) != "Add the role" || !strings.Contains(m.st.Message, "committed") {
		t.Fatalf("the commit is made: %q %q", log, m.st.Message)
	}
	for i, r := range gitRows(a) {
		if r.file.path == "notes.md" {
			a.GitSel = i
		}
	}
	follow(m, press(m, "u"), 0)
	if m.st.Mode != ModeConfirm || !strings.Contains(m.st.Prompt, "Delete notes.md") {
		t.Fatalf("u asks before throwing work away: %q", m.st.Prompt)
	}
	follow(m, press(m, "y"), 0)
	if _, err := os.Stat(filepath.Join(a.Repo, "notes.md")); !os.IsNotExist(err) {
		t.Fatal("yes deletes the untracked file")
	}
	m.Update(tea.KeyMsg{Type: tea.KeyEsc})
	m.Update(tea.KeyMsg{Type: tea.KeyEsc})
	if a.GitDiff != nil || a.TreeFocus {
		t.Fatal("esc closes the diff, then leaves the view")
	}
}
