package ui

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
)

func TestTheWordsOfTheFileCompleteTheOneBeingTyped(t *testing.T) {
	m, _ := editing(t, 0, 0)
	a := m.st.App
	a.FileLines = []string{"const username = 1", "const userCount = 2", ""}
	a.setCursor(pos{2, 0})
	typeText(m, "u")
	if a.Complete != nil {
		t.Fatal("one letter is too few")
	}
	typeText(m, "s")
	c := a.Complete
	if c == nil || strings.Join(c.items, " ") != "userCount username" || c.prefix != "us" {
		t.Fatalf("after two letters, the words that start with them: %+v", c)
	}
	if out := appScreen(m); !strings.Contains(out, "userCount") || !strings.Contains(out, "⇥ ⏎") {
		t.Fatalf("the list under the cursor, and the footer:\n%s", out)
	}
	keyMsg(m, tea.KeyDown)
	keyMsg(m, tea.KeyTab)
	if a.FileLines[2] != "username" || a.Complete != nil || a.CurCol != 8 {
		t.Fatalf("⇥ takes the chosen word: %q col %d", a.FileLines[2], a.CurCol)
	}
	typeText(m, " us")
	if a.Complete == nil {
		t.Fatal("a new word opens the list again")
	}
	keyMsg(m, tea.KeyEsc)
	typeText(m, "e")
	if a.Complete != nil || a.FileLines[2] != "username use" {
		t.Fatalf("esc dismisses it for this word: %q %+v", a.FileLines[2], a.Complete)
	}
	keyMsg(m, tea.KeyBackspace)
	keyMsg(m, tea.KeyBackspace)
	keyMsg(m, tea.KeyBackspace)
	typeText(m, "Us")
	if c := a.Complete; c == nil || c.items[0] != "username" && c.items[0] != "userCount" {
		t.Fatalf("another case still completes: %+v", c)
	}
	keyMsg(m, tea.KeyEnter)
	if !strings.HasPrefix(a.FileLines[2], "username user") {
		t.Fatalf("⏎ puts the word in the file's own case: %q", a.FileLines[2])
	}
}

func TestF7AndF12OnADefinitionListTheReferences(t *testing.T) {
	m, _ := editing(t, 3, 9) // "  const user = 1": the cursor on user
	a := m.st.App
	m.Update(tea.KeyMsg{Type: tea.KeyF7})
	if s := a.Search; s == nil || s.Query != "user" || !s.Word || !s.Case {
		t.Fatalf("F7 opens the search on the name, whole word: %+v", s)
	}
	a.Search = nil
	m.Update(tea.KeyMsg{Type: tea.KeyF12})
	if s := a.Search; s == nil || s.Query != "user" {
		t.Fatalf("F12 on the definition itself lists its uses: %+v", s)
	}
	a.Search = nil
	keyMsg(m, tea.KeyEsc)
	m.Update(tea.KeyMsg{Type: tea.KeyF7})
	if s := a.Search; s == nil || s.Query != "user" {
		t.Fatalf("F7 works while reading too: %+v", s)
	}
}

func TestHashInThePaletteListsTheRepositorysSymbols(t *testing.T) {
	m, _ := codeRepo(t)
	a := m.st.App
	_ = os.MkdirAll(filepath.Join(a.Repo, "src"), 0o755)
	_ = os.WriteFile(filepath.Join(a.Repo, "src/lib.ts"), []byte("import x from 'y'\nexport function loadUser() {}\n"), 0o644)
	a.Files = append(a.Files, "src/lib.ts")
	m.Update(tea.KeyMsg{Type: tea.KeyCtrlK})
	cmd := press(m, "#")
	if items := paletteItems(m.st); len(items) != 1 || !strings.Contains(items[0].Label, "reading") {
		t.Fatalf("while the index is read: %+v", items)
	}
	run(cmd, m)
	press(m, "load")
	items := paletteItems(m.st)
	if len(items) != 1 || items[0].Label != "loadUser" || items[0].Detail != "src/lib.ts:2" || items[0].Action != "at:src/lib.ts:2:17" {
		t.Fatalf("the symbols that match, with where they are: %+v", items)
	}
	follow(m, press(m, "enter"), 0)
	if a.File != "src/lib.ts" || a.cursor() != (pos{1, 16}) || !a.Editing {
		t.Fatalf("⏎ opens the file on the symbol: %s %+v", a.File, a.cursor())
	}
	if len(m.jumpBack) == 0 || m.jumpBack[len(m.jumpBack)-1].file != "src/routes/login.ts" {
		t.Fatal("⌃t goes back to where you were")
	}
}

func TestTheFilesOpenedLatelyComeFirstInThePalette(t *testing.T) {
	m, _ := codeRepo(t)
	a := m.st.App
	run(m.runPalette(paletteItem{Action: "file:src/routes/logout.ts"}, ""), m)
	a.Palette = &Palette{}
	items := paletteItems(m.st)
	if len(items) == 0 || items[0].Section != "recent files" || items[0].Label != "login.ts" || items[0].Action != "file:src/routes/login.ts" {
		t.Fatalf("the file opened before this one, first: %+v", items[:min(3, len(items))])
	}
	for _, it := range items {
		if it.Label == "logout.ts" && it.Section == "recent files" {
			t.Fatal("the open file is not listed")
		}
	}
	a.Palette = &Palette{Attach: true}
	if items := paletteItems(m.st); items[0].Section != "recent files" || items[0].Action != "attach:src/routes/logout.ts" {
		t.Fatalf("@ offers the open file and the recent ones first: %+v", items[0])
	}
}

func TestLongLinesScrollAcrossWhileEditingAndWrapWhileReading(t *testing.T) {
	m, _ := editing(t, 0, 0)
	a := m.st.App
	a.FileLines[0] = strings.Repeat("x", 150) + "END"
	if out := appScreen(m); strings.Contains(out, "END") {
		t.Fatal("the line's end is off the screen")
	}
	keyMsg(m, tea.KeyEnd)
	if a.ColOff == 0 {
		t.Fatal("the view scrolls across to the cursor")
	}
	if out := appScreen(m); !strings.Contains(out, "END") {
		t.Fatalf("the cursor's column is in view:\n%s", out)
	}
	keyMsg(m, tea.KeyHome)
	if a.ColOff != 0 {
		t.Fatal("and back")
	}
	keyMsg(m, tea.KeyEsc)
	m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("z"), Alt: true})
	if !a.Wrap {
		t.Fatal("⌥z wraps")
	}
	if out := appScreen(m); !strings.Contains(out, "END") {
		t.Fatalf("a long line goes on under itself while reading:\n%s", out)
	}
}
