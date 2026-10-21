package ui

import (
	"strings"
	"testing"

	"github.com/Brasth/Rig/shell/internal/view"
)

func TestForkListsTurnsAndOpensTheCopy(t *testing.T) {
	m, src := appModel(t)
	withConversation(t, m, src)
	tr := transcript("idle", ce("user", "first question"), ce("assistant", "a"), ce("user", "second question"), ce("assistant", "b"))
	tr.Turns = 2
	src.chatView = tr
	follow(m, m.appSend("/fork"), 0)
	if p := m.st.App.Palette; p == nil || !p.Fork {
		t.Fatal("/fork opens the list")
	}
	var rows []string
	for _, it := range paletteItems(m.st) {
		rows = append(rows, it.Label+"|"+it.Detail+"|"+it.Action)
	}
	list := strings.Join(rows, "\n")
	for _, want := range []string{"turn 2 · second question|the whole conversation|fork:2", "turn 1 · first question|branch after its answer|fork:1"} {
		if !strings.Contains(list, want) {
			t.Fatalf("missing %q:\n%s", want, list)
		}
	}
	m.st.App.Palette.Query = "first"
	src.forked = &view.ChatForked{Session: "c-feedbeef", Parent: "claude", Turn: 1, ForkedFrom: "c-1a2b3c4d"}
	src.chatView = transcript("idle", ce("user", "first question"), ce("assistant", "a"), ce("system", "forked from c-1a2b3c4d at turn 1"))
	follow(m, press(m, "enter"), 0)
	if len(src.forks) != 1 || !strings.HasSuffix(src.forks[0], " 1") {
		t.Fatalf("⏎ forks at that turn: %v", src.forks)
	}
	if m.chatID != "c-feedbeef" || !strings.Contains(m.st.Message, "forked at turn 1") {
		t.Fatalf("the copy is the open conversation: %q %q", m.chatID, m.st.Message)
	}
}
