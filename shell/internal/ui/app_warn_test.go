package ui

import (
	"strings"
	"testing"
	"time"
)

func TestWhatWillTripTheNextTurnIsShownAboveTheReplyBox(t *testing.T) {
	m, src := appModel(t)
	withConversation(t, m, src)
	src.chatStatus = chatOn()
	src.chatStatus.Warnings = []string{"clerk is not signed in · type /mcp login clerk", "b", "never shown, only two at a time"}
	m.csBusy, m.csLast = false, time.Time{}
	run(m.fetchChatStatus(), m)
	out := appScreen(m)
	if !strings.Contains(out, "⚠ clerk is not signed in · type /mcp login clerk") {
		t.Fatalf("the warning shows:\n%s", out)
	}
	if strings.Contains(out, "never shown") {
		t.Fatalf("two warnings at most:\n%s", out)
	}
	src.chatStatus = chatOn()
	m.csBusy, m.csLast = false, time.Time{}
	run(m.fetchChatStatus(), m)
	if strings.Contains(appScreen(m), "⚠") {
		t.Fatal("no warning once it is fixed")
	}
}
