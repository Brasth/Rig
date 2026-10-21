package ui

import (
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
)

func TestCtrlDAddsTheNextOccurrenceAndTypingChangesThemAll(t *testing.T) {
	m, _ := editing(t, 0, 0)
	a := m.st.App
	a.FileLines = []string{"foo(foo)", "bar", "foo"}
	a.setCursor(pos{0, 1}) // on the first foo
	keyMsg(m, tea.KeyCtrlD)
	if from, to, ok := a.selRange(); !ok || from != (pos{0, 0}) || to != (pos{0, 3}) || len(a.Extra) != 0 {
		t.Fatalf("⌃d on a word selects it first: %v %v %v", from, to, a.Extra)
	}
	keyMsg(m, tea.KeyCtrlD)
	keyMsg(m, tea.KeyCtrlD)
	if len(a.Extra) != 2 || a.cursor() != (pos{2, 3}) {
		t.Fatalf("⌃d twice more: three selections, the cursor on the last: %v %v", a.Extra, a.cursor())
	}
	typeText(m, "qux")
	if strings.Join(a.FileLines, "|") != "qux(qux)|bar|qux" {
		t.Fatalf("typing changes every selection: %q", a.FileLines)
	}
	if len(a.Extra) != 2 || a.Extra[0] != (span{pos{0, 3}, pos{0, 3}}) || a.Extra[1] != (span{pos{0, 7}, pos{0, 7}}) || a.cursor() != (pos{2, 3}) {
		t.Fatalf("each cursor at the end of its change: %v %v", a.Extra, a.cursor())
	}
	keyMsg(m, tea.KeyBackspace)
	if strings.Join(a.FileLines, "|") != "qu(qu)|bar|qu" {
		t.Fatalf("⌫ at every cursor: %q", a.FileLines)
	}
	keyMsg(m, tea.KeyCtrlZ)
	if strings.Join(a.FileLines, "|") != "qux(qux)|bar|qux" {
		t.Fatalf("one undo for the change at all of them: %q", a.FileLines)
	}
	keyMsg(m, tea.KeyEsc)
	if len(a.Extra) != 0 {
		t.Fatal("esc keeps the primary selection only")
	}
}

func TestF2RenamesEveryWholeWordInTheFile(t *testing.T) {
	m, _ := editing(t, 0, 0)
	a := m.st.App
	a.FileLines = []string{"const user = get(user)", "users.push(user)", "// user"}
	a.setCursor(pos{0, 7})
	keyMsg(m, tea.KeyF2)
	if a.CodeAsk == nil || a.CodeAsk.kind != "rename-word" || !strings.Contains(a.CodeAsk.label, "4 places") {
		t.Fatalf("F2 asks for the new name, counting whole words only: %+v", a.CodeAsk)
	}
	a.CodeAsk.text = ""
	typeText(m, "account")
	keyMsg(m, tea.KeyEnter)
	if strings.Join(a.FileLines, "|") != "const account = get(account)|users.push(account)|// account" {
		t.Fatalf("renamed everywhere, users untouched: %q", a.FileLines)
	}
	if !strings.Contains(m.st.Message, "renamed in 4 places") || a.CodeAsk != nil {
		t.Fatalf("%q", m.st.Message)
	}
}
