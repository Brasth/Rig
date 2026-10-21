package ui

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
)

func TestConflictsAreParsedAndResolved(t *testing.T) {
	lines := strings.Split("a\n<<<<<<< HEAD\nours 1\nours 2\n||||||| base\nold\n=======\ntheirs 1\n>>>>>>> feature\nz\n<<<<<<<\nx\n=======\ny\n>>>>>>>\n", "\n")
	cs := conflicts(lines)
	if len(cs) != 2 || cs[0].oursName != "HEAD" || cs[0].theirsName != "feature" || strings.Join(cs[0].ours, ",") != "ours 1,ours 2" ||
		strings.Join(cs[0].theirs, ",") != "theirs 1" || cs[1].start != 10 || cs[1].end != 14 {
		t.Fatalf("two conflicts, the diff3 base left out: %+v", cs)
	}
	got := resolveConflict(lines, cs[0], "both")
	if strings.Join(got[:5], ",") != "a,ours 1,ours 2,theirs 1,z" || len(conflicts(got)) != 1 {
		t.Fatalf("both keeps ours then theirs: %v", got)
	}
	got = resolveConflict(lines, cs[1], "theirs")
	if got[10] != "y" || got[11] != "" {
		t.Fatalf("theirs: %v", got[9:])
	}
}

func TestAConflictedFileIsResolvedSideBySide(t *testing.T) {
	m := gitRepo(t)
	a := m.st.App
	repo := a.Repo
	git := func(args ...string) string {
		out, err := exec.Command("git", append([]string{"-C", repo}, args...)...).CombinedOutput()
		if err != nil && !strings.Contains(string(out), "CONFLICT") {
			t.Fatalf("git %v: %v %s", args, err, out)
		}
		return string(out)
	}
	git("stash", "-u")
	git("switch", "-qc", "feature")
	_ = os.WriteFile(filepath.Join(repo, "README.md"), []byte("# repo\n\ntheirs line\n"), 0o644)
	git("commit", "-qam", "theirs")
	git("switch", "-q", "main")
	_ = os.WriteFile(filepath.Join(repo, "README.md"), []byte("# repo\n\nours line\n"), 0o644)
	git("commit", "-qam", "ours")
	if out := git("merge", "feature"); !strings.Contains(out, "CONFLICT") {
		t.Fatalf("a conflict was expected: %s", out)
	}
	follow(m, press(m, "g"), 0)
	rows := gitRows(a)
	for i, r := range rows {
		if r.file.path == "README.md" {
			a.GitSel = i
			if r.file.code != 'U' {
				t.Fatalf("marked as a conflict: %q", r.file.code)
			}
		}
	}
	follow(m, press(m, "enter"), 0)
	if a.Conflict == nil || a.File != "README.md" {
		t.Fatal("⏎ on a conflicted file opens its conflicts")
	}
	out := appScreen(m)
	for _, want := range []string{"1 conflicts", "ours (HEAD)", "theirs (feature)", "ours line", "theirs line", "o ours", "t theirs", "b both"} {
		if !strings.Contains(out, want) {
			t.Fatalf("missing %q:\n%s", want, out)
		}
	}
	follow(m, press(m, "t"), 0)
	data, _ := os.ReadFile(filepath.Join(repo, "README.md"))
	if string(data) != "# repo\n\ntheirs line\n" {
		t.Fatalf("t keeps theirs and writes the file: %q", data)
	}
	if status, _ := gitOut(repo, "status", "--porcelain", "--", "README.md"); !strings.HasPrefix(status, "M ") {
		t.Fatalf("no conflicts left: marked resolved (staged): %q", status)
	}
	if !strings.Contains(appScreen(m), "no conflicts left") {
		t.Fatalf("says so:\n%s", appScreen(m))
	}
	m.Update(tea.KeyMsg{Type: tea.KeyEsc})
	if a.Conflict != nil {
		t.Fatal("esc shows the file")
	}
}

func TestASearchResultOpensWithTheCursorOnTheMatch(t *testing.T) {
	m, _ := codeRepo(t)
	a := m.st.App
	a.Search = &SearchState{Query: "user", Results: []SearchHit{{Path: "src/routes/login.ts", Line: 4, Text: "  const user = 1"}}}
	follow(m, press(m, "enter"), 0)
	if a.File != "src/routes/login.ts" || !a.Editing || a.cursor() != (pos{3, 8}) {
		t.Fatalf("the cursor on the match, editing: %q %v %v", a.File, a.Editing, a.cursor())
	}
}
