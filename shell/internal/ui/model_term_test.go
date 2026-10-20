package ui

import (
	"errors"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/Brasth/Rig/shell/internal/view"
)

type fakePane struct {
	writes   [][]byte
	lines    []string
	app      bool
	done     bool
	err      error
	closed   int
	resizes  [][2]int
	updates  chan struct{}
	cursorOn []bool
}

func newFakePane() *fakePane {
	return &fakePane{updates: make(chan struct{}, 1), lines: []string{"$ ready"}}
}

func (f *fakePane) Resize(c, r int)          { f.resizes = append(f.resizes, [2]int{c, r}) }
func (f *fakePane) Write(b []byte)           { f.writes = append(f.writes, append([]byte(nil), b...)) }
func (f *fakePane) AppCursor() bool          { return f.app }
func (f *fakePane) Updates() <-chan struct{} { return f.updates }
func (f *fakePane) Exited() (bool, error)    { return f.done, f.err }
func (f *fakePane) Close()                   { f.closed++ }
func (f *fakePane) Lines(styled, cursor bool) []string {
	f.cursorOn = append(f.cursorOn, cursor)
	return f.lines
}

type spawned struct {
	argv       []string
	dir        string
	env        []string
	cols, rows int
}

// termModel is a ready model on the Term tab whose spawns are recorded and answered with fake panes.
func termModel(t *testing.T) (*Model, *fakeSource, *[]spawned, *[]*fakePane) {
	t.Helper()
	m, src := newReady(t)
	m.w, m.h = 100, 36 // narrower than the composite screen: these tests are about the Session tab as one program
	press(m, "shift+tab")
	if m.st.Tab != SessionTab {
		t.Fatalf("tab %q", m.st.Tab)
	}
	var calls []spawned
	var panes []*fakePane
	m.spawn = func(argv []string, dir string, env []string, cols, rows int) (Pane, error) {
		calls = append(calls, spawned{argv, dir, env, cols, rows})
		p := newFakePane()
		panes = append(panes, p)
		return p, nil
	}
	src.parentPlan = &view.ShellPlan{Available: true, Argv: []string{"/usr/bin/codex"}, Dir: "/repo", Env: []string{"RIG_SHELL_ACTIVE=1"}, Label: "codex"}
	src.shellPlan = &view.ShellPlan{Available: true, Argv: []string{"/usr/bin/dc", "exec", "--no-start", "/repo"}, Dir: "/repo", Label: "container shell"}
	return m, src, &calls, &panes
}

// runAll runs a command and feeds every message it produces back, so a plan reply starts the pane.
func runAll(m *Model, cmd tea.Cmd) {
	if cmd == nil {
		return
	}
	if msg := cmd(); msg != nil {
		if _, isBatch := msg.(tea.BatchMsg); !isBatch {
			_, next := m.Update(msg)
			_ = next
		}
	}
}

func TestPStartsTheParentCLIInThePaneSizedToTheBody(t *testing.T) {
	m, src, calls, panes := termModel(t)
	runAll(m, press(m, "p"))
	if src.parentCalls != 1 || len(*calls) != 1 {
		t.Fatalf("parent asked %d, spawned %d", src.parentCalls, len(*calls))
	}
	c := (*calls)[0]
	if strings.Join(c.argv, " ") != "/usr/bin/codex" || c.dir != "/repo" || c.env[0] != "RIG_SHELL_ACTIVE=1" {
		t.Fatalf("spawned %+v", c)
	}
	if c.cols != 100 || c.rows != 31 {
		t.Fatalf("size %dx%d, want the 100x31 body", c.cols, c.rows)
	}
	if !m.paneFocus || m.paneName != "codex" || len(*panes) != 1 {
		t.Fatal("a new pane takes the keyboard")
	}
}

func TestSStartsAContainerShellFromTheServersCommand(t *testing.T) {
	m, src, calls, _ := termModel(t)
	runAll(m, press(m, "s"))
	if len(src.shellIDs) != 1 || src.shellIDs[0] != "" {
		t.Fatalf("asked for %v: the repository's container, not a job's", src.shellIDs)
	}
	if got := strings.Join((*calls)[0].argv, " "); got != "/usr/bin/dc exec --no-start /repo" {
		t.Fatalf("ran %q", got)
	}
}

