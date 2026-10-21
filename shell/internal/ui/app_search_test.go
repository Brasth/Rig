package ui

import (
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/Brasth/Rig/shell/internal/view"
)

func TestResumeSearchesWhatWasSaidInTheConversations(t *testing.T) {
	m, src := appModel(t)
	withConversation(t, m, src)
	now := float64(m.now().Unix())
	src.chatStatus = chatOn()
	src.chatStatus.Sessions = []view.ChatSession{{ID: "c-1a2b3c4d", Parent: "claude", Turns: 2, Title: "Refactor rate limiting", Updated: now - 60}}
	src.hits = []view.ChatHit{{Session: "c-00old000", Parent: "codex", Title: "Fix the build", Where: "codex", Snippet: "the retry budget is exhausted", Updated: now - 9e6}}
	m.st.Now = m.now()
	run(m.appSend("/resume"), m)
	typeText(m, "r") // one letter is not a search
	if len(src.searches) != 0 {
		t.Fatalf("not for one letter: %v", src.searches)
	}
	follow(m, typeCmd(m, "e"), 0)
	if len(src.searches) != 1 || src.searches[0] != "re" {
		t.Fatalf("two letters search the messages: %v", src.searches)
	}
	var rows []string
	for _, it := range paletteItems(m.st) {
		rows = append(rows, it.Section+"|"+it.Label+"|"+it.Detail+"|"+it.Action)
	}
	list := strings.Join(rows, "\n")
	if !strings.Contains(list, "found in messages|Fix the build|codex: “the retry budget is exhausted”|resume:c-00old000") {
		t.Fatalf("a hit in a message shows with who said it and the words around it:\n%s", list)
	}
	if !strings.Contains(list, "Refactor rate limiting") {
		t.Fatalf("the title matches stay:\n%s", list)
	}
	// typing on while the search is out starts the next once it ends
	m.searching = true
	follow(m, typeCmd(m, "t"), 0)
	if len(src.searches) != 1 {
		t.Fatalf("one search at a time: %v", src.searches)
	}
	m.searching = false
	follow(m, m.onChatFound(chatFoundMsg{q: "re", hits: src.hits}), 0)
	if len(src.searches) != 2 || src.searches[1] != "ret" {
		t.Fatalf("what was typed meanwhile is searched next: %v", src.searches)
	}
	// opening a hit that the sixty listed do not hold
	src.chatView = &view.ChatTranscript{Session: "c-00old000", Parent: "codex", State: "idle", Turns: 1, Entries: []view.ChatEntry{ce("user", "Fix the build")}}
	m.st.App.Found, m.st.App.FoundQ = src.hits, "ret"
	follow(m, m.resumeSession("c-00old000"), 0)
	if m.chatID != "c-00old000" || m.parentName != "codex" {
		t.Fatalf("it opens with its parent: %q %q", m.chatID, m.parentName)
	}
}

// typeCmd types s into the open palette and gives back what the last key asked for.
func typeCmd(m *Model, s string) tea.Cmd {
	var cmd tea.Cmd
	for _, r := range s {
		_, cmd = m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{r}})
	}
	return cmd
}
