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
	mid := midColumn(v, st, g.mid, g.grid)
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
	amber := func(text string) string {
		line := fit(" "+text, w)
		if st.Styled {
			return tone("attention").Render(line)
		}
		return line
	}
	ask := waitingJob(v)
	switch {
	case typingChat(st) && st.Message != "":
		msg = fit(" "+clean(st.Message), w)
		if st.Styled && problem(st.Message) {
			msg = tone("attention").Render(msg)
		} else if st.Styled {
			msg = muted(msg)
		}
	case strings.TrimSpace(msg) != "" && !typingChat(st):
	case st.Chat != nil && st.Chat.Ask != nil && !st.PaneRunning:
		msg = amber("the parent needs input: allow or deny the command in the parent pane.")
	case ask != nil:
		msg = amber(clean(ask.ID) + " needs input: allow or deny the command in the parent pane.")
	case typingChat(st):
		msg = fit("", w)
	}
	lines = append(lines, msg, footerWith(sessionKeys(st, row, ask != nil), st, w))
	return lines[:h]
}

// sessionKeys is the footer for the pane with the keyboard. On the jobs it is the mockup's line word for word.
func sessionKeys(st State, row *view.Row, jobAsks bool) []string {
	if st.PaneFocus {
		return []string{"Ctrl-] back to the board (every other key goes to the program)"}
	}
	parentAsks := st.Chat != nil && st.Chat.Ask != nil && !st.PaneRunning
	switch {
	case st.Picker != nil:
		return []string{"↑↓ choose", "Enter save", "Esc close"}
	case st.Mode == ModeConfirm:
		return []string{"y confirm", "n cancel"}
	case st.Mode == ModeInput && !typingChat(st):
		return []string{"Enter queue it", "Esc discard"}
	case typingChat(st) && len(slashMatches(st.Prompt)) > 0:
		return []string{"↑↓ choose", "Tab complete", "Enter run", "Esc clear"}
	case typingChat(st) && st.Prompt != "":
		return []string{"Enter send", "Esc clear", "↑↓ jobs", "PgUp/PgDn scroll", "Ctrl-C clear"}
	case typingChat(st) && (parentAsks || jobAsks):
		return []string{"Tab pane", "a allow", "d deny", "m message", "/ commands", "↑↓ jobs", "Ctrl-C quit"}
	case typingChat(st):
		return []string{"Tab pane", "Enter send", "/ commands", "↑↓ jobs", "PgUp/PgDn scroll", "Ctrl-C quit"}
	case st.Focus == "workspace":
		return []string{"Tab pane", "u up", "x shell", "d doctor", "? help", "q quit"}
	}
	keys := []string{"Tab pane", "Enter details"}
	if row != nil && row.Ask {
		keys = append(keys, "a allow", "d deny")
	}
	return append(keys, "e enqueue", "v evidence", "w workspace", "? help", "q quit")
}

// waitingJob is the first job waiting on a permission answer: the conversation shows its request, as the mockup does.
func waitingJob(v *view.View) *view.Row {
	for _, r := range v.Rows("Jobs") {
		if r.Ask {
			r := r
			return &r
		}
	}
	return nil
}

// leftColumn is the workspace in the mockup's short words (Workspace, Ports, Worktrees, Doctor, Memory), as many
// sections as fit, with the workspace pane's keys on its last row.
func leftColumn(st State, w, h int) []string {
	out := make([]string, 0, h)
	switch {
	case st.Workspace == nil && st.WorkspaceE != "":
		out = append(out, paneRule(st, "workspace", "Workspace", w))
		for _, l := range wrap("unavailable: "+st.WorkspaceE, w-2, "  ") {
			out = append(out, fit(" "+l, w))
		}
	case st.Workspace == nil:
		out = append(out, paneRule(st, "workspace", "Workspace", w), fit(" Loading workspace…", w))
	default:
		sections := st.Workspace.Brief
		if len(sections) == 0 {
			sections = st.Workspace.Sections // a server older than the short form
		}
		for i, sec := range sections {
			if i > 0 {
				out = append(out, fit("", w))
			}
			if i == 0 {
				out = append(out, paneRule(st, "workspace", sanitize(sec.Title), w))
			} else {
				out = append(out, ruleS(st, sanitize(sec.Title), w))
			}
			for _, l := range sec.Lines {
				out = append(out, briefLine(st, l, w))
			}
		}
	}
	if len(out) > h-1 {
		out = out[:h-1]
	}
	for len(out) < h-1 {
		out = append(out, fit("", w))
	}
	hint := fit(" u up  x shell  d doctor", w)
	if st.Styled {
		hint = muted(hint)
	}
	return append(out, hint)[:h]
}

