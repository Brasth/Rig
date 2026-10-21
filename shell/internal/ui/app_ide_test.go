package ui

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	tea "github.com/charmbracelet/bubbletea"
)

// editing opens login.ts in the editor with the cursor at line l, column c.
func editing(t *testing.T, l, c int) (*Model, *fakeSource) {
	t.Helper()
	m, src := codeRepo(t)
	press(m, "e")
	m.st.App.setCursor(pos{l, c})
	return m, src
}

func keyMsg(m *Model, t tea.KeyType) { m.Update(tea.KeyMsg{Type: t}) }

func TestSelectingCopyingAndUndoingAsInAnIDE(t *testing.T) {
	m, _ := editing(t, 3, 2)
	a := m.st.App
	keyMsg(m, tea.KeyShiftEnd)
	if from, to, ok := a.selRange(); !ok || a.textOf(from, to) != "const user = 1" {
		t.Fatalf("⇧End selects to the line's end")
	}
	typeText(m, "let u = 2")
	if a.FileLines[3] != "  let u = 2" {
		t.Fatalf("typing replaces the selection: %q", a.FileLines[3])
	}
	keyMsg(m, tea.KeyCtrlZ)
	if a.FileLines[3] != "  const user = 1" {
		t.Fatalf("⌃z undoes the typing at once: %q", a.FileLines[3])
	}
	keyMsg(m, tea.KeyCtrlY)
	if a.FileLines[3] != "  let u = 2" {
		t.Fatalf("⌃y redoes it: %q", a.FileLines[3])
	}
	keyMsg(m, tea.KeyCtrlZ)
	if a.Dirty {
		t.Fatal("undone back to the file as read: nothing unsaved")
	}
	keyMsg(m, tea.KeyCtrlX) // no selection: the line
	if a.FileLines[3] != "})" || m.clip != "  const user = 1\n" {
		t.Fatalf("⌃x with nothing selected cuts the line: %q %q", a.FileLines[3], m.clip)
	}
	a.setCursor(pos{1, 0})
	keyMsg(m, tea.KeyCtrlV)
	if a.FileLines[1] != "  const user = 1" || a.FileLines[2] != "" {
		t.Fatalf("⌃v pastes it: %q", a.FileLines[:4])
	}
	keyMsg(m, tea.KeyCtrlA)
	if from, to, _ := a.selRange(); from != (pos{}) || to.l != len(a.FileLines)-1 {
		t.Fatal("⌃a selects everything")
	}
}

func TestBracketsQuotesAndNewLines(t *testing.T) {
	m, _ := editing(t, 1, 0)
	a := m.st.App
	typeText(m, "f(")
	if a.FileLines[1] != "f()" || a.CurCol != 2 {
		t.Fatalf("( brings its ): %q %d", a.FileLines[1], a.CurCol)
	}
	typeText(m, "'a")
	typeText(m, "'")
	typeText(m, ")")
	if a.FileLines[1] != "f('a')" || a.CurCol != 6 {
		t.Fatalf("a closing quote and bracket already there are stepped over: %q %d", a.FileLines[1], a.CurCol)
	}
	typeText(m, " {")
	keyMsg(m, tea.KeyEnter)
	if a.FileLines[1] != "f('a') {" || a.FileLines[2] != "  " || a.FileLines[3] != "}" || a.CurLine != 2 || a.CurCol != 2 {
		t.Fatalf("⏎ between braces opens an indented line: %q", a.FileLines[1:4])
	}
	typeText(m, "(")
	keyMsg(m, tea.KeyBackspace)
	if a.FileLines[2] != "  " {
		t.Fatalf("⌫ in an empty pair takes both: %q", a.FileLines[2])
	}
	typeText(m, "don't")
	if a.FileLines[2] != "  don't" {
		t.Fatalf("a quote after a letter is just a quote: %q", a.FileLines[2])
	}
}

