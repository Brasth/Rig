package ui

import (
	"bytes"
	"embed"
	"encoding/base64"
	"fmt"
	"io"
	"os"
	"strings"
	"sync"
)

// The design shows each agent as its logo in a tile ringed in its colour, and says a terminal draws the logos with an
// image protocol and falls back to the coloured letter tile. This draws them with the kitty graphics protocol's
// Unicode placeholders, which Ghostty and kitty support: each tile image is sent to the terminal once, and a mark is
// then three ordinary text cells (U+10EEEE with the row and column diacritics, the image id in the foreground colour),
// so it lays out, scrolls, redraws and passes through tmux as text does.

//go:embed logos/*.png
var logoFiles embed.FS

// logoIDs are the image ids the tiles are sent under: one per agent, kept below 256 so a 256-colour foreground can
// carry them, and high in that range to stay clear of ids other programs in the same window pick first.
var logoIDs = map[string]int{"claude": 231, "codex": 232, "opencode": 233, "pi": 234, "cursor": 235, "grok": 236}

// placeholderCells are a tile's three cells: row 0, columns 0, 1 and 2.
const placeholderCells = "\U0010EEEE̅̅" + "\U0010EEEE̅̍" + "\U0010EEEE̅̎"

// logoMark is the agent's tile as placeholder cells, or "" when the agent has no logo.
func logoMark(agent string) string {
	id, ok := logoIDs[agent]
	if !ok {
		return ""
	}
	return fmt.Sprintf("\x1b[38;5;%dm", id) + placeholderCells + "\x1b[39m"
}

// LogosSupported reports whether this terminal draws images with kitty's Unicode placeholders. RIG_LOGOS=1 or 0
// forces it; otherwise Ghostty and kitty are recognised, also from inside tmux, whose panes keep the variables the
// terminal set when tmux started.
func LogosSupported(env func(string) string) bool {
	switch env("RIG_LOGOS") {
	case "1":
		return true
	case "0":
		return false
	}
	return env("TERM_PROGRAM") == "ghostty" || env("TERM") == "xterm-ghostty" || env("GHOSTTY_RESOURCES_DIR") != "" ||
		env("TERM") == "xterm-kitty" || env("KITTY_WINDOW_ID") != ""
}

// logoTransmit is the escape sequences that send every tile and give each a virtual placement three cells wide and
// one high for the placeholders to show; inside tmux each is wrapped to pass through to the terminal.
func logoTransmit(tmux bool) []byte {
	var out bytes.Buffer
	for agent, id := range logoIDs {
		data, err := logoFiles.ReadFile("logos/" + agent + ".png")
		if err != nil {
			continue
		}
		enc := base64.StdEncoding.EncodeToString(data)
		for i := 0; i < len(enc); i += 4096 {
			chunk := enc[i:min(len(enc), i+4096)]
			more := 0
			if i+4096 < len(enc) {
				more = 1
			}
			keys := fmt.Sprintf("m=%d", more)
			if i == 0 {
				keys = fmt.Sprintf("a=T,U=1,f=100,t=d,i=%d,c=3,r=1,q=2,m=%d", id, more)
			}
			apc := "\x1b_G" + keys + ";" + chunk + "\x1b\\"
			if tmux {
				apc = "\x1bPtmux;" + strings.ReplaceAll(apc, "\x1b", "\x1b\x1b") + "\x1b\\"
			}
			out.WriteString(apc)
		}
	}
	return out.Bytes()
}

// LogoWriter is the board's output: the terminal, with the tiles sent after each switch to the alternate screen (the
// terminal keeps that screen's images apart, and drops them when it is left) and again when asked, after a resize
// that may be a tmux re-attach to another terminal.
type LogoWriter struct {
	*os.File
	payload []byte
	mu      sync.Mutex
	pending bool
}

var logoOut *LogoWriter

// NewLogoWriter wraps the terminal f; it is also the writer ResendLogos asks.
func NewLogoWriter(f *os.File, tmux bool) *LogoWriter {
	logoOut = &LogoWriter{File: f, payload: logoTransmit(tmux)}
	return logoOut
}

func (w *LogoWriter) Write(p []byte) (int, error) {
	n, err := w.File.Write(p)
	if err != nil {
		return n, err
	}
	w.mu.Lock()
	send := w.pending || bytes.Contains(p, []byte("\x1b[?1049h"))
	w.pending = false
	w.mu.Unlock()
	if send {
		_, err = w.File.Write(w.payload)
	}
	return n, err
}

// ResendLogos sends the tiles again with the next frame.
func ResendLogos() {
	if logoOut == nil {
		return
	}
	logoOut.mu.Lock()
	logoOut.pending = true
	logoOut.mu.Unlock()
}

var _ io.Writer = (*LogoWriter)(nil)
