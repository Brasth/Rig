package ui

import (
	"regexp"
	"strings"

	"github.com/charmbracelet/lipgloss"
	"github.com/charmbracelet/x/ansi"
)

// A parent writes markdown and a terminal shows it raw, which is the thing that most makes a reply hard to read. mdLines
// draws it: headings, bullets, quotes, code blocks with a gutter, tables in columns, `code` and **bold** inline. It is
// built line by line so the half-written reply that is still streaming in draws the same way as a finished one.

var (
	mdHeading = regexp.MustCompile(`^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$`)
	mdBullet  = regexp.MustCompile(`^(\s*)[-*+]\s+(.*)$`)
	mdNumber  = regexp.MustCompile(`^(\s*)(\d{1,3})[.)]\s+(.*)$`)
	mdQuote   = regexp.MustCompile(`^\s{0,3}>\s?(.*)$`)
	mdRule    = regexp.MustCompile(`^\s{0,3}(?:-\s*){3,}$|^\s{0,3}(?:\*\s*){3,}$|^\s{0,3}(?:_\s*){3,}$`)
	mdFence   = regexp.MustCompile("^\\s*(`{3,}|~{3,})\\s*([A-Za-z0-9_+.#-]*)")
	mdTable   = regexp.MustCompile(`^\s*\|.*\|\s*$`)
	mdTableSp = regexp.MustCompile(`^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$`)
	mdLink    = regexp.MustCompile(`\[([^\]]+)\]\(([^)]+)\)`)
	mdInline  = regexp.MustCompile("`([^`]+)`|\\*\\*([^*]+)\\*\\*")
)

// mdSpan is a piece of one line with the way to draw it.
type mdSpan struct {
	text string
	kind int // 0 text, 1 code, 2 bold
}

// mdLines draws markdown text as terminal lines no wider than cw.
func mdLines(st State, text string, cw int) []string {
	var out []string
	rows := strings.Split(strings.TrimRight(text, "\n"), "\n")
	inCode := false
	fence := ""
	blank := true // the last line drawn was blank: no run of blank lines
	pushBlank := func() {
		if !blank {
			out = append(out, "")
			blank = true
		}
	}
	for i := 0; i < len(rows); i++ {
		raw := strings.TrimRight(sanitizeKeepIndent(strings.ReplaceAll(rows[i], "\t", "    ")), " ")
		if f := mdFence.FindStringSubmatch(raw); f != nil && (!inCode || strings.HasPrefix(f[1], fence)) {
			if inCode {
				inCode = false
				out = append(out, st.faint("  ╰"))
			} else {
				inCode, fence = true, f[1][:1]
				label := f[2]
				if label != "" {
					label = " " + label
				}
				out = append(out, st.faint("  ╭"+label))
			}
			blank = false
			continue
		}
		if inCode {
			for _, l := range hardWrap(raw, cw-6) {
				out = append(out, st.faint("  │ ")+st.paint(colText, l))
			}
			blank = false
			continue
		}
		switch {
		case strings.TrimSpace(raw) == "":
			pushBlank()
		case mdTable.MatchString(raw):
			j := i
			var table []string
			for j < len(rows) && mdTable.MatchString(strings.TrimRight(rows[j], " \t")) {
				table = append(table, strings.TrimRight(rows[j], " \t"))
				j++
			}
			out = append(out, tableLines(st, table, cw)...)
			blank = false
			i = j - 1
		case mdRule.MatchString(raw):
			out = append(out, st.faint(strings.Repeat("─", min(cw-2, 40))))
			blank = false
		case mdHeading.MatchString(raw):
			h := mdHeading.FindStringSubmatch(raw)
			pushBlankBefore(&out, &blank)
			for _, l := range wrapSpans(st, parseSpans(h[2]), cw, "", "", true) {
				out = append(out, st.paint(colAccent, l))
			}
			blank = false
		case mdBullet.MatchString(raw):
			b := mdBullet.FindStringSubmatch(raw)
			pad := strings.Repeat(" ", 2+min(len(b[1])/2*2, 8))
			out = append(out, wrapSpans(st, parseSpans(b[2]), cw, pad+st.faint("• "), pad+"  ", false)...)
			blank = false
		case mdNumber.MatchString(raw):
			n := mdNumber.FindStringSubmatch(raw)
			pad := strings.Repeat(" ", 2+min(len(n[1])/2*2, 8))
			out = append(out, wrapSpans(st, parseSpans(n[3]), cw, pad+st.faint(n[2]+". "), pad+strings.Repeat(" ", len(n[2])+2), false)...)
			blank = false
		case mdQuote.MatchString(raw):
			q := mdQuote.FindStringSubmatch(raw)
			out = append(out, wrapSpans(st, parseSpans(q[1]), cw, st.faint("  ▎ "), "    ", false)...)
			blank = false
		default:
			out = append(out, wrapSpans(st, parseSpans(strings.TrimLeft(raw, " ")), cw, "", "", false)...)
			blank = false
		}
	}
	return out
}

func pushBlankBefore(out *[]string, blank *bool) {
	if len(*out) > 0 && !*blank {
		*out = append(*out, "")
	}
}

// sanitizeKeepIndent is sanitize for a line whose leading spaces matter (a list level, code).
func sanitizeKeepIndent(s string) string {
	lead := len(s) - len(strings.TrimLeft(s, " "))
	return strings.Repeat(" ", lead) + sanitize(strings.TrimLeft(s, " "))
}

