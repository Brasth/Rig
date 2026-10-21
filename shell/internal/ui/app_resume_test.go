package ui

import (
	"strings"
	"testing"
	"time"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/Brasth/Rig/shell/internal/view"
)

func TestResumeListsEarlierConversationsAndOpensTheChosenOne(t *testing.T) {
	m, src := appModel(t)
	withConversation(t, m, src)
	now := float64(m.now().Unix())
	src.chatStatus = chatOn()
	src.chatStatus.Sessions = []view.ChatSession{
		{ID: "c-1a2b3c4d", Parent: "claude", State: "idle", Turns: 2, Title: "Add rate limiting to POST /login", Updated: now - 3600},
		{ID: "c-99999999", Parent: "codex", State: "idle", Turns: 1, Title: "Fix the flaky payment test", Updated: now - 3*86400},
	}
	m.st.Now = m.now()
	run(m.appSend("/resume"), m)
	if p := m.st.App.Palette; p == nil || !p.Resume {
		t.Fatal("/resume opens the list")
	}
	var rows []string
	for _, it := range paletteItems(m.st) {
		rows = append(rows, it.Label+"|"+it.Detail+"|"+it.Action)
	}
	list := strings.Join(rows, "\n")
	for _, want := range []string{"Add rate limiting to POST /login|claude · 2 turns · 1h ago · open|resume:c-1a2b3c4d",
		"Fix the flaky payment test|codex · 1 turn · 3d ago|resume:c-99999999"} {
		if !strings.Contains(list, want) {
			t.Fatalf("each conversation with its first message, parent, turns and age; missing %q:\n%s", want, list)
		}
	}
	m.st.App.Palette.Query = "flaky"
	items := paletteItems(m.st)
	if len(items) != 1 || items[0].Action != "resume:c-99999999" {
		t.Fatalf("typing searches the first messages: %+v", items)
	}
	m.st.App.Palette.Query = "codex"
	if items := paletteItems(m.st); len(items) != 1 {
		t.Fatalf("and the parent: %+v", items)
	}
	m.st.App.Palette.Query = "flaky"
	src.chatView = &view.ChatTranscript{Session: "c-99999999", Parent: "codex", State: "idle", Turns: 1,
		Entries: []view.ChatEntry{ce("user", "Fix the flaky payment test"), ce("assistant", "It races on the clock.")}}
	_, cmd := m.Update(press(m, "enter")())
	run(cmd, m)
	if m.chatID != "c-99999999" || m.parentName != "codex" || m.st.ChatParent != "codex" {
		t.Fatalf("⏎ opens it with its own parent: %q %q %q", m.chatID, m.parentName, m.st.ChatParent)
	}
	if out := appScreen(m); !strings.Contains(out, "It races on the clock.") || m.st.App.Tab != 1 {
		t.Fatalf("its conversation shows:\n%s", out)
	}
	src.chatSent, src.chatParents = nil, nil
	src.chatStarted = &view.ChatStarted{Session: "c-99999999", Parent: "codex"}
	run(m.appSend("and the retry?"), m)
	if len(src.chatSent) != 1 || src.chatSent[0][1] != "c-99999999" || src.chatParents[0] != "codex" {
		t.Fatalf("the next message goes on in that conversation, to its parent: %v %v", src.chatSent, src.chatParents)
	}
	m.st.ChatSessions = nil
	m.st.App.Palette = &Palette{Resume: true}
	if items := paletteItems(m.st); items[0].Label != "nothing to resume" || items[0].Action != "" {
		t.Fatalf("with none it says so: %+v", items)
	}
}

func TestAMessageTypedWhileTheParentWorksWaitsBehindItAndGoesWhenTheTurnEnds(t *testing.T) {
	m, src := appModel(t)
	withConversation(t, m, src)
	src.chatView = transcript("running", ce("user", "first"))
	m.chLast = time.Time{}
	run(m.fetchChat(true), m)
	src.chatSent = nil
	typeText(m, "second")
	run(press(m, "enter"), m)
	typeText(m, "third")
	run(press(m, "enter"), m)
	a := m.st.App
	if len(src.chatSent) != 0 || len(a.Outbox) != 2 || m.st.Prompt != "" {
		t.Fatalf("while it works, a message queues instead of failing: sent %v, queued %v, box %q", src.chatSent, a.Outbox, m.st.Prompt)
	}
	out := appScreen(m)
	for _, want := range []string{"↳ next  second", "↳ then  third", "↑ takes the last one back"} {
		if !strings.Contains(out, want) {
			t.Fatalf("the queue shows under the spinner; missing %q:\n%s", want, out)
		}
	}
	keyMsg(m, tea.KeyUp)
	if m.st.Prompt != "third" || len(a.Outbox) != 1 {
		t.Fatalf("↑ in an empty box takes the last one back: %q %v", m.st.Prompt, a.Outbox)
	}
	m.setPrompt("", 0)
	// the turn ends well: the first queued message goes, and only that one
	src.chatView = transcript("idle", ce("user", "first"), ce("assistant", "done"))
	src.chatStarted = &view.ChatStarted{Session: "c-1a2b3c4d", Parent: "claude"}
	m.chBusy = false
	follow(m, m.fetchChat(true), 0) // the refresh shows the turn ended, and what that sends
	if len(src.chatSent) != 1 || src.chatSent[0][0] != "second" || len(a.Outbox) != 0 {
		t.Fatalf("the queued message goes when the turn ends: %v, left %v", src.chatSent, a.Outbox)
	}
}

func TestQueuedMessagesComeBackToTheBoxWhenTheTurnFailsOrIsStopped(t *testing.T) {
	for _, how := range []string{"failed", "stopped"} {
		m, src := appModel(t)
		withConversation(t, m, src)
		src.chatView = transcript("running", ce("user", "first"))
		run(m.fetchChat(true), m)
		src.chatSent = nil
		typeText(m, "second")
		run(press(m, "enter"), m)
		typeText(m, "third")
		run(press(m, "enter"), m)
		a := m.st.App
		if how == "failed" {
			src.chatView = transcript("failed", ce("user", "first"), ce("error", "boom"))
			m.chBusy = false
			run(m.fetchChat(true), m)
		} else {
			m.Update(chatCancelMsg{})
		}
		if len(src.chatSent) != 0 || len(a.Outbox) != 0 || m.st.Prompt != "second\n\nthird" || !strings.Contains(m.st.Message, "back in the box") {
			t.Fatalf("%s: nothing is sent behind it; the messages are back: sent %v, queued %v, box %q, %q", how, src.chatSent, a.Outbox, m.st.Prompt, m.st.Message)
		}
	}
}

func TestAMessageIsNotQueuedWhenTheParentIsWaitingOnYouOrNothingRuns(t *testing.T) {
	m, src := appModel(t)
	withConversation(t, m, src)
	src.chatStarted = &view.ChatStarted{Session: "c-1a2b3c4d", Parent: "claude"}
	run(m.appSend("hello"), m)
	if len(src.chatSent) != 1 || len(m.st.App.Outbox) != 0 {
		t.Fatalf("an idle parent gets it at once: %v", src.chatSent)
	}
	if m.working() {
		// the optimistic transcript says running now; a request waiting on you is answered, not queued behind
		m.st.Chat.Ask = &view.ChatAsk{ID: "a1", Tool: "Bash"}
		if m.working() {
			t.Fatal("with a request waiting, typing answers it")
		}
	}
}
