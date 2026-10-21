package ui

import (
	"encoding/json"
	"strings"
	"testing"
	"time"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/Brasth/Rig/shell/internal/view"
)

func withParentCommands(t *testing.T, m *Model, src *fakeSource) {
	t.Helper()
	src.parentCmds = &view.ParentCommands{}
	_ = json.Unmarshal([]byte(`{"parent": "claude", "commands": [{"name": "review", "help": "Review the open diff", "kind": "skill"},
		{"name": "git:commit", "help": "Commit with a message", "kind": "command"}, {"name": "help", "help": "theirs", "kind": "command"}]}`), src.parentCmds)
	src.chatStatus = chatOn()
	m.csLast, m.cmdsFor = time.Time{}, ""
	_, cmd := m.Update(m.fetchChatStatus()()) // the status names the parent, and its commands are asked for
	run(cmd, m)
}

func TestTheParentsSkillsAndCommandsAreInTheSlashMenu(t *testing.T) {
	m, src := appModel(t)
	withConversation(t, m, src)
	withParentCommands(t, m, src)
	if len(m.st.ParentCommands) != 2 {
		t.Fatalf("the parent's two, without the one named as Rig's own /help: %v", m.st.ParentCommands)
	}
	typeText(m, "/rev")
	screen := appScreen(m)
	if !strings.Contains(screen, "/review") || !strings.Contains(screen, "skill · Review the open diff") {
		t.Fatalf("the skill in the menu:\n%s", screen)
	}
	run(press(m, "enter"), m)
	if m.st.Prompt != "/review " {
		t.Fatalf("⏎ puts it in the box for its arguments: %q", m.st.Prompt)
	}
	typeText(m, "the auth change")
	run(press(m, "enter"), m)
	if n := len(src.chatSent); n == 0 || src.chatSent[n-1][0] != "/review the auth change" {
		t.Fatalf("sent to the parent as typed; the server expands it for a parent that needs it: %v", src.chatSent)
	}
	m.st.Prompt = ""
	typeText(m, "/commit")
	if got := m.st.slashes(); len(got) != 1 || got[0].Name != "/git:commit" {
		t.Fatalf("a namespaced command is found by its own name: %q %v", m.st.Prompt, got)
	}
}

func TestThePaletteFindsTheParentsSkills(t *testing.T) {
	m, src := appModel(t)
	withConversation(t, m, src)
	withParentCommands(t, m, src)
	m.Update(tea.KeyMsg{Type: tea.KeyCtrlK})
	m.st.App.Palette.Query = "review"
	items := paletteItems(m.st)
	for i, it := range items {
		if it.Action == "slash:/review" {
			m.st.App.Palette.Sel = i
			run(press(m, "enter"), m)
			if m.st.Prompt != "/review " || m.st.App.Palette != nil {
				t.Fatalf("⏎ puts the skill in the box: %q", m.st.Prompt)
			}
			return
		}
	}
	t.Fatalf("the skill is a palette row: %v", items)
}

func TestAnOlderServerLeavesRigsOwnCommands(t *testing.T) {
	m, src := appModel(t)
	withConversation(t, m, src)
	src.chatStatus = chatOn()
	m.csLast, m.cmdsFor = time.Time{}, ""
	_, cmd := m.Update(m.fetchChatStatus()())
	run(cmd, m)
	if m.st.ParentCommands != nil || m.cmdsFor != "claude" {
		t.Fatal("no chat.commands: asked once, nothing added")
	}
}
