package ui

import (
	"errors"
	"strings"
	"testing"

	"github.com/Brasth/Rig/shell/internal/view"
)

func marksTranscript() *view.ChatTranscript {
	tr := transcript("idle", ce("user", "first change"), ce("assistant", "done"))
	tr.Marks = []view.ChatMark{
		{Turn: 1, Title: "first change", Files: []view.ChatFile{{Path: "a.py", Added: 2}, {Path: "new.txt", Added: 1}}},
		{Turn: 2, Title: "second change", Files: []view.ChatFile{{Path: "a.py", Added: 1, Deleted: 1}}},
	}
	return tr
}

func rewindRows(m *Model) string {
	var rows []string
	for _, it := range paletteItems(m.st) {
		rows = append(rows, it.Label+"|"+it.Detail+"|"+it.Action)
	}
	return strings.Join(rows, "\n")
}

func TestRewindListsTheTurnsThatChangedFilesNewestFirstAndFiltersThem(t *testing.T) {
	m, src := appModel(t)
	withConversation(t, m, src)
	src.chatView = marksTranscript()
	follow(m, m.appSend("/rewind"), 0)
	if p := m.st.App.Palette; p == nil || !p.Rewind {
		t.Fatal("/rewind opens the list")
	}
	rows := rewindRows(m)
	second := strings.Index(rows, "turn 2 · second change|1 file: a.py|rewind-turn:2")
	first := strings.Index(rows, "turn 1 · first change|2 files: a.py, new.txt|rewind-turn:1")
	if second < 0 || first < 0 || second > first {
		t.Fatalf("newest first, each with its files:\n%s", rows)
	}
	m.st.App.Palette.Query = "new.txt"
	if items := paletteItems(m.st); len(items) != 1 || items[0].Action != "rewind-turn:1" {
		t.Fatalf("typing filters by message or file: %+v", items)
	}
	m.st.Chat.CanUndo = true
	m.st.App.Palette.Query = ""
	if !strings.Contains(rewindRows(m), "undo the last rewind|put back what it took away|rewind-undo") {
		t.Fatal("after a rewind the first row takes it back")
	}
	m.st.Chat.Marks, m.st.Chat.CanUndo = nil, false
	if !strings.Contains(rewindRows(m), "nothing to rewind|no turn has changed files") {
		t.Fatal("no turns: it says so")
	}
}

func TestRewindShowsWhatWouldChangeThenPutsItBackAndRefreshes(t *testing.T) {
	m, src := appModel(t)
	withConversation(t, m, src)
	src.chatView = marksTranscript()
	follow(m, m.appSend("/rewind"), 0)
	src.rewound = &view.ChatRewound{Turn: 1, Paths: []string{"a.py", "new.txt"}, Edited: []string{"a.py"}, Dry: true}
	m.st.App.Palette.Query = "first"
	enter(m)
	if len(src.rewinds) != 1 || !strings.Contains(src.rewinds[0], `1 "" dry=true undo=false`) {
		t.Fatalf("choosing a turn only asks what would change: %v", src.rewinds)
	}
	rows := rewindRows(m)
	for _, want := range []string{"put back all 2 files|1 edited since will be overwritten (undo brings them back)|rewind-do:1",
		"a.py|edited since: its changes are kept for undo|rewind-file:1:a.py", "new.txt|put just this file back|rewind-file:1:new.txt"} {
		if !strings.Contains(rows, want) {
			t.Fatalf("missing %q:\n%s", want, rows)
		}
	}
	src.rewound = &view.ChatRewound{Turn: 1, Paths: []string{"a.py", "new.txt"}}
	enter(m)
	if got := src.rewinds[len(src.rewinds)-1]; !strings.Contains(got, `1 "" dry=false undo=false`) {
		t.Fatalf("⏎ on the first row puts everything back: %v", src.rewinds)
	}
	if m.st.App.Palette != nil || !strings.Contains(m.st.Message, "put back 2 files") {
		t.Fatalf("the list closes with a note: %q", m.st.Message)
	}
}

func TestRewindOneFileUndoAndErrors(t *testing.T) {
	m, src := appModel(t)
	withConversation(t, m, src)
	src.chatView = marksTranscript()
	src.rewound = &view.ChatRewound{Turn: 1, Paths: []string{"new.txt"}}
	follow(m, m.doRewind(1, "new.txt", false), 0)
	if !strings.Contains(src.rewinds[0], `1 "new.txt" dry=false`) || m.st.Message != "put back new.txt" {
		t.Fatalf("one file: %v %q", src.rewinds, m.st.Message)
	}
	src.rewound = &view.ChatRewound{Paths: []string{"a.py", "new.txt"}}
	follow(m, m.doRewind(0, "", true), 0)
	if !strings.Contains(src.rewinds[1], "undo=true") || m.st.Message != "undid the rewind: 2 files back" {
		t.Fatalf("undo: %v %q", src.rewinds, m.st.Message)
	}
	src.rewindErr = errors.New("session c-1a2b3c4d is still answering")
	follow(m, m.doRewind(1, "", false), 0)
	if !strings.Contains(m.st.Message, "rewind: session c-1a2b3c4d is still answering") {
		t.Fatalf("a refusal is shown: %q", m.st.Message)
	}
}

func enter(m *Model) {
	follow(m, press(m, "enter"), 0)
}
