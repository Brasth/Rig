package ui

import (
	"regexp"
	"strings"
	"unicode"

	"github.com/charmbracelet/lipgloss"
	"github.com/charmbracelet/x/ansi"
)

var (
	oscSeq = regexp.MustCompile(`\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)`)
	csiSeq = regexp.MustCompile(`\x1b\[[0-?]*[ -/]*[@-~]`)
)

// clean renders worker-supplied text literally: terminal escape sequences and
// control characters are removed, whitespace is collapsed to single spaces.
func clean(s string) string {
	s = oscSeq.ReplaceAllString(s, "")
	s = csiSeq.ReplaceAllString(s, "")
	var b strings.Builder
	for _, r := range s {
		switch {
		case unicode.IsSpace(r):
			b.WriteRune(' ')
		case unicode.IsControl(r) || r == '\x1b':
		default:
			b.WriteRune(r)
		}
	}
	return strings.Join(strings.Fields(b.String()), " ")
}

// sanitize strips terminal sequences and control characters but keeps runs of
// spaces, for server-formatted lines whose columns are already aligned.
func sanitize(s string) string {
	s = oscSeq.ReplaceAllString(s, "")
	s = csiSeq.ReplaceAllString(s, "")
	var b strings.Builder
	for _, r := range s {
		switch {
		case r == '\t':
			b.WriteString("  ")
		case unicode.IsControl(r) || r == '\x1b':
		default:
			b.WriteRune(r)
		}
	}
	return strings.TrimRight(b.String(), " \n\r")
}

// fit truncates s to w cells with an ellipsis and pads it to exactly w cells.
func fit(s string, w int) string {
	if w <= 0 {
		return ""
	}
	if lipgloss.Width(s) > w {
		s = ansi.Truncate(s, w, "…")
	}
	if pad := w - lipgloss.Width(s); pad > 0 {
		s += strings.Repeat(" ", pad)
	}
	return s
}

// wrap breaks text into lines of at most w cells, continuing with indent.
func wrap(text string, w int, indent string) []string {
	text = clean(text)
	if text == "" {
		return []string{""}
	}
	var lines []string
	line := ""
	prefix := ""
	for _, word := range strings.Fields(text) {
		for lipgloss.Width(word) > w-lipgloss.Width(prefix) && w-lipgloss.Width(prefix) > 1 {
			room := w - lipgloss.Width(prefix) - lipgloss.Width(line)
			if line != "" {
				lines = append(lines, prefix+line)
				line, prefix = "", indent
				continue
			}
			head := ansi.Truncate(word, room, "")
			if head == "" {
				break
			}
			lines = append(lines, prefix+head)
			word = strings.TrimPrefix(word, head)
			prefix = indent
		}
		if line == "" {
			line = word
		} else if lipgloss.Width(prefix+line+" "+word) <= w {
			line += " " + word
		} else {
			lines = append(lines, prefix+line)
			line, prefix = word, indent
		}
	}
	return append(lines, prefix+line)
}

var asciiGlyphs = map[string]string{"●": "*", "✓": "+", "✗": "x", "–": "-", "○": "o", "·": ".", "…": "~", "│": "|", "─": "-", "━": "=", "┬": "+", "›": ">", "▏": "_", "×": "x"}

// asciiOnly maps Unicode UI glyphs to ASCII for terminals that cannot show them.
func asciiOnly(s string) string {
	var b strings.Builder
	for _, r := range s {
		if m, ok := asciiGlyphs[string(r)]; ok {
			b.WriteString(m)
		} else {
			b.WriteRune(r)
		}
	}
	return b.String()
}