// parseSpans splits a line into plain, `code` and **bold** pieces; a link keeps its words.
func parseSpans(s string) []mdSpan {
	s = mdLink.ReplaceAllString(s, "$1")
	var out []mdSpan
	last := 0
	for _, m := range mdInline.FindAllStringSubmatchIndex(s, -1) {
		if m[0] > last {
			out = append(out, mdSpan{s[last:m[0]], 0})
		}
		if m[2] >= 0 {
			out = append(out, mdSpan{s[m[2]:m[3]], 1})
		} else {
			out = append(out, mdSpan{s[m[4]:m[5]], 2})
		}
		last = m[1]
	}
	if last < len(s) {
		out = append(out, mdSpan{s[last:], 0})
	}
	return out
}

// paintSpan draws one word the way its mdSpan says.
func paintSpan(st State, word string, kind int, whole bool) string {
	switch {
	case whole || kind == 0:
		return st.paint(colText, word)
	case kind == 1:
		return st.paint(colAccent, word)
	}
	return st.bold(word)
}

// wrapSpans breaks spans into lines no wider than cw, word by word, so a styled phrase that runs over a line end
// keeps its style on both lines. first starts the first line, rest each one after it.
func wrapSpans(st State, spans []mdSpan, cw int, first, rest string, whole bool) []string {
	type word struct {
		text string
		kind int
		glue bool // no space before it: punctuation straight after a styled word
	}
	var words []word
	endedOpen := false // the last span ended without a space
	for _, sp := range spans {
		fields := strings.Fields(sp.text)
		for n, w := range fields {
			words = append(words, word{w, sp.kind, n == 0 && len(words) > 0 && endedOpen && !strings.HasPrefix(sp.text, " ")})
		}
		if len(fields) > 0 {
			endedOpen = !strings.HasSuffix(sp.text, " ")
		}
	}
	if len(words) == 0 {
		return []string{first}
	}
	var lines []string
	prefix := first
	line, width := "", 0
	room := func() int { return max(8, cw-lipgloss.Width(prefix)) }
	flush := func() {
		lines = append(lines, prefix+line)
		prefix, line, width = rest, "", 0
	}
	for _, w := range words {
		for lipgloss.Width(w.text) > room() {
			if width > 0 {
				flush()
				continue
			}
			head := ansi.Truncate(w.text, room(), "")
			if head == "" {
				break
			}
			line = paintSpan(st, head, w.kind, whole)
			flush()
			w.text = strings.TrimPrefix(w.text, head)
		}
		need := lipgloss.Width(w.text)
		gap := 1
		if w.glue {
			gap = 0
		}
		if width > 0 && width+gap+need > room() {
			flush()
		}
		if width > 0 {
			line += strings.Repeat(" ", gap)
			width += gap
		}
		line += paintSpan(st, w.text, w.kind, whole)
		width += need
	}
	if width > 0 {
		flush()
	}
	return lines
}

// hardWrap cuts a line of code into pieces of at most w cells, keeping what it says.
func hardWrap(s string, w int) []string {
	if w < 8 {
		w = 8
	}
	if lipgloss.Width(s) <= w {
		return []string{s}
	}
	var out []string
	for lipgloss.Width(s) > w {
		head := ansi.Truncate(s, w, "")
		if head == "" {
			break
		}
		out = append(out, head)
		s = strings.TrimPrefix(s, head)
	}
	return append(out, s)
}

// tableLines draws a markdown table in aligned columns; a table too wide for the line has its cells shortened.
func tableLines(st State, rows []string, cw int) []string {
	var cells [][]string
	header := true
	var headerRow []string
	for _, r := range rows {
		if mdTableSp.MatchString(r) {
			continue
		}
		parts := strings.Split(strings.Trim(strings.TrimSpace(r), "|"), "|")
		for k := range parts {
			parts[k] = strings.TrimSpace(plainMarkdown(parts[k]))
		}
		if header {
			headerRow, header = parts, false
		}
		cells = append(cells, parts)
	}
	cols := 0
	for _, r := range cells {
		cols = max(cols, len(r))
	}
	if cols == 0 {
		return nil
	}
	widths := make([]int, cols)
	for _, r := range cells {
		for k, c := range r {
			widths[k] = max(widths[k], lipgloss.Width(c))
		}
	}
	for sum(widths)+3*cols+1 > cw-2 {
		k := 0
		for j := range widths {
			if widths[j] > widths[k] {
				k = j
			}
		}
		if widths[k] <= 4 {
			break
		}
		widths[k]--
	}
	var out []string
	for n, r := range cells {
		line := st.faint("  │")
		for k := 0; k < cols; k++ {
			c := ""
			if k < len(r) {
				c = r[k]
			}
			c = fit(c, widths[k])
			pad := strings.Repeat(" ", max(0, widths[k]-lipgloss.Width(c)))
			if n == 0 && len(headerRow) > 0 {
				c = st.bold(c)
			} else {
				c = st.paint(colText, c)
			}
			line += " " + c + pad + " " + st.faint("│")
		}
		out = append(out, line)
		if n == 0 {
			rule := st.faint("  ├")
			for k := 0; k < cols; k++ {
				rule += st.faint(strings.Repeat("─", widths[k]+2))
				if k < cols-1 {
					rule += st.faint("┼")
				}
			}
			out = append(out, rule+st.faint("┤"))
		}
	}
	return out
}

func sum(xs []int) int {
	t := 0
	for _, x := range xs {
		t += x
	}
	return t
}
