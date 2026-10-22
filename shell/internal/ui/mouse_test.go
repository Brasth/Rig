package ui

import (
	"encoding/base64"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"

	"github.com/Brasth/Rig/shell/internal/view"
)

// clickOn clicks the first cell of text on the screen, as a terminal reports it.
func clickOn(t *testing.T, m *Model, text string) {
	t.Helper()
	x, y := findOnScreen(t, m, text)
	clickAt(m, x, y)
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

// clickAt presses the left button and lets it go at one cell, as a click arrives.
func clickAt(m *Model, x, y int) {
	run(mouseAt(m, tea.MouseButtonLeft, tea.MouseActionPress, x, y), m)
	run(mouseAt(m, tea.MouseButtonLeft, tea.MouseActionRelease, x, y), m)
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
	if !strings.Contains(hovered[0], "\x1b[1m\x1b[4mxy\x1b[0m\x1b[4mz\x1b[24m") {
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
	clickAt(m, 1, 30)
	if m.st.App.Palette != nil {
		t.Fatal("a click outside the palette closes it")
	}
	clickOn(t, m, "panel")
	if !m.st.App.Panel {
		t.Fatal("⇥ panel opens the agents panel")
	}
	clickAt(m, 2, 20)
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
	clickAt(m, x+6, y)
	if m.st.App.Settings == nil || m.st.App.Palette != nil {
		t.Fatal("the clicked row ran: Settings is open")
	}
}

func TestClickingASettingsChoiceAppliesIt(t *testing.T) {
	m, src := mouseModel(t)
	src.appSettings = sampleSettings()
	run(m.runPalette(paletteItem{Action: "settings"}, ""), m)
	clickOn(t, m, "more settings") // the short list first; the rest is one click away
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
	m.View()
	run(mouseAt(m, tea.MouseButtonWheelUp, tea.MouseActionPress, 50, 10), m)
	if m.st.ChatUp != 0 {
		t.Fatal("a conversation that fits does not scroll")
	}
	var long []view.ChatEntry
	for i := 0; i < 40; i++ {
		long = append(long, ce("user", fmt.Sprint("question ", i)), ce("assistant", "an answer"))
	}
	src.chatView = transcript("idle", long...)
	run(m.fetchChat(true), m)
	m.View()
	run(mouseAt(m, tea.MouseButtonWheelUp, tea.MouseActionPress, 50, 10), m)
	if m.st.ChatUp != wheelStep {
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
	clickAt(m, 5, 5)
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

func TestAThreadScrollsWithTheWheelAndPages(t *testing.T) {
	m, src := mouseModel(t)
	withConversation(t, m, src)
	var log []string
	for i := 1; i <= 120; i++ {
		log = append(log, fmt.Sprintf("line %03d %s", i, strings.Repeat("word ", 30)))
	}
	m.st.App.Thread = "job-7f2"
	m.st.Log = &view.LogReply{Text: strings.Join(log, "\n")}
	screen := ansi.Strip(m.View())
	if !strings.Contains(screen, "line 120") || strings.Contains(screen, "line 100 ") {
		t.Fatalf("the newest lines, wrapped:\n%s", screen)
	}
	run(mouseAt(m, tea.MouseButtonWheelUp, tea.MouseActionPress, 50, 10), m)
	if m.st.LogUp != wheelStep || m.st.ChatUp != 0 {
		t.Fatalf("the wheel scrolls the thread, not the conversation: %d %d", m.st.LogUp, m.st.ChatUp)
	}
	for i := 0; i < 60; i++ {
		run(press(m, "pgup"), m)
		m.View()
	}
	if m.st.LogUp != m.st.App.LogMax {
		t.Fatalf("it stops at the oldest line: %d of %d", m.st.LogUp, m.st.App.LogMax)
	}
	if screen := ansi.Strip(m.View()); !strings.Contains(screen, "line 001") || !strings.Contains(screen, "G newest") {
		t.Fatalf("PgUp reaches the oldest line:\n%s", screen)
	}
	m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{'G'}})
	if m.st.LogUp != 0 {
		t.Fatal("G follows the newest lines again")
	}
}

func TestDraggingSelectsTextAndLettingGoCopiesIt(t *testing.T) {
	m, src := mouseModel(t)
	withConversation(t, m, src)
	out, err := os.Create(filepath.Join(t.TempDir(), "out"))
	if err != nil {
		t.Fatal(err)
	}
	defer out.Close()
	NewTermWriter(out, true)
	t.Cleanup(func() { logoOut = nil })
	x, y := findOnScreen(t, m, "I put a sliding-window")
	run(mouseAt(m, tea.MouseButtonLeft, tea.MouseActionPress, x+2, y), m)
	run(mouseAt(m, tea.MouseButtonLeft, tea.MouseActionMotion, x+20, y), m)
	if line := strings.Split(m.View(), "\n")[y]; !strings.Contains(line, "\x1b[7m") {
		t.Fatalf("what is dragged over is lit: %q", line)
	}
	run(mouseAt(m, tea.MouseButtonLeft, tea.MouseActionRelease, x+20, y), m)
	if !strings.HasPrefix(m.st.Message, "copied 19 characters") {
		t.Fatalf("letting go copies: %q", m.st.Message)
	}
	if screen := ansi.Strip(m.View()); !strings.Contains(screen, "✓ copied 19 characters") || m.sel != nil {
		t.Fatal("the footer says so, and the highlight goes")
	}
	logoOut.Write([]byte("frame"))
	data, _ := os.ReadFile(out.Name())
	want := base64.StdEncoding.EncodeToString([]byte("put a sliding-windo"))
	if !strings.Contains(string(data), "\x1b]52;c;"+want+"\x07") || !strings.Contains(string(data), "\x1bPtmux;\x1b\x1b]52;c;"+want) {
		t.Fatalf("to the clipboard, through tmux too: %q", data)
	}
	run(mouseAt(m, tea.MouseButtonLeft, tea.MouseActionPress, x+2, y), m)
	run(mouseAt(m, tea.MouseButtonLeft, tea.MouseActionMotion, x+5, y), m)
	m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{'x'}})
	if m.sel != nil {
		t.Fatal("a key ends the selection")
	}
	m.press = nil
	tx, ty := findOnScreen(t, m, "changes")
	run(mouseAt(m, tea.MouseButtonLeft, tea.MouseActionPress, tx, ty), m)
	run(mouseAt(m, tea.MouseButtonLeft, tea.MouseActionMotion, tx+20, ty), m)
	run(mouseAt(m, tea.MouseButtonLeft, tea.MouseActionRelease, tx+20, ty), m)
	if m.st.App.Tab != 1 {
		t.Fatal("a drag that starts on a tab selects, it does not click")
	}
}

