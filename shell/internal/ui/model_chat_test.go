package ui

import (
	"errors"
	"strings"
	"testing"
	"time"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/Brasth/Rig/shell/internal/view"
)

func chatOn(sessions ...string) *view.ChatStatus {
	st := &view.ChatStatus{Enabled: true, Available: []string{"claude"}, Configured: "claude", Parent: "claude"}
	for _, id := range sessions {
		st.Sessions = append(st.Sessions, struct {
			ID     string `json:"id"`
			Parent string `json:"parent"`
			State  string `json:"state"`
			Turns  int    `json:"turns"`
		}{ID: id, Parent: "claude", State: "idle", Turns: 1})
	}
	return st
}

func transcript(state string, entries ...view.ChatEntry) *view.ChatTranscript {
	return &view.ChatTranscript{Session: "c-1a2b3c4d", Parent: "claude", State: state, Turns: 1, Entries: entries}
}

func ce(role, text string) view.ChatEntry { return view.ChatEntry{Role: role, Text: text} }

// ready returns a model with the experiment on and the Chat tab showing.
func readyChat(t *testing.T) (*Model, *fakeSource) {
	t.Helper()
	m, src := newReady(t)
	src.chatStatus = chatOn("c-1a2b3c4d")
	run(m.fetchChatStatus(), m)
	if !m.st.ChatOn {
		t.Fatal("status should turn the tab on")
	}
	m.st.Tab = ChatTab
	return m, src
}

func TestTheChatTabExistsOnlyWhileTheExperimentIsOn(t *testing.T) {
	m, src := newReady(t)
	if len(TabList(m.st)) != len(Tabs) || strings.Contains(Render(m.view, m.st, 120, 20), "Chat") {
		t.Fatal("no Chat tab by default")
	}
	src.chatStatus = &view.ChatStatus{Enabled: false}
	run(m.fetchChatStatus(), m)
	if m.st.ChatOn {
		t.Fatal("a disabled experiment adds no tab")
	}
	m.csLast = time.Time{}
	src.chatStatus = chatOn()
	run(m.fetchChatStatus(), m)
	if !m.st.ChatOn || TabList(m.st)[len(TabList(m.st))-1] == ChatTab {
		t.Fatal("on a window that fits the Session screen the chat is its middle column, not a tab")
	}
	m.Update(tea.WindowSizeMsg{Width: 100, Height: 30})
	if TabList(m.st)[len(TabList(m.st))-1] != ChatTab {
		t.Fatal("Chat is the last tab when on and the window is too small for the Session screen")
	}
	if !strings.Contains(Render(m.view, m.st, 100, 20), "Chat") {
		t.Fatal("the tab bar shows it")
	}
}

func TestAServerWithoutTheExperimentIsNotAnError(t *testing.T) {
	m, src := newReady(t)
	src.chatStatusErr = errors.New("unknown method chat.status")
	run(m.fetchChatStatus(), m)
	if m.st.ChatOn || m.st.Message != "" || m.st.Stale != "" {
		t.Fatalf("an older server just has no Chat tab: %+v", m.st)
	}
}

func TestTurningTheExperimentOffWhileOnTheTabLeavesIt(t *testing.T) {
	m, src := readyChat(t)
	src.chatStatus = &view.ChatStatus{Enabled: false}
	m.csLast = time.Time{}
	run(m.fetchChatStatus(), m)
	if m.st.Tab != "Jobs" || m.st.ChatOn {
		t.Fatalf("tab %q on %v", m.st.Tab, m.st.ChatOn)
	}
}

func TestStatusIsPolledSlowlyAndTheNewestSessionIsFollowed(t *testing.T) {
	m, src := readyChat(t)
	if m.chatID != "c-1a2b3c4d" {
		t.Fatalf("followed %q", m.chatID)
	}
	if m.fetchChatStatus() != nil {
		t.Fatal("status is not refetched within the interval")
	}
	_ = src
}

func TestTheTranscriptIsFetchedOnlyOnTheTabAndOnlyForAKnownSession(t *testing.T) {
	m, src := readyChat(t)
	src.chatView = transcript("idle", ce("user", "hi"), ce("assistant", "hello"))
	run(m.fetchChat(true), m)
	if len(src.chatReads) != 1 || src.chatReads[0] != "c-1a2b3c4d" || m.st.Chat == nil {
		t.Fatalf("reads %v", src.chatReads)
	}
	m.chLast = time.Now()
	if m.fetchChat(false) != nil {
		t.Fatal("not refetched inside the interval")
	}
	m.st.Tab = "Jobs"
	if m.fetchChat(true) != nil {
		t.Fatal("not on another tab")
	}
	m.st.Tab, m.chatID = ChatTab, ""
	if m.fetchChat(true) != nil {
		t.Fatal("no session, nothing to read")
	}
}

func TestWritingASendsOnEnterAndAdoptsTheSession(t *testing.T) {
	m, src := readyChat(t)
	src.chatStarted = &view.ChatStarted{Session: "c-1a2b3c4d", Parent: "claude", State: "running"}
	src.chatView = transcript("running", ce("user", "what changed?"))
	press(m, "c")
	if m.st.Mode != ModeInput || m.st.InputLabel != "chat" {
		t.Fatalf("mode %v label %q", m.st.Mode, m.st.InputLabel)
	}
	if !strings.Contains(Render(m.view, m.st, 100, 14), "chat › ") {
		t.Fatal("the input line is labelled chat, not enqueue")
	}
	press(m, "w", "h", "y")
	run(press(m, "enter"), m)
	if len(src.chatSent) != 1 || src.chatSent[0] != [2]string{"why", "c-1a2b3c4d"} {
		t.Fatalf("sent %v", src.chatSent)
	}
	if len(src.calls) != 0 {
		t.Fatalf("a chat message is not an enqueue: %v", src.calls)
	}
	if m.st.InputLabel != "" || m.st.Prompt != "" {
		t.Fatal("the input is cleared and relabelled")
	}
}

