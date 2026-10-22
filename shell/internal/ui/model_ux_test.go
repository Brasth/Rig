package ui

import (
	"strings"
	"testing"
	"time"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/Brasth/Rig/shell/internal/view"
)

func typeText(m *Model, text string) {
	for _, r := range text {
		m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{r}})
	}
}

func TestTheScreenOpensAsTheMockupDrawsIt(t *testing.T) {
	m, _, _ := chatSession(t)
	press(m, "esc")
	run(m.fetchChatStatus(), m)
	if typingChat(m.st) || m.st.Focus != "" {
		t.Fatal("the jobs have the keyboard")
	}
	out := Render(m.view, m.st, 120, 36)
	for _, want := range []string{"│ › Ask the parent, or /queue to park a task▏", " Tab pane  Enter details  a allow  d deny  e enqueue  v evidence  w workspace  ? help  q quit"} {
		if !strings.Contains(out, want) {
			t.Errorf("missing %q:\n%s", want, out)
		}
	}
	press(m, "m")
	if !typingChat(m.st) || m.st.Prompt != "" {
		t.Fatal("m starts a message")
	}
}

func TestTheAskLineKeepsTheKeyboardOnceItHasIt(t *testing.T) {
	m, src, _ := chatSession(t)
	m.SetParent("claude")
	src.chatStarted = &view.ChatStarted{Session: "c-1a2b3c4d", Parent: "claude", State: "running"}
	if !typingChat(m.st) {
		t.Fatal("i gave the ask line the keyboard")
	}
	typeText(m, "pvq") // board letters are text while the box has the keyboard
	if m.st.Prompt != "pvq" || len(src.parentNames) != 0 {
		t.Fatalf("prompt %q, parent screens %v", m.st.Prompt, src.parentNames)
	}
	m.Update(tea.KeyMsg{Type: tea.KeyCtrlC})
	if m.st.Prompt != "" {
		t.Fatal("Ctrl-C clears a draft before it quits")
	}
	typeText(m, "why")
	run(press(m, "enter"), m)
	if len(src.chatSent) != 1 || src.chatSent[0][0] != "why" || !typingChat(m.st) || m.st.Prompt != "" {
		t.Fatalf("sent %v; the box stays ready and empty: %+v", src.chatSent, m.st.Prompt)
	}
	if last := m.st.Chat.Entries[len(m.st.Chat.Entries)-1]; last.Role != "user" || last.Text != "why" {
		t.Fatalf("the message shows at once: %+v", last)
	}
}

func TestEscClearsThenHandsTheKeyboardToTheBoard(t *testing.T) {
	m, src, _ := chatSession(t)
	typeText(m, "hello")
	press(m, "esc")
	if m.st.Prompt != "" || !typingChat(m.st) {
		t.Fatal("the first Esc clears the draft")
	}
	press(m, "esc")
	if typingChat(m.st) || m.st.Focus != "" {
		t.Fatalf("the second Esc gives the jobs the keyboard: %q", m.st.Focus)
	}
	runAll(m, press(m, "p"))
	if src.parentCalls != 1 {
		t.Fatal("p opens the parent's own screen on board keys")
	}
	m, _, _ = chatSession(t)
	press(m, "esc", "i")
	if !typingChat(m.st) {
		t.Fatal("i types again")
	}
	press(m, "esc", "/")
	if !typingChat(m.st) || m.st.Prompt != "/" {
		t.Fatal("/ types again, starting a command")
	}
}

func TestUpAndDownMoveTheJobsWhileTyping(t *testing.T) {
	m, _, _ := chatSession(t)
	m.Update(tea.KeyMsg{Type: tea.KeyDown})
	if m.st.Selected["Jobs"] != 1 || !typingChat(m.st) {
		t.Fatalf("down moves the jobs selection: %d", m.st.Selected["Jobs"])
	}
	m.Update(tea.KeyMsg{Type: tea.KeyPgUp})
	if m.st.ChatUp != 8 {
		t.Fatal("PgUp scrolls the conversation")
	}
}

