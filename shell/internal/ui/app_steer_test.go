package ui

import (
	"strings"
	"testing"

	"github.com/Brasth/Rig/shell/internal/view"
)

func TestSteerInterruptsTheTurnWithAMessageOrSendsTheQueuedOneNow(t *testing.T) {
	m, src := appModel(t)
	withConversation(t, m, src)
	src.chatView = transcript("running", ce("user", "first"))
	run(m.fetchChat(true), m)
	src.chatStarted = &view.ChatStarted{Session: "c-1a2b3c4d", Parent: "claude"}
	src.chatSent = nil
	src.chatView = transcript("running", ce("user", "first"), ce("system", "interrupted"), ce("user", "do it the other way"))
	follow(m, m.appSend("/steer do it the other way"), 0)
	if len(src.steered) != 1 || src.steered[0] != "do it the other way" || len(src.chatSent) != 0 {
		t.Fatalf("/steer goes through chat.steer, not chat.send: %v %v", src.steered, src.chatSent)
	}
	if m.st.Chat == nil || m.st.Chat.State != "running" || !strings.Contains(appScreen(m), "do it the other way") {
		t.Fatalf("the message shows as sent and the parent is working again")
	}
	// a queued message goes now with a bare /steer
	src.chatView = transcript("running", ce("user", "first"))
	run(m.fetchChat(true), m)
	run(m.appSend("later please"), m)
	if len(m.st.App.Outbox) != 1 {
		t.Fatal("queued first")
	}
	follow(m, m.appSend("/steer"), 0)
	if len(src.steered) != 2 || src.steered[1] != "later please" || len(m.st.App.Outbox) != 0 {
		t.Fatalf("a bare /steer sends the queued message now: %v, left %v", src.steered, m.st.App.Outbox)
	}
	// idle: a plain send
	src.chatView = transcript("idle", ce("user", "first"), ce("assistant", "done"))
	m.chBusy = false
	run(m.fetchChat(true), m)
	src.chatSent = nil
	follow(m, m.appSend("/steer now what"), 0)
	if len(src.chatSent) != 1 || len(src.steered) != 2 {
		t.Fatalf("nothing to interrupt: a plain send: %v %v", src.chatSent, src.steered)
	}
}
