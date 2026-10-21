package ui

import (
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
)

func key(m *Model, t tea.KeyType, alt bool) {
	m.Update(tea.KeyMsg{Type: t, Alt: alt})
}

func TestTheBoxEditsAtTheCursorAsCodexDoes(t *testing.T) {
	m, src := appModel(t)
	withConversation(t, m, src)
	for i := range m.view.Jobs {
		m.view.Jobs[i].Ask = false // ← → would pick an answer for a waiting request
	}
	typeText(m, "hello world")
	for i := 0; i < 5; i++ {
		key(m, tea.KeyLeft, false)
	}
	typeText(m, "big ")
	if m.st.Prompt != "hello big world" || m.st.caret() != 10 {
		t.Fatalf("typed at the cursor: %q at %d", m.st.Prompt, m.st.caret())
	}
	if screen := appScreen(m); !strings.Contains(screen, "hello big ▏world") {
		t.Fatalf("the cursor is drawn where it is:\n%s", screen)
	}
	key(m, tea.KeyLeft, true) // ⌥← to the start of "big"
	key(m, tea.KeyBackspace, false)
	if m.st.Prompt != "hellobig world" {
		t.Fatalf("⌫ removes the character before the cursor: %q", m.st.Prompt)
	}
	key(m, tea.KeyHome, false)
	key(m, tea.KeyDelete, false)
	if m.st.Prompt != "ellobig world" || m.st.caret() != 0 {
		t.Fatalf("Home, then Delete: %q", m.st.Prompt)
	}
	key(m, tea.KeyCtrlE, false)
	key(m, tea.KeyCtrlW, false)
	if m.st.Prompt != "ellobig " {
		t.Fatalf("⌃e, then ⌃w removes a word: %q", m.st.Prompt)
	}
	key(m, tea.KeyLeft, false)
	key(m, tea.KeyCtrlU, false)
	if m.st.Prompt != " " {
		t.Fatalf("⌃u removes what is before the cursor: %q", m.st.Prompt)
	}
}

func TestUpAndDownBringBackWhatWasSent(t *testing.T) {
	m, src := appModel(t)
	withConversation(t, m, src)
	for _, msg := range []string{"first", "second"} {
		m.st.Prompt = ""
		typeText(m, msg)
		run(press(m, "enter"), m)
	}
	m.st.Prompt = ""
	typeText(m, "a draft")
	key(m, tea.KeyUp, false)
	if m.st.Prompt != "a draft" || m.st.caret() != 0 {
		t.Fatalf("in a draft of your own, ↑ goes to its start: %q %d", m.st.Prompt, m.st.caret())
	}
	m.st.Prompt = ""
	for _, want := range []string{"second", "first", "Add rate limiting to POST /login", "Add rate limiting to POST /login"} {
		key(m, tea.KeyUp, false)
		if m.st.Prompt != want {
			t.Fatalf("↑: %q, want %q (the conversation's own messages reach back past this run)", m.st.Prompt, want)
		}
	}
	key(m, tea.KeyDown, false)
	key(m, tea.KeyDown, false)
	if m.st.Prompt != "second" {
		t.Fatalf("↓ goes forward: %q", m.st.Prompt)
	}
	key(m, tea.KeyDown, false)
	if m.st.Prompt != "" || m.histAt != -1 {
		t.Fatalf("past the newest, what was typed: %q", m.st.Prompt)
	}
	key(m, tea.KeyUp, false)
	typeText(m, "!")
	key(m, tea.KeyUp, false)
	if m.st.Prompt != "second!" {
		t.Fatalf("an edited message is a draft: ↑ no longer replaces it: %q", m.st.Prompt)
	}
}

func TestALongDraftShowsTheTextAroundTheCursor(t *testing.T) {
	st := State{Prompt: strings.Repeat("a", 50) + "MIDDLE" + strings.Repeat("b", 50)}
	st.PromptBack = 53
	out := st.draft(20)
	if !strings.Contains(out, "MID▏DLE") || !strings.HasPrefix(out, "…") || !strings.HasSuffix(out, "…") || len([]rune(out)) != 20 {
		t.Fatalf("%q", out)
	}
}
