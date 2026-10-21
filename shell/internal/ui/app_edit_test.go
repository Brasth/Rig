package ui

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

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
	if got := rowTexts(draft(st, 40, 8)); len(got) != 1 || got[0] != "hrewe\x1b[7m \x1b[27mte" {
		t.Fatalf("the text keeps its place, the space under the cursor lit: %q", got)
	}
	st.PromptBack = 0
	if got := rowTexts(draft(st, 40, 8)); got[0] != "hrewe te\x1b[7m \x1b[27m" {
		t.Fatalf("at the end, a lit space: %q", got)
	}
}

func TestADraftWrapsAndShowsTheRowsAroundTheCursor(t *testing.T) {
	st := State{Prompt: "abcdefghij\n\nxyz"}
	if got := rowTexts(draft(st, 5, 8)); strings.Join(got, "|") != "abcd|efgh|ij||xyz▏" {
		t.Fatalf("lines, an empty one, and a long one in pieces: %q", got)
	}
	st.PromptBack = 4 // on the empty line
	if got := rowTexts(draft(st, 5, 8)); got[3] != "▏" {
		t.Fatalf("the cursor on the empty line: %q", got)
	}
	st = State{Prompt: strings.Repeat("line\n", 20) + "end"}
	got := rowTexts(draft(st, 40, 8))
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

func rowTexts(rows []draftRow) []string {
	var out []string
	for _, r := range rows {
		out = append(out, r.text)
	}
	return out
}

// boxCell is where rune i of the box's text is on the screen.
func boxCell(t *testing.T, m *Model, text string, i int) (int, int) {
	t.Helper()
	x, y := findOnScreen(t, m, text)
	return x + len([]rune(text[:0])) + i, y
}

func TestTheMousePlacesTheCursorAndSelectsInTheBox(t *testing.T) {
	m, src := mouseModel(t)
	withConversation(t, m, src)
	out, _ := os.Create(filepath.Join(t.TempDir(), "out"))
	defer out.Close()
	NewTermWriter(out, false)
	t.Cleanup(func() { logoOut = nil })
	typeText(m, "hello brave world")
	x, y := boxCell(t, m, "hello brave", 6)
	clickAt(m, x, y)
	if m.st.caret() != 6 || m.st.PromptSel {
		t.Fatalf("a click puts the cursor there: %d", m.st.caret())
	}
	m.clickAt = time.Time{} // not a double click
	run(mouseAt(m, tea.MouseButtonLeft, tea.MouseActionPress, x, y), m)
	run(mouseAt(m, tea.MouseButtonLeft, tea.MouseActionMotion, x+4, y), m)
	if s0, s1 := m.st.selection(); string([]rune(m.st.Prompt)[s0:s1]) != "brave" {
		t.Fatalf("dragging selects: %d %d", s0, s1)
	}
	run(mouseAt(m, tea.MouseButtonLeft, tea.MouseActionRelease, x+4, y), m)
	if !strings.HasPrefix(m.st.Message, "copied 5") {
		t.Fatalf("and copies it: %q", m.st.Message)
	}
	typeText(m, "bold")
	if m.st.Prompt != "hello bold world" {
		t.Fatalf("typing replaces the selection: %q", m.st.Prompt)
	}
	x, y = boxCell(t, m, "hello bold world", 13)
	clickAt(m, x, y)
	clickAt(m, x, y)
	if s0, s1 := m.st.selection(); string([]rune(m.st.Prompt)[s0:s1]) != "world" {
		t.Fatalf("a double click selects the word: %d %d", s0, s1)
	}
	key(m, tea.KeyBackspace, false)
	if m.st.Prompt != "hello bold " {
		t.Fatalf("⌫ removes the selection: %q", m.st.Prompt)
	}
}

func TestShiftAndTheArrowsSelect(t *testing.T) {
	m, src := appModel(t)
	withConversation(t, m, src)
	typeText(m, "one two three")
	m.Update(tea.KeyMsg{Type: tea.KeyShiftLeft})
	m.Update(tea.KeyMsg{Type: tea.KeyCtrlShiftLeft})
	if s0, s1 := m.st.selection(); string([]rune(m.st.Prompt)[s0:s1]) != "three" {
		t.Fatalf("⇧← and ⌃⇧← select back: %d %d", s0, s1)
	}
	key(m, tea.KeyLeft, false)
	if m.st.PromptSel || m.st.caret() != 8 {
		t.Fatal("← leaves the selection at its start")
	}
	m.Update(tea.KeyMsg{Type: tea.KeyShiftHome})
	key(m, tea.KeyDelete, false)
	if m.st.Prompt != "three" {
		t.Fatalf("⇧Home selects to the start; Delete removes it: %q", m.st.Prompt)
	}
}

func TestAClickPlacesTheCursorInAFileBeingEdited(t *testing.T) {
	m, _ := codeRepo(t)
	m.SetMouse(true)
	press(m, "e")
	x, y := findOnScreen(t, m, "const user = 1")
	clickAt(m, x+6, y)
	if a := m.st.App; a.CurLine != 3 || a.CurCol != 8 {
		t.Fatalf("the cursor goes where the file was clicked: %d:%d", a.CurLine, a.CurCol)
	}
}

// draft is the box's rows alone, without how many are hidden above and below.
func draft(st State, room, most int) []draftRow {
	rows, _, _ := st.draftRows(room, most)
	return rows
}

func TestALongDraftScrollsInsideTheBoxWithTheWheel(t *testing.T) {
	m, src := appModel(t)
	withConversation(t, m, src)
	m.SetMouse(true)
	a := m.st.App
	var lines []string
	for i := 1; i <= 20; i++ {
		lines = append(lines, fmt.Sprintf("line %02d", i))
	}
	draftText := strings.Join(lines, "\n")
	m.setPrompt(draftText, len([]rune(draftText)))
	screen := func() string { // what View draws: also where the zones are, for the pointer
		m.View()
		return strings.Join(m.plain, "\n")
	}
	out := screen()
	if !strings.Contains(out, "line 20") || strings.Contains(out, "line 01") || !strings.Contains(out, "↑") {
		t.Fatalf("typing shows the rows around the cursor, and says more are above:\n%s", out)
	}
	if a.BoxTotal != 20 || a.BoxTop != 12 {
		t.Fatalf("the box knows where it is: top %d of %d", a.BoxTop, a.BoxTotal)
	}
	var y int
	for row, text := range m.plain {
		if strings.Contains(text, "line 20") {
			y = row
		}
	}
	x := len([]rune(m.plain[y][:strings.Index(m.plain[y], "line 20")])) // in cells, not bytes
	wheel := func() {
		m.Update(tea.MouseMsg{X: x, Y: y, Button: tea.MouseButtonWheelUp, Action: tea.MouseActionPress})
		screen()
	}
	before := m.st.LogUp
	wheel()
	if out := strings.Join(m.plain, "\n"); !strings.Contains(out, "line 11") || strings.Contains(out, "line 20") || !strings.Contains(out, "↓") {
		t.Fatalf("the wheel scrolls the box up, with a mark that more is below:\n%s", out)
	}
	for i := 0; i < 8; i++ {
		wheel()
	}
	if out := strings.Join(m.plain, "\n"); !strings.Contains(out, "line 01") || !strings.Contains(out, "›") {
		t.Fatalf("all the way up is the draft's start:\n%s", out)
	}
	if m.st.LogUp != before {
		t.Fatal("the conversation did not move")
	}
	typeText(m, "!")
	if out := screen(); !strings.Contains(out, "line 20!") || strings.Contains(out, "line 01") {
		t.Fatalf("typing follows the cursor again:\n%s", out)
	}
	m.setPrompt("short", 5) // a short draft has nothing to scroll: the wheel goes to the conversation
	screen()
	if a.BoxTotal > boxMostRows {
		t.Fatal("a short draft has nothing to scroll")
	}
}
