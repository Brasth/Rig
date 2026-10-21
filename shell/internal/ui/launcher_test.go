package ui

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	tea "github.com/charmbracelet/bubbletea"
)

func launcherFor(t *testing.T) (*Launcher, string, *[]string) {
	t.Helper()
	t.Cleanup(func() { ApplyTheme("rig") })
	base := t.TempDir()
	home, code := filepath.Join(base, "home"), filepath.Join(base, "code")
	ready, bare := filepath.Join(base, "my-repo"), filepath.Join(base, "web")
	for _, d := range []string{home, filepath.Join(ready, ".rig"), bare} {
		if err := os.MkdirAll(d, 0o755); err != nil {
			t.Fatal(err)
		}
	}
	_ = os.WriteFile(filepath.Join(ready, ".rig", "harness.toml"), []byte("[project]\nenabled = true\n"), 0o644)
	now := time.Now()
	recents, _ := json.Marshal([]Recent{{ready, float64(now.Add(-2 * time.Hour).Unix())}, {bare, float64(now.Unix())}})
	_ = os.WriteFile(filepath.Join(home, "recent.json"), recents, 0o644)
	l := NewLauncher(home, code, "")
	l.st.Styled = false
	var ran []string
	l.run = func(name string, args ...string) ([]byte, error) {
		ran = append(ran, name+" "+strings.Join(args, " "))
		if name == "git" && args[0] == "clone" {
			return nil, os.MkdirAll(args[len(args)-1], 0o755)
		}
		return nil, nil
	}
	l.Update(tea.WindowSizeMsg{Width: 120, Height: 36})
	return l, base, &ran
}

func TestTheLauncherListsRecentProjectsAndOpensOne(t *testing.T) {
	l, base, _ := launcherFor(t)
	out := l.View()
	for _, want := range []string{"Open a project", "Type a path, paste a git URL, or pick a recent one.", "Recent", "my-repo", "2h", "web", "not set up",
		"⌃g my GitHub repos"} {
		if !strings.Contains(out, want) {
			t.Errorf("missing %q:\n%s", want, out)
		}
	}
	_, cmd := l.Update(tea.KeyMsg{Type: tea.KeyEnter})
	if l.Open != filepath.Join(base, "my-repo") || cmd == nil {
		t.Fatalf("Enter opens the chosen project: %q", l.Open)
	}
}

func TestANotSetUpProjectIsSetUpAfterAQuestion(t *testing.T) {
	l, base, ran := launcherFor(t)
	l.Update(tea.KeyMsg{Type: tea.KeyDown})
	l.Update(tea.KeyMsg{Type: tea.KeyEnter})
	if !strings.Contains(l.confirm, "Set up Rig in") || l.Open != "" {
		t.Fatalf("asks first: %q", l.confirm)
	}
	l.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("y")})
	if l.Open != filepath.Join(base, "web") || len(*ran) != 1 || !strings.Contains((*ran)[0], "rig init") {
		t.Fatalf("rig init in it, then open: %q %v", l.Open, *ran)
	}
}

func TestAGitURLIsClonedThenOpened(t *testing.T) {
	l, base, ran := launcherFor(t)
	for _, r := range "github.com/brasth/api-gateway" {
		l.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{r}})
	}
	_, cmd := l.Update(tea.KeyMsg{Type: tea.KeyEnter})
	if !strings.Contains(l.View(), "Getting api-gateway ready") {
		t.Fatalf("the opening screen:\n%s", l.View())
	}
	l.Update(cmd())
	out := l.View()
	for _, want := range []string{"✓ clone", "✓ find the dev container", "○ set up Rig here", "⏎ open when ready"} {
		if !strings.Contains(out, want) {
			t.Errorf("missing %q:\n%s", want, out)
		}
	}
	if (*ran)[0] != "git clone --quiet https://github.com/brasth/api-gateway "+filepath.Join(base, "code", "api-gateway") {
		t.Fatalf("ran %v", *ran)
	}
	l.Update(tea.KeyMsg{Type: tea.KeyEnter})
	l.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("y")})
	if l.Open != filepath.Join(base, "code", "api-gateway") {
		t.Fatalf("opened %q", l.Open)
	}
}