func TestAnUnavailableOrFailedPlanStartsNothingAndSaysWhy(t *testing.T) {
	m, src, calls, _ := termModel(t)
	src.parentPlan = &view.ShellPlan{Available: false, Reason: "parent CLI 'codex' is not on PATH"}
	runAll(m, press(m, "p"))
	if len(*calls) != 0 || !strings.Contains(m.st.Message, "Cannot start: parent CLI 'codex' is not on PATH") {
		t.Fatalf("calls %v message %q", *calls, m.st.Message)
	}
	src.parentPlan, src.parentErr = nil, errors.New("api gone")
	runAll(m, press(m, "p"))
	if !strings.Contains(m.st.Message, "Could not start: api gone") {
		t.Fatalf("message %q", m.st.Message)
	}
	src.parentErr = nil
	src.parentPlan = &view.ShellPlan{Available: true, Argv: []string{"x"}}
	m.spawn = func([]string, string, []string, int, int) (Pane, error) { return nil, errors.New("exec format error") }
	runAll(m, press(m, "p"))
	if m.pane != nil || !strings.Contains(m.st.Message, "Could not start: exec format error") {
		t.Fatalf("message %q", m.st.Message)
	}
}

func TestWhileTypingEveryKeyGoesToTheProgramIncludingCtrlC(t *testing.T) {
	m, _, _, panes := termModel(t)
	runAll(m, press(m, "p"))
	p := (*panes)[0]
	for _, k := range []tea.KeyMsg{{Type: tea.KeyRunes, Runes: []rune("q")}, {Type: tea.KeyCtrlC}, {Type: tea.KeyTab}, {Type: tea.KeyEnter},
		{Type: tea.KeyUp}} {
		_, cmd := m.Update(k)
		if cmd != nil {
			t.Fatalf("%v produced a command: the board must not act on it", k)
		}
	}
	got := ""
	for _, w := range p.writes {
		got += string(w)
	}
	if got != "q\x03\t\r\x1b[A" {
		t.Fatalf("the program received %q", got)
	}
	if m.st.Tab != SessionTab {
		t.Fatal("Tab did not change tabs while typing")
	}
	p.app = true
	m.Update(tea.KeyMsg{Type: tea.KeyDown})
	if string(p.writes[len(p.writes)-1]) != "\x1bOB" {
		t.Fatalf("application cursor keys: %q", p.writes[len(p.writes)-1])
	}
}

func TestCtrlBracketReturnsToTheBoardAndEnterGoesBack(t *testing.T) {
	m, _, _, panes := termModel(t)
	runAll(m, press(m, "p"))
	p := (*panes)[0]
	m.Update(tea.KeyMsg{Type: tea.KeyCtrlCloseBracket})
	if m.paneFocus || len(p.writes) != 0 {
		t.Fatal("Ctrl-] is the board's, never the program's")
	}
	press(m, "tab")
	if m.st.Tab == SessionTab {
		t.Fatal("the board's tab key works again")
	}
	press(m, "shift+tab")
	press(m, "enter")
	if !m.paneFocus {
		t.Fatal("Enter types into the pane again")
	}
	_, cmd := m.Update(tea.KeyMsg{Type: tea.KeyCtrlCloseBracket})
	_ = cmd
	press(m, "q")
}

func TestQQuitsOnlyWhenNotTyping(t *testing.T) {
	m, _, _, _ := termModel(t)
	if cmd := press(m, "q"); cmd == nil {
		t.Fatal("q quits the board when no pane has the keyboard")
	}
	m2, _, _, _ := termModel(t)
	runAll(m2, press(m2, "p"))
	_, cmd := m2.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("q")})
	if cmd != nil {
		t.Fatal("q goes to the program while typing")
	}
}

func TestAnExitedProgramReleasesTheKeyboardAndSaysHow(t *testing.T) {
	m, _, _, panes := termModel(t)
	runAll(m, press(m, "p"))
	p := (*panes)[0]
	p.done, p.err = true, errors.New("exit status 3")
	_, next := m.Update(paneMsg{p: p})
	if next != nil || m.paneFocus {
		t.Fatal("no more waiting, no keyboard")
	}
	if !strings.Contains(m.st.Message, "codex exited: exit status 3") {
		t.Fatalf("message %q", m.st.Message)
	}
	press(m, "enter")
	if m.paneFocus {
		t.Fatal("cannot type into a dead program")
	}
	m.View()
	if !strings.Contains(m.st.PaneTitle, "exited: exit status 3") {
		t.Fatalf("title %q", m.st.PaneTitle)
	}
}

