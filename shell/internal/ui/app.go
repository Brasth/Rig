package ui

import (
	"fmt"
	"regexp"
	"strings"

	"github.com/charmbracelet/lipgloss"
	"github.com/charmbracelet/x/ansi"

	"github.com/Brasth/Rig/shell/internal/view"
)

// The app is the design's "ADE screens" terminal UI, rig shell's default: one centred conversation with the parent,
// the agents panel on Tab, Ctrl-K commands, the changes and code tabs, and the first-run screens. The board (the
// plan's ADE mockup) is still there behind `rig shell --layout board`.

// AppState is the app's own state; nil means the board is showing.
type AppState struct {
	Repo, Branch string
	Tab          int  // 1 conversation, 2 changes, 3 code
	Panel        bool // the agents panel (Tab)
	PanelSel     int  // the selected agent in the panel
	Palette      *Palette
	Welcome      bool // the "Who do you want to talk to?" screen
	WelcomeSel   int
	NewJob       *view.Row // a helper the parent has just started, shown once
	Help         bool
	AnswerDeny   bool     // the answer chip chosen with ← →: deny, else allow
	ConfirmLead  string   // a question u or h asked: confirmed with ⏎; the letter typed into the reply box, or "\x00"
	Thread       string   // the job whose thread (its log) is open, from the panel
	Files        []string // the repository's tracked files, for Ctrl-K
	File         string   // the file the code tab shows
	FileLines    []string
	FileErr      string
	FileOff      int
	Preview      bool // the file is previewed (markdown rendered, a csv as a table) instead of shown as source
	Selecting    bool // v: lines are being selected to ask the parent about
	SelFrom      int
	SelTo        int
	Finding      bool // /: typing a word to find in the file
	Find         string
	Changed      map[string]string // the files each active helper is changing, by path, to its worker
	Diff         *view.JobDiff     // the changes tab's job, its diff
	DiffID       string
	DiffFile     int
	DiffOff      int
	Comments     map[string][]string // what you said to each helper from the changes tab
	Commenting   bool
	Search       *SearchState
	Settings     *SettingsScreen
	Onboard      *Onboarding // the first-run steps after the parent: helpers, then where agents work
	FirstRun     bool        // the welcome screen opened by itself: the other steps follow it
	InstallSel   int         // the no-agents screen's choice
	ProblemSeen  string      // a problem screen you closed with esc, not shown again for the same problem
	History      *HistoryState
	ShipScreen   *ShipState
}

// Palette is Ctrl-K: ask the parent, open a file, or run a command.
type Palette struct {
	Query string
	Sel   int
}

// paletteItem is one row of the palette.
type paletteItem struct {
	Section, Label, Detail, Action string
}

const appColumn = 84 // the conversation's width in the design

// confirmLine is a question waiting on y or n; one u or h asked from the reply box is answered with ⏎ or esc.
func confirmLine(st State, w int) string {
	yes, no := "y", "n"
	if st.App != nil && st.App.ConfirmLead != "" {
		yes, no = "⏎", "esc"
	}
	return center(st.paint(colAmber, st.Prompt)+"   "+st.dim(yes)+" "+st.faint("yes")+"    "+st.dim(no)+" "+st.faint("no"), w)
}

// AppRightKeys are the right half of the design's footer on the conversation, changes and code screens.
var AppRightKeys = [][2]string{{"u", "undo"}, {"h", "hold"}, {"⌃p", "files"}, {"?", "help"}}

var (
	mdCode = regexp.MustCompile("`([^`]+)`")
	mdBold = regexp.MustCompile(`\*\*([^*]+)\*\*`)
)

// plainMarkdown drops the inline markers a parent writes (code and bold), which a terminal line would show raw.
func plainMarkdown(s string) string {
	return mdBold.ReplaceAllString(mdCode.ReplaceAllString(s, "$1"), "$1")
}

var agentColors = map[string]string{"claude": "#6d4aff", "codex": "#0d9488", "opencode": "#db2777", "cursor": "#2563eb",
	"pi": "#4d7c0f", "grok": "#c2410c", "omp": "#0891b2", "agy": "#ca8a04", "parent": "#6d4aff"}

func (st State) paint(c, s string) string {
	if !st.Styled {
		return s
	}
	return lipgloss.NewStyle().Foreground(lipgloss.Color(c)).Render(s)
}

func (st State) bold(s string) string {
	if !st.Styled {
		return s
	}
	return lipgloss.NewStyle().Foreground(lipgloss.Color(colText)).Bold(true).Render(s)
}

func (st State) faint(s string) string { return st.paint(colFaint, s) }
func (st State) dim(s string) string   { return st.paint(colMuted, s) }

