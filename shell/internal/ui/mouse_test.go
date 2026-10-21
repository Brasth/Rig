package ui

import (
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"
)

// clickOn clicks the first cell of text on the screen, as a terminal reports it.
func clickOn(t *testing.T, m *Model, text string) {
	t.Helper()
	x, y := findOnScreen(t, m, text)
	run(mouseAt(m, tea.MouseButtonLeft, tea.MouseActionPress, x, y), m)
}

func findOnScreen(t *testing.T, m *Model, text string) (int, int) {
	t.Helper()
	for y, line := range strings.Split(m.View(), "\n") {
		plain := ansi.Strip(line)
		if at := strings.Index(plain, text); at >= 0 {
			return ansi.StringWidth(plain[:at]), y
		}
	}
	t.Fatalf("%q is not on the screen:\n%s", text, ansi.Strip(m.View()))
	return 0, 0
}

func mouseAt(m *Model, b tea.MouseButton, act tea.MouseAction, x, y int) tea.Cmd {
	_, cmd := m.Update(tea.MouseMsg{X: x, Y: y, Button: b, Action: act})
	return cmd
}

func mouseModel(t *testing.T) (*Model, *fakeSource) {
	m, src := appModel(t)
	m.SetMouse(true)
	return m, src
}

func TestZonesAreTakenOutOfTheFrameAndTheHoveredOneIsUnderlined(t *testing.T) {
	st := State{Mouse: true}
	line := "ab " + st.zone("key:x", "\x1b[1mxy\x1b[0mz") + " " + st.zone("layer:p", "q"+st.zone("row:a:1", "rs"))
	out, zones := zonesOf([]string{line}, -1, -1)
	if ansi.Strip(out[0]) != "ab xyz qrs" || strings.Contains(out[0], "\x1b_") {
		t.Fatalf("the marks are gone: %q", out[0])
	}
	if len(zones) != 3 || zones[0].x0 != 3 || zones[0].x1 != 6 || zones[2].x0 != 8 || zones[2].x1 != 10 {
		t.Fatalf("where each zone is: %+v", zones)
	}
	if id, layers := hitAt(zones, 9, 0); id != "row:a:1" || len(layers) != 1 {
		t.Fatalf("the innermost zone, inside its layer: %q %v", id, layers)
	}
	if id, _ := hitAt(zones, 7, 0); id != "" {
		t.Fatalf("a layer alone is not a click target: %q", id)
	}
	hovered, _ := zonesOf([]string{line}, 4, 0)
	if !strings.Contains(hovered[0], "\x1b[4m\x1b[1m\x1b[4mxy\x1b[0m\x1b[4mz\x1b[24m") {
		t.Fatalf("the hovered zone is underlined through its resets: %q", hovered[0])
	}
	if off := (State{}).zone("key:x", "s"); off != "s" {
		t.Fatal("no marks without the mouse")
	}
}

func TestFooterGlyphsAreTheirKeys(t *testing.T) {
	for glyph, want := range map[string]string{"⏎": "enter", "esc": "esc", "⇥": "tab", "⌃k": "ctrl+k", "⌥c": "alt+c", "space": " ", "u": "u", "?": "?"} {
		if k, ok := keyFor(glyph); !ok || k.String() != want {
			t.Errorf("%s: %q", glyph, k.String())
		}
	}
	if _, ok := keyFor("↑↓"); ok {
		t.Error("a pair of arrows is no one key")
	}
}

func TestClickingTheTopBarAndFooterWorksAsTheirKeys(t *testing.T) {
	m, src := mouseModel(t)
	withConversation(t, m, src)
	clickOn(t, m, "changes")
	if m.st.App.Tab != 2 {
		t.Fatalf("the changes tab: %d", m.st.App.Tab)
	}
	clickOn(t, m, "conversation")
	if m.st.App.Tab != 1 {
		t.Fatal("back on the conversation")
	}
	clickOn(t, m, "⌃k")
	if m.st.App.Palette == nil {
		t.Fatal("the footer's ⌃k opens the palette")
	}
	run(mouseAt(m, tea.MouseButtonLeft, tea.MouseActionPress, 1, 30), m)
	if m.st.App.Palette != nil {
		t.Fatal("a click outside the palette closes it")
	}
	clickOn(t, m, "panel")
	if !m.st.App.Panel {
		t.Fatal("⇥ panel opens the agents panel")
	}
	run(mouseAt(m, tea.MouseButtonLeft, tea.MouseActionPress, 2, 20), m)
	if m.st.App.Panel {
		t.Fatal("a click beside the panel closes it")
	}
}

func TestClickingAPaletteRowRunsIt(t *testing.T) {
	m, src := mouseModel(t)
	withConversation(t, m, src)
	src.appSettings = sampleSettings()
	m.Update(tea.KeyMsg{Type: tea.KeyCtrlK})
	m.st.App.Palette.Query = "settings"
	x, y := findOnScreen(t, m, "│     settings")
	run(mouseAt(m, tea.MouseButtonLeft, tea.MouseActionPress, x+6, y), m)
	if m.st.App.Settings == nil || m.st.App.Palette != nil {
		t.Fatal("the clicked row ran: Settings is open")
	}
}

