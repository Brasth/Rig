package ui

import (
	"fmt"
	"strings"

	"github.com/charmbracelet/lipgloss"

	"github.com/Brasth/Rig/shell/internal/view"
)

// The Session tab on a wide terminal is the mockup's screen: the workspace on the left, the parent's own
// terminal in the middle, the jobs and queue on the right, and the selected job's evidence docked below.
const (
	sessionMinW = 120
	sessionMinH = 30
	sessLeftW   = 26
	sessRightW  = 40
	dockRows    = 4
)

type sessionGeom struct {
	ok               bool
	left, mid, right int
	grid             int // rows of the three columns
}

// sessionGeometry is the three-column layout, or ok=false when the terminal is too small for it.
func sessionGeometry(w, h int) sessionGeom {
	if w < sessionMinW || h < sessionMinH {
		return sessionGeom{}
	}
	// header, tab bar, evidence rule, message and footer take five rows; the dock takes dockRows more
	return sessionGeom{ok: true, left: sessLeftW, right: sessRightW, mid: w - sessLeftW - sessRightW - 2, grid: h - 5 - dockRows}
}

// SessionPaneSize is the room the middle column gives the parent's terminal (its title takes the first row).
func SessionPaneSize(w, h int) (cols, rows int, ok bool) {
	g := sessionGeometry(w, h)
	return g.mid, g.grid - 1, g.ok
}

func heavy(st State, title string, w int) string {
	head := "━ " + title + " "
	if lipgloss.Width(head) > w {
		head = fit(head, w)
	} else {
		head += strings.Repeat("━", w-lipgloss.Width(head))
	}
	if st.Styled {
		return lipgloss.NewStyle().Foreground(lipgloss.Color(colAccent)).Bold(true).Render(head)
	}
	return head
}

func sessionFrame(v *view.View, st State, w, h int) []string {
	g := sessionGeometry(w, h)
	rows := v.Rows("Jobs")
	sel := clampSel(st.Selected["Jobs"], len(rows))
	left := leftColumn(st, g.left, g.grid)
	mid := midColumn(st, g.mid, g.grid)
	right := rightColumn(v, st, g.right, g.grid)
	lines := []string{headerLine(v, st, w), tabLine(v, st, w)}
	for i := 0; i < g.grid; i++ {
		lines = append(lines, left[i]+bar(st)+mid[i]+bar(st)+right[i])
	}
	var row *view.Row
	if len(rows) > 0 {
		row = &rows[sel]
	}
	lines = append(lines, ruleS(st, dockTitle(st, row), w))
	lines = append(lines, dockLines(st, row, w, dockRows)...)
	msg := messageLine(st, w)
	if strings.TrimSpace(msg) == "" && row != nil && row.Ask {
		msg = fit(" "+clean(row.ID)+" needs input: a allows, d denies.", w)
		if st.Styled {
			msg = tone("attention").Render(msg)
		}
	}
	lines = append(lines, msg, footerWith(sessionKeys(st, row), st, w))
	return lines[:h]
}

func sessionKeys(st State, row *view.Row) []string {
	if st.PaneFocus {
		return []string{"Ctrl-] back to the board (every other key goes to the program)"}
	}
	keys := []string{"Tab pane", "Enter type", "p parent", "s shell", "e enqueue", "v evidence", "? help", "q quit"}
	switch {
	case st.Mode == ModeInput:
		return []string{"Enter queue it", "Esc discard"}
	case st.Mode == ModeConfirm:
		return []string{"y confirm", "n cancel"}
	case row != nil && row.Ask:
		return append([]string{"a allow", "d deny"}, keys...)
	}
	return keys
}

// leftColumn is the workspace's sections, each under its own rule, as many as fit.
func leftColumn(st State, w, h int) []string {
	out := make([]string, 0, h)
	switch {
	case st.Workspace == nil && st.WorkspaceE != "":
		out = append(out, ruleS(st, "Workspace", w))
		for _, l := range wrap("unavailable: "+st.WorkspaceE, w-2, "  ") {
			out = append(out, fit(" "+l, w))
		}
	case st.Workspace == nil:
		out = append(out, ruleS(st, "Workspace", w), fit(" Loading workspace…", w))
	default:
		for i, sec := range st.Workspace.Sections {
			if i > 0 {
				out = append(out, fit("", w))
			}
			out = append(out, ruleS(st, sanitize(sec.Title), w))
			for _, l := range sec.Lines {
				text := fit(" "+sanitize(l.Text), w)
				out = append(out, tonedLine(l.Tone, text, st.Styled))
			}
		}
	}
	if len(out) > h {
		out = out[:h]
	}
	for len(out) < h-1 {
		out = append(out, fit("", w))
	}
	if len(out) < h {
		hint := fit(" w the full env view", w)
		if st.Styled {
			hint = muted(hint)
		}
		out = append(out, hint)
	}
	return out
}

