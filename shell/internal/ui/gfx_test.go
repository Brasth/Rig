package ui

import (
	"bytes"
	"encoding/base64"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"

	"github.com/charmbracelet/lipgloss"
	"github.com/charmbracelet/x/ansi"
)

func TestALogoMarkIsThreeCellsAndSurvivesFitting(t *testing.T) {
	mark := logoMark("claude")
	if w := lipgloss.Width(mark); w != 3 {
		t.Fatalf("a tile is three cells: %d", w)
	}
	if !strings.Contains(mark, "\x1b[38;5;231m") || !strings.Contains(mark, "\U0010EEEE̅̎") {
		t.Fatalf("the image id in the foreground and the placeholder cells: %q", mark)
	}
	line := fit("ab "+mark+" claude", 20)
	if lipgloss.Width(line) != 20 || !strings.Contains(line, placeholderCells) {
		t.Fatalf("fitting keeps the cells whole: %q", line)
	}
	if cut := ansi.Truncate("ab "+mark+" claude", 4, ""); strings.Contains(cut, "̍") {
		t.Fatalf("a cut keeps whole cells: %q", cut)
	}
	if logoMark("omp") != "" {
		t.Fatal("an agent without a logo keeps the letter tile")
	}
}

func TestLogosAreUsedOnGhosttyAndKittyOnly(t *testing.T) {
	cases := []struct {
		env  map[string]string
		want bool
	}{
		{map[string]string{"TERM_PROGRAM": "ghostty"}, true},
		{map[string]string{"TERM": "tmux-256color", "TERM_PROGRAM": "tmux", "GHOSTTY_RESOURCES_DIR": "/Applications/Ghostty.app"}, true},
		{map[string]string{"TERM": "xterm-kitty"}, true},
		{map[string]string{"TERM_PROGRAM": "Apple_Terminal"}, false},
		{map[string]string{"TERM_PROGRAM": "ghostty", "RIG_LOGOS": "0"}, false},
		{map[string]string{"RIG_LOGOS": "1"}, true},
	}
	for _, c := range cases {
		if got := LogosSupported(func(k string) string { return c.env[k] }); got != c.want {
			t.Errorf("%v: %v", c.env, got)
		}
	}
}

func TestTheTilesAreSentAsPNGsWithAVirtualPlacement(t *testing.T) {
	out := string(logoTransmit(false))
	for agent, id := range logoIDs {
		head := "\x1b_Ga=T,U=1,f=100,t=d,i=" + strconv.Itoa(id) + ",c=3,r=1,q=2,m="
		at := strings.Index(out, head)
		if at < 0 {
			t.Fatalf("%s is sent: %q", agent, out[:80])
		}
		var data strings.Builder
		rest := out[at:]
		for {
			semi := strings.IndexByte(rest, ';')
			end := strings.Index(rest, "\x1b\\")
			data.WriteString(rest[semi+1 : end])
			if strings.Contains(rest[:semi], "m=0") {
				break
			}
			rest = rest[end+2:]
		}
		png, err := base64.StdEncoding.DecodeString(data.String())
		if err != nil || !bytes.HasPrefix(png, []byte("\x89PNG")) {
			t.Fatalf("%s: the chunks join into its PNG: %v", agent, err)
		}
	}
	wrapped := string(logoTransmit(true))
	if !strings.HasPrefix(wrapped, "\x1bPtmux;\x1b\x1b_G") || strings.Count(wrapped, "\x1bPtmux;") != strings.Count(out, "\x1b_G") {
		t.Fatal("inside tmux each command passes through")
	}
}

func TestTheWriterSendsTheTilesOnTheAlternateScreenAndWhenAsked(t *testing.T) {
	path := filepath.Join(t.TempDir(), "out")
	f, err := os.Create(path)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	w := NewLogoWriter(f, false)
	t.Cleanup(func() { logoOut = nil })
	read := func() string { b, _ := os.ReadFile(path); return string(b) }
	w.Write([]byte("hello"))
	if strings.Contains(read(), "\x1b_G") {
		t.Fatal("not before the alternate screen")
	}
	w.Write([]byte("\x1b[?1049h\x1b[2J"))
	if strings.Count(read(), "a=T,U=1") != len(logoIDs) {
		t.Fatal("sent once on entering the alternate screen")
	}
	w.Write([]byte("frame"))
	ResendLogos()
	w.Write([]byte("frame after a resize"))
	if strings.Count(read(), "a=T,U=1") != 2*len(logoIDs) {
		t.Fatal("sent again when asked")
	}
}

func TestTheAppDrawsLogoTilesWhenTheTerminalCan(t *testing.T) {
	m, src := appModel(t)
	withConversation(t, m, src)
	m.SetLogos(true)
	st := m.st
	st.Styled = true
	for i, line := range AppFrame(m.view, st, 120, 36) {
		if w := lipgloss.Width(line); w != 120 {
			t.Fatalf("line %d is %d cells", i, w)
		}
	}
	if !strings.Contains(strings.Join(AppFrame(m.view, st, 120, 36), "\n"), "\x1b[38;5;231m"+placeholderCells) {
		t.Fatal("claude's tile is drawn")
	}
}