func TestSlashListsTheCommandsAndRunsTheChosenOne(t *testing.T) {
	m, src, _ := chatSession(t)
	typeText(m, "/")
	out := Render(m.view, m.st, 120, 36)
	listed := map[string]bool{}
	for _, c := range m.st.slashes() {
		listed[c.Name] = true
	}
	for _, c := range SlashCommands {
		if !listed[c.Name] {
			t.Errorf("the list is missing %s", c.Name)
		}
	}
	if !strings.Contains(out, SlashCommands[0].Name) || !strings.Contains(out, "/plan") {
		t.Errorf("the menu shows its first rows:\n%s", out)
	}
	typeText(m, "n")
	typeText(m, "e")
	if got := m.st.slashes(); len(got) != 1 || got[0].Name != "/new" {
		t.Fatalf("typing narrows the list: %v", got)
	}
	run(press(m, "enter"), m)
	if m.chatID != "" || m.st.Chat != nil || !strings.Contains(m.st.Message, "new conversation") || len(src.chatSent) != 0 {
		t.Fatalf("/new starts a new conversation without sending anything: %q %v", m.st.Message, src.chatSent)
	}
	typeText(m, "/qu")
	m.Update(tea.KeyMsg{Type: tea.KeyTab})
	if m.st.Prompt != "/queue " {
		t.Fatalf("Tab completes a command that takes text: %q", m.st.Prompt)
	}
	typeText(m, "Docs for rate limit")
	run(press(m, "enter"), m)
	if last := src.calls[len(src.calls)-1]; last.method != "queue.add" || last.params["text"] != "Docs for rate limit" || len(src.chatSent) != 0 {
		t.Fatalf("/queue parks the task: %v", src.calls)
	}
	typeText(m, "/jo")
	m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	if m.st.Tab != "Jobs" || typingChat(m.st) {
		t.Fatalf("/jobs opens the Jobs tab and the board keys: %q", m.st.Tab)
	}
}

func TestSlashStopEndsTheRunningTurn(t *testing.T) {
	m, src, _ := chatSession(t)
	m.chatID = "c-1a2b3c4d"
	m.st.Chat.State = "running"
	typeText(m, "/stop")
	run(press(m, "enter"), m)
	if len(src.chatCancelled) != 1 || src.chatCancelled[0] != "c-1a2b3c4d" {
		t.Fatalf("cancelled %v", src.chatCancelled)
	}
}

func TestAProjectWithNoParentChoosesOneInThePicker(t *testing.T) {
	m, src, _, _ := sessionModel(t, 120, 36)
	src.chatStatus = chatOn()
	src.chatStatus.Configured, src.chatStatus.Parent = "", "claude"
	src.chatStatus.Available = []string{"claude", "codex"}
	src.chatStatus.Parents = []string{"agy", "claude", "codex", "grok", "omp", "opencode", "pi"}
	m.csLast = time.Time{}
	run(m.fetchChatStatus(), m)
	if m.st.Picker == nil {
		t.Fatal("the picker opens by itself when the project names no parent")
	}
	out := Render(m.view, m.st, 120, 36)
	for _, want := range []string{"Choose the parent", "claude     installed · in use", "codex      installed", "grok       not installed"} {
		if !strings.Contains(out, want) {
			t.Errorf("missing %q:\n%s", want, out)
		}
	}
	if m.st.Picker.Items[0].Name != "claude" || m.st.Picker.Items[1].Name != "codex" {
		t.Fatalf("installed first, in Rig's order: %+v", m.st.Picker.Items)
	}
	press(m, "j", "j")
	run(press(m, "enter"), m)
	if len(src.parentSet) != 0 || m.st.Picker == nil || !strings.Contains(m.st.Message, "not installed") {
		t.Fatalf("an uninstalled parent cannot be chosen: %v %q", src.parentSet, m.st.Message)
	}
	press(m, "k")
	run(press(m, "enter"), m)
	if len(src.parentSet) != 1 || src.parentSet[0] != "codex" || m.st.Picker != nil {
		t.Fatalf("saved %v", src.parentSet)
	}
	if m.parentName != "codex" || m.st.ChatParent != "codex" || !strings.Contains(m.st.Message, "saved in .rig/harness.toml") {
		t.Fatalf("the new parent answers from now on: %q %q", m.parentName, m.st.Message)
	}
	m.csLast = time.Time{}
	run(m.fetchChatStatus(), m)
	if m.st.Picker != nil {
		t.Fatal("the picker opens by itself only once")
	}
	typeText(m, "/parent")
	run(press(m, "enter"), m)
	if m.st.Picker == nil || m.st.Picker.Current != "codex" || m.st.Picker.Items[m.st.Picker.Sel].Name != "codex" {
		t.Fatalf("/parent opens it on the parent in use: %+v", m.st.Picker)
	}
	press(m, "esc")
	if m.st.Picker != nil {
		t.Fatal("Esc closes it")
	}
}

