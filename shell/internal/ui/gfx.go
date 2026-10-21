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
const placeholderCells = "\U0010EEEE\u0305\u0305" + "\U0010EEEE\u0305\u030D" + "\U0010EEEE\u0305\u030E"

// diacritics are kitty's row and column numbers for image placeholders (its gen/rowcolumn-diacritics.txt, in order).
var diacritics = []rune{
	0x0305, 0x030D, 0x030E, 0x0310, 0x0312, 0x033D, 0x033E, 0x033F, 0x0346, 0x034A, 0x034B, 0x034C, 0x0350,
	0x0351, 0x0352, 0x0357, 0x035B, 0x0363, 0x0364, 0x0365, 0x0366, 0x0367, 0x0368, 0x0369, 0x036A, 0x036B,
	0x036C, 0x036D, 0x036E, 0x036F, 0x0483, 0x0484, 0x0485, 0x0486, 0x0487, 0x0592, 0x0593, 0x0594, 0x0595,
	0x0597, 0x0598, 0x0599, 0x059C, 0x059D, 0x059E, 0x059F, 0x05A0, 0x05A1, 0x05A8, 0x05A9, 0x05AB, 0x05AC,
	0x05AF, 0x05C4, 0x0610, 0x0611, 0x0612, 0x0613, 0x0614, 0x0615, 0x0616, 0x0617, 0x0657, 0x0658, 0x0659,
	0x065A, 0x065B, 0x065D, 0x065E, 0x06D6, 0x06D7, 0x06D8, 0x06D9, 0x06DA, 0x06DB, 0x06DC, 0x06DF, 0x06E0,
	0x06E1, 0x06E2, 0x06E4, 0x06E7, 0x06E8, 0x06EB, 0x06EC, 0x0730, 0x0732, 0x0733, 0x0735, 0x0736, 0x073A,
	0x073D, 0x073F, 0x0740, 0x0741, 0x0743, 0x0745, 0x0747, 0x0749, 0x074A, 0x07EB, 0x07EC, 0x07ED, 0x07EE,
	0x07EF, 0x07F0, 0x07F1, 0x07F3, 0x0816, 0x0817, 0x0818, 0x0819, 0x081B, 0x081C, 0x081D, 0x081E, 0x081F,
	0x0820, 0x0821, 0x0822, 0x0823, 0x0825, 0x0826, 0x0827, 0x0829, 0x082A, 0x082B, 0x082C, 0x082D, 0x0951,
	0x0953, 0x0954, 0x0F82, 0x0F83, 0x0F86, 0x0F87, 0x135D, 0x135E, 0x135F, 0x17DD, 0x193A, 0x1A17, 0x1A75,
	0x1A76, 0x1A77, 0x1A78, 0x1A79, 0x1A7A, 0x1A7B, 0x1A7C, 0x1B6B, 0x1B6D, 0x1B6E, 0x1B6F, 0x1B70, 0x1B71,
	0x1B72, 0x1B73, 0x1CD0, 0x1CD1, 0x1CD2, 0x1CDA, 0x1CDB, 0x1CE0, 0x1DC0, 0x1DC1, 0x1DC3, 0x1DC4, 0x1DC5,
	0x1DC6, 0x1DC7, 0x1DC8, 0x1DC9, 0x1DCB, 0x1DCC, 0x1DD1, 0x1DD2, 0x1DD3, 0x1DD4, 0x1DD5, 0x1DD6, 0x1DD7,
	0x1DD8, 0x1DD9, 0x1DDA, 0x1DDB, 0x1DDC, 0x1DDD, 0x1DDE, 0x1DDF, 0x1DE0, 0x1DE1, 0x1DE2, 0x1DE3, 0x1DE4,
	0x1DE5, 0x1DE6, 0x1DFE, 0x20D0, 0x20D1, 0x20D4, 0x20D5, 0x20D6, 0x20D7, 0x20DB, 0x20DC, 0x20E1, 0x20E7,
	0x20E9, 0x20F0, 0x2CEF, 0x2CF0, 0x2CF1, 0x2DE0, 0x2DE1, 0x2DE2, 0x2DE3, 0x2DE4, 0x2DE5, 0x2DE6, 0x2DE7,
	0x2DE8, 0x2DE9, 0x2DEA, 0x2DEB, 0x2DEC, 0x2DED, 0x2DEE, 0x2DEF, 0x2DF0, 0x2DF1, 0x2DF2, 0x2DF3, 0x2DF4,
	0x2DF5, 0x2DF6, 0x2DF7, 0x2DF8, 0x2DF9, 0x2DFA, 0x2DFB, 0x2DFC, 0x2DFD, 0x2DFE, 0x2DFF, 0xA66F, 0xA67C,
	0xA67D, 0xA6F0, 0xA6F1, 0xA8E0, 0xA8E1, 0xA8E2, 0xA8E3, 0xA8E4, 0xA8E5, 0xA8E6, 0xA8E7, 0xA8E8, 0xA8E9,
	0xA8EA, 0xA8EB, 0xA8EC, 0xA8ED, 0xA8EE, 0xA8EF, 0xA8F0, 0xA8F1, 0xAAB0, 0xAAB2, 0xAAB3, 0xAAB7, 0xAAB8,
	0xAABE, 0xAABF, 0xAAC1, 0xFE20, 0xFE21, 0xFE22, 0xFE23, 0xFE24, 0xFE25, 0xFE26, 0x10A0F, 0x10A38, 0x1D185,
	0x1D186, 0x1D187, 0x1D188, 0x1D189, 0x1D1AA, 0x1D1AB, 0x1D1AC, 0x1D1AD, 0x1D242, 0x1D243, 0x1D244,
}

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
// markIDs are the rig mark's images, one per theme (its stem takes the theme's text colour), shown by the splash.
var markIDs = map[string]int{"rig": 241, "light": 242, "dark": 243, "material": 244, "dracula": 245}