// tile is an agent's mark, three cells wide: the design's logo tile ringed in the agent's colour where the terminal
// draws images (gfx.go), else its fallback, "the coloured letter tile": the first letter on the agent's colour.
func (st State) tile(agent string) string {
	c, ok := agentColors[agent]
	if !ok {
		c = colAccent
	}
	letter := "r"
	if r := []rune(agent); len(r) > 0 && agent != "parent" {
		letter = string(r[0])
	}
	if !st.Styled {
		return "[" + letter + "]"
	}
	if mark := logoMark(agent); st.Logos && mark != "" {
		return mark
	}
	return lipgloss.NewStyle().Background(lipgloss.Color(c)).Foreground(lipgloss.Color("#ffffff")).Bold(true).Render(" " + letter + " ")
}

// center puts s in the middle of w cells.
func center(s string, w int) string {
	pad := max(0, (w-lipgloss.Width(s))/2)
	return fit(strings.Repeat(" ", pad)+s, w)
}

// spread lays out a left, centre and right part on one line of w cells.
func spread(left, mid, right string, w int) string {
	lw, mw, rw := lipgloss.Width(left), lipgloss.Width(mid), lipgloss.Width(right)
	start := max(lw+2, (w-mw)/2)
	if start+mw+rw+2 > w {
		mid, mw = "", 0
		start = lw
	}
	line := left + strings.Repeat(" ", max(0, start-lw)) + mid
	gap := w - lipgloss.Width(line) - rw
	if gap < 1 {
		return fit(line, w)
	}
	return line + strings.Repeat(" ", gap) + right
}

// keyHints is the design's footer: keys in the darker grey, their meaning in the faint one, centred, four apart.
func keyHints(st State, pairs [][2]string, w int) string {
	parts := make([]string, 0, len(pairs))
	for _, p := range pairs {
		parts = append(parts, st.dim(p[0])+" "+st.faint(p[1]))
	}
	return center(strings.Join(parts, "    "), w)
}

// roundBox draws lines inside a rounded border w cells wide; lit uses the text colour, as the design's focused box.
func roundBox(st State, lines []string, w int, lit bool) []string {
	edge := colBorder
	if lit {
		edge = colText
	}
	p := func(s string) string { return st.paint(edge, s) }
	out := []string{p("╭" + strings.Repeat("─", w-2) + "╮")}
	for _, l := range lines {
		out = append(out, p("│")+fit(l, w-2)+p("│"))
	}
	return append(out, p("╰"+strings.Repeat("─", w-2)+"╯"))
}

// overlay draws box over base at column x, row y; the base shows around it.
func overlay(base, box []string, x, y int) {
	for i, line := range box {
		if y+i < 0 || y+i >= len(base) {
			continue
		}
		row := base[y+i]
		bw := lipgloss.Width(line)
		base[y+i] = ansi.Truncate(row, x, "") + "\x1b[0m" + line + "\x1b[0m" + ansi.TruncateLeft(row, x+bw, "")
	}
}

// indentAll puts every line of a column at margin x in a w-wide body.
func indentAll(lines []string, x, w int) []string {
	out := make([]string, len(lines))
	for i, l := range lines {
		out[i] = fit(strings.Repeat(" ", x)+l, w)
	}
	return out
}

func appProject(v *view.View, a *AppState) string {
	if v != nil && v.Header.Project != "" {
		return v.Header.Project
	}
	parts := strings.Split(strings.TrimRight(a.Repo, "/"), "/")
	return parts[len(parts)-1]
}

func appParent(st State) string {
	if st.Chat != nil && st.Chat.Parent != "" {
		return st.Chat.Parent
	}
	if st.ChatParent != "" {
		return st.ChatParent
	}
	return "parent"
}

// activeJobs are the helpers at work; doneJobs the finished ones the parent accepted today.
func activeJobs(v *view.View) []view.Row {
	var out []view.Row
	for _, r := range v.Rows("Jobs") {
		if !strings.Contains(finishedLabels, r.Label) || r.Label == "" {
			out = append(out, r)
		}
	}
	return out
}

func doneJobs(v *view.View) []view.Row {
	var out []view.Row
	for _, r := range v.Rows("Jobs") {
		if r.Label == "Verified" {
			out = append(out, r)
		}
	}
	return out
}

