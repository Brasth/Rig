package term

import (
	"os"
	"strings"
	"syscall"
	"testing"
	"time"

	tea "github.com/charmbracelet/bubbletea"
)

func start(t *testing.T, script string, cols, rows int) *Pane {
	t.Helper()
	p, err := Start([]string{"sh", "-c", script}, t.TempDir(), nil, cols, rows)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(p.Close)
	return p
}

// waitFor polls the screen until it contains want (the emulator is fed by a goroutine).
func waitFor(t *testing.T, p *Pane, want string) string {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		if text := p.Text(); strings.Contains(text, want) {
			return text
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatalf("screen never showed %q; it shows:\n%s", want, p.Text())
	return ""
}

func TestOutputAndInputRoundTrip(t *testing.T) {
	p := start(t, `echo ready; read x; echo "got:$x"`, 40, 6)
	waitFor(t, p, "ready")
	p.Write([]byte("hello\r"))
	waitFor(t, p, "got:hello")
	if !p.Wait(5 * time.Second) {
		t.Fatal("the program should have ended")
	}
	exited, err := p.Exited()
	if !exited || err != nil {
		t.Fatalf("exited %v err %v", exited, err)
	}
}

func TestExitStatusIsReported(t *testing.T) {
	p := start(t, `exit 7`, 40, 6)
	if !p.Wait(5 * time.Second) {
		t.Fatal("no exit")
	}
	exited, err := p.Exited()
	if !exited || err == nil || !strings.Contains(err.Error(), "exit status 7") {
		t.Fatalf("exited %v err %v", exited, err)
	}
}

func TestStartRefusesNothingToRun(t *testing.T) {
	if _, err := Start(nil, "", nil, 80, 24); err == nil {
		t.Fatal("no argv")
	}
	if _, err := Start([]string{"/no/such/program-anywhere"}, "", nil, 80, 24); err == nil {
		t.Fatal("a missing program is an error, not a dead pane")
	}
}

func TestTheProgramSeesTheRealSizeAndResizes(t *testing.T) {
	p := start(t, `stty size; read x; stty size`, 50, 12)
	waitFor(t, p, "12 50")
	p.Resize(70, 20)
	p.Write([]byte("\r"))
	text := waitFor(t, p, "20 70")
	if c, r := p.Size(); c != 70 || r != 20 {
		t.Fatalf("size %dx%d", c, r)
	}
	if lines := p.Lines(false, false); len(lines) != 20 || len([]rune(lines[0])) != 70 {
		t.Fatalf("emulated screen is %d rows of %d", len(lines), len([]rune(lines[0])))
	}
	_ = text
}

func TestColoursAreRenderedAndPlainTextStaysPlain(t *testing.T) {
	p := start(t, `printf '\033[31mred\033[0m plain \033[1;42mbold\033[0m \033[38;5;200mx256\033[0m \033[38;2;1;2;3mtrue\033[0m\n'; sleep 5`, 60, 4)
	waitFor(t, p, "red plain bold x256 true")
	styled := p.Lines(true, false)[0]
	for _, want := range []string{"\x1b[0;31mred", "\x1b[0;1;42mbold", "\x1b[0;38;5;200mx256", "\x1b[0;38;2;1;2;3mtrue"} {
		if !strings.Contains(styled, want) {
			t.Errorf("styled row lacks %q: %q", want, styled)
		}
	}
	if strings.Contains(p.Lines(false, false)[0], "\x1b") {
		t.Error("unstyled lines carry no escapes")
	}
}

func TestTheCursorIsDrawnOnlyWhenAsked(t *testing.T) {
	p := start(t, `printf 'ab'; sleep 5`, 10, 3)
	waitFor(t, p, "ab")
	if strings.Contains(p.Lines(true, false)[0], "\x1b[7m") {
		t.Error("no cursor when unfocused")
	}
	if !strings.Contains(p.Lines(true, true)[0], "\x1b[7m") {
		t.Error("cursor when focused")
	}
	p2 := start(t, `printf 'ab\033[?25l'; sleep 5`, 10, 3)
	waitFor(t, p2, "ab")
	time.Sleep(50 * time.Millisecond)
	if strings.Contains(p2.Lines(true, true)[0], "\x1b[7m") {
		t.Error("a program that hides the cursor gets none")
	}
}

func TestAnAlternateScreenProgramDrawsWhereItSays(t *testing.T) {
	p := start(t, `printf '\033[?1049h\033[2J\033[3;5HFULLSCREEN'; sleep 5`, 30, 6)
	waitFor(t, p, "FULLSCREEN")
	lines := p.Lines(false, false)
	if !strings.HasPrefix(lines[2], "    FULLSCREEN") {
		t.Fatalf("row 3 is %q", lines[2])
	}
}

func TestUTF8AndControlCharactersInOutput(t *testing.T) {
	p := start(t, `printf 'caf\303\251 \342\224\200 ok\007\033]0;title\007\n'; sleep 5`, 40, 3)
	waitFor(t, p, "café ─ ok")
	if got := p.Title(); got != "title" {
		t.Errorf("title %q", got)
	}
	if strings.ContainsRune(p.Text(), 7) {
		t.Error("the bell is not on screen")
	}
}

func TestAProgramThatAsksWhereTheCursorIsGetsAnAnswer(t *testing.T) {
	// ESC [ 6 n asks for the cursor position; the emulator answers ESC [ row ; col R through the pty.
	p := start(t, `stty raw -echo; printf '\033[6n'; dd bs=1 count=6 2>/dev/null | od -c | head -1; sleep 1`, 60, 6)
	waitFor(t, p, "033   [   1   ;   1   R")
}

func TestHeavyOutputKeepsUp(t *testing.T) {
	p := start(t, `i=0; while [ $i -lt 3000 ]; do echo "line $i of a long run of output"; i=$((i+1)); done; echo FINISHED-RUN`, 80, 10)
	waitFor(t, p, "FINISHED-RUN")
}

func TestCloseEndsTheWholeProcessGroup(t *testing.T) {
	p := start(t, `sleep 300 & echo $! > pid.txt; echo started; wait`, 40, 4)
	waitFor(t, p, "started")
	var child []byte
	for i := 0; i < 100 && len(child) == 0; i++ {
		child, _ = os.ReadFile(p.cmd.Dir + "/pid.txt")
		time.Sleep(20 * time.Millisecond)
	}
	if len(child) == 0 {
		t.Fatal("the background child never started")
	}
	pid := 0
	for _, c := range strings.TrimSpace(string(child)) {
		pid = pid*10 + int(c-'0')
	}
	p.Close()
	p.Close() // twice is fine
	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		if err := syscall.Kill(pid, 0); err != nil {
			return
		}
		if b, _ := os.ReadFile("/proc/" + string(child[:len(child)-1]) + "/stat"); strings.Contains(string(b), ") Z") {
			return
		}
		time.Sleep(20 * time.Millisecond)
	}
	t.Fatal("the child survived Close")
}

