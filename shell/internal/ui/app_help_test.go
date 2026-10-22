package ui

import (
	"strings"
	"testing"

	"github.com/Brasth/Rig/shell/internal/view"
)

func TestHelpIsByWhatYouWantToDoAndEachRowDoesIt(t *testing.T) {
	m, src := appModel(t)
	withConversation(t, m, src)
	m.st.Chat = turnThatChanged("idle", 1, 1, "a.py")
	follow(m, m.appSend("/help"), 0)
	if p := m.st.App.Palette; p == nil || !p.Help {
		t.Fatal("/help opens what you can do")
	}
	var rows []string
	for _, it := range paletteItems(m.st) {
		rows = append(rows, it.Section+"|"+it.Label)
	}
	list := strings.Join(rows, "\n")
	for _, want := range []string{"Keep going|Ask about a file", "Check and keep|Put the files back as they were", "Make it yours|Switch assistant",
		"Something wrong?|What is wrong with this project", "All of it|Every key"} {
		if !strings.Contains(list, want) {
			t.Fatalf("missing %q:\n%s", want, list)
		}
	}
	m.st.App.Palette.Query = "wrong"
	if items := paletteItems(m.st); len(items) < 1 || items[0].Action != "doctor" {
		t.Fatalf("typing narrows by words: %+v", items)
	}
	// a row does the thing: doctor opens, review goes to this turn's files, a command is put in the box
	m.st.App.Palette.Query = "what is wrong"
	follow(m, press(m, "enter"), 0)
	if p := m.st.App.Palette; p == nil || !p.Doctor {
		t.Fatalf("⏎ on 'what is wrong' opens the doctor: %+v", m.st.App.Palette)
	}
	m.st.App.Palette = nil
	follow(m, m.helpAction("review-turn"), 0)
	if m.st.App.Tab != 3 || len(m.st.App.GitScope) != 1 {
		t.Fatalf("review goes to this turn's files: tab %d %v", m.st.App.Tab, m.st.App.GitScope)
	}
	m.st.App.Tab, m.st.Chat = 1, transcript("idle", ce("user", "hi"), ce("assistant", "hello"))
	follow(m, m.helpAction("commit-turn"), 0)
	if !strings.Contains(m.st.Message, "nothing has changed yet") {
		t.Fatalf("with nothing changed it says so: %q", m.st.Message)
	}
	m.st.Chat = &view.ChatTranscript{Session: "c-1a2b3c4d", Parent: "claude", State: "idle"}
	follow(m, m.helpAction("stop-turn"), 0)
	if !strings.Contains(m.st.Message, "not working on anything") {
		t.Fatalf("stopping an idle parent says so: %q", m.st.Message)
	}
	follow(m, m.helpAction("help-keys"), 0)
	if !m.st.App.Help {
		t.Fatal("every key opens the full list")
	}
}

func TestSettingsShowTheShortListFirstAndTheRestOnRequest(t *testing.T) {
	m, src := appModel(t)
	withConversation(t, m, src)
	src.appSettings = sampleSettings()
	run(m.runPalette(paletteItem{Action: "settings"}, ""), m)
	sc := m.st.App.Settings
	out := appScreen(m)
	for _, want := range []string{"general", "assistants", "git and saving", "notifications", "a  more settings"} {
		if !strings.Contains(out, want) {
			t.Fatalf("the short list has %q:\n%s", want, out)
		}
	}
	for _, hidden := range []string{"who does what", "approvals", "sandbox", "editor", "raw file"} {
		if strings.Contains(strings.Split(out, "\n")[2], hidden) {
			t.Fatalf("%q waits behind 'more settings':\n%s", hidden, out)
		}
	}
	press(m, "tab", "tab", "tab", "tab")
	if got := SettingsSections[sc.Section]; got != "general" {
		t.Fatalf("⇥ goes round the four that show: %s", got)
	}
	press(m, "a")
	out = appScreen(m)
	for _, want := range []string{"who does what", "approvals", "sandbox", "editor", "raw file", "a  fewer settings"} {
		if !strings.Contains(out, want) {
			t.Fatalf("a shows the rest, %q:\n%s", want, out)
		}
	}
	press(m, "tab", "tab")
	if got := SettingsSections[sc.Section]; got != "routing" {
		t.Fatalf("⇥ now visits the others too: %s", got)
	}
	press(m, "a")
	if got := SettingsSections[sc.Section]; got != "general" || sc.Advanced {
		t.Fatalf("hiding them while on one goes back to general: %s %v", got, sc.Advanced)
	}
	// asked for by name, the section shows even though it is not in the short list
	follow(m, m.openSettings("keys"), 0)
	if !m.st.App.Settings.Advanced || SettingsSections[m.st.App.Settings.Section] != "keys" {
		t.Fatal("/settings keys opens it and shows the rest")
	}
}

func TestHelpSaysWhatTheAssistantCannotDoHere(t *testing.T) {
	st := State{ChatParent: "grok", ChatLimits: []string{"Asks before running a command or editing: grok's headless mode cannot ask", "How full the context is: grok does not report it"}}
	var got []paletteItem
	for _, it := range helpItems(st, "") {
		if strings.HasPrefix(it.Section, "What grok cannot do here") {
			got = append(got, it)
		}
	}
	if len(got) != 2 || got[0].Label != "Asks before running a command or editing" || !strings.Contains(got[0].Detail, "cannot ask") {
		t.Fatalf("the gaps are listed in help: %+v", got)
	}
	if rows := helpItems(st, "context"); len(rows) != 1 || !strings.Contains(rows[0].Label, "context") {
		t.Fatalf("they can be searched: %+v", rows)
	}
}
