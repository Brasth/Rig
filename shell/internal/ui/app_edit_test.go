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

func TestTheCursorIsABlockOnTheCharacterUnderIt(t *testing.T) {
	st := State{Prompt: "hrewe te", PromptBack: 3, Styled: true}
	if got := st.draftRows(40, 8); len(got) != 1 || got[0] != "hrewe\x1b[7m \x1b[27mte" {
		t.Fatalf("the text keeps its place, the space under the cursor lit: %q", got)
	}
	st.PromptBack = 0
	if got := st.draftRows(40, 8); got[0] != "hrewe te\x1b[7m \x1b[27m" {
		t.Fatalf("at the end, a lit space: %q", got)
	}
}

func TestADraftWrapsAndShowsTheRowsAroundTheCursor(t *testing.T) {
	st := State{Prompt: "abcdefghij\n\nxyz"}
	if got := st.draftRows(5, 8); strings.Join(got, "|") != "abcd|efgh|ij||xyz▏" {
		t.Fatalf("lines, an empty one, and a long one in pieces: %q", got)
	}
	st.PromptBack = 4 // on the empty line
	if got := st.draftRows(5, 8); got[3] != "▏" {
		t.Fatalf("the cursor on the empty line: %q", got)
	}
	st = State{Prompt: strings.Repeat("line\n", 20) + "end"}
	got := st.draftRows(40, 8)
	if len(got) != 8 || got[7] != "end▏" {
		t.Fatalf("eight rows at most, those around the cursor: %q", got)
	}
}

func TestNewLinesAndMovingBetweenThem(t *testing.T) {
	m, src := appModel(t)
	withConversation(t, m, src)
	typeText(m, "first line")
	m.Update(tea.KeyMsg{Type: tea.KeyEnter, Alt: true})
	typeText(m, "two")
	key(m, tea.KeyCtrlJ, false)
	typeText(m, "third line")
	if m.st.Prompt != "first line\ntwo\nthird line" || len(src.chatSent) != 0 {
		t.Fatalf("⌥⏎ and ⌃j start a new line, nothing is sent: %q", m.st.Prompt)
	}
	if screen := appScreen(m); !strings.Contains(screen, "› first line") || !strings.Contains(screen, "   two") {
		t.Fatalf("the box grows a row for each line:\n%s", screen)
	}
	key(m, tea.KeyUp, false)
	if m.st.caret() != len("first line\ntwo") {
		t.Fatalf("↑ goes to the line above, as far in as the line has: %d", m.st.caret())
	}
	key(m, tea.KeyUp, false)
	key(m, tea.KeyHome, false)
	typeText(m, "> ")
	if m.st.Prompt != "> first line\ntwo\nthird line" {
		t.Fatalf("↑ again, then Home goes to the line's start: %q", m.st.Prompt)
	}
	key(m, tea.KeyDown, false)
	key(m, tea.KeyEnd, false)
	typeText(m, "!")
	if m.st.Prompt != "> first line\ntwo!\nthird line" {
		t.Fatalf("↓, then End goes to the line's end: %q", m.st.Prompt)
	}
	run(press(m, "enter"), m)
	if n := len(src.chatSent); n == 0 || src.chatSent[n-1][0] != "> first line\ntwo!\nthird line" {
		t.Fatalf("⏎ sends every line: %v", src.chatSent)
	}
}
