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
		return lipgloss.NewStyle().Foreground(lipgloss.Color(colAmber)).Bold(true)
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

// selectedRow draws the selected entry as a dark bar with bold text; the state label keeps its tone on the bar.
func selectedRow(marker, label, toneName, rest, second string, eh int) []string {
	bar := lipgloss.NewStyle().Background(lipgloss.Color(colSelBg)).Foreground(lipgloss.Color(colText)).Bold(true)
	lab := tone(toneName).Background(lipgloss.Color(colSelBg)).Bold(true)
	lines := []string{bar.Render(marker+" ") + lab.Render(label) + bar.Render("  "+rest)}
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