// AppFrame renders the app at w by h.
func AppFrame(v *view.View, st State, w, h int) []string {
	a := st.App
	if w < 60 || h < 16 {
		lines := make([]string, h)
		for i := range lines {
			lines[i] = fit("", w)
		}
		lines[0] = fit(" rig · window too small (need 60×16); q quits", w)
		return lines
	}
	confirm := func(lines []string) []string {
		switch {
		case st.Mode == ModeConfirm:
			lines[len(lines)-1] = confirmLine(st, w)
		case st.Message != "" && problem(st.Message):
			lines[len(lines)-1] = center(st.paint(colAmber, clean(st.Message)), w)
		}
		return lines
	}
	if a.Welcome {
		return confirm(welcomeScreen(st, w, h))
	}
	if a.Onboard != nil && a.Onboard.Step == 2 {
		return confirm(helpersScreen(st, w, h))
	}
	if a.Onboard != nil && a.Onboard.Step == 3 {
		return confirm(sandboxScreen(st, w, h))
	}
	switch problemScreen(st) {
	case "noagents":
		return confirm(noAgentsScreen(st, w, h))
	case "signedout":
		return confirm(parentLostScreen(v, st, w, h))
	case "sandbox":
		return confirm(sandboxProblemScreen(v, st, w, h))
	}
	if a.History != nil {
		return confirm(historyScreen(st, w, h))
	}
	if a.ShipScreen != nil {
		return confirm(shipScreen(st, w, h))
	}
	if a.Settings != nil {
		lines := settingsScreenLines(v, st, w, h-1)
		keys := [][2]string{{"←→", "change"}, {"⇥", "section"}, {"g", "all repos / this repo"}, {"esc", "back"}}
		if SettingsSections[a.Settings.Section] == "toml" {
			keys = [][2]string{{"E", "edit in $EDITOR"}, {"⇥", "section"}, {"esc", "back"}}
		}
		switch {
		case st.Mode == ModeConfirm:
			lines = append(lines, confirmLine(st, w))
		case st.Message != "" && problem(st.Message):
			lines = append(lines, center(st.paint(colAmber, clean(st.Message)), w))
		default:
			lines = append(lines, keyHints(st, keys, w))
		}
		return lines
	}
	lines := []string{topBar(v, st, w), st.paint(colBorder, strings.Repeat("─", w))}
	// The conversation, changes and code screens end in the design's full-width footer under a rule.
	ruled := a.Search == nil && a.Thread == "" && (a.Tab == 2 || a.Tab == 3 || st.Chat != nil || st.ChatE != "")
	body := h - 3
	if ruled {
		body = h - 4
	}
	var main []string
	var keys [][2]string
	footer := ""
	switch {
	case a.Search != nil:
		main, footer = searchScreen(st, w, body)
	case a.Thread != "":
		main, keys = threadBody(st, w, body)
	case a.Tab == 2:
		main, footer = changesScreen(v, st, w, body)
	case a.Tab == 3:
		main, footer = codeScreen(st, w, body)
	case st.Chat == nil && st.ChatE == "":
		main, keys = emptyBody(v, st, w, body)
	default:
		main, keys = conversationBody(v, st, w, body)
		footer = splitHints(st, keys, AppRightKeys, w)
	}
	if a.Panel {
		overlay(main, agentsPanel(v, st, min(46, w/2), body), w-min(46, w/2), 0)
		keys = [][2]string{{"↑↓", "agent"}, {"⏎", "watch thread"}, {"s", "stop"}, {"⇥", "close"}}
	}
	if a.Palette != nil {
		pw := min(70, w-8)
		overlay(main, paletteBox(v, st, pw), (w-pw)/2, 2)
		keys = [][2]string{{"↑↓", "move"}, {"⏎", "open"}, {"esc", "close"}}
	}
	if a.NewJob != nil && a.Palette == nil {
		dw := min(76, w-8)
		overlay(main, newJobBox(st, *a.NewJob, dw), (w-dw)/2, 3)
		keys = [][2]string{{"⏎", "watch thread"}, {"t", "tell " + appParent(st)}, {"s", "stop"}, {"esc", "close"}}
	}
	if a.Help {
		hw := min(64, w-8)
		overlay(main, helpBox(st, hw), (w-hw)/2, 2)
		keys = [][2]string{{"esc", "close"}}
	}
	lines = append(lines, main...)
	if ruled {
		lines = append(lines, st.paint(colBorder, strings.Repeat("─", w)))
	}
	switch {
	case st.Mode == ModeConfirm:
		lines = append(lines, confirmLine(st, w))
	case st.Message != "" && problem(st.Message):
		lines = append(lines, center(st.paint(colAmber, clean(st.Message)), w))
	case footer != "" && !a.Panel && a.Palette == nil && a.NewJob == nil && !a.Help:
		lines = append(lines, footer)
	default:
		lines = append(lines, keyHints(st, keys, w))
	}
	for len(lines) < h {
		lines = append(lines, fit("", w))
	}
	return lines[:h]
}

