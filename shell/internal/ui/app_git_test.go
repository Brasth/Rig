package ui

import (
	"fmt"
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

func TestTheCursorLineShowsItsBlameInline(t *testing.T) {
	m := gitRepo(t)
	a := m.st.App
	follow(m, m.openFile("src/routes/login.ts"), 0) // as saved: blame lines up with the file
	press(m, "e")
	a.setCursor(pos{0, 0})
	if out := appScreen(m); !strings.Contains(out, "Huy, ") || !strings.Contains(out, "· first") {
		t.Fatalf("the cursor line ends with who changed it, when and why:\n%s", out)
	}
	a.setCursor(pos{4, 0})
	if out := appScreen(m); !strings.Contains(out, "You · not committed yet") {
		t.Fatalf("a line not committed says so:\n%s", out)
	}
	typeText(m, "x")
	if out := appScreen(m); strings.Contains(out, "not committed yet") || strings.Contains(out, "· first") {
		t.Fatalf("with edits not saved the lines move: no blame until saved:\n%s", out)
	}
}

func TestTheGitViewSwitchesBranchesAndPulls(t *testing.T) {
	m := gitRepo(t)
	a := m.st.App
	repo := a.Repo
	follow(m, press(m, "g"), 0)
	out := appScreen(m)
	for _, want := range []string{"⎇ main ▾", "Message (c to commit)", "✓ Commit", "↓ Pull", "↑ Push"} {
		if !strings.Contains(out, want) {
			t.Fatalf("missing %q:\n%s", want, out)
		}
	}
	m.SetMouse(true)
	x, y := findOnScreen(t, m, "Message (c to commit)")
	follow(m, func() tea.Msg { return nil }, 0)
	clickAt(m, x, y)
	if a.CodeAsk == nil && !strings.Contains(m.st.Message, "nothing staged") {
		t.Fatalf("a click on the message box commits (or says nothing is staged): %q", m.st.Message)
	}
	a.CodeAsk = nil
	follow(m, press(m, "b"), 0)
	if a.Palette == nil || !a.Palette.Branch || len(a.Branches) != 1 {
		t.Fatalf("b lists the branches: %v", a.Branches)
	}
	typeText(m, "feature")
	follow(m, press(m, "enter"), 0)
	if b, _ := gitOut(repo, "branch", "--show-current"); strings.TrimSpace(b) != "feature" || a.Git.branch != "feature" {
		t.Fatalf("a new name makes the branch and switches to it: %q %q", b, m.st.Message)
	}
	follow(m, m.openBranches(), 0)
	a.Palette.Query = "main"
	follow(m, press(m, "enter"), 0)
	if b, _ := gitOut(repo, "branch", "--show-current"); strings.TrimSpace(b) != "main" {
		t.Fatalf("choosing a branch switches to it: %q %q", b, m.st.Message)
	}
	// a remote with one more commit: P pulls it
	upstream := t.TempDir()
	for _, args := range [][]string{{"clone", "-q", "--bare", repo, upstream}} {
		if out, err := exec.Command("git", args...).CombinedOutput(); err != nil {
			t.Fatalf("%v %s", err, out)
		}
	}
	other := t.TempDir()
	for _, args := range [][]string{{"clone", "-q", upstream, other}, {"-C", other, "-c", "user.email=t@example.com", "-c", "user.name=Ana",
		"commit", "-q", "--allow-empty", "-m", "from Ana"}, {"-C", other, "push", "-q"}, {"-C", repo, "remote", "add", "origin", upstream},
		{"-C", repo, "fetch", "-q", "origin"}, {"-C", repo, "branch", "-q", "--set-upstream-to=origin/main"}, {"-C", repo, "stash", "-q", "-u"}} {
		if out, err := exec.Command("git", args...).CombinedOutput(); err != nil {
			t.Fatalf("%v %s", err, out)
		}
	}
	follow(m, m.fetchGit(), 0)
	if !strings.Contains(appScreen(m), "↓1") {
		t.Fatalf("one commit to pull:\n%s", appScreen(m))
	}
	follow(m, press(m, "P"), 0)
	log, _ := gitOut(repo, "log", "-1", "--format=%s")
	if strings.TrimSpace(log) != "from Ana" || m.st.Message != "pulled" {
		t.Fatalf("P pulls: %q %q", log, m.st.Message)
	}
}

func TestOneHunkIsStagedOnItsOwn(t *testing.T) {
	m := gitRepo(t)
	a := m.st.App
	repo := a.Repo
	// a file with two changes far apart: two hunks
	var body strings.Builder
	for i := 1; i <= 30; i++ {
		fmt.Fprintf(&body, "line %d\n", i)
	}
	_ = os.WriteFile(filepath.Join(repo, "long.txt"), []byte(body.String()), 0o644)
	for _, args := range [][]string{{"add", "long.txt"}, {"commit", "-qm", "long"}} {
		if out, err := exec.Command("git", append([]string{"-C", repo}, args...)...).CombinedOutput(); err != nil {
			t.Fatalf("%v %s", err, out)
		}
	}
	changed := strings.Replace(strings.Replace(body.String(), "line 2\n", "line TWO\n", 1), "line 28\n", "line TWENTY-EIGHT\n", 1)
	_ = os.WriteFile(filepath.Join(repo, "long.txt"), []byte(changed), 0o644)
	follow(m, press(m, "g"), 0)
	for i, r := range gitRows(a) {
		if r.file.path == "long.txt" {
			a.GitSel = i
		}
	}
	follow(m, press(m, "enter"), 0)
	if d := a.GitDiff; d == nil || len(hunkPatchParts(d.raw)) != 2 || !strings.Contains(appScreen(m), "hunk 1 of 2") {
		t.Fatalf("two hunks:\n%s", appScreen(m))
	}
	follow(m, press(m, "]"), 0)
	if a.GitDiff.hunk != 1 || !strings.Contains(appScreen(m), "hunk 2 of 2") {
		t.Fatal("] chooses the next hunk")
	}
	follow(m, press(m, "h"), 0)
	staged, _ := gitOut(repo, "diff", "--cached", "--", "long.txt")
	unstaged, _ := gitOut(repo, "diff", "--", "long.txt")
	if !strings.Contains(staged, "+line TWENTY-EIGHT") || strings.Contains(staged, "+line TWO") || !strings.Contains(unstaged, "+line TWO") {
		t.Fatalf("h stages only the second hunk:\nstaged %s\nunstaged %s", staged, unstaged)
	}
	if !strings.Contains(m.st.Message, "staged hunk 2 of long.txt") {
		t.Fatalf("says what it did: %q", m.st.Message)
	}
	// the staged view: h there unstages it again
	for i, r := range gitRows(a) {
		if r.kind == "staged" && r.file.path == "long.txt" {
			a.GitSel = i
		}
	}
	follow(m, press(m, "enter"), 0)
	if a.GitDiff == nil || !a.GitDiff.staged {
		t.Fatal("the staged diff")
	}
	follow(m, press(m, "h"), 0)
	if staged, _ := gitOut(repo, "diff", "--cached", "--", "long.txt"); strings.TrimSpace(staged) != "" {
		t.Fatalf("h in the staged diff unstages the hunk: %s", staged)
	}
}

func hunkPatchParts(raw string) []string { _, parts := hunks(raw); return parts }
