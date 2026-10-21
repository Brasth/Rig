package ui

import (
	"encoding/json"
	"strings"
	"testing"
	"time"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/Brasth/Rig/shell/internal/view"
)

func withParentCommands(t *testing.T, m *Model, src *fakeSource, parent ...string) {
	t.Helper()
	src.parentCmds = &view.ParentCommands{}
	_ = json.Unmarshal([]byte(`{"parent": "claude", "commands": [{"name": "review", "help": "Review the open diff", "kind": "skill"},
		{"name": "git:commit", "help": "Commit with a message", "kind": "command"}, {"name": "help", "help": "theirs", "kind": "command"}]}`), src.parentCmds)
	src.chatStatus = chatOn()
	if len(parent) > 0 {
		src.chatStatus.Parent = parent[0]
		m.parentName = parent[0]
	}
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

func TestADollarWordListsTheParentsSkillsAsCodexDoes(t *testing.T) {
	m, src := appModel(t)
	withConversation(t, m, src)
	withParentCommands(t, m, src, "codex")
	typeText(m, "/rev")
	if got := m.st.slashes(); len(got) != 0 {
		t.Fatalf("codex names its skills with $, not /: %v", got)
	}
	m.st.Prompt = ""
	typeText(m, "please $rev")
	if got := m.st.slashes(); len(got) != 1 || got[0].Name != "$review" {
		t.Fatalf("the skill, not the command: %v", got)
	}
	if screen := appScreen(m); !strings.Contains(screen, "$review") {
		t.Fatalf("the menu shows it:\n%s", screen)
	}
	run(press(m, "enter"), m)
	if m.st.Prompt != "please $review " || len(src.chatSent) != 0 {
		t.Fatalf("⏎ completes the word in place and sends nothing yet: %q", m.st.Prompt)
	}
	typeText(m, "the login change")
	run(press(m, "enter"), m)
	if n := len(src.chatSent); n == 0 || src.chatSent[n-1][0] != "please $review the login change" {
		t.Fatalf("sent as typed: %v", src.chatSent)
	}
	m.st.Prompt = ""
	typeText(m, "$")
	if got := m.st.slashes(); len(got) != 1 {
		t.Fatalf("$ alone lists every skill (commands are not $ names): %v", got)
	}
	typeText(m, "5 off")
	if got := m.st.slashes(); got != nil {
		t.Fatal("a space ends the word")
	}
}

func TestEachParentsBoxNamesSkillsAsItsOwnDoes(t *testing.T) {
	m, src := appModel(t)
	withConversation(t, m, src)
	withParentCommands(t, m, src) // claude
	typeText(m, "$rev")
	if got := m.st.slashes(); got != nil {
		t.Fatalf("claude has no $ mentions: %v", got)
	}
	m.st.Prompt = ""
	typeText(m, "/rev")
	if got := m.st.slashes(); len(got) != 1 || got[0].Name != "/review" {
		t.Fatalf("claude's / lists its skills: %v", got)
	}
	m2, src2 := appModel(t)
	withConversation(t, m2, src2)
	withParentCommands(t, m2, src2, "pi")
	typeText(m2, "/skill")
	if got := m2.st.slashes(); len(got) != 1 || got[0].Name != "/skill:review" {
		t.Fatalf("pi calls a skill /skill:name: %v", got)
	}
}
