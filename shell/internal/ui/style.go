package ui

import (
	"fmt"
	"regexp"
	"strconv"
	"strings"

	"github.com/charmbracelet/lipgloss"
	"github.com/muesli/termenv"
)

// The board's palette. The default, "rig", is the ADE mockup's: xterm-256 values, so it reads the same on any
// 256-colour terminal. The others are the design's themes (Settings › General): hex values, drawn in true colour where
// the terminal has it and as the nearest 256 colour where it does not. ApplyTheme switches between them.
var (
	colAccent = "111" // #87afff  running, active
	colAmber  = "179" // #d7af5f  needs attention
	colGreen  = "114" // #87d787  verified
	colRed    = "210" // #ff8787  failed
	colBorder = "60"  // #5f5f87  rules and separators
	colMuted  = "249" // #b2b2b2  secondary text
	colText   = "255" // #eeeeee
	colSelBg  = "235" // #262626  selected row
	colGround = "233" // #121212
)

// Theme is one palette: ground and text, secondary text, rules, and the four state colours.
type Theme struct {
	Ground, Text, Muted, Border, SelBg, Accent, Amber, Green, Red string
}

// Themes are the board's palettes by name, in the order the Settings tab offers them.
var Themes = []struct {
	Name  string
	Theme Theme
}{
	{"rig", Theme{"233", "255", "249", "60", "235", "111", "179", "114", "210"}},
	// the design's terminal boards: #fafafa window, #18181b text, indigo accent
	{"light", Theme{"#fafafa", "#18181b", "#52525b", "#d4d4d8", "#f0f0f2", "#4f46e5", "#9a5a06", "#15803d", "#b91c1c"}},
	{"dark", Theme{"#15161a", "#ececf1", "#a2a4b0", "#3a3c46", "#1f2026", "#8b8cff", "#f5c66b", "#62e09c", "#ff7b7b"}},
	{"material", Theme{"#1d1b20", "#e6e0e9", "#cac4d0", "#605d66", "#2b2930", "#d0bcff", "#ffd68c", "#a8e2b2", "#ffb4ab"}},
	{"dracula", Theme{"#282a36", "#f8f8f2", "#c5c8d6", "#6272a4", "#343746", "#8be9fd", "#f1fa8c", "#50fa7b", "#ff5555"}},
}

// ThemeNames lists the theme names in order.
func ThemeNames() []string {
	out := make([]string, len(Themes))
	for i, t := range Themes {
		out[i] = t.Name
	}
	return out
}

// ApplyTheme switches the palette; an unknown name keeps the current one and returns false.
func ApplyTheme(name string) bool {
	for _, t := range Themes {
		if t.Name == name {
			p := t.Theme
			colGround, colText, colMuted, colBorder, colSelBg = p.Ground, p.Text, p.Muted, p.Border, p.SelBg
			colAccent, colAmber, colGreen, colRed = p.Accent, p.Amber, p.Green, p.Red
			return true
		}
	}
	return false
}

// sgrColor is the SGR parameters for a palette colour as foreground or background: a 256-colour index as is, a hex
// colour in true colour when the terminal has it, else its nearest 256 colour.
func sgrColor(c string, bg bool) string {
	base := "38"
	if bg {
		base = "48"
	}
	if !strings.HasPrefix(c, "#") {
		return base + ";5;" + c
	}
	if lipgloss.ColorProfile() == termenv.TrueColor {
		r, _ := strconv.ParseUint(c[1:3], 16, 8)
		g, _ := strconv.ParseUint(c[3:5], 16, 8)
		b, _ := strconv.ParseUint(c[5:7], 16, 8)
		return fmt.Sprintf("%s;2;%d;%d;%d", base, r, g, b)
	}
	return base + ";" + strings.TrimPrefix(termenv.ANSI256.Color(c).Sequence(false), "38;")
}

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

// baseSGR is the theme's text colour on its ground (the mockup's #eeeeee on #121212 by default), so the board looks
// the same whatever the terminal's own theme.
func baseSGR() string {
	return "\x1b[" + sgrColor(colText, false) + ";" + sgrColor(colGround, true) + "m"
}

var sgrSeq = regexp.MustCompile(`\x1b\[([0-9;]*)m`)

// Paint lays the mockup's text colour and background under every cell of a frame. Any escape that resets a colour
// to the terminal default (0, 39, 49, or an empty reset) is followed by the mockup's, so styled runs, the
// emulated pane's own output and the padding all sit on the same ground. It never changes a line's width.
func Paint(frame []string) []string {
	out := make([]string, len(frame))
	base := baseSGR()
	for i, line := range frame {
		out[i] = base + sgrSeq.ReplaceAllStringFunc(line, fixSGR) + "\x1b[0m"
	}
	return out
}

// fixSGR re-applies the ground colours a sequence leaves at the terminal's defaults.
func fixSGR(seq string) string {
	fgDefault, bgDefault := sgrDefaults(seq)
	if fgDefault {
		seq += "\x1b[" + sgrColor(colText, false) + "m"
	}
	if bgDefault {
		seq += "\x1b[" + sgrColor(colGround, true) + "m"
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

// tint lays a background under a styled segment: every escape that drops the background to the default puts the
// tint back. It ends on the default background, which Paint turns into the ground. It never changes a width.
func tint(s, bg string) string {
	on := "\x1b[" + sgrColor(bg, true) + "m"
	return on + sgrSeq.ReplaceAllStringFunc(s, func(seq string) string {
		if _, bgDefault := sgrDefaults(seq); bgDefault {
			return seq + on
		}
		return seq
	}) + "\x1b[49m"
}

// Border draws the mockup's thin #5f5f87 frame around a board w cells wide.
func Border(board []string, w int) []string {
	out := make([]string, 0, len(board)+2)
	out = append(out, border("┌"+strings.Repeat("─", w)+"┐"))
	for _, line := range board {
		out = append(out, border("│")+line+border("│"))
	}
	return append(out, border("└"+strings.Repeat("─", w)+"┘"))
}