func TestIndentOutdentAndComments(t *testing.T) {
	m, _ := editing(t, 2, 0)
	a := m.st.App
	keyMsg(m, tea.KeyShiftDown)
	keyMsg(m, tea.KeyShiftDown)
	keyMsg(m, tea.KeyTab)
	if a.FileLines[2] != "  router.post('/login', async (req, res) => {" || a.FileLines[3] != "    const user = 1" || a.FileLines[4] != "})" {
		t.Fatalf("⇥ indents the selected lines (not the one the selection ends at the start of): %q", a.FileLines[2:5])
	}
	keyMsg(m, tea.KeyShiftTab)
	if a.FileLines[2] != "router.post('/login', async (req, res) => {" {
		t.Fatalf("⇧⇥ outdents them: %q", a.FileLines[2])
	}
	m.Update(tea.KeyMsg{Type: tea.KeyCtrlUnderscore})
	if a.FileLines[2] != "// router.post('/login', async (req, res) => {" || a.FileLines[3] != "//   const user = 1" {
		t.Fatalf("⌃/ comments them out: %q", a.FileLines[2:4])
	}
	m.Update(tea.KeyMsg{Type: tea.KeyCtrlUnderscore})
	if a.FileLines[3] != "  const user = 1" {
		t.Fatalf("and back in: %q", a.FileLines[3])
	}
}

func TestFindAndGoToLine(t *testing.T) {
	m, _ := editing(t, 0, 0)
	a := m.st.App
	keyMsg(m, tea.KeyCtrlF)
	typeText(m, "r")
	run(press(m, "enter"), m)
	if a.CurLine != 0 || a.CurCol != 5 {
		t.Fatalf("⏎ finds the next one: %d:%d", a.CurLine, a.CurCol)
	}
	run(press(m, "enter"), m)
	if a.CurLine != 0 || a.CurCol != 10 {
		t.Fatalf("and the next on ⏎ again: %d:%d", a.CurLine, a.CurCol)
	}
	press(m, "esc")
	keyMsg(m, tea.KeyCtrlG)
	typeText(m, "4")
	run(press(m, "enter"), m)
	if a.CurLine != 3 || a.CodeAsk != nil {
		t.Fatalf("⌃g goes to the line: %d", a.CurLine)
	}
}

func TestEachTabKeepsItsUnsavedChangesAndQuittingAsks(t *testing.T) {
	m, _ := editing(t, 0, 0)
	a := m.st.App
	typeText(m, "// hi\n")
	run(m.runPalette(paletteItem{Action: "file:src/routes/logout.ts"}, ""), m)
	if a.File != "src/routes/logout.ts" || a.Dirty || a.Bufs["src/routes/login.ts"] == nil {
		t.Fatal("another tab: login.ts's changes wait in its own")
	}
	if !strings.Contains(appScreen(m), "login.ts ◆") {
		t.Fatalf("its tab says it is not saved:\n%s", appScreen(m))
	}
	m.Update(tea.KeyMsg{Type: tea.KeyCtrlPgUp})
	if a.File != "src/routes/login.ts" || !a.Dirty || !a.Editing || !strings.HasPrefix(a.FileLines[0], "// hi") {
		t.Fatalf("back on it, the changes are there: %q", a.FileLines[0])
	}
	press(m, "esc")
	if a.Editing || !a.Dirty {
		t.Fatal("esc stops editing and keeps the changes")
	}
	m.Update(tea.KeyMsg{Type: tea.KeyCtrlC})
	if m.st.Mode != ModeConfirm || !strings.Contains(m.st.Prompt, "not saved") {
		t.Fatalf("quitting with changes not saved asks first: %q", m.st.Prompt)
	}
}

func TestTheExplorerMakesRenamesAndDeletesFiles(t *testing.T) {
	m, _ := codeRepo(t)
	a := m.st.App
	m.Update(tea.KeyMsg{Type: tea.KeyCtrlB})
	press(m, "n")
	if a.CodeAsk == nil || a.CodeAsk.text != "src/routes/" {
		t.Fatalf("n asks for the new file's name, in the chosen file's folder: %+v", a.CodeAsk)
	}
	typeText(m, "health.ts")
	run(press(m, "enter"), m)
	if _, err := os.Stat(filepath.Join(a.Repo, "src/routes/health.ts")); err != nil || a.File != "src/routes/health.ts" || !a.Editing {
		t.Fatalf("made and opened to edit: %v %q", err, a.File)
	}
	press(m, "esc")
	m.Update(tea.KeyMsg{Type: tea.KeyCtrlB})
	press(m, "r")
	for range "health.ts" {
		key(m, tea.KeyBackspace, false)
	}
	typeText(m, "ping.ts")
	run(press(m, "enter"), m)
	if _, err := os.Stat(filepath.Join(a.Repo, "src/routes/ping.ts")); err != nil || a.File != "src/routes/ping.ts" {
		t.Fatalf("r renames it: %v %q", err, a.File)
	}
	press(m, "d")
	run(press(m, "y"), m)
	if _, err := os.Stat(filepath.Join(a.Repo, "src/routes/ping.ts")); err == nil || a.File == "src/routes/ping.ts" {
		t.Fatal("d deletes it, after a question")
	}
}

