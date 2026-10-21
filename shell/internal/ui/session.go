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
	sessLeftW   = 26 // the mockup's widths at 120 columns; wider terminals keep its proportions up to the caps
	sessRightW  = 40
	sessLeftMax = 44
	sessRightMx = 72
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
	left := min(sessLeftMax, max(sessLeftW, w*sessLeftW/sessionMinW))
	right := min(sessRightMx, max(sessRightW, w*sessRightW/sessionMinW))
	// header, tab bar, evidence rule, message and footer take five rows; the dock takes dockRows more
	return sessionGeom{ok: true, left: left, right: right, mid: w - left - right - 2, grid: h - 5 - dockRows}
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
	panel := func(s string) string { // the side columns and the dock sit on the lighter panel
		if st.Styled {
			return tint(s, colPanel)
		}
		return s
	}
	for i := 0; i < g.grid; i++ {
		lines = append(lines, panel(left[i])+bar(st)+mid[i]+bar(st)+panel(right[i]))
	}
	var row *view.Row
	if len(rows) > 0 {
		row = &rows[sel]
	}
	lines = append(lines, panel(ruleS(st, dockTitle(st, row), w)))
	for _, l := range dockLines(st, row, w, dockRows) {
		lines = append(lines, panel(l))
	}
	msg := messageLine(st, w)
	if typingChat(st) { // the box shows the draft; this line says what happened, or what the parent waits for
		switch {
		case st.Chat != nil && st.Chat.Ask != nil && st.Prompt == "":
			msg = fit(" the parent needs input: a allows, d denies.", w)
			if st.Styled {
				msg = tone("attention").Render(msg)
			}
		case st.Message != "":
			msg = fit(" "+clean(st.Message), w)
			if st.Styled && problem(st.Message) {
				msg = tone("attention").Render(msg)
			} else if st.Styled {
				msg = muted(msg)
			}
		case row != nil && row.Ask:
			msg = fit(" "+clean(row.ID)+" needs input: Esc, then a allows or d denies.", w)
			if st.Styled {
				msg = tone("attention").Render(msg)
			}
		default:
			msg = fit("", w)
		}
	} else if strings.TrimSpace(msg) == "" && st.Chat != nil && st.Chat.Ask != nil && !st.PaneRunning {
		msg = fit(" the parent needs input: a allows, d denies.", w)
		if st.Styled {
			msg = tone("attention").Render(msg)
		}
	} else if strings.TrimSpace(msg) == "" && row != nil && row.Ask {
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
	enter := "Enter type"
	if st.ChatOn && !st.PaneRunning {
		enter = "Enter ask the parent"
	}
	keys := []string{"Tab pane", enter, "p parent", "e enqueue", "v evidence", "w workspace", "? help", "q quit"}
	switch {
	case st.Picker != nil:
		return []string{"↑↓ choose", "Enter save", "Esc close"}
	case typingChat(st) && len(slashMatches(st.Prompt)) > 0:
		return []string{"↑↓ choose", "Tab complete", "Enter run", "Esc clear"}
	case typingChat(st) && st.Chat != nil && st.Chat.Ask != nil && st.Prompt == "":
		return []string{"a allow", "d deny", "Esc board keys", "Ctrl-C quit"}
	case typingChat(st) && st.Prompt != "":
		return []string{"Enter send", "Esc clear", "↑↓ jobs", "PgUp/PgDn scroll", "Ctrl-C clear"}
	case typingChat(st):
		return []string{"Enter send", "/ commands", "↑↓ jobs", "PgUp/PgDn scroll", "Tab pane", "Esc board keys", "Ctrl-C quit"}
	case st.Mode == ModeInput:
		return []string{"Enter queue it", "Esc discard"}
	case st.Mode == ModeConfirm:
		return []string{"y confirm", "n cancel"}
	case (st.Chat != nil && st.Chat.Ask != nil && !st.PaneRunning) || (row != nil && row.Ask):
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

// chatPlaceholder is the ask box while nothing is typed.
const chatPlaceholder = "Ask the parent anything · / for commands"

// midColumn is the parent: its own terminal while one runs (p), else the conversation, as the mockup shows it.
func midColumn(st State, w, h int) []string {
	if st.ChatOn && !st.PaneRunning {
		return chatColumn(st, w, h)
	}
	out := make([]string, 0, h)
	out = append(out, heavy(st, parentTitle(st), w))
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
	jobs := withID(v.Rows("Jobs"))
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

// chatColumn is the conversation: the transcript, then the ask box, a raised card that is always ready for typing.
// Typing / lists the commands above the box; /parent (or a project with no parent yet) shows the parent picker.
func chatColumn(st State, w, h int) []string {
	title := "Parent"
	if st.ChatParent != "" {
		title += " · " + clean(st.ChatParent)
	}
	if st.Chat != nil {
		title = "Parent · " + clean(st.Chat.Parent)
		if st.Chat.State == "running" {
			title += " · working"
		}
	}
	out := []string{heavy(st, title, w)}
	body := h - 1 - askBoxRows
	var lines []string
	switch {
	case st.Picker != nil:
		lines = pickerLines(st, w)
	case st.Chat == nil && st.ChatE == "":
		who := "the parent (" + clean(st.ChatParent) + ")"
		if st.ChatParent == "" {
			who = "the parent; none is installed here, so install claude or codex, or start with rig shell --parent NAME"
		}
		lines = []string{"", " No conversation yet.", ""}
		for _, l := range wrap("Type below and press Enter to talk to "+who+". The answer appears here as it is written.", max(8, w-2), "") {
			lines = append(lines, " "+l)
		}
		lines = append(lines, "")
		for _, l := range wrap("/parent changes the parent · /queue TEXT parks a task · /help lists every key", max(8, w-2), "") {
			lines = append(lines, " "+l)
		}
		if st.Styled {
			for i := range lines {
				lines[i] = muted(lines[i])
			}
		}
	default:
		lines = append(chatAll(st, w), askLines(st, w)...)
	}
	view := lastLines(lines, st.ChatUp, w, body)
	if menu := slashMenu(st, w); typingChat(st) && len(menu) > 0 && st.Picker == nil {
		n := min(len(menu), body)
		copy(view[body-n:], menu[len(menu)-n:])
	}
	out = append(out, view...)
	return append(out, askBox(st, w)...)[:h]
}

// askBoxRows is the height of the ask box: a rounded card around one line.
const askBoxRows = 3

// askBox is the ask box. While it has the keyboard it shows the draft and a cursor; on board keys it is dimmed.
func askBox(st State, w int) []string {
	inner := max(4, w-4)
	text := " › "
	switch {
	case typingChat(st) && st.Prompt != "":
		text += st.Prompt + "▏"
		if lipgloss.Width(text) > inner { // keep the cursor end in view while typing a long message
			r := []rune(st.Prompt)
			text = " › …" + string(r[max(0, len(r)-(inner-6)):]) + "▏"
		}
		text = fit(text, inner)
	case typingChat(st):
		text = fit(text+"▏"+chatPlaceholder, inner)
		if st.Styled {
			text = " › ▏" + muted(fit(chatPlaceholder, inner-4))
		}
	case st.Picker != nil:
		text = fit(" › choose the parent above", inner)
		if st.Styled {
			text = muted(text)
		}
	default:
		text = fit(" › Enter or i to type", inner)
		if st.Styled {
			text = muted(text)
		}
	}
	top := " ╭" + strings.Repeat("─", inner) + "╮ "
	mid := " │" + text + "│ "
	bot := " ╰" + strings.Repeat("─", inner) + "╯ "
	if !st.Styled {
		return []string{fit(top, w), fit(mid, w), fit(bot, w)}
	}
	edge := colBorder
	if typingChat(st) {
		edge = colAccent // the box with the keyboard is lit
	}
	paint := func(s string) string { return lipgloss.NewStyle().Foreground(lipgloss.Color(edge)).Render(s) }
	card := func(s string) string { return " " + tint(s, colRaised) + " " }
	return []string{
		fit(card(paint("╭"+strings.Repeat("─", inner)+"╮")), w),
		fit(card(paint("│")+text+paint("│")), w),
		fit(card(paint("╰"+strings.Repeat("─", inner)+"╯")), w),
	}
}

// pickerLines is the parent picker: the parent CLIs this machine has, the one in use marked, ↑↓ Enter to choose.
func pickerLines(st State, w int) []string {
	pk := st.Picker
	lines := []string{""}
	for _, l := range wrap("Choose the parent: the CLI you talk to here. It plans the work and launches the jobs.", max(8, w-2), "") {
		if st.Styled {
			l = lipgloss.NewStyle().Foreground(lipgloss.Color(colText)).Bold(true).Render(l)
		}
		lines = append(lines, " "+l)
	}
	lines = append(lines, "")
	for i, item := range pk.Items {
		note := "installed"
		if !item.Installed {
			note = "not installed"
		}
		if item.Name == pk.Current {
			note += " · in use"
		}
		mark := "   "
		if i == pk.Sel {
			mark = " › "
		}
		line := " " + fit(fmt.Sprintf("%s%-10s %s", mark, item.Name, note), w-2) + " "
		switch {
		case !st.Styled:
		case i == pk.Sel:
			line = " " + tint(lipgloss.NewStyle().Foreground(lipgloss.Color(colAccent)).Bold(true).Render(fit(fmt.Sprintf("%s%-10s %s", mark, item.Name, note), w-2)), colRaised) + " "
		case !item.Installed:
			line = muted(line)
		}
		lines = append(lines, line)
	}
	lines = append(lines, "")
	for _, l := range wrap("↑↓ choose · Enter saves it to .rig/harness.toml · Esc keeps the current one", max(8, w-2), "") {
		if st.Styled {
			l = muted(l)
		}
		lines = append(lines, " "+l)
	}
	return lines
}

// slashMenu is the command list above the box while a / command is being typed, the chosen one raised.
func slashMenu(st State, w int) []string {
	matches := slashMatches(st.Prompt)
	out := make([]string, 0, len(matches))
	sel := clampSel(st.SlashSel, len(matches))
	for i, c := range matches {
		line := fit(fmt.Sprintf("   %-12s %s", c.Name+c.Args, c.Help), w-2)
		if st.Styled {
			style := lipgloss.NewStyle().Foreground(lipgloss.Color(colMuted))
			if i == sel {
				style = lipgloss.NewStyle().Foreground(lipgloss.Color(colAccent)).Bold(true)
			}
			line = tint(style.Render(line), colRaised)
		}
		out = append(out, " "+line+" ")
	}
	return out
}

// askLines is the mockup's request block: what the parent wants to run, in amber, and the keys that answer it. A
// parent whose headless mode cannot ask says so once, under its first answer.
func askLines(st State, w int) []string {
	if st.Chat == nil {
		return nil
	}
	paint := func(s string) string {
		if st.Styled {
			return tone("attention").Render(s)
		}
		return s
	}
	if ask := st.Chat.Ask; ask != nil {
		var out []string
		out = append(out, " "+paint("parent › asks to run:"))
		for _, l := range wrap(clean(ask.Preview), max(8, w-5), "") {
			out = append(out, "   "+paint(l))
		}
		hint := "   a allow  d deny"
		if st.Styled {
			hint = muted(hint)
		}
		return append(out, hint, "")
	}
	if !st.Chat.Asks && len(st.Chat.Entries) > 0 {
		note := " " + clean(st.Chat.Parent) + " cannot ask for permission headless: it follows its own settings. p opens its own screen."
		var out []string
		for _, l := range wrap(note, max(8, w-2), "  ") {
			if st.Styled {
				l = muted(l)
			}
			out = append(out, " "+l)
		}
		return out
	}
	return nil
}

// parentTitle is the middle column's heading, as the mockup words it: "Parent · codex", and what it is doing.
// How to get back to the board is the footer's job, so the heading never carries it.
func parentTitle(st State) string {
	if st.PaneTitle == "" {
		return "Parent"
	}
	title := sanitize(st.PaneTitle)
	if i := strings.Index(title, " ("); i > 0 {
		title = title[:i]
	}
	return "Parent · " + title
}

// withID puts the job id after the worker on a job's second line, as the mockup shows it: "claude · job-7f2 · ASK".
func withID(rows []view.Row) []view.Row {
	out := make([]view.Row, len(rows))
	for i, r := range rows {
		out[i] = r
		if r.ID == "" || strings.Contains(r.Second, r.ID) {
			continue
		}
		lead := r.Worker + " · "
		switch {
		case r.Worker == "":
			out[i].Second = r.ID + " · " + r.Second
		case strings.HasPrefix(r.Second, lead):
			out[i].Second = lead + r.ID + " · " + strings.TrimPrefix(r.Second, lead)
		case r.Second == r.Worker || r.Second == "":
			out[i].Second = r.Worker + " · " + r.ID
		default: // an active job's second line is what it is doing now
			out[i].Second = lead + r.ID + " · " + r.Second
		}
	}
	return out
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