func TestClickingASettingsChoiceAppliesIt(t *testing.T) {
	m, src := mouseModel(t)
	src.appSettings = sampleSettings()
	run(m.runPalette(paletteItem{Action: "settings"}, ""), m)
	clickOn(t, m, "editor")
	if SettingsSections[m.st.App.Settings.Section] != "editor" {
		t.Fatalf("the clicked section: %d", m.st.App.Settings.Section)
	}
	clickOn(t, m, "general")
	clickOn(t, m, "dracula")
	if !strings.HasPrefix(m.st.Message, "theme dracula") {
		t.Fatalf("a click on a theme applies it: %q", m.st.Message)
	}
	clickOn(t, m, "Rig in this repo")
	clickOn(t, m, "Rig in this repo")
	if m.st.Mode != ModeConfirm {
		t.Fatal("a second click on an on/off row turns it over (off asks first)")
	}
}

func TestClickingAnAnswerChipAnswers(t *testing.T) {
	m, src := mouseModel(t)
	withConversation(t, m, src)
	clickOn(t, m, " deny ")
	if len(src.calls) == 0 || src.calls[len(src.calls)-1].method != "job.deny" {
		t.Fatalf("deny was clicked: %v", src.calls)
	}
}

func TestTheWheelScrollsAndTheMovingPointerHovers(t *testing.T) {
	m, src := mouseModel(t)
	withConversation(t, m, src)
	run(mouseAt(m, tea.MouseButtonWheelUp, tea.MouseActionPress, 50, 10), m)
	if m.st.ChatUp != 3 {
		t.Fatalf("the wheel scrolls the conversation: %d", m.st.ChatUp)
	}
	run(mouseAt(m, tea.MouseButtonWheelDown, tea.MouseActionPress, 50, 10), m)
	if m.st.ChatUp != 0 {
		t.Fatal("and back down")
	}
	x, y := findOnScreen(t, m, "changes")
	run(mouseAt(m, tea.MouseButtonNone, tea.MouseActionMotion, x, y), m)
	if line := strings.Split(m.View(), "\n")[y]; !strings.Contains(line, "\x1b[4m") {
		t.Fatalf("the tab under the pointer is underlined: %q", line)
	}
}

func TestAClickSkipsTheSplash(t *testing.T) {
	m, _ := mouseModel(t)
	m.st.App.Splash = 3
	run(mouseAt(m, tea.MouseButtonLeft, tea.MouseActionPress, 5, 5), m)
	if m.st.App.Splash != 0 {
		t.Fatal("a click goes straight to the screen")
	}
}

func TestEveryScreenKeepsItsWidthWithTheMouseOn(t *testing.T) {
	m, src := mouseModel(t)
	src.appSettings = sampleSettings()
	check := func(name string) {
		t.Helper()
		for i, line := range strings.Split(m.View(), "\n") {
			if strings.Contains(line, "\x1b_") || ansi.StringWidth(line) != 120 {
				t.Fatalf("%s line %d: %d cells %q", name, i, ansi.StringWidth(line), line)
			}
		}
		if len(m.zones) == 0 {
			t.Fatalf("%s has nothing to click", name)
		}
	}
	check("first conversation")
	withConversation(t, m, src)
	check("conversation")
	m.Update(tea.KeyMsg{Type: tea.KeyCtrlK})
	check("palette")
	m.st.App.Palette = nil
	m.st.App.Panel = true
	check("panel")
	m.st.App.Panel = false
	run(m.openTab(2), m)
	check("changes")
	run(m.openTab(3), m)
	check("code")
	run(m.openTab(1), m)
	m.Update(tea.KeyMsg{Type: tea.KeyCtrlF})
	check("search")
	m.st.App.Search = nil
	run(m.runPalette(paletteItem{Action: "settings"}, ""), m)
	for i := range SettingsSections {
		m.st.App.Settings.Section = i
		check("settings " + SettingsSections[i])
	}
	m.st.App.Settings = nil
	src.today = sampleToday()
	run(m.runPalette(paletteItem{Action: "today"}, ""), m)
	check("done today")
}

func TestTheLauncherOpensAClickedProject(t *testing.T) {
	l, base, _ := launcherFor(t)
	l.SetMouse(true)
	for y, line := range strings.Split(l.View(), "\n") {
		if at := strings.Index(ansi.Strip(line), "web"); at >= 0 && !strings.Contains(line, "e.g.") {
			l.Update(tea.MouseMsg{X: at, Y: y, Button: tea.MouseButtonLeft, Action: tea.MouseActionPress})
			break
		}
	}
	if l.confirm == "" || !strings.Contains(l.confirm, "web") {
		t.Fatalf("a click on the project not set up asks to set it up: %q (open %q, base %s)", l.confirm, l.Open, base)
	}
}