func TestAStalePaneMessageIsIgnored(t *testing.T) {
	m, _, _, panes := termModel(t)
	runAll(m, press(m, "p"))
	old := (*panes)[0]
	old.done = true
	m.Update(paneMsg{p: old})
	runAll(m, press(m, "p"))
	if len(*panes) != 2 {
		t.Fatalf("a finished pane is replaced: %d spawns", len(*panes))
	}
	if _, cmd := m.Update(paneMsg{p: old}); cmd != nil {
		t.Fatal("a message from the replaced pane must not keep a waiter alive")
	}
	if old.closed == 0 {
		t.Fatal("the replaced pane was closed")
	}
}

func TestARunningProgramIsNeverReplacedSilently(t *testing.T) {
	m, _, calls, _ := termModel(t)
	runAll(m, press(m, "p"))
	m.Update(tea.KeyMsg{Type: tea.KeyCtrlCloseBracket})
	runAll(m, press(m, "s"))
	if len(*calls) != 1 || !strings.Contains(m.st.Message, "already running") {
		t.Fatalf("spawns %d message %q", len(*calls), m.st.Message)
	}
}

func TestXClosesThePaneOnlyAfterConfirmation(t *testing.T) {
	m, _, _, panes := termModel(t)
	press(m, "x")
	if !strings.Contains(m.st.Message, "nothing is running") {
		t.Fatalf("message %q", m.st.Message)
	}
	runAll(m, press(m, "p"))
	m.Update(tea.KeyMsg{Type: tea.KeyCtrlCloseBracket})
	press(m, "x")
	if m.st.Mode != ModeConfirm {
		t.Fatal("confirm first")
	}
	press(m, "n")
	if (*panes)[0].closed != 0 || m.pane == nil {
		t.Fatal("declining keeps the program")
	}
	press(m, "x")
	press(m, "y")
	if (*panes)[0].closed != 1 || m.pane != nil {
		t.Fatal("confirming ends the program")
	}
}

func TestResizingTheWindowResizesTheProgram(t *testing.T) {
	m, _, _, panes := termModel(t)
	runAll(m, press(m, "p"))
	m.Update(tea.WindowSizeMsg{Width: 90, Height: 30})
	if got := (*panes)[0].resizes; len(got) != 1 || got[0] != [2]int{90, 25} {
		t.Fatalf("resizes %v", got)
	}
}

func TestViewShowsTheScreenAndTheCursorOnlyWhileTyping(t *testing.T) {
	m, _, _, panes := termModel(t)
	runAll(m, press(m, "p"))
	p := (*panes)[0]
	p.lines = []string{"user@box:~$ ls", "a.txt  b.txt"}
	out := m.View()
	if !strings.Contains(out, "user@box:~$ ls") || !strings.Contains(out, "typing") {
		t.Fatalf("frame:\n%s", out)
	}
	if !p.cursorOn[len(p.cursorOn)-1] {
		t.Fatal("cursor while typing")
	}
	m.Update(tea.KeyMsg{Type: tea.KeyCtrlCloseBracket})
	m.View()
	if p.cursorOn[len(p.cursorOn)-1] {
		t.Fatal("no cursor once the board has the keyboard")
	}
}

func TestCloseEndsARunningProgramAndIsHarmlessWithout(t *testing.T) {
	m, _, _, panes := termModel(t)
	m.Close()
	runAll(m, press(m, "p"))
	m.Close()
	if (*panes)[0].closed != 1 {
		t.Fatal("the board's exit closes the pane")
	}
}

func TestTheTermTabHelpsBeforeAnythingRuns(t *testing.T) {
	m, _, _, _ := termModel(t)
	out := m.View()
	for _, want := range []string{"p  the parent CLI", "s  a shell in this project's container", "Ctrl-] returns"} {
		if !strings.Contains(out, want) {
			t.Errorf("help lacks %q:\n%s", want, out)
		}
	}
}