func TestTheReplyStreamsUnderASpinnerWithTheTimeItHasTaken(t *testing.T) {
	m, src, _ := chatSession(t)
	now := time.Date(2026, 10, 21, 12, 0, 42, 0, time.UTC)
	m.now = func() time.Time { return now }
	src.chatView = transcript("running", view.ChatEntry{Role: "user", Text: "why", At: "2026-10-21T12:00:00Z"})
	src.chatView.Partial = "Because the tests"
	m.chBusy = false
	run(m.fetchChat(true), m)
	out := m.View()
	for _, want := range []string{"parent › Because the tests", "working… 42s", "/stop stops it", "Parent · claude · working"} {
		if !strings.Contains(out, want) {
			t.Errorf("missing %q:\n%s", want, Render(m.view, m.st, 120, 36))
		}
	}
	if cmd := m.spin(); cmd != nil {
		t.Fatal("one spinner tick at a time")
	}
	m.Update(spinMsg{})
	if !m.spinning {
		t.Fatal("the spinner keeps turning while the parent works")
	}
	src.chatView = transcript("idle", view.ChatEntry{Role: "user", Text: "why"}, ce("assistant", "Because the tests were stale."))
	m.chBusy = false
	run(m.fetchChat(true), m)
	m.spinning = false
	if m.spin() != nil || strings.Contains(m.View(), "working…") {
		t.Fatal("no spinner once the answer is in")
	}
}

func TestTabMovesBetweenThePanesAsTheMockupsFooterSays(t *testing.T) {
	m, src, _ := chatSession(t)
	press(m, "esc")
	if m.st.Focus != "" || typingChat(m.st) {
		t.Fatalf("Esc: the jobs: %q", m.st.Focus)
	}
	out := Render(m.view, m.st, 120, 36)
	if !strings.Contains(out, " Tab pane  Enter details  a allow  d deny  e enqueue  v evidence  w workspace  ? help  q quit") {
		t.Fatalf("the mockup's footer, word for word:\n%s", out)
	}
	press(m, "tab")
	if m.st.Focus != "parent" || !typingChat(m.st) {
		t.Fatalf("Tab: the ask line: %q", m.st.Focus)
	}
	press(m, "tab")
	if m.st.Focus != "workspace" || !strings.Contains(Render(m.view, m.st, 120, 36), "Tab pane  u up  x shell  d doctor") {
		t.Fatalf("Tab again: the workspace: %q", m.st.Focus)
	}
	press(m, "u")
	if m.st.Mode != ModeConfirm || !strings.Contains(m.st.Prompt, "rig env up") {
		t.Fatalf("u asks before starting the container: %q", m.st.Prompt)
	}
	var ran []string
	m.exec = func(argv []string, done func(error) tea.Msg) tea.Cmd { ran = argv; return nil }
	press(m, "y")
	if strings.Join(ran, " ") != "rig env up" {
		t.Fatalf("ran %v", ran)
	}
	src.shellPlan = &view.ShellPlan{Available: true, Argv: []string{"dc", "exec"}, Workspace: "a3f9c1"}
	run(press(m, "x"), m)
	if strings.Join(ran, " ") != "dc exec" {
		t.Fatalf("x opens a shell in the container: %v", ran)
	}
	press(m, "d")
	if m.st.Tab != EnvTab {
		t.Fatal("d opens the doctor's full report")
	}
	m, _, _ = chatSession(t)
	press(m, "tab", "tab")
	if m.st.Focus != "" || typingChat(m.st) {
		t.Fatal("Tab cycles back to the jobs")
	}
}