// topBar is the design's: the mark, the repo and branch and the parent; the three tabs; and what is working.
func topBar(v *view.View, st State, w int) string {
	a := st.App
	logo := st.bold("r") + st.paint("#6d4aff", "•") + st.paint("#0d9488", "•") + st.paint("#db2777", "•")
	left := "   " + logo + "   " + st.bold(appProject(v, a))
	if a.Branch != "" {
		branch := a.Branch
		if r := []rune(branch); len(r) > 18 {
			branch = string(r[:17]) + "…"
		}
		left += st.faint(" / " + branch)
	}
	left += "   " + st.tile(appParent(st)) + " " + appParent(st)
	tab := func(n int, name, extra string) string {
		label := st.faint(fmt.Sprint(n)) + " " + name
		if a.Tab == n && st.Styled {
			label = st.faint(fmt.Sprint(n)) + " " + lipgloss.NewStyle().Foreground(lipgloss.Color(colText)).Bold(true).Underline(true).Render(name)
		} else if a.Tab == n {
			label = fmt.Sprint(n) + " [" + name + "]"
		} else {
			label = st.faint(fmt.Sprint(n)) + " " + st.dim(name)
		}
		return label + extra
	}
	changes := ""
	if n := len(doneJobs(v)) + countLabel(v, "Unverified"); n > 0 {
		changes = " " + st.paint(colGreen, fmt.Sprint(n))
	}
	mid := tab(1, "conversation", "") + "     " + tab(2, "changes", changes) + "     " + tab(3, "code", "")
	working := len(activeJobs(v))
	right := st.dim("⌃k") + "   " + st.dim("⇥") + " " + st.faint("panel") + "   "
	if working > 0 {
		right = st.paint(colAccent, "●") + " " + st.dim(fmt.Sprintf("%d working", working)) + "   " + right
	}
	return spread(left, mid, right, w)
}

func countLabel(v *view.View, label string) int {
	n := 0
	for _, r := range v.Rows("Jobs") {
		if r.Label == label {
			n++
		}
	}
	return n
}

// replyBox is the design's rounded reply box with the draft or the placeholder.
func replyBox(st State, w int, placeholder string, lit bool) []string {
	text := " " + st.faint("›") + " "
	if st.Prompt != "" {
		draft := st.Prompt + "▏"
		if lipgloss.Width(draft) > w-6 {
			r := []rune(st.Prompt)
			draft = "…" + string(r[max(0, len(r)-(w-8)):]) + "▏"
		}
		text += draft
	} else {
		text += "▏" + st.faint(placeholder)
	}
	return roundBox(st, []string{text}, w, lit)
}

// conversationBody is the design's main screen: what the parent is working on, the conversation, the change cards,
// the questions waiting on you, and the reply box.
func conversationBody(v *view.View, st State, w, h int) ([]string, [][2]string) {
	cw := min(appColumn, w-8)
	x := (w - cw) / 2
	parent := appParent(st)
	var lines []string
	lines = append(lines, "")
	title, state := "Conversation", "ready"
	for _, e := range st.Chat.Entries {
		if e.Role == "user" {
			title = clean(e.Text)
			break
		}
	}
	if st.Chat.State == "running" {
		state = "working"
	}
	lines = append(lines, st.tile(parent)+"  "+st.bold(fit(title, cw-5)))
	lines = append(lines, "     "+st.faint(parent+" · "+state))
	lines = append(lines, "", "")
	entries := st.Chat.Entries
	if st.Chat.State == "running" && strings.TrimSpace(st.Chat.Partial) != "" {
		entries = append(append([]view.ChatEntry(nil), entries...), view.ChatEntry{Role: "assistant", Text: st.Chat.Partial})
	}
	for _, e := range entries {
		switch e.Role {
		case "user":
			lines = append(lines, bubble(st, e.Text, cw)...)
		case "assistant":
			for _, raw := range strings.Split(strings.TrimRight(e.Text, "\n"), "\n") {
				for _, l := range wrap(plainMarkdown(sanitize(raw)), cw, "") {
					lines = append(lines, st.dim(l))
				}
			}
		case "tool":
			lines = append(lines, st.faint("  · "+fit(clean(e.Text), cw-4)))
			continue
		case "error":
			for _, l := range wrap(e.Text, cw-2, "") {
				lines = append(lines, st.paint(colAmber, "! "+l))
			}
		default:
			lines = append(lines, st.faint("  "+fit(clean(e.Text), cw-2)))
			continue
		}
		lines = append(lines, "")
	}
	for _, r := range appliedCards(v, st) {
		lines = append(lines, changeCard(st, r, cw)...)
		lines = append(lines, "")
	}
	var keys [][2]string
	if len(appliedCards(v, st)) > 0 {
		keys = append(keys, [2]string{"d", "see changes"})
	}
	if q := questionLines(v, st, cw); len(q) > 0 {
		lines = append(lines, q...)
		keys = append(keys, [2]string{"← →", "pick answer"})
	}
	keys = append(keys, [2]string{"⏎", "send"})
	if st.Chat.State == "running" && st.Chat.Ask == nil {
		lines = append(lines, workingLine(st))
	}
	if st.Prompt != "" {
		keys = [][2]string{{"⏎", "send"}, {"esc", "clear"}, {"⇥", "agents"}, {"⌃k", "commands"}}
	}
	box := replyBox(st, cw, "Reply to "+parent, false)
	if menu := slashMenu(st, cw); len(menu) > 0 {
		box = append(menu, box...)
		keys = [][2]string{{"↑↓", "choose"}, {"⇥", "complete"}, {"⏎", "run"}, {"esc", "clear"}}
	}
	room := h - len(box) - 1
	shown := lastLines(lines, st.ChatUp, cw, room)
	out := indentAll(append(append(shown, box...), ""), x, w)
	return out[:h], keys
}