// briefLine is one workspace line in its tone; a worktree's "● writer" is blue on its own, as in the mockup.
func briefLine(st State, l view.Line, w int) string {
	text := fit(" "+sanitize(l.Text), w)
	if st.Styled && l.Tone == "" {
		if i := strings.Index(text, "● writer"); i >= 0 {
			return text[:i] + tone("active").Render("● writer") + text[i+len("● writer"):]
		}
	}
	return tonedLine(l.Tone, text, st.Styled)
}

// paneRule is a column's title rule; the pane that has the keyboard has its title lit.
func paneRule(st State, pane, title string, w int) string {
	if st.Focus == pane && st.Styled {
		return lipgloss.NewStyle().Foreground(lipgloss.Color(colAccent)).Bold(true).Render(rule(title, w))
	}
	return ruleS(st, title, w)
}

// chatPlaceholder is the ask box while nothing is typed.
const chatPlaceholder = "Ask the parent, or /queue to park a task"

// midColumn is the parent: its own terminal while one runs (p), else the conversation, as the mockup shows it.
func midColumn(v *view.View, st State, w, h int) []string {
	if st.ChatOn && !st.PaneRunning {
		return chatColumn(v, st, w, h)
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
	out := []string{paneRule(st, "jobs", rangeText("Jobs", jobs, off, sel, cap, eh), w)}
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
	if hidden > 0 { // the mockup keeps "↓ N more below" on the column's last row
		more := fit(fmt.Sprintf("  ↓ %d more below", hidden), w)
		if st.Styled {
			more = muted(more)
		}
		out[h-1] = more
	}
	return out[:h]
}

// chatColumn is the conversation: the transcript, then the ask box, which has the keyboard unless Tab moved it.
// Typing / lists the commands above the box; /parent (or a project with no parent yet) shows the parent picker.
func chatColumn(v *view.View, st State, w, h int) []string {
	title := "Parent"
	parent := st.ChatParent
	if st.Chat != nil {
		parent = st.Chat.Parent
	}
	if parent != "" {
		title += " · " + clean(parent)
		if st.ChatModel != "" && parent == st.ChatParent {
			title += " " + clean(st.ChatModel) // the mockup's "Parent · claude opus"
		}
	}
	if st.Chat != nil && st.Chat.State == "running" {
		title += " · working"
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
		if ask := askLines(v, st, w); len(ask) > 0 {
			lines = append(append(lines, ""), ask...)
		}
	default:
		lines = append(chatAll(st, w), askLines(v, st, w)...)
	}
	view := lastLines(lines, st.ChatUp, w, body)
	if menu := slashMenu(st, w); typingChat(st) && len(menu) > 0 && st.Picker == nil {
		n := min(len(menu), body)
		copy(view[body-n:], menu[len(menu)-n:])
	}
	out = append(out, view...)
	return append(out, askBox(st, w)...)[:h]
}

// askBoxRows is the height of the ask box: the mockup's rule and its "›" line.
const askBoxRows = 2

// askBox is the mockup's input: a rule, then "› Ask the parent, or /queue to park a task▏". The cursor shows while
// the box has the keyboard.
func askBox(st State, w int) []string {
	sep := " " + strings.Repeat("─", max(0, w-2)) + " "
	if st.Styled {
		sep = border(sep)
	}
	var text string
	switch {
	case typingChat(st) && st.Prompt != "":
		text = " › " + st.Prompt + "▏"
		if lipgloss.Width(text) > w { // keep the cursor end in view while typing a long message
			r := []rune(st.Prompt)
			text = " › …" + string(r[max(0, len(r)-(w-5)):]) + "▏"
		}
		text = fit(text, w)
	case typingChat(st):
		text = fit(" › "+chatPlaceholder+"▏", w)
		if st.Styled {
			text = muted(text)
		}
	case st.Picker != nil:
		text = fit(" › choose the parent above", w)
		if st.Styled {
			text = muted(text)
		}
	default:
		text = fit(" › "+chatPlaceholder, w)
		if st.Styled {
			text = muted(text)
		}
	}
	return []string{fit(sep, w), text}
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

// askLines is the mockup's request block: who asks to run what, in amber, where, and the keys that answer it. The
// parent's own request comes first; else the first job waiting on one, as the mockup shows job-7f2's. A parent whose
// headless mode cannot ask says so once, under its first answer.
func askLines(v *view.View, st State, w int) []string {
	paint := func(s string) string {
		if st.Styled {
			return tone("attention").Render(s)
		}
		return s
	}
	grey := func(s string) string {
		if st.Styled {
			return muted(s)
		}
		return s
	}
	var block []string
	who, what, where := "", "", ""
	switch {
	case st.Chat != nil && st.Chat.Ask != nil:
		who, what = "asks to run:", clean(st.Chat.Ask.Preview)
	case waitingJob(v) != nil:
		job := waitingJob(v)
		who, what = clean(job.ID)+" asks to run:", strings.TrimPrefix(clean(job.Second), "permission: ")
	}
	if who != "" {
		if st.Workspace != nil && st.Workspace.Context != "" {
			parts := strings.Fields(st.Workspace.Context)
			where = "in container " + clean(parts[len(parts)-1])
		}
		out := []string{grey(" parent › ") + paint(who)}
		for _, l := range wrap(what, max(8, w-5), "") {
			out = append(out, paint("   "+l))
		}
		if where != "" {
			out = append(out, grey("   "+where))
		}
		block = append(out, grey("   a allow  d deny  m message"), "")
	}
	if st.Chat != nil && !st.Chat.Asks && len(st.Chat.Entries) > 0 {
		note := " " + clean(st.Chat.Parent) + " cannot ask for permission headless: it follows its own settings. p opens its own screen."
		for _, l := range wrap(note, max(8, w-2), "  ") {
			block = append(block, " "+grey(l))
		}
	}
	return block
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

// withID writes a job's second line the mockup's way: "claude · job-7f2 · ASK" for a request, the worker, the id and
// what it is doing for an active job, and the worker with a short outcome for a finished one ("opencode · finished 4m").
func withID(rows []view.Row) []view.Row {
	out := make([]view.Row, len(rows))
	for i, r := range rows {
		out[i] = r
		worker, lead := r.Worker, r.Worker+" · "
		if worker == "" {
			worker = "parent"
		}
		switch {
		case r.Ask:
			out[i].Second = worker + " · " + r.ID + " · ASK"
		case strings.Contains(finishedLabels, r.Label) && r.Label != "":
			out[i].Second = worker + " · " + briefOutcome(strings.TrimPrefix(r.Second, lead))
		case r.ID == "" || strings.Contains(r.Second, r.ID):
		case r.Worker == "":
			out[i].Second = r.ID + " · " + r.Second
		case strings.HasPrefix(r.Second, lead):
			out[i].Second = lead + r.ID + " · " + strings.TrimPrefix(r.Second, lead)
		case r.Second == r.Worker || r.Second == "":
			out[i].Second = lead + r.ID
		default: // an active job's second line is what it is doing now
			out[i].Second = lead + r.ID + " · " + r.Second
		}
	}
	return out
}

// briefOutcome is a finished job's outcome in a few words: "Stop confirmed; the worker..." is "stop confirmed".
func briefOutcome(text string) string {
	text = strings.TrimSpace(text)
	if i := strings.IndexAny(text, ";."); i > 0 {
		text = text[:i]
	}
	text = strings.TrimSuffix(strings.TrimSuffix(text, " ago"), " failed")
	text = strings.Replace(text, " ago", "", 1)
	if strings.HasPrefix(text, "Parent accepted") {
		text = "accepted"
	}
	if r := []rune(text); len(r) > 0 {
		text = strings.ToLower(string(r[0])) + string(r[1:])
	}
	return text
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
		label := "  " + fmt.Sprintf("%-10s", e[0])
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