func TestASelectionOverSeveralRowsLosesTheMargin(t *testing.T) {
	plain := []string{"     first row of it   ", "     second row", "     third row   "}
	if got := selectedText(plain, textSel{ax: 11, ay: 0, bx: 9, by: 2}); got != "row of it\nsecond row\nthird" {
		t.Fatalf("%q", got)
	}
	if got := selectedText(plain, textSel{ax: 9, ay: 2, bx: 11, by: 0}); got != "row of it\nsecond row\nthird" {
		t.Fatalf("dragged upwards: %q", got)
	}
}

func TestAWholeListRowIsNotUnderlinedOnHover(t *testing.T) {
	st := State{Mouse: true}
	row := st.zone("row:panel:0", " (c)  claude  Cwd /Users/someone/project…   ›            ")
	button := st.zone("row:open:settings", "settings")
	out, _ := zonesOf([]string{row + " " + button}, 5, 0)
	if strings.Contains(out[0], "\x1b[4m") {
		t.Fatalf("a hovered list row gets no line under it: %q", out[0])
	}
	out, _ = zonesOf([]string{row + " " + button}, ansi.StringWidth(ansi.Strip(row))+3, 0)
	if !strings.Contains(out[0], "\x1b[4msettings\x1b[24m") {
		t.Fatalf("a hovered button still is: %q", out[0])
	}
}

func TestAHoveredRowUnderlinesItsTextNotItsPadding(t *testing.T) {
	st := State{Mouse: true}
	row := st.zone("row:tree:package.json", "    \x1b[1mpackage.json\x1b[0m        ")
	out, _ := zonesOf([]string{row}, 6, 0)
	if !strings.Contains(out[0], "    \x1b[1m\x1b[4mpackage.json\x1b[24m\x1b[0m        ") {
		t.Fatalf("only the name is underlined: %q", out[0])
	}
}