// bubble is one of your messages: right-aligned on the raised surface, as the design draws it.
func bubble(st State, text string, cw int) []string {
	bw := min(cw*2/3, 60)
	rows := []string{}
	for _, raw := range strings.Split(strings.TrimRight(text, "\n"), "\n") {
		rows = append(rows, wrap(sanitize(raw), bw-4, "")...)
	}
	widest := 0
	for _, r := range rows {
		widest = max(widest, lipgloss.Width(r))
	}
	out := make([]string, 0, len(rows))
	for _, r := range rows {
		cell := "  " + fit(r, widest) + "  "
		if st.Styled {
			cell = lipgloss.NewStyle().Background(lipgloss.Color(colSelBg)).Foreground(lipgloss.Color(colText)).Render(cell)
		}
		out = append(out, strings.Repeat(" ", max(0, cw-widest-4))+cell)
	}
	return out
}

// changeCard is the design's card for work a helper finished and the parent accepted: what it changed and how it was
// checked (from done today), then which job it was, with the keys that see and undo it.
func changeCard(st State, r view.Row, cw int) []string {
	head := "  " + st.bold(fit(clean(r.Title), cw-24))
	right := st.paint(colGreen, "● checked") + "  "
	if st.Today != nil {
		for _, t := range st.Today.Rows {
			if t.JobID != r.ID || t.State != "applied" {
				continue
			}
			head = "  " + st.bold(fmt.Sprintf("%d files changed", t.Files)) + "  " + st.paint(colGreen, fmt.Sprintf("+%d", t.Added)) + " " +
				st.paint(colRed, fmt.Sprintf("−%d", t.Deleted))
			passed := len(t.Checks) > 0
			for _, c := range t.Checks {
				passed = passed && c.OK
			}
			switch {
			case passed:
				right = st.paint(colGreen, "● checks pass") + "  "
			case len(t.Checks) > 0:
				right = st.paint(colRed, "● a check failed") + "  "
			}
		}
	}
	line1 := head + strings.Repeat(" ", max(1, cw-2-lipgloss.Width(head)-lipgloss.Width(right))) + right
	about := clean(r.Title)
	if r.Second != "" {
		about += " · " + clean(r.Second)
	}
	hints := "2 changes   u undo"
	line2 := "  " + st.paint(colAccent, "●") + " " + st.dim(fit(about, max(8, cw-12-len(hints)))) + "     " + st.faint(hints)
	return roundBox(st, []string{line1, "", line2}, cw, false)
}

// questionLines is what waits on you: the parent's own request, else a helper's, with the answer chips.
func questionLines(v *view.View, st State, cw int) []string {
	parent := appParent(st)
	who, what := "", ""
	switch {
	case st.Chat != nil && st.Chat.Ask != nil:
		who, what = st.tile(parent)+"  "+st.faint(parent+" asks to run"), clean(st.Chat.Ask.Preview)
	case waitingJob(v) != nil:
		job := waitingJob(v)
		worker := job.Worker
		who = st.tile(worker) + "  " + st.faint(worker+" asks, via "+parent)
		what = strings.TrimPrefix(clean(job.Second), "permission: ")
	default:
		return nil
	}
	out := []string{"", who}
	for _, l := range wrap(what, cw, "") {
		out = append(out, st.dim(l))
	}
	chip := func(label string, on bool) string {
		if on && st.Styled {
			return lipgloss.NewStyle().Background(lipgloss.Color(colText)).Foreground(lipgloss.Color(colGround)).Bold(true).Render(" " + label + " ")
		}
		if on {
			return "[" + label + "]"
		}
		return " " + label + " "
	}
	deny := st.App != nil && st.App.AnswerDeny
	return append(out, "", chip("allow", !deny)+"   "+chip("deny", deny)+"    "+st.faint("or type below"), "")
}