func TestTheMouseSelectsInTheFileAndTheWheelScrollsIt(t *testing.T) {
	m, _ := editing(t, 0, 0)
	m.SetMouse(true)
	a := m.st.App
	x, y := findOnScreen(t, m, "const user = 1")
	clickAt(m, x+6, y)
	m.clickAt = m.clickAt.Add(-time.Second)
	run(mouseAt(m, tea.MouseButtonLeft, tea.MouseActionPress, x, y), m)
	run(mouseAt(m, tea.MouseButtonLeft, tea.MouseActionMotion, x+10, y), m)
	run(mouseAt(m, tea.MouseButtonLeft, tea.MouseActionRelease, x+10, y), m)
	if from, to, ok := a.selRange(); !ok || a.textOf(from, to) != "const user" {
		t.Fatalf("dragging selects in the file")
	}
	a.FileLines = append(a.FileLines, make([]string, 100)...)
	run(mouseAt(m, tea.MouseButtonWheelDown, tea.MouseActionPress, x, y), m)
	if a.FileOff != 3 || a.CurLine != 3 {
		t.Fatalf("the wheel scrolls the file, the cursor stays: %d %d", a.FileOff, a.CurLine)
	}
}

func TestLinesMoveAndCopyUpAndDown(t *testing.T) {
	m, _ := editing(t, 3, 2)
	a := m.st.App
	key(m, tea.KeyUp, true) // ⌥↑
	if a.FileLines[2] != "  const user = 1" || a.FileLines[3] != "router.post('/login', async (req, res) => {" || a.CurLine != 2 {
		t.Fatalf("⌥↑ moves the line up, the cursor with it: %q", a.FileLines[:5])
	}
	key(m, tea.KeyDown, true)
	if a.FileLines[3] != "  const user = 1" || a.CurLine != 3 {
		t.Fatalf("⌥↓ moves it back: %q", a.FileLines[:5])
	}
	key(m, tea.KeyShiftDown, true) // ⇧⌥↓
	if a.FileLines[3] != "  const user = 1" || a.FileLines[4] != "  const user = 1" || a.CurLine != 4 {
		t.Fatalf("⇧⌥↓ copies the line below, the cursor on the copy: %q", a.FileLines)
	}
	keyMsg(m, tea.KeyCtrlZ)
	keyMsg(m, tea.KeyCtrlL)
	keyMsg(m, tea.KeyCtrlL)
	if from, to, ok := a.selRange(); !ok || from != (pos{3, 0}) || to != (pos{5, 0}) {
		t.Fatalf("⌃l selects the line, and the next on each ⌃l: %v %v", from, to)
	}
	key(m, tea.KeyUp, true)
	if a.FileLines[2] != "  const user = 1" || a.FileLines[3] != "})" {
		t.Fatalf("⌥↑ moves the selected lines together: %q", a.FileLines)
	}
}

func TestTheMatchingBracketAndTheWordAreLit(t *testing.T) {
	m, _ := editing(t, 2, 42) // on the { that opens the block
	a := m.st.App
	if _, to, ok := matchBracket(a.FileLines, a.cursor()); !ok || to != (pos{4, 0}) {
		t.Fatalf("the { pairs with the } two lines down: %v", to)
	}
	keyMsg(m, tea.KeyCtrlCloseBracket)
	if a.cursor() != (pos{4, 0}) {
		t.Fatalf("⌃] goes to it: %v", a.cursor())
	}
	if _, to, ok := matchBracket(a.FileLines, pos{2, 28}); !ok || to != (pos{2, 37}) {
		t.Fatalf("( of (req, res) pairs with its ): %v", to)
	}
	if wordAt(a.FileLines[3], 9) != "user" || len(wordSpans("user users user_ user", "user")) != 2 {
		t.Fatal("the word under the cursor, and only whole words")
	}
}

