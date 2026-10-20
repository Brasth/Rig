package term

import (
	"strconv"
	"strings"
	"unicode"

	"github.com/hinshun/vt10x"
)

// Attribute bits as vt10x stores them in Glyph.Mode; the package keeps the constants unexported.
const (
	attrReverse   = 1 << 0
	attrUnderline = 1 << 1
	attrBold      = 1 << 2
	attrItalic    = 1 << 4
	attrBlink     = 1 << 5
)

type style struct {
	fg, bg vt10x.Color
	mode   int16
}

func (s style) plain() bool {
	return s.fg == vt10x.DefaultFG && s.bg == vt10x.DefaultBG && s.mode&(attrUnderline|attrBold|attrItalic|attrBlink) == 0
}

// sgr is the escape that selects s from a reset state.
func (s style) sgr() string {
	parts := []string{"0"}
	if s.mode&attrBold != 0 {
		parts = append(parts, "1")
	}
	if s.mode&attrItalic != 0 {
		parts = append(parts, "3")
	}
	if s.mode&attrUnderline != 0 {
		parts = append(parts, "4")
	}
	if s.mode&attrBlink != 0 {
		parts = append(parts, "5")
	}
	parts = append(parts, colorCode(s.fg, true)...)
	parts = append(parts, colorCode(s.bg, false)...)
	return "\x1b[" + strings.Join(parts, ";") + "m"
}

func colorCode(c vt10x.Color, foreground bool) []string {
	if c >= 1<<24 { // DefaultFG, DefaultBG, DefaultCursor
		return nil
	}
	base := 30
	if !foreground {
		base = 40
	}
	switch {
	case c < 8:
		return []string{strconv.Itoa(base + int(c))}
	case c < 16:
		return []string{strconv.Itoa(base + 60 + int(c) - 8)}
	case c < 256:
		return []string{strconv.Itoa(base + 8), "5", strconv.Itoa(int(c))}
	}
	return []string{strconv.Itoa(base + 8), "2", strconv.Itoa(int(c >> 16 & 0xff)), strconv.Itoa(int(c >> 8 & 0xff)), strconv.Itoa(int(c & 0xff))}
}

func cellRune(g vt10x.Glyph) rune {
	if g.Char == 0 || (g.Char < 0x20) || g.Char == 0x7f || (g.Char >= 0x80 && g.Char < 0xa0) || !unicode.IsPrint(g.Char) && g.Char != ' ' {
		return ' '
	}
	return g.Char
}

// renderRow draws one screen row. Styled output groups cells of equal style into runs.
func renderRow(v vt10x.View, y, cols int, styled bool, cursorX int) string {
	var b strings.Builder
	var last style
	have := false
	for x := 0; x < cols; x++ {
		g := v.Cell(x, y)
		ch := cellRune(g)
		if !styled {
			b.WriteRune(ch)
			continue
		}
		s := style{fg: g.FG, bg: g.BG, mode: g.Mode}
		reverse := x == cursorX
		if reverse {
			b.WriteString(s.sgr())
			b.WriteString("\x1b[7m")
			b.WriteRune(ch)
			b.WriteString("\x1b[0m")
			have = false
			continue
		}
		if !have || s != last {
			if s.plain() {
				if have {
					b.WriteString("\x1b[0m")
				}
				have = false
				last = s
				b.WriteRune(ch)
				continue
			}
			b.WriteString(s.sgr())
			last, have = s, true
		}
		b.WriteRune(ch)
	}
	if styled && have {
		b.WriteString("\x1b[0m")
	}
	return b.String()
}