// emptyBody is the design's first conversation: what should we work on, the box, and four suggestions.
func emptyBody(v *view.View, st State, w, h int) ([]string, [][2]string) {
	cw := min(70, w-8)
	x := (w - cw) / 2
	parent := appParent(st)
	repo := appProject(v, st.App)
	lines := []string{"", "", ""}
	lines = append(lines, st.tile(parent)+"  "+st.bold("What should we work on in "+repo+"?"))
	lines = append(lines, st.dim(parent+" plans, starts helpers when needed, checks and applies."), "")
	text := " " + st.faint("›") + " "
	if st.Prompt != "" {
		text += st.Prompt + "▏"
	} else {
		text += "▏" + st.faint("ask "+parent+" anything about "+repo)
	}
	lines = append(lines, roundBox(st, []string{text}, cw, true)...)
	if menu := slashMenu(st, cw); len(menu) > 0 {
		lines = append(lines, menu...)
	}
	lines = append(lines, "", "")
	for i, s := range Suggestions {
		lines = append(lines, st.dim(fmt.Sprint(i+1))+"  "+s)
	}
	lines = append(lines, "", "")
	if st.Workspace != nil && st.Workspace.Context != "" {
		helpers := len(activeJobs(v))
		note := "no helpers yet"
		if helpers > 0 {
			note = fmt.Sprintf("%d helpers working", helpers)
		}
		lines = append(lines, st.paint(colGreen, "●")+" "+st.faint("sandbox "+lastWord(st.Workspace.Context)+" · "+note))
	}
	for len(lines) < h {
		lines = append(lines, "")
	}
	keys := [][2]string{{"⏎", "send"}, {"1-4", "suggestion"}, {"⌃k", "commands"}, {"⇥", "agents"}, {"?", "more"}}
	return indentAll(lines[:h], x, w), keys
}

// Suggestions are the first conversation's starting points.
var Suggestions = []string{"explain this codebase", "find and fix the failing tests", "add a feature", "review the open changes"}

func lastWord(s string) string {
	f := strings.Fields(s)
	if len(f) == 0 {
		return ""
	}
	return f[len(f)-1]
}

// welcomeScreen is "Who do you want to talk to?": the installed parents, the chosen one boxed.
func welcomeScreen(st State, w, h int) []string {
	a := st.App
	logo := st.bold("r") + st.paint("#6d4aff", "•") + st.paint("#0d9488", "•") + st.paint("#db2777", "•")
	right := ""
	if a.FirstRun {
		right = stepDots(st, 1)
	}
	lines := []string{spread("   "+logo+"   "+st.bold("rig"), "", right, w), fit("", w)}
	cw := min(60, w-8)
	body := []string{"", "", st.bold("Who do you want to talk to?"), st.dim("It plans, hands work to helpers and checks it."), "", ""}
	items := WelcomeItems(st)
	for i, it := range items {
		if !it.Installed {
			continue
		}
		row := "   " + st.tile(it.Name) + "  " + it.Name
		if i == a.WelcomeSel {
			row = " " + st.bold("›") + " " + st.tile(it.Name) + "  " + st.bold(it.Name)
			if it.Name == st.ChatParent {
				row += "  " + st.faint("in use")
			}
			body = append(body, roundBox(st, []string{row}, cw, true)...)
		} else {
			body = append(body, row)
		}
		body = append(body, "")
	}
	var missing []string
	for _, it := range items {
		if !it.Installed {
			missing = append(missing, it.Name)
		}
	}
	if len(missing) > 0 {
		body = append(body, "", "   "+st.faint(strings.Join(missing, ", ")+" not installed"))
	}
	lines = append(lines, indentAll(body, (w-cw)/2, w)...)
	for len(lines) < h-1 {
		lines = append(lines, fit("", w))
	}
	lines = append(lines[:h-1], keyHints(st, [][2]string{{"↑↓", "choose"}, {"⏎", "continue"}, {"esc", "skip"}}, w))
	return lines
}

// WelcomeItems are the parents the welcome screen offers, installed first.
func WelcomeItems(st State) []PickItem {
	if st.Picker != nil {
		return st.Picker.Items
	}
	return nil
}

