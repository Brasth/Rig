package ui

import (
	"strings"
	"testing"
	"time"

	"github.com/Brasth/Rig/shell/internal/view"
)

func TestARunningToolShowsHowLongAndTheRunningListStopsIt(t *testing.T) {
	m, src := appModel(t)
	withConversation(t, m, src)
	now := m.now()
	tool := view.ChatEntry{Role: "tool", Text: "Bash npm test", At: now.Add(-42 * time.Second).UTC().Format(time.RFC3339)}
	src.chatView = transcript("running", ce("user", "run the tests"), ce("assistant", "Running them."), tool)
	run(m.fetchChat(true), m)
	m.st.Now = now
	if out := appScreen(m); !strings.Contains(out, "npm test") || !strings.Contains(out, "42s") {
		t.Fatalf("the tool line says how long it has run:\n%s", out)
	}
	src.chatView = transcript("running", ce("user", "run the tests"), tool, ce("result", "ok"))
	m.chBusy = false
	run(m.fetchChat(true), m)
	if _, _, ok := runningTool(m.st); ok {
		t.Fatal("a tool with its result is not running")
	}
	src.chatView = transcript("running", ce("user", "run the tests"), ce("assistant", "Running them."), tool)
	m.chBusy = false
	run(m.fetchChat(true), m)
	m.view = &view.View{Jobs: []view.Row{{ID: "j1", Title: "Fix the flaky test", Label: "Running", Second: "codex"}, {ID: "j2", Title: "Old", Label: "Accepted"}}}
	follow(m, m.appSend("/running"), 0)
	m.st.Now = now
	var rows []string
	for _, it := range paletteItems(m.st) {
		rows = append(rows, it.Section+"|"+it.Label+"|"+it.Action)
	}
	list := strings.Join(rows, "\n")
	if !strings.Contains(list, "parent|Bash npm test") || !strings.Contains(list, "helpers|Fix the flaky test|running:job:j1") || strings.Contains(list, "Old") {
		t.Fatalf("the parent's tool and the helpers at work:\n%s", list)
	}
	follow(m, typeCmd(m, "x"), 0)
	if len(src.chatCancelled) != 1 || m.st.App.Palette != nil {
		t.Fatalf("x on the parent's row stops the turn: %v", src.chatCancelled)
	}
	src.chatView = transcript("idle", ce("user", "hi"), ce("assistant", "hello"))
	m.chBusy = false
	run(m.fetchChat(true), m)
	m.view = &view.View{}
	follow(m, m.appSend("/running"), 0)
	if items := paletteItems(m.st); len(items) != 1 || items[0].Label != "nothing is running" {
		t.Fatalf("idle: %+v", items)
	}
}
