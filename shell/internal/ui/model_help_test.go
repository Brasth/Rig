package ui

import (
	"strings"
	"testing"
)

func TestQuestionMarkOpensTheKeyListAndEscCloses(t *testing.T) {
	m, _ := newReady(t)
	press(m, "?")
	if !m.st.HelpOpen {
		t.Fatal("? opens the key list")
	}
	out := Render(m.view, m.st, 100, 30)
	for _, want := range []string{"Keys", "park a task in the queue", "stop the selected job", "this list"} {
		if !strings.Contains(out, want) {
			t.Fatalf("key list lacks %q:\n%s", want, out)
		}
	}
	press(m, "j", "x") // keys other than ? Esc q do nothing while the list shows
	if !m.st.HelpOpen || m.st.Mode != ModeNormal {
		t.Fatalf("the list swallows other keys: %+v", m.st)
	}
	press(m, "esc")
	if m.st.HelpOpen {
		t.Fatal("esc closes the key list")
	}
	press(m, "?", "?")
	if m.st.HelpOpen {
		t.Fatal("? toggles it closed")
	}
}

func TestWJumpsToTheWorkspaceAndTheFooterNamesIt(t *testing.T) {
	m, _ := newReady(t)
	if footer := footerLine(nil, 0, m.st, 120); !strings.Contains(footer, "w workspace") || !strings.Contains(footer, "? help") {
		t.Fatalf("footer: %q", footer)
	}
	press(m, "w")
	if m.st.Tab != WorkspaceTab {
		t.Fatalf("w went to %q", m.st.Tab)
	}
}