// agentsPanel is the design's panel on Tab: done today, the agents at work, the queue, and the sandbox.
func agentsPanel(v *view.View, st State, pw, h int) []string {
	iw := pw - 5
	parent := appParent(st)
	head := func(title, right string) string {
		t := st.bold(title)
		return t + strings.Repeat(" ", max(1, iw-lipgloss.Width(t)-lipgloss.Width(right))) + st.faint(right)
	}
	lines := []string{""}
	done := appliedCards(v, st)
	lines = append(lines, head("Done today", fmt.Sprintf("%d · all ›", len(done))))
	if len(done) == 0 {
		lines = append(lines, st.faint("  nothing applied yet"))
	}
	for i, r := range done {
		if i == 3 {
			break
		}
		undo := ""
		if i == 0 && st.Today != nil {
			undo = st.dim("u") + " " + st.faint("undo")
		}
		top := " " + st.tile(r.Worker) + "  " + fit(clean(r.Title), iw-12)
		lines = append(lines, top+strings.Repeat(" ", max(1, iw-lipgloss.Width(top)-lipgloss.Width(undo)))+undo)
		lines = append(lines, "      "+st.paint(colGreen, fit(r.Second, iw-6)))
	}
	lines = append(lines, "", head("Agents", "started by "+parent))
	parentRow := " " + st.tile(parent) + "  " + st.bold(parent) + "  " + st.faint("parent · in the conversation")
	if st.Styled {
		parentRow = lipgloss.NewStyle().Background(lipgloss.Color(colSelBg)).Render(fit(parentRow, iw))
	}
	lines = append(lines, parentRow)
	jobs := activeJobs(v)
	sel := clampSel(st.App.PanelSel, len(jobs))
	for i, r := range jobs {
		worker := r.Worker
		if worker == "" {
			worker = "helper"
		}
		top := " " + st.tile(worker) + "  " + worker + "  " + st.faint(fit(clean(r.Title), max(4, iw-lipgloss.Width(worker)-12)))
		top = fit(top, iw-4) + st.faint("  › ")
		activity := "      " + st.paint(colAccent, fit(strings.TrimPrefix(clean(r.Second), "permission: "), iw-8))
		if r.Ask {
			activity = "      " + st.paint(colAmber, fit("asks: "+strings.TrimPrefix(clean(r.Second), "permission: "), iw-8))
		}
		if i == sel {
			lines = append(lines, roundBox(st, []string{top, activity}, iw, true)...)
		} else {
			lines = append(lines, top, activity)
		}
	}
	if len(jobs) == 0 {
		lines = append(lines, st.faint("  no helpers at work"))
	}
	queue := v.Rows("Queue")
	lines = append(lines, "", head("Queue", parent+" picks next"))
	for i, q := range queue {
		if i == 4 {
			lines = append(lines, st.faint(fmt.Sprintf("  +%d more", len(queue)-4)))
			break
		}
		lines = append(lines, st.dim(fmt.Sprintf("  %d  %s", i+1, fit(clean(q.Title), iw-5))))
	}
	if len(queue) == 0 {
		lines = append(lines, st.faint("  empty"))
	}
	foot := []string{}
	if st.Workspace != nil && st.Workspace.Context != "" {
		foot = append(foot, st.paint(colGreen, "●")+" "+st.faint("sandbox "+lastWord(st.Workspace.Context)))
	}
	room := h - len(foot)
	if len(lines) > room {
		lines = lines[:room]
	}
	for len(lines) < room {
		lines = append(lines, "")
	}
	lines = append(lines, foot...)
	edge := st.paint(colBorder, "│")
	out := make([]string, len(lines))
	for i, l := range lines {
		out[i] = edge + "  " + fit(l, iw) + "  "
	}
	return out
}

// paletteItems are Ctrl-K's rows for a query: ask the parent, matching files, matching commands.
func paletteItems(st State) []paletteItem {
	q := strings.ToLower(strings.TrimSpace(st.App.Palette.Query))
	var out []paletteItem
	if q != "" {
		out = append(out, paletteItem{"ask", "ask " + appParent(st) + ": " + st.App.Palette.Query, "", "ask"})
		n := 0
		for _, f := range st.App.Files {
			if strings.Contains(strings.ToLower(f), q) {
				dir, name := "", f
				if i := strings.LastIndex(f, "/"); i >= 0 {
					dir, name = f[:i], f[i+1:]
				}
				out = append(out, paletteItem{"files", name, dir, "file:" + f})
				if n++; n == 4 {
					break
				}
			}
		}
	}
	for _, c := range PaletteCommands {
		if q == "" || strings.Contains(c[0], q) {
			out = append(out, paletteItem{"commands", c[0], c[1], c[2]})
		}
	}
	return out
}

// PaletteCommands are Ctrl-K's commands: label, detail, action.
var PaletteCommands = [][3]string{
	{"switch parent", "choose who you talk to", "parent"},
	{"new conversation", "start again with the parent", "new"},
	{"queue a task", "park it for the parent to pick", "queue"},
	{"change theme", "rig, light, dark, material, dracula", "theme"},
	{"search the code", "text in every file (⌃f)", "search"},
	{"done today", "what was applied, undo and redo", "today"},
	{"ship changes", "push, or open a pull request", "ship"},
	{"settings", "theme, agents, routing, sandbox, toml", "settings"},
	{"start the sandbox", "rig env up", "env-up"},
	{"stop a helper", "from the agents panel", "panel"},
	{"the board", "jobs, queue, workspace and evidence", "board"},
	{"help", "every key", "help"},
	{"quit", "leave rig shell", "quit"},
}

