package ui

import (
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/Brasth/Rig/shell/internal/view"
)

func TestShiftTabGoesRoundThePermissionModesAndTheStatusLineNamesIt(t *testing.T) {
	m, src := appModel(t)
	withConversation(t, m, src)
	src.chatView = transcript("idle", ce("user", "hi"), ce("assistant", "hello"))
	src.chatStarted = &view.ChatStarted{Session: "c-1a2b3c4d", Parent: "claude"}
	run(m.fetchChat(true), m)
	typeText(m, "a draft")
	src.chatSent = nil
	follow(m, func() tea.Cmd { _, c := m.Update(tea.KeyMsg{Type: tea.KeyShiftTab}); return c }(), 0)
	if len(src.chatSent) != 1 || src.chatSent[0][0] != "/mode next" {
		t.Fatalf("⇧Tab asks for the next mode: %v", src.chatSent)
	}
	if m.st.Prompt != "a draft" {
		t.Fatalf("the draft in the box stays: %q", m.st.Prompt)
	}
	tr := transcript("idle", ce("user", "hi"), ce("assistant", "hello"))
	tr.Mode = "edits"
	m.st.Chat = tr
	m.st.Prompt = ""
	if out := appScreen(m); !strings.Contains(out, "auto-edit") {
		t.Fatalf("the status line names the mode:\n%s", out)
	}
	// not while a turn runs
	src.chatSent = nil
	m.st.Chat = transcript("running", ce("user", "hi"))
	follow(m, func() tea.Cmd { _, c := m.Update(tea.KeyMsg{Type: tea.KeyShiftTab}); return c }(), 0)
	if len(src.chatSent) != 0 || !strings.Contains(m.st.Message, "between turns") {
		t.Fatalf("it waits for the turn: %v %q", src.chatSent, m.st.Message)
	}
}