// markCols and markRows are the cells the splash's mark fills: the mark is half again as wide as it is tall.
const markCols, markRows = 12, 4

// sentImage is one image the board sends, and the cells it fills.
type sentImage struct {
	file           string
	id, cols, rows int
}

// sentImages is every image the board sends: the agents' tiles and the rig marks.
func sentImages() []sentImage {
	var out []sentImage
	for agent, id := range logoIDs {
		out = append(out, sentImage{agent, id, 3, 1})
	}
	for theme, id := range markIDs {
		out = append(out, sentImage{"mark-" + theme, id, markCols, markRows})
	}
	return out
}

// markRowsFor is the splash's mark for the theme as placeholder lines; nil when the theme has none.
func markRowsFor(theme string) []string {
	id, ok := markIDs[theme]
	if !ok {
		return nil
	}
	return imageRows(id, markCols, markRows)
}

func logoTransmit(tmux bool) []byte {
	var out bytes.Buffer
	for _, img := range sentImages() {
		id := img.id
		data, err := logoFiles.ReadFile("logos/" + img.file + ".png")
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
				keys = fmt.Sprintf("a=T,U=1,f=100,t=d,i=%d,c=%d,r=%d,q=2,m=%d", id, img.cols, img.rows, more)
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
	tmux    bool
	mu      sync.Mutex
	pending bool
	extra   [][]byte // images to send with the next frame (a previewed file)
}

var logoOut *LogoWriter

// NewLogoWriter wraps the terminal f; it is also the writer ResendLogos asks.
func NewLogoWriter(f *os.File, tmux bool) *LogoWriter {
	logoOut = &LogoWriter{File: f, payload: logoTransmit(tmux), tmux: tmux}
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
	extra := w.extra
	w.extra = nil
	w.mu.Unlock()
	if send {
		_, err = w.File.Write(w.payload)
	}
	for _, e := range extra {
		if _, err = w.File.Write(e); err != nil {
			break
		}
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

// previewImageID is the image id a previewed picture is sent under; each new one replaces the last.
const previewImageID = 240

// SendImage sends a PNG to the terminal with the next frame, to show in cols × rows cells; false where it cannot.
func SendImage(png []byte, cols, rows int) bool {
	if logoOut == nil {
		return false
	}
	payload := transmitPNG(png, previewImageID, cols, rows, logoOut.tmux)
	logoOut.mu.Lock()
	logoOut.extra = append(logoOut.extra, payload)
	logoOut.mu.Unlock()
	return true
}

func transmitPNG(png []byte, id, cols, rows int, tmux bool) []byte {
	var out bytes.Buffer
	enc := base64.StdEncoding.EncodeToString(png)
	for i := 0; i < len(enc); i += 4096 {
		chunk := enc[i:min(len(enc), i+4096)]
		more := 0
		if i+4096 < len(enc) {
			more = 1
		}
		keys := fmt.Sprintf("m=%d", more)
		if i == 0 {
			keys = fmt.Sprintf("a=T,U=1,f=100,t=d,i=%d,c=%d,r=%d,q=2,m=%d", id, cols, rows, more)
		}
		apc := "\x1b_G" + keys + ";" + chunk + "\x1b\\"
		if tmux {
			apc = "\x1bPtmux;" + strings.ReplaceAll(apc, "\x1b", "\x1b\x1b") + "\x1b\\"
		}
		out.WriteString(apc)
	}
	return out.Bytes()
}

// imageRows are the placeholder lines that show image id in cols × rows cells, each cell numbered by row and column.
func imageRows(id, cols, rows int) []string {
	cols, rows = min(cols, len(diacritics)), min(rows, len(diacritics))
	out := make([]string, rows)
	for r := 0; r < rows; r++ {
		var b strings.Builder
		fmt.Fprintf(&b, "\x1b[38;5;%dm", id)
		for c := 0; c < cols; c++ {
			b.WriteRune(0x10EEEE)
			b.WriteRune(diacritics[r])
			b.WriteRune(diacritics[c])
		}
		b.WriteString("\x1b[39m")
		out[r] = b.String()
	}
	return out
}
