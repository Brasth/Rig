package ui

import "github.com/charmbracelet/lipgloss"

// Tone names come from the server (tui_rows). Colour only reinforces the label
// and glyph already on every row; nothing depends on it.
func tone(name string) lipgloss.Style {
	switch name {
	case "attention":
		return lipgloss.NewStyle().Foreground(lipgloss.Color("11")).Bold(true)
	case "active":
		return lipgloss.NewStyle().Foreground(lipgloss.Color("12"))
	case "verified":
		return lipgloss.NewStyle().Foreground(lipgloss.Color("10"))
	case "failed":
		return lipgloss.NewStyle().Foreground(lipgloss.Color("9"))
	}
	return lipgloss.NewStyle()
}

func paintSelected(first, second string, eh int) []string {
	sel := lipgloss.NewStyle().Reverse(true)
	lines := []string{sel.Render(first)}
	if eh == 2 {
		lines = append(lines, sel.Render(second))
	}
	return lines
}

// Decorate adds bold to the header and faint to the key footer of a rendered
// frame. It never changes a line's width.
func Decorate(frame []string) []string {
	if len(frame) < 2 {
		return frame
	}
	out := append([]string(nil), frame...)
	out[0] = lipgloss.NewStyle().Bold(true).Render(out[0])
	out[len(out)-1] = lipgloss.NewStyle().Faint(true).Render(out[len(out)-1])
	return out
}
