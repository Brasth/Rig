package ui

import (
	"strings"
	"testing"

	"github.com/Brasth/Rig/shell/internal/view"
)

func TestEnterOnAQueuedMessageSendsItNowInterruptingTheTurn(t *testing.T) {
	m, src := appModel(t)
	withConversation(t, m, src)
	m.view = &view.View{} // no helper waiting on an answer: an empty ⏎ is not an allow
	src.chatView = transcript("running", ce("user", "first"))
	run(m.fetchChat(true), m)
	src.chatStarted = &view.ChatStarted{Session: "c-1a2b3c4d", Parent: "claude"}
	src.chatSent = nil
	run(m.appSend("do it the other way"), m)
	if len(m.st.App.Outbox) != 1 || len(src.chatSent) != 0 || !strings.Contains(m.st.Message, "⏎ again sends it now") {
		t.Fatalf("⏎ while the parent works queues, and says ⏎ again sends it now: %v %q", m.st.App.Outbox, m.st.Message)
	}
	if !strings.Contains(appScreen(m), "⏎ sends the first one now") {
		t.Fatal("the queue says so under the spinner")
	}
	src.chatView = transcript("running", ce("user", "first"), ce("system", "interrupted"), ce("user", "do it the other way"))
	follow(m, press(m, "enter"), 0)
	if len(src.steered) != 1 || src.steered[0] != "do it the other way" || len(src.chatSent) != 0 || len(m.st.App.Outbox) != 0 {
		t.Fatalf("⏎ on the empty box sends the queued message through chat.steer: %v %v %v", src.steered, src.chatSent, m.st.App.Outbox)
	}
	if m.st.Chat == nil || m.st.Chat.State != "running" || !strings.Contains(appScreen(m), "do it the other way") {
		t.Fatal("the message shows as sent and the parent is working again")
	}
	// ⏎ on an empty box with nothing queued does nothing; idle with a queue drains as before, no interruption
	src.steered = nil
	follow(m, press(m, "enter"), 0)
	if len(src.steered) != 0 {
		t.Fatal("nothing queued: ⏎ does nothing")
	}
	for _, c := range SlashCommands {
		if c.Name == "/steer" {
			t.Fatal("there is no /steer command: ⏎ is the way")
		}
	}
}