func TestWritesAfterExitAreIgnored(t *testing.T) {
	p := start(t, `true`, 20, 3)
	p.Wait(5 * time.Second)
	p.Write([]byte("anything"))
	p.Resize(30, 5) // no panic, no resize of a dead pty
}

func TestUpdatesCoalesceAndAnnounceExit(t *testing.T) {
	p := start(t, `echo one; echo two`, 20, 3)
	seen := 0
	deadline := time.After(5 * time.Second)
	for {
		select {
		case <-p.Updates():
			seen++
			if ok, _ := p.Exited(); ok {
				if seen < 1 {
					t.Fatal("no updates")
				}
				return
			}
		case <-deadline:
			t.Fatal("exit was never announced")
		}
	}
}

func TestSizesAreClamped(t *testing.T) {
	p := start(t, `sleep 5`, -5, 0)
	if c, r := p.Size(); c < 2 || r < 2 {
		t.Fatalf("size %dx%d", c, r)
	}
	p.Resize(100000, 100000)
	if c, r := p.Size(); c > 500 || r > 200 {
		t.Fatalf("size %dx%d", c, r)
	}
}

func TestKeyTranslation(t *testing.T) {
	r := func(s string) tea.KeyMsg { return tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune(s)} }
	cases := []struct {
		name string
		key  tea.KeyMsg
		app  bool
		want string
	}{
		{"letters", r("hi"), false, "hi"},
		{"unicode", r("é"), false, "é"},
		{"enter", tea.KeyMsg{Type: tea.KeyEnter}, false, "\r"},
		{"tab", tea.KeyMsg{Type: tea.KeyTab}, false, "\t"},
		{"shift-tab", tea.KeyMsg{Type: tea.KeyShiftTab}, false, "\x1b[Z"},
		{"backspace", tea.KeyMsg{Type: tea.KeyBackspace}, false, "\x7f"},
		{"esc", tea.KeyMsg{Type: tea.KeyEsc}, false, "\x1b"},
		{"space", tea.KeyMsg{Type: tea.KeySpace, Runes: []rune(" ")}, false, " "},
		{"up", tea.KeyMsg{Type: tea.KeyUp}, false, "\x1b[A"},
		{"up app", tea.KeyMsg{Type: tea.KeyUp}, true, "\x1bOA"},
		{"left", tea.KeyMsg{Type: tea.KeyLeft}, false, "\x1b[D"},
		{"home", tea.KeyMsg{Type: tea.KeyHome}, false, "\x1b[H"},
		{"end app", tea.KeyMsg{Type: tea.KeyEnd}, true, "\x1bOF"},
		{"pgup", tea.KeyMsg{Type: tea.KeyPgUp}, false, "\x1b[5~"},
		{"pgdn", tea.KeyMsg{Type: tea.KeyPgDown}, false, "\x1b[6~"},
		{"delete", tea.KeyMsg{Type: tea.KeyDelete}, false, "\x1b[3~"},
		{"insert", tea.KeyMsg{Type: tea.KeyInsert}, false, "\x1b[2~"},
		{"ctrl-a", tea.KeyMsg{Type: tea.KeyCtrlA}, false, "\x01"},
		{"ctrl-c", tea.KeyMsg{Type: tea.KeyCtrlC}, false, "\x03"},
		{"ctrl-d", tea.KeyMsg{Type: tea.KeyCtrlD}, false, "\x04"},
		{"ctrl-z", tea.KeyMsg{Type: tea.KeyCtrlZ}, false, "\x1a"},
		{"ctrl-backslash", tea.KeyMsg{Type: tea.KeyCtrlBackslash}, false, "\x1c"},
		{"f1", tea.KeyMsg{Type: tea.KeyF1}, false, "\x1bOP"},
		{"f5", tea.KeyMsg{Type: tea.KeyF5}, false, "\x1b[15~"},
		{"f12", tea.KeyMsg{Type: tea.KeyF12}, false, "\x1b[24~"},
		{"ctrl-right", tea.KeyMsg{Type: tea.KeyCtrlRight}, false, "\x1b[1;5C"},
		{"shift-up", tea.KeyMsg{Type: tea.KeyShiftUp}, false, "\x1b[1;2A"},
		{"alt-x", tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("x"), Alt: true}, false, "\x1bx"},
		{"alt-up", tea.KeyMsg{Type: tea.KeyUp, Alt: true}, false, "\x1b\x1b[A"},
		{"paste keeps text and turns newlines into returns", tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("a\nb\r\nc"), Paste: true}, false, "a\rb\rc"},
	}
	for _, c := range cases {
		if got := string(KeyBytes(c.key, c.app)); got != c.want {
			t.Errorf("%s: got %q want %q", c.name, got, c.want)
		}
	}
	if KeyBytes(tea.KeyMsg{Type: tea.KeyF20}, false) != nil || KeyBytes(tea.KeyMsg{Type: tea.KeyType(-999)}, false) != nil {
		t.Error("unknown keys have no bytes")
	}
}

func TestApplicationCursorModeFollowsTheProgram(t *testing.T) {
	p := start(t, `printf '\033[?1h'; echo set; sleep 5`, 20, 3)
	waitFor(t, p, "set")
	time.Sleep(50 * time.Millisecond)
	if !p.AppCursor() {
		t.Fatal("application cursor keys were requested")
	}
}

func TestRowsThatScrollOffTheTopAreKept(t *testing.T) {
	p, err := Start([]string{"sh", "-c", "seq 1 40"}, "", nil, 20, 5)
	if err != nil {
		t.Skip("no pty here:", err)
	}
	defer p.Close()
	if !p.Wait(5 * time.Second) {
		t.Fatal("the program did not finish")
	}
	if n := p.Scrollback(); n < 30 {
		t.Fatalf("the rows that left the top are kept: %d", n)
	}
	up := p.LinesFrom(p.Scrollback(), false, false)
	if len(up) != 5 || strings.TrimSpace(up[0]) != "1" {
		t.Fatalf("scrolled all the way up, the first rows: %q", up)
	}
	live := p.LinesFrom(0, false, false)
	if strings.TrimSpace(live[0]) == "1" {
		t.Fatalf("not scrolled: the screen's own top: %q", live)
	}
}
