package ui

import (
	"strings"
	"testing"
)

func TestAtInTheMiddleOfAMessageNamesAFileAtTheCursorWithOptionalLines(t *testing.T) {
	m, src := appModel(t)
	withConversation(t, m, src)
	a := m.st.App
	a.Files = []string{"pkg/auth.py", "README.md", "pkg/db.py"}
	typeText(m, "fix the bug in")
	typeText(m, "x@") // an @ inside a word is just an @
	if a.Palette != nil || m.st.Prompt != "fix the bug inx@" {
		t.Fatalf("not a mention: %q", m.st.Prompt)
	}
	m.setPrompt("fix the bug in ", len([]rune("fix the bug in ")))
	typeText(m, "@")
	if a.Palette == nil || !a.Palette.Attach || m.st.Prompt != "fix the bug in " {
		t.Fatalf("an @ that starts a word opens the file list without being typed: %+v %q", a.Palette, m.st.Prompt)
	}
	a.Palette.Query = "auth"
	items := paletteItems(m.st)
	if len(items) != 1 || items[0].Action != "attach:pkg/auth.py" {
		t.Fatalf("typing narrows: %+v", items)
	}
	a.Palette.Query = "auth.py:10-40"
	items = paletteItems(m.st)
	if len(items) != 1 || items[0].Label != "auth.py:10-40" || items[0].Detail != "pkg · lines 10-40" || items[0].Action != "attach:pkg/auth.py:10-40" {
		t.Fatalf("a range picks just those lines: %+v", items)
	}
	// the file goes in at the cursor, which moved into the middle
	m.setPrompt("fix the bug in  please", len([]rune("fix the bug in ")))
	press(m, "enter")
	if m.st.Prompt != "fix the bug in @pkg/auth.py:10-40  please" || !strings.HasSuffix(string([]rune(m.st.Prompt)[:len([]rune("fix the bug in @pkg/auth.py:10-40 "))]), " ") {
		t.Fatalf("inserted at the cursor: %q", m.st.Prompt)
	}
	if a.Palette != nil {
		t.Fatal("the list closes")
	}
	// an empty box still opens it on @, as before
	m.setPrompt("", 0)
	typeText(m, "@")
	if a.Palette == nil || !a.Palette.Attach {
		t.Fatal("@ in an empty box")
	}
}
