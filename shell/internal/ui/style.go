package ui

import (
	"regexp"
	"strings"

	"github.com/charmbracelet/lipgloss"
)

// The board's palette is the design mockup's: xterm-256 values, so it reads the same on any 256-colour terminal.
const (
	colAccent = "111" // #87afff  running, active
	colAmber  = "179" // #d7af5f  needs attention
	colGreen  = "114" // #87d787  verified
	colRed    = "210" // #ff8787  failed
	colBorder = "60"  // #5f5f87  rules and separators
	colMuted  = "249" // #b2b2b2  secondary text
	colText   = "255" // #eeeeee
	colSelBg  = "235" // #262626  selected row
)

// Tone names come from the server (tui_rows). Colour only reinforces the label
// and glyph already on every row; nothing depends on it.
func tone(name string) lipgloss.Style {
	switch name {
	case "attention":
		return lipgloss.NewStyle().Foreground(lipgloss.Color(colAmber)) // the mockup's amber is never bold
	case "active":
		return lipgloss.NewStyle().Foreground(lipgloss.Color(colAccent))
	case "verified":
		return lipgloss.NewStyle().Foreground(lipgloss.Color(colGreen)).Bold(true)
	case "failed":
		return lipgloss.NewStyle().Foreground(lipgloss.Color(colRed)).Bold(true)
	}
	return lipgloss.NewStyle()
}

func muted(s string) string {
	return lipgloss.NewStyle().Foreground(lipgloss.Color(colMuted)).Render(s)
}

func border(s string) string {
	return lipgloss.NewStyle().Foreground(lipgloss.Color(colBorder)).Render(s)
}

// selectedRow draws the selected entry as the mockup does: a dark bar with bold #eeeeee text, both lines.
func selectedRow(marker, label, toneName, rest, second string, eh int) []string {
	bar := lipgloss.NewStyle().Background(lipgloss.Color(colSelBg)).Foreground(lipgloss.Color(colText)).Bold(true)
	lines := []string{bar.Render(marker + " " + label + "  " + rest)}
	if eh == 2 {
		lines = append(lines, bar.Render(second))
	}
	return lines
}

var (
	attentionCount = regexp.MustCompile(`! \d+ need attention`)
	runningCount   = regexp.MustCompile(`● \d+ running`)
	activeTab      = regexp.MustCompile(`\[[^\]]+\]`)
)

// Decorate colours the chrome of a rendered frame (header, tab bar, key footer). It never changes a line's
// width: the active tab's brackets become padding on a dark chip.
func Decorate(frame []string) []string {
	if len(frame) < 2 {
		return frame
	}
	out := append([]string(nil), frame...)
	amber := lipgloss.NewStyle().Foreground(lipgloss.Color(colAmber))
	blue := lipgloss.NewStyle().Foreground(lipgloss.Color(colAccent))
	head := attentionCount.ReplaceAllStringFunc(out[0], func(m string) string { return amber.Render(m) })
	head = runningCount.ReplaceAllStringFunc(head, func(m string) string { return blue.Render(m) })
	out[0] = muted(head)
	chip := lipgloss.NewStyle().Background(lipgloss.Color(colSelBg)).Foreground(lipgloss.Color(colText)).Bold(true)
	tabs := activeTab.ReplaceAllStringFunc(out[1], func(m string) string { return chip.Render(" " + m[1:len(m)-1] + " ") })
	out[1] = muted(tabs)
	out[len(out)-1] = muted(out[len(out)-1])
	return out
}

// ruleS is rule, with the border colour when the frame is styled.
func ruleS(st State, title string, w int) string {
	line := rule(title, w)
	if !st.Styled {
		return line
	}
	return colorRule(line)
}

