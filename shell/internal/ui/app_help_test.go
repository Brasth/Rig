package ui

import (
	"strings"
	"testing"

	"github.com/Brasth/Rig/shell/internal/view"
)

func TestHelpIsByWhatYouWantToDoAndEachRowDoesIt(t *testing.T) {
	m, src := appModel(t)
	withConversation(t, m, src)
	m.st.Chat = turnThatChanged("idle", 1, 1, "a.py")
	follow(m, m.appSend("/help"), 0)
	if p := m.st.App.Palette; p == nil || !p.Help {
		t.Fatal("/help opens what you can do")
	}
	var rows []string
	for _, it := range paletteItems(m.st) {
		rows = append(rows, it.Section+"|"+it.Label)
	}
	list := strings.Join(rows, "\n")
	for _, want := range []string{"Keep going|Ask about a file", "Check and keep|Put the files back as they were", "Make it yours|Switch assistant",
		"Something wrong?|What is wrong with this project", "All of it|Every key"} {
		if !strings.Contains(list, want) {
			t.Fatalf("missing %q:\n%s", want, list)
		}
	}
	m.st.App.Palette.Query = "wrong"
	if items := paletteItems(m.st); len(items) < 1 || items[0].Action != "doctor" {
		t.Fatalf("typing narrows by words: %+v", items)
	}
	// a row does the thing: doctor opens, review goes to this turn's files, a command is put in the box
	m.st.App.Palette.Query = "what is wrong"
	follow(m, press(m, "enter"), 0)
	if p := m.st.App.Palette; p == nil || !p.Doctor {
		t.Fatalf("⏎ on 'what is wrong' opens the doctor: %+v", m.st.App.Palette)
	}
	m.st.App.Palette = nil
	follow(m, m.helpAction("review-turn"), 0)
	if m.st.App.Tab != 3 || len(m.st.App.GitScope) != 1 {
		t.Fatalf("review goes to this turn's files: tab %d %v", m.st.App.Tab, m.st.App.GitScope)
	}
	m.st.App.Tab, m.st.Chat = 1, transcript("idle", ce("user", "hi"), ce("assistant", "hello"))
	follow(m, m.helpAction("commit-turn"), 0)
	if !strings.Contains(m.st.Message, "nothing has changed yet") {
		t.Fatalf("with nothing changed it says so: %q", m.st.Message)
	}
	m.st.Chat = &view.ChatTranscript{Session: "c-1a2b3c4d", Parent: "claude", State: "idle"}
	follow(m, m.helpAction("stop-turn"), 0)
	if !strings.Contains(m.st.Message, "not working on anything") {
		t.Fatalf("stopping an idle parent says so: %q", m.st.Message)
	}
	follow(m, m.helpAction("help-keys"), 0)
	if !m.st.App.Help {
		t.Fatal("every key opens the full list")
	}
}
