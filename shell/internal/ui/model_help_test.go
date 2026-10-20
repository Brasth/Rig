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
	if m.st.Tab != EnvTab {
		t.Fatalf("w went to %q", m.st.Tab)
	}
}

func TestTheTabStripIsTheMockupsAndLogsIsAScreenUnderJobs(t *testing.T) {
	m, src := newReady(t)
	strip := strings.Split(Render(m.view, m.st, 120, 36), "\n")[1]
	order := []string{"Session", "Jobs", "Queue", "Workflows", "Env"}
	last := -1
	for _, name := range order {
		at := strings.Index(strip, name)
		if at <= last {
			t.Fatalf("%q out of order in %q", name, strip)
		}
		last = at
	}
	for _, gone := range []string{"Workspace", "Fleet", "Logs", "Term"} {
		if strings.Contains(strip, gone) {
			t.Fatalf("%q is not a tab: %q", gone, strip)
		}
	}
	src.logReply = logReply("job-1", "text")
	press(m, "l")
	if m.st.Tab != LogsTab {
		t.Fatalf("l opens the log, got %q", m.st.Tab)
	}
	if !strings.Contains(strings.Split(Render(m.view, m.st, 120, 36), "\n")[1], "[Jobs") {
		t.Fatal("Jobs stays the highlighted tab while its log shows")
	}
	press(m, "esc")
	if m.st.Tab != "Jobs" {
		t.Fatalf("esc returns to Jobs, got %q", m.st.Tab)
	}
	press(m, "l", "tab")
	if m.st.Tab != "Queue" {
		t.Fatalf("tab from the log goes to the tab after Jobs, got %q", m.st.Tab)
	}
	press(m, "l")
	if m.st.Tab != "Queue" {
		t.Fatal("l only opens a log from Jobs")
	}
}

func TestOldTabNamesAreAliases(t *testing.T) {
	for old, want := range map[string]string{"Workspace": EnvTab, "Fleet": EnvTab, "Term": SessionTab, "Jobs": "Jobs", "Logs": "Logs"} {
		if got := TabAlias(old); got != want {
			t.Errorf("TabAlias(%q) = %q, want %q", old, got, want)
		}
		if !ValidTab(TabAlias(old)) {
			t.Errorf("%q does not name a tab", old)
		}
	}
	if ValidTab("Nowhere") {
		t.Error("an unknown tab is not valid")
	}
}

func TestTheEnvTabShowsWorkspaceAndFleetAndOneFailureDoesNotHideTheOther(t *testing.T) {
	st := NewState()
	st.Tab = EnvTab
	st.Workspace = loadWorkspace(t)
	st.FleetE = "engine not running"
	out := Render(loadView(t), st, 100, 40)
	for _, want := range []string{"kind     devcontainer", "Fleet unavailable: engine not running"} {
		if !strings.Contains(out, want) {
			t.Fatalf("missing %q:\n%s", want, out)
		}
	}
}

func TestSetTabOpensOnThatTab(t *testing.T) {
	m := NewModel(&fakeSource{}, false)
	m.SetTab(SessionTab)
	if m.st.Tab != SessionTab {
		t.Fatalf("tab %q", m.st.Tab)
	}
}