func paletteBox(v *view.View, st State, pw int) []string {
	iw := pw - 4
	text := "  " + st.faint("›") + " " + st.App.Palette.Query + "▏"
	if st.App.Palette.Query == "" {
		text = "  " + st.faint("›") + " ▏" + st.faint("search files and commands, or ask "+appParent(st))
	}
	lines := []string{text, ""}
	items := paletteItems(st)
	sel := clampSel(st.App.Palette.Sel, len(items))
	section := ""
	for i, it := range items {
		if i >= 12 {
			break
		}
		if it.Section != section {
			section = it.Section
			lines = append(lines, st.faint(section))
		}
		row := "   " + it.Label
		if it.Detail != "" {
			row += "  " + st.faint(it.Detail)
		}
		if it.Section == "ask" {
			row = " " + st.tile(appParent(st)) + " " + it.Label
		}
		if i == sel {
			row = fit(row, iw-4)
			if st.Styled {
				row = lipgloss.NewStyle().Background(lipgloss.Color(colSelBg)).Bold(true).Render(row)
			} else {
				row = ">" + row[1:]
			}
		}
		lines = append(lines, row)
	}
	return roundBox(st, indentAll(append([]string{""}, append(lines, "")...), 2, pw-4), pw, false)
}

// newJobBox is the design's "claude started codex" card: the task, where it runs, and how far it has got.
func newJobBox(st State, r view.Row, dw int) []string {
	parent := appParent(st)
	worker := r.Worker
	if worker == "" {
		worker = "helper"
	}
	iw := dw - 6
	lines := []string{"", st.tile(parent) + " " + st.faint("→") + " " + st.tile(worker) + "  " + st.bold(parent+" started "+worker) + "  " + st.faint("just now"), ""}
	for _, l := range wrap(clean(r.Title), iw-4, "") {
		cell := "  " + fit(l, iw-4) + "  "
		if st.Styled {
			cell = lipgloss.NewStyle().Background(lipgloss.Color(colSelBg)).Render(cell)
		}
		lines = append(lines, cell)
	}
	lines = append(lines, "")
	lines = append(lines, st.faint("where ")+"  worktree "+r.ID)
	if r.Second != "" {
		lines = append(lines, st.faint("now   ")+"  "+fit(clean(r.Second), iw-8))
	}
	lines = append(lines, "", st.paint(colGreen, "✓ admitted")+"  "+st.bold("● "+strings.ToLower(clean(r.Label)))+"  "+st.faint("○ checked  ○ applied"), "")
	box := roundBox(st, indentAll(lines, 2, iw+2), dw, false)
	return box
}

// helpBox lists the app's keys.
func helpBox(st State, hw int) []string {
	rows := [][2]string{
		{"type, ⏎", "talk to the parent"},
		{"⇥", "the agents panel: done today, helpers, the queue"},
		{"⌃k", "commands, files, or ask the parent"},
		{"1 2 3", "conversation, changes, code (with the box empty)"},
		{"/", "commands: /parent /queue /new /stop /theme /quit"},
		{"1 2", "allow or deny a request the conversation shows"},
		{"PgUp PgDn", "scroll the conversation"},
		{"esc", "close, or clear the box"},
		{"⌃c", "clear the box, or quit"},
	}
	lines := []string{"", st.bold("Keys"), ""}
	for _, r := range rows {
		lines = append(lines, st.dim(fmt.Sprintf("%-10s", r[0]))+" "+r[1])
	}
	return roundBox(st, indentAll(append(lines, ""), 2, hw-4), hw, false)
}

// threadBody is a helper's thread: its log, followed.
func threadBody(st State, w, h int) ([]string, [][2]string) {
	cw := min(100, w-8)
	lines := []string{"", st.bold("Thread · " + st.App.Thread), ""}
	switch {
	case st.Log == nil && st.LogE != "":
		lines = append(lines, st.paint(colAmber, "unavailable: "+clean(st.LogE)))
	case st.Log == nil:
		lines = append(lines, st.faint("loading…"))
	default:
		room := h - len(lines)
		all := strings.Split(strings.TrimRight(st.Log.Text, "\n"), "\n")
		if len(all) > room {
			all = all[len(all)-room:]
		}
		for _, l := range all {
			lines = append(lines, st.dim(fit(sanitize(l), cw)))
		}
	}
	for len(lines) < h {
		lines = append(lines, "")
	}
	return indentAll(lines[:h], (w-cw)/2, w), [][2]string{{"esc", "back"}, {"s", "stop"}, {"⇥", "agents"}}
}

// appliedCards are what was applied today, as rows for the change cards and the panel: from applies.today when the
// server has it, else the jobs the parent verified.
func appliedCards(v *view.View, st State) []view.Row {
	if st.Today == nil {
		var out []view.Row
		for _, r := range doneJobs(v) {
			r.Second = r.Worker + " · " + briefOutcome(strings.TrimPrefix(r.Second, r.Worker+" · "))
			out = append(out, r)
		}
		return out
	}
	var out []view.Row
	for _, r := range st.Today.Rows {
		if r.State != "applied" {
			continue
		}
		out = append(out, view.Row{ID: r.JobID, Title: r.Title, Worker: r.Worker, Label: "Verified",
			Second: fmt.Sprintf("%s · applied · %d files +%d −%d", r.Worker, r.Files, r.Added, r.Deleted)})
	}
	return out
}