func TestAFailedSendKeepsTheDraft(t *testing.T) {
	m, src := readyChat(t)
	src.chatSendErr = errors.New("claude is not on PATH")
	press(m, "c", "h", "i")
	run(press(m, "enter"), m)
	if m.st.Mode != ModeInput || m.st.Prompt != "" && !strings.Contains(m.st.Message, "draft retained") {
		t.Fatalf("mode %v prompt %q message %q", m.st.Mode, m.st.Prompt, m.st.Message)
	}
	if !strings.Contains(m.st.Message, "claude is not on PATH") || m.st.InputLabel != "chat" {
		t.Fatalf("message %q label %q", m.st.Message, m.st.InputLabel)
	}
}

func TestEscDropsTheChatInputLabel(t *testing.T) {
	m, _ := readyChat(t)
	press(m, "c")
	press(m, "esc")
	if m.st.Mode != ModeNormal || m.st.InputLabel != "" || m.inputFor != "" {
		t.Fatal("esc leaves chat input cleanly")
	}
	press(m, "tab", "shift+tab") // Session, then Jobs
	press(m, "e")
	if m.inputFor == "chat" || m.st.InputLabel != "" {
		t.Fatal("enqueue after chat is an enqueue")
	}
}

func TestNStartsANewSessionOnTheNextMessage(t *testing.T) {
	m, src := readyChat(t)
	src.chatStarted = &view.ChatStarted{Session: "c-99999999", Parent: "claude", State: "running"}
	src.chatView = transcript("running")
	press(m, "N")
	if m.chatID != "" || m.st.Chat != nil {
		t.Fatal("N forgets the session")
	}
	press(m, "c", "x")
	run(press(m, "enter"), m)
	if src.chatSent[0][1] != "" {
		t.Fatalf("a new session is requested with an empty id, got %q", src.chatSent[0][1])
	}
	if m.chatID != "c-99999999" {
		t.Fatalf("adopted %q", m.chatID)
	}
}

func TestXStopsARunningTurnAfterConfirmationOnly(t *testing.T) {
	m, src := readyChat(t)
	press(m, "x")
	if m.st.Mode == ModeConfirm || !strings.Contains(m.st.Message, "no turn is running") {
		t.Fatal("nothing to stop when idle")
	}
	m.st.Chat = transcript("running")
	press(m, "x")
	if m.st.Mode != ModeConfirm || !strings.Contains(m.st.Prompt, "c-1a2b3c4d") {
		t.Fatalf("confirm expected: %v %q", m.st.Mode, m.st.Prompt)
	}
	press(m, "n")
	if len(src.chatCancelled) != 0 {
		t.Fatal("declining stops nothing")
	}
	press(m, "x")
	run(press(m, "y"), m)
	if len(src.chatCancelled) != 1 || src.chatCancelled[0] != "c-1a2b3c4d" {
		t.Fatalf("cancelled %v", src.chatCancelled)
	}
}

func TestChatScrollsAndFollowsLikeTheLogs(t *testing.T) {
	m, src := readyChat(t)
	var entries []view.ChatEntry
	for i := 0; i < 60; i++ {
		entries = append(entries, ce("assistant", "reply number "+string(rune('A'+i%26))+strings.Repeat("!", i%3)))
	}
	src.chatView = transcript("idle", entries...)
	run(m.fetchChat(true), m)
	press(m, "k", "k")
	if m.st.ChatUp != 2 || !strings.Contains(Render(m.view, m.st, 80, 20), "2 lines back") {
		t.Fatalf("ChatUp %d", m.st.ChatUp)
	}
	press(m, "G")
	if m.st.ChatUp != 0 {
		t.Fatal("G follows")
	}
}

func TestChatTextCannotInjectTerminalSequences(t *testing.T) {
	st := NewState()
	st.ChatOn, st.Tab = true, ChatTab
	st.Chat = transcript("idle", ce("assistant", "fine\x1b[2J\x1b]0;owned\x07 text"), ce("tool", "Read /a\x1b[31m"))
	out := Render(nil, st, 80, 12)
	if strings.Contains(out, "\x1b") {
		t.Fatalf("escape reached the frame: %q", out)
	}
}

func TestSwitchingParentDoesNotBringTheOldConversationBack(t *testing.T) {
	m, src := readyChat(t)
	stale := m.fetchChat(true) // a read of claude's conversation still on its way
	m.Update(parentSetMsg{name: "codex"})
	if m.chatID != "" || appParent(m.st) != "codex" {
		t.Fatalf("switched: %q %q", m.chatID, appParent(m.st))
	}
	src.chatView = transcript("idle", ce("user", "hi"), ce("assistant", "hello"))
	run(stale, m)
	m.csLast = time.Time{}
	run(m.fetchChatStatus(), m) // the server still lists claude's conversation as the newest
	if m.chatID != "" || m.st.Chat != nil || appParent(m.st) != "codex" {
		t.Fatalf("claude came back: id %q parent %q", m.chatID, appParent(m.st))
	}
}