func TestAJobsRequestIsAnsweredFromTheConversation(t *testing.T) {
	m, src, _ := chatSession(t)
	if out := Render(m.view, m.st, 120, 36); !strings.Contains(out, "parent › job-7f2 asks to run:") || !strings.Contains(out, "a allow  d deny  m message") {
		t.Fatalf("the request shows in the conversation:\n%s", out)
	}
	run(press(m, "a"), m)
	if last := src.calls[len(src.calls)-1]; last.method != "job.allow" || last.params["id"] != "job-7f2" {
		t.Fatalf("a allows it: %v", src.calls)
	}
	m, src, _ = chatSession(t)
	press(m, "m")
	typeText(m, "allow, and pin the version")
	if m.st.Prompt != "allow, and pin the version" {
		t.Fatalf("after m, a and d are text: %q", m.st.Prompt)
	}
	for _, c := range src.calls {
		if c.method == "job.allow" || c.method == "job.deny" {
			t.Fatal("typing a message never answers the request")
		}
	}
}

func TestTheTitleNamesTheParentsModel(t *testing.T) {
	m, src, _ := chatSession(t)
	src.chatStatus.Parent, src.chatStatus.Model = "claude", "opus"
	m.csLast = time.Time{}
	run(m.fetchChatStatus(), m)
	if !strings.Contains(Render(m.view, m.st, 120, 36), "━ Parent · claude opus ━") {
		t.Fatal("the mockup's \"Parent · claude opus\"")
	}
}

func TestTheWorkingLineSaysWhatTheParentIsDoingWhenItIsNotAnswering(t *testing.T) {
	m, src, _ := chatSession(t)
	now := time.Date(2026, 10, 21, 12, 0, 42, 0, time.UTC)
	m.now = func() time.Time { return now }
	src.chatView = transcript("running", view.ChatEntry{Role: "user", Text: "why", At: "2026-10-21T12:00:00Z"})
	src.chatView.Activity = "reconnecting 3/5 · the connection to the model dropped"
	m.chBusy = false
	run(m.fetchChat(true), m)
	if out := m.View(); !strings.Contains(out, "working… 42s · reconnecting 3/5") {
		t.Fatalf("the reconnect shows on the working line:\n%s", out)
	}
	// no report and a long silence: the line says it is quiet rather than looking like nothing is happening
	src.chatView = transcript("running", view.ChatEntry{Role: "user", Text: "why", At: "2026-10-21T12:00:00Z"})
	m.now = func() time.Time { return now.Add(3 * time.Minute) }
	m.chBusy = false
	run(m.fetchChat(true), m)
	if out := m.View(); !strings.Contains(out, "quiet for 3m 42s") {
		t.Fatalf("a long silence is named:\n%s", out)
	}
	// a short silence is not worth a note
	m.now = func() time.Time { return now }
	m.chBusy = false
	run(m.fetchChat(true), m)
	if out := m.View(); strings.Contains(out, "quiet for") {
		t.Fatalf("no note for a short wait:\n%s", out)
	}
}