// colorRule paints the dashes of a rule in the border colour and its title in the muted one.
func colorRule(line string) string {
	var b strings.Builder
	var run []rune
	var kind byte
	flush := func() {
		if len(run) == 0 {
			return
		}
		if kind == 'b' {
			b.WriteString(border(string(run)))
		} else {
			b.WriteString(muted(string(run)))
		}
		run = run[:0]
	}
	for _, r := range line {
		k := byte('t')
		if r == '─' || r == '┬' || r == '┴' || r == '┼' || r == '│' {
			k = 'b'
		}
		if k != kind {
			flush()
			kind = k
		}
		run = append(run, r)
	}
	flush()
	return b.String()
}

// bar is the vertical separator between columns.
func bar(st State) string {
	if st.Styled {
		return border("│")
	}
	return "│"
}

// sepTop is the junction where the list's rule meets the detail's.
func sepTop(st State) string {
	if st.Styled {
		return border("┬")
	}
	return "┬"
}

// The mockup's ground: #eeeeee text on #121212, so the board looks the same whatever the terminal's theme.
const (
	colGround = "233" // #121212
	baseSGR   = "\x1b[38;5;" + colText + ";48;5;" + colGround + "m"
)

var sgrSeq = regexp.MustCompile(`\x1b\[([0-9;]*)m`)

// Paint lays the mockup's text colour and background under every cell of a frame. Any escape that resets a colour
// to the terminal default (0, 39, 49, or an empty reset) is followed by the mockup's, so styled runs, the
// emulated pane's own output and the padding all sit on the same ground. It never changes a line's width.
func Paint(frame []string) []string {
	out := make([]string, len(frame))
	for i, line := range frame {
		out[i] = baseSGR + sgrSeq.ReplaceAllStringFunc(line, fixSGR) + "\x1b[0m"
	}
	return out
}

// fixSGR re-applies the ground colours a sequence leaves at the terminal's defaults.
func fixSGR(seq string) string {
	fgDefault, bgDefault := sgrDefaults(seq)
	if fgDefault {
		seq += "\x1b[38;5;" + colText + "m"
	}
	if bgDefault {
		seq += "\x1b[48;5;" + colGround + "m"
	}
	return seq
}

// sgrDefaults says whether an SGR sequence leaves the foreground or the background at the terminal's default.
func sgrDefaults(seq string) (fgDefault, bgDefault bool) {
	params := strings.Split(seq[2:len(seq)-1], ";")
	for i := 0; i < len(params); i++ {
		switch p := params[i]; {
		case p == "" || p == "0":
			fgDefault, bgDefault = true, true
		case p == "39":
			fgDefault = true
		case p == "49":
			bgDefault = true
		case p == "38" || p == "48":
			if p == "38" {
				fgDefault = false
			} else {
				bgDefault = false
			}
			if i+1 < len(params) && params[i+1] == "5" {
				i += 2
			} else if i+1 < len(params) && params[i+1] == "2" {
				i += 4
			}
		case len(p) == 2 && (p[0] == '3' || p[0] == '9') && p[1] >= '0' && p[1] <= '7':
			fgDefault = false
		case (len(p) == 2 && p[0] == '4' && p[1] >= '0' && p[1] <= '7') || (len(p) == 3 && p[:2] == "10"):
			bgDefault = false
		}
	}
	return fgDefault, bgDefault
}

// Tinted panels, as codex and grok draw theirs: the side columns and the dock sit on a slightly lighter panel, the
// conversation on the darkest ground, and the ask box, its menus and the selected row on a raised surface.
const (
	colPanel  = "234" // #1c1c1c
	colRaised = colSelBg
)

// tint lays a background under a styled segment: every escape that drops the background to the default puts the
// tint back. It ends on the default background, which Paint turns into the ground. It never changes a width.
func tint(s, bg string) string {
	on := "\x1b[48;5;" + bg + "m"
	return on + sgrSeq.ReplaceAllStringFunc(s, func(seq string) string {
		if _, bgDefault := sgrDefaults(seq); bgDefault {
			return seq + on
		}
		return seq
	}) + "\x1b[49m"
}
