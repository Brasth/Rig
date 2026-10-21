package ui

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/Brasth/Rig/shell/internal/view"
	tea "github.com/charmbracelet/bubbletea"
)

func TestCtrlSlashFindsInTheConversation(t *testing.T) {
	m, src := appModel(t)
	withConversation(t, m, src)
	src.chatView = transcript("idle", ce("user", "add a limiter"), ce("assistant", "I added a sliding-window limiter."),
		ce("user", "and tests?"), ce("assistant", "Tests cover both limits."))
	m.chBusy = false
	run(m.fetchChat(true), m)
	a := m.st.App
	appScreen(m) // makes chatLines
	m.Update(tea.KeyMsg{Type: tea.KeyCtrlUnderscore})
	if a.ChatFind == nil {
		t.Fatal("⌃/ opens find in the conversation")
	}
	typeText(m, "limit")
	out := appScreen(m)
	if !strings.Contains(out, "find in conversation") || !strings.Contains(out, "1 of 3") {
		t.Fatalf("the row says which of how many:\n%s", out)
	}
	hits := chatMatches(a.chatLines, "limit")
	if len(hits) != 3 || hits[0] <= hits[1] {
		t.Fatalf("matches newest first: %v", hits)
	}
	m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	if a.ChatFind.at != 1 {
		t.Fatal("⏎ goes to the next (older) match")
	}
	m.Update(tea.KeyMsg{Type: tea.KeyEsc})
	if a.ChatFind != nil {
		t.Fatal("esc closes it")
	}
}

func TestRestingOnANameShowsWhereItIsDefined(t *testing.T) {
	m, _ := codeRepo(t)
	m.SetMouse(true)
	repo := m.st.App.Repo
	_ = os.WriteFile(filepath.Join(repo, "src/routes/util.ts"), []byte("export function check(u) {\n  return u\n}\n"), 0o644)
	_ = os.WriteFile(filepath.Join(repo, "src/routes/use.ts"), []byte("import { check } from './util'\ncheck(1)\n"), 0o644)
	m.st.App.Files = append(m.st.App.Files, "src/routes/util.ts", "src/routes/use.ts")
	follow(m, m.runPalette(paletteItem{Action: "file:src/routes/use.ts"}, ""), 0)
	x, y := findOnScreen(t, m, "check(1)")
	_, cmd := m.Update(tea.MouseMsg{X: x + 1, Y: y, Button: tea.MouseButtonNone, Action: tea.MouseActionMotion})
	if cmd == nil {
		t.Fatal("resting on a name starts the lookup")
	}
	msg := cmd() // the tick, without waiting
	if _, ok := msg.(hoverMsg); !ok {
		t.Fatalf("a hover message: %T", msg)
	}
	m.Update(msg)
	a := m.st.App
	if a.Hover == nil || a.Hover.file != "src/routes/util.ts" || a.Hover.line != 0 || !strings.Contains(a.Hover.text, "function check") {
		t.Fatalf("the defining line and where: %+v", a.Hover)
	}
	if out := m.View(); !strings.Contains(out, "function check(u)") || !strings.Contains(out, "util.ts:1") {
		t.Fatalf("the box is drawn:\n%s", out)
	}
	m.Update(tea.MouseMsg{X: x + 40, Y: y + 3, Button: tea.MouseButtonNone, Action: tea.MouseActionMotion})
	if a.Hover != nil {
		t.Fatal("moving off the name takes the box away")
	}
}

func TestANewAnswerWhileElsewhereMarksTheConversation(t *testing.T) {
	m, src := appModel(t)
	withConversation(t, m, src)
	a := m.st.App
	m.chBusy = false
	run(m.fetchChat(true), m)
	if a.NewChat || a.SeenChat == 0 {
		t.Fatalf("on the conversation everything is seen: %v %d", a.NewChat, a.SeenChat)
	}
	a.Tab = 3
	src.chatView = transcript("idle", append(append([]view.ChatEntry(nil), src.chatView.Entries...), ce("assistant", "one more thing"))...)
	m.chBusy = false
	run(m.fetchChat(true), m)
	if !a.NewChat {
		t.Fatal("an answer that arrived on another tab is new")
	}
	if out := appScreen(m); !strings.Contains(out, "1 conversation●") {
		t.Fatalf("a dot on the tab:\n%s", out)
	}
	a.Tab = 1
	m.chBusy = false
	run(m.fetchChat(true), m)
	if a.NewChat {
		t.Fatal("back on the conversation it is seen")
	}
}