func TestFindAndReplace(t *testing.T) {
	m, _ := editing(t, 0, 0)
	a := m.st.App
	a.FileLines = []string{"a cat and a cat", "the Cat"}
	keyMsg(m, tea.KeyCtrlR)
	typeText(m, "cat")
	keyMsg(m, tea.KeyEnter)
	if a.CodeAsk == nil || a.CodeAsk.kind != "replace" || !strings.Contains(appScreen(m), "1 of 3") {
		t.Fatalf("⌃r finds, says which of how many, then asks for the new text:\n%s", appScreen(m))
	}
	typeText(m, "dog")
	keyMsg(m, tea.KeyEnter)
	if a.FileLines[0] != "a dog and a cat" {
		t.Fatalf("⏎ replaces the one found and goes to the next: %q", a.FileLines)
	}
	keyMsg(m, tea.KeyCtrlA)
	if a.FileLines[0] != "a dog and a dog" || a.FileLines[1] != "the dog" || a.CodeAsk != nil {
		t.Fatalf("⌃a replaces every one: %q", a.FileLines)
	}
	keyMsg(m, tea.KeyCtrlZ)
	if a.FileLines[1] != "the Cat" {
		t.Fatal("one undo takes back replace all")
	}
}

func TestGoToASymbol(t *testing.T) {
	m, _ := codeRepo(t)
	m.st.App.FileLines = []string{"package main", "", "type Server struct {", "}", "", "func (s *Server) Start() error {", "}",
		"class Box:", "  def open(self):", "export const handler = async (req) => {"}
	m.Update(tea.KeyMsg{Type: tea.KeyCtrlO})
	var names []string
	for _, it := range paletteItems(m.st) {
		names = append(names, it.Label+"@"+it.Detail)
	}
	if got := strings.Join(names, ","); got != "Server@line 3,Start@line 6,Box@line 8,open@line 9,handler@line 10" {
		t.Fatalf("⌃o lists the file's symbols: %s", got)
	}
	m.st.App.Palette.Query = "start"
	run(press(m, "enter"), m)
	if m.st.App.FileOff != 2 || m.st.App.Palette != nil {
		t.Fatalf("⏎ goes to it: %d", m.st.App.FileOff)
	}
}

func TestAClickInTheFileStartsEditingThere(t *testing.T) {
	m, _ := codeRepo(t)
	m.SetMouse(true)
	x, y := findOnScreen(t, m, "const user")
	clickAt(m, x+6, y)
	a := m.st.App
	if !a.Editing || a.cursor() != (pos{3, 8}) {
		t.Fatalf("a click edits the file with the cursor where it was: %v %v", a.Editing, a.cursor())
	}
	run(mouseAt(m, tea.MouseButtonLeft, tea.MouseActionPress, x+12, y), m)
	m.Update(tea.MouseMsg{X: x + 12, Y: y, Button: tea.MouseButtonLeft, Action: tea.MouseActionRelease})
	m.Update(tea.MouseMsg{X: x, Y: y, Button: tea.MouseButtonLeft, Action: tea.MouseActionPress, Shift: true})
	m.Update(tea.MouseMsg{X: x, Y: y, Button: tea.MouseButtonLeft, Action: tea.MouseActionRelease, Shift: true})
	if from, to, ok := a.selRange(); !ok || a.textOf(from, to) != "const user =" {
		t.Fatalf("⇧click selects from the cursor: %q", a.textOf(from, to))
	}
	m.clickAt = time.Time{}
	for i := 0; i < 3; i++ {
		clickAt(m, x, y)
	}
	if from, to, ok := a.selRange(); !ok || from != (pos{3, 0}) || to != (pos{4, 0}) {
		t.Fatalf("a third click selects the line: %v %v", from, to)
	}
}

func TestThePaletteOpensWhileEditing(t *testing.T) {
	m, _ := editing(t, 0, 0)
	m.Update(tea.KeyMsg{Type: tea.KeyCtrlP})
	if m.st.App.Palette == nil || m.st.App.FileLines[0] != "import { Router } from 'express'" {
		t.Fatal("⌃p opens the files while a file is edited, as an IDE's quick open")
	}
}
