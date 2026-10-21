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
