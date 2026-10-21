package ui

import (
	"strings"
	"testing"
	"time"

	"github.com/Brasth/Rig/shell/internal/view"
)

// runningThenEnded puts the conversation in the middle of a turn that began a while ago, then lets it end as end says.
func runningThenEnded(t *testing.T, m *Model, src *fakeSource, ago time.Duration, end *view.ChatTranscript) {
	t.Helper()
	at := m.now().UTC().Add(-ago).Format(time.RFC3339)
	running := transcript("running", view.ChatEntry{Role: "user", Text: "refactor the limiter", At: at})
	src.chatView = running
	m.chatID, m.chBusy = "c-1a2b3c4d", false
	run(m.fetchChat(true), m)
	for i := range end.Entries {
		if end.Entries[i].Role == "user" {
			end.Entries[i].At = at
		}
	}
	m.chBusy = false
	_, cmd := m.Update(chatMsg{chat: end})
	follow(m, cmd, 0)
}

func notifyModel(t *testing.T, done bool) (*Model, *fakeSource, *[]string) {
	m, src := appModel(t)
	var notices []string
	m.ringer = func() {}
	m.notifier = func(title, text string) { notices = append(notices, text) }
	src.appSettings = sampleSettings()
	src.appSettings.NotifyDone, src.appSettings.Desktop = done, true
	run(m.fetchAppSettings(), m)
	return m, src, &notices
}

func TestALongTurnThatEndsOrFailsNotifies(t *testing.T) {
	m, src, notices := notifyModel(t, true)
	end := transcript("idle", ce("user", "refactor the limiter"), ce("assistant", "Refactored the limiter into one file.\nMore detail below."))
	runningThenEnded(t, m, src, 90*time.Second, end)
	if len(*notices) != 1 || !strings.HasPrefix((*notices)[0], "claude finished after ") || !strings.HasSuffix((*notices)[0], ": Refactored the limiter into one file.") {
		t.Fatalf("a turn of a minute and a half ends with a notice naming what it said: %v", *notices)
	}
	failed := transcript("failed", ce("user", "refactor the limiter"), ce("error", "boom"))
	runningThenEnded(t, m, src, 40*time.Second, failed)
	if len(*notices) != 2 || !strings.HasPrefix((*notices)[1], "claude's turn failed after ") {
		t.Fatalf("a failure notifies too: %v", *notices)
	}
}

func TestShortStoppedAndUnwantedTurnsDoNotNotify(t *testing.T) {
	m, src, notices := notifyModel(t, true)
	runningThenEnded(t, m, src, 5*time.Second, transcript("idle", ce("user", "x"), ce("assistant", "quick")))
	cancelled := transcript("idle", ce("user", "x"), ce("assistant", "partial"))
	cancelled.Note = "cancelled"
	runningThenEnded(t, m, src, 2*time.Minute, cancelled)
	if len(*notices) != 0 {
		t.Fatalf("a short turn, or one you stopped, is not worth a notice: %v", *notices)
	}
	m, src, notices = notifyModel(t, false)
	runningThenEnded(t, m, src, 2*time.Minute, transcript("idle", ce("user", "x"), ce("assistant", "done")))
	if len(*notices) != 0 {
		t.Fatalf("with the setting off nothing: %v", *notices)
	}
	m, src, notices = notifyModel(t, true)
	src.chatView = transcript("idle", ce("user", "x"), ce("assistant", "old answer"))
	m.chatID, m.chBusy = "c-1a2b3c4d", false
	run(m.fetchChat(true), m)
	m.chBusy = false
	run(m.fetchChat(true), m)
	if len(*notices) != 0 {
		t.Fatalf("a refresh of a conversation that was idle already is not a turn ending: %v", *notices)
	}
}

func TestCompactMovesToTheConversationThatCarriesTheSummary(t *testing.T) {
	m, src, _ := notifyModel(t, false)
	pct := 12
	old := transcript("idle", ce("user", "/compact"), ce("assistant", "We are adding rate limiting."), ce("system", "compacted: continuing in c-2b3c4d5e (this one stays in /resume)"))
	old.Successor = "c-2b3c4d5e"
	at := m.now().UTC().Format(time.RFC3339)
	src.chatView = transcript("running", view.ChatEntry{Role: "user", Text: "/compact", At: at})
	m.chatID, m.chBusy = "c-1a2b3c4d", false
	run(m.fetchChat(true), m)
	src.chatView = &view.ChatTranscript{Session: "c-2b3c4d5e", Parent: "claude", State: "idle", ContextPct: &pct,
		Entries: []view.ChatEntry{ce("system", "continuing from c-1a2b3c4d: the summary below goes with your next message"),
			ce("summary", "We are adding rate limiting to POST /login.\n\nDone: the limiter. Left: tests.")}}
	m.chBusy = false
	_, cmd := m.Update(chatMsg{chat: old})
	follow(m, cmd, 0)
	if m.chatID != "c-2b3c4d5e" || !strings.Contains(m.st.Message, "compacted") {
		t.Fatalf("watching it end, the box moves to the new conversation: %q %q", m.chatID, m.st.Message)
	}
	out := appScreen(m)
	for _, want := range []string{"summary carried over", "We are adding rate limiting to POST /login.", "Left: tests.", "context 12%"} {
		if !strings.Contains(out, want) {
			t.Fatalf("the new conversation shows the summary and the context; missing %q:\n%s", want, out)
		}
	}
	// reopening the old one later does not jump again: only a turn seen ending does
	m.chatID, m.st.Chat = "c-1a2b3c4d", nil
	src.chatView = old
	m.chBusy = false
	follow(m, m.fetchChat(true), 0)
	if m.chatID != "c-1a2b3c4d" {
		t.Fatalf("an old conversation reopened stays open: %q", m.chatID)
	}
	high := 85
	m.st.Chat.ContextPct = &high
	if out := appScreen(m); !strings.Contains(out, "context 85%") {
		t.Fatalf("a full context shows:\n%s", out)
	}
}