// midColumn is the parent's terminal when one runs, else how to start it.
func midColumn(st State, w, h int) []string {
	out := make([]string, 0, h)
	out = append(out, heavy(st, termTitle(st), w))
	if len(st.PaneLines) == 0 {
		for _, l := range []string{
			"",
			" p  start the parent CLI for this project here",
			" s  a shell in this project's container",
			"",
			" Enter types into it; Ctrl-] returns to the board.",
			" Jobs and their evidence stay in view while it runs.",
		} {
			out = append(out, fit(l, w))
		}
	} else {
		for i := 0; i < h-1 && i < len(st.PaneLines); i++ {
			out = append(out, fit(st.PaneLines[i], w))
		}
	}
	for len(out) < h {
		out = append(out, fit("", w))
	}
	return out[:h]
}

// rightColumn is the Jobs list (attention first, finished last) with the queue under it.
func rightColumn(v *view.View, st State, w, h int) []string {
	jobs := v.Rows("Jobs")
	queue := v.Rows("Queue")
	sel := clampSel(st.Selected["Jobs"], len(jobs))
	qBlock := 0
	if len(queue) > 0 {
		qBlock = 2 + min(len(queue), 3) // a blank line, the rule, up to three queued rows
	}
	jobsH := max(3, h-qBlock)
	cap := jobsH - 1
	eh := 1
	if cap >= twoLineMinBody {
		eh = 2
	}
	off := st.Offsets["Jobs"]
	out := []string{ruleS(st, rangeText("Jobs", jobs, off, sel, cap, eh), w)}
	hidden := 0
	if len(jobs) > 0 {
		if _, end := windowRange(jobs, off, sel, cap, eh, true); end < len(jobs) {
			cap--
			_, end = windowRange(jobs, off, sel, cap, eh, true)
			hidden = len(jobs) - end
		}
	}
	list := listLines(jobs, sel, off, w, cap, eh, st.Styled, true)
	for hidden == 0 && len(list) > 1 && strings.TrimSpace(list[len(list)-1]) == "" {
		list = list[:len(list)-1] // the queue sits right under the jobs, as in the mockup
	}
	out = append(out, list...)
	if hidden > 0 {
		more := fit(fmt.Sprintf("  ↓ %d more below", hidden), w)
		if st.Styled {
			more = muted(more)
		}
		out = append(out, more)
	}
	if qBlock > 0 && len(out)+qBlock <= h {
		out = append(out, fit("", w), ruleS(st, fmt.Sprintf("Queue %d pending", len(queue)), w))
		for i, q := range queue {
			if i == 2 && len(queue) > 3 {
				out = append(out, fit(fmt.Sprintf("  +%d more queued", len(queue)-2), w))
				break
			}
			if i == 3 {
				break
			}
			out = append(out, fit(fmt.Sprintf("  · Queued       %s", clean(q.Title)), w))
		}
	}
	for len(out) < h {
		out = append(out, fit("", w))
	}
	return out[:h]
}

func dockTitle(st State, row *view.Row) string {
	if row == nil {
		return "Evidence"
	}
	return "Evidence · " + sanitize(row.ID) + " · " + clean(row.Title)
}

// dockLines is the selected job's evidence in four lines: what changed, its checks, its contract, its ports.
func dockLines(st State, row *view.Row, w, n int) []string {
	out := make([]string, 0, n)
	pad := func() []string {
		for len(out) < n {
			out = append(out, fit("", w))
		}
		return out[:n]
	}
	switch {
	case row == nil:
		out = append(out, fit(" No job selected.", w))
		return pad()
	case st.DockEvidence == nil && st.DockE != "":
		out = append(out, fit(" Evidence unavailable: "+clean(st.DockE), w))
		return pad()
	case st.DockEvidence == nil || st.DockID != row.ID:
		out = append(out, fit(" Loading evidence…", w))
		return pad()
	}
	sections := map[string][]view.Line{}
	for _, sec := range st.DockEvidence.Sections {
		sections[sec.Title] = sec.Lines
	}
	join := func(lines []view.Line, strip string) string {
		var parts []string
		for _, l := range lines {
			t := strings.TrimSpace(strings.TrimPrefix(strings.TrimSpace(sanitize(l.Text)), strip))
			if t != "" {
				parts = append(parts, t)
			}
		}
		return strings.Join(parts, "  ·  ")
	}
	changed := []view.Line{}
	for _, l := range sections["Change"] {
		if strings.HasPrefix(strings.TrimSpace(l.Text), "changed") {
			changed = append(changed, l)
		}
	}
	entries := [][2]string{
		{"diff", orElse(join(changed, "changed"), "no changes recorded")},
		{"checks", orElse(join(sections["Checks"], ""), "none recorded")},
		{"contract", orElse(join(sections["Contract"], ""), "none")},
		{"ports", orElse(join(sections["Ports"], ""), "none")},
	}
	for _, e := range entries {
		label := " " + fmt.Sprintf("%-9s", e[0])
		if st.Styled {
			label = muted(label)
		}
		out = append(out, fit(label+e[1], w))
	}
	return pad()
}

func orElse(s, fallback string) string {
	if s == "" {
		return fallback
	}
	return s
}
