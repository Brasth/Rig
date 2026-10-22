package ui

import (
	"fmt"
	"path/filepath"
	"regexp"
	"strings"
	"time"

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
	Facts        string // what the first-conversation screen says about the repo: its stack, files, dev container
	// ImageCols and ImageRows are the cells a previewed picture was sent to the terminal for (gfx.go); 0 when it was not
	ImageCols, ImageRows int
	Tab                  int  // 1 conversation, 2 changes, 3 code
	Panel                bool // the agents panel (Tab)
	PanelSel             int  // the selected agent in the panel
	PanelHidden          bool // ⇥ hid the docked panel (160 columns and up)
	Palette              *Palette
	Welcome              bool // the "Who do you want to talk to?" screen
	JustStarted          bool // the parent just chosen on the first run: its note says it is ready
	WelcomeSel           int
	NewJob               *view.Row // a helper the parent has just started, shown once
	Help                 bool
	AnswerDeny           bool     // the answer chip chosen with ← →: deny, else allow
	ConfirmLead          string   // a question u or h asked: confirmed with ⏎; the letter typed into the reply box, or "\x00"
	Splash               int      // the splash's frame while rig shell opens; 0 once it is gone
	ChatMax, LogMax      int      // how far the conversation and a thread can scroll up, as last drawn
	chatKey              string   // what chatLines were made for
	chatLines            []string // the transcript's lines, kept between frames
	logKey               string   // and a thread's
	logLines             []string
	Thread               string   // the job whose thread (its log) is open, from the panel
	Files                []string // the repository's tracked files, for Ctrl-K
	File                 string   // the file the code tab shows
	FileLines            []string
	FileErr              string
	FileOff              int
	Preview              bool // the file is previewed (markdown rendered, a csv as a table) instead of shown as source
	Selecting            bool // v: lines are being selected to ask the parent about
	SelFrom              int
	SelTo                int
	Finding              bool // /: typing a word to find in the file
	Find                 string
	Changed              map[string]string       // the files each active helper is changing, by path, to its worker
	Added                map[string]bool         // of those, the ones new in the helper's copy
	ChangedLines         map[string]map[int]bool // and the lines of the file here each helper's change touches
	Tabs                 []string                // the code tab's open files
	TreeOpen             map[string]bool         // explorer folders opened or closed by hand
	TreeFocus            bool                    // ⌃b: the keys move in the explorer
	TreeSel              int
	TreeFilter           string
	TreeFiltering        bool
	Editing              bool // e: the file is being edited here
	Dirty                bool // it has changes not yet saved (⌃s)
	EdSel                bool // some of the file being edited is selected, from EdAnchor to the cursor
	EdAnchor             pos
	SavedID              string              // the version of FileLines last read or saved (linesID)
	Undo, Redo           []snapshot          // the edits, for ⌃z and ⌃y
	Bufs                 map[string]*codeBuf // other tabs' unsaved changes
	CodeAsk              *codeAsk            // go to line, find, a new file or a new name, asked on the code tab's last row
	GitView              bool                // the side bar shows git (g) instead of the files
	GitSel               int
	GitScope             []string // g after a turn: the git view shows only these files, the turn's (t shows all)
	Git                  *gitState
	GitDiff              *gitDiffView      // a file's diff, shown in place of the file
	Conflict             *conflictView     // a conflicted file's conflicts, each with its two sides, in place of the file
	Extra                []span            // more selections than the cursor's: ⌃d add next occurrence, F2 rename
	Term                 bool              // ⌃`: a shell in a split at the foot of the screen
	TermFocus            bool              // its keys go to the shell
	TermUp               int               // rows scrolled up into the split's scrollback
	Problems             *view.JobProblems // what the helpers' failed checks found, for the status bar
	renameOld, renameNew string            // F2: the names, for the question about the rest of the repository
	Outline              bool              // o: the file's symbols in the side bar instead of the files
	OutlineSel           int
	ChatFind             *chatFind  // ⌃/: find in the conversation
	Hover                *hoverInfo // the name the pointer rests on, and where it is defined
	SeenChat             int        // how many conversation entries were on screen last: more are new
	SeenJobs             map[string]bool
	NewChat, NewJobs     bool // a dot in the top bar: something arrived while you were elsewhere
	Blame                bool // b: who last changed each line
	BlameFor             string
	BlameLines           []blameLine
	Gutter               map[int]byte // the open file's lines changed since the last commit
	GutterFor            string
	pendingAt            *jumpTo     // where the cursor goes once the file being opened is read
	Complete             *completion // the words that complete the one being typed
	noComplete           pos         // the word esc dismissed the list for: where it starts, and what was typed of it
	noCompleteWord       string
	ColOff               int            // cells scrolled off the left while editing, so the cursor stays in view
	Wrap                 bool           // ⌥z: long lines wrap while reading
	Recent               []string       // the files opened lately, newest first, for ⌃p
	SymIndex             *symIndex      // every symbol of the repository, for # in the palette
	SelfTest             *view.SelfTest // /selftest: the report as far as it has got
	SelfTestE            string
	Findings             *view.ShellDoctor // /doctor: what shell.doctor found, nil while it is being asked
	FindingsE            string
	Found                []view.ChatHit // /resume: the conversations with a message that matches what was typed (FoundQ)
	FoundQ               string
	Tasks                []taskItem  // the repository's scripts and targets, for ⌃k › run a task
	FileLog              []gitCommit // the commits that touched the open file, for l
	FileLogFor           string
	Branches             []string // the branch list: local branches, newest first
	RemoteBranches       []string
	reloadFiles          bool          // after a switch of branch the explorer reads the files again
	findAt               pos           // where find last stopped, while not editing
	renameFrom           string        // the file or folder the rename question is about
	CurLine, CurCol      int           // the editing cursor, in runes
	FileCRLF             bool          // the file ends its lines with CRLF
	FileTruncated        bool          // only its first lines were read, so it is not edited here
	Diff                 *view.JobDiff // the changes tab's job, its diff
	DiffID               string
	DiffFile             int
	DiffOff              int
	Comments             map[string][]string // what you said to each helper from the changes tab
	Commenting           bool
	DiffHunk             int               // the changes tab's chosen hunk of the open file
	DiffFollow           bool              // scroll to the chosen hunk on the next draw
	Reviewed             map[string]string // hunks of helpers' work marked ok (v) or sent back to be redone (x), by hunkKey
	RedoHunk             *redoTarget       // the hunk a request to redo is being written for
	BoxTop, BoxTotal     int               // the reply box as last drawn: its first row shown and how many rows the draft has
	Attached             []attachment      // pictures dropped on the box, shown above it while the draft names them
	Outbox               []string          // messages typed while the parent was working, sent one by one as its turns end
	Sending              bool              // a queued message is on its way: the next waits for it
	Search               *SearchState
	Settings             *SettingsScreen
	Onboard              *Onboarding // the first-run steps after the parent: helpers, then where agents work
	FirstRun             bool        // the welcome screen opened by itself: the other steps follow it
	InstallSel           int         // the no-agents screen's choice
	ProblemSeen          string      // a problem screen you closed with esc, not shown again for the same problem
	History              *HistoryState
	ShipScreen           *ShipState
}

// lineSpan is the end of "@file:10-40" and "@file:25": the lines of a file to add.
var lineSpan = regexp.MustCompile(`^\d+(-\d+)?$`)

// Palette is Ctrl-K: ask the parent, open a file, or run a command.
type Palette struct {
	Query  string
	Sel    int
	Attach bool // @: the chosen file goes into your message instead of opening
	Model  bool // /model: the parent's models, or the name typed
	Effort bool // /effort, or the reasoning row of /model: the parent's reasoning levels
	Resume bool // /resume: the conversations of this repository, to open one again
	Fork   bool // /fork: the turns of this conversation, each to branch from
	// Running is /running: the parent's tool call, the helpers at work (Jobs, as they were when it opened), the terminal.
	SelfTest bool // /selftest: does each installed parent work through Rig, flag by flag
	Doctor   bool // /doctor: what is wrong with the project, each with its fix
	Running  bool
	Jobs     []view.Row
	Rewind   bool // /rewind: the turns that changed files, each to put back (RewindTurn: the one chosen, whose files are listed)
	// RewindTurn and RewindPlan are the second step: the chosen turn and what putting it back would change.
	RewindTurn int
	RewindPlan *view.ChatRewound
	Symbols    bool // ⌃o in the code tab: the open file's functions and types, to go to
	Branch     bool // b in the git view: a branch to switch to, or a new one
	Problems   bool // m in the code tab: what the helpers' checks found, each to go to
	Tasks      bool // ⌃k › run a task: the repository's scripts and targets, each to run in the terminal
	Log        bool // l in the code tab: the commits that touched the open file, each to show
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
	return center(st.paint(colAmber, st.Prompt)+"   "+st.hint([2]string{yes, "yes"})+"    "+st.hint([2]string{no, "no"}), w)
}

// AppRightKeys are the right half of the design's footer on the conversation, changes and code screens.
var AppRightKeys = [][2]string{{"u", "undo"}, {"h", "hold"}, {"⌃p", "files"}, {"?", "help"}, {"⌃q", "quit"}}

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

// surface puts a line on the card surface, w cells wide: the design's white boxes.
func (st State) surface(s string, w int) string {
	if !st.Styled {
		return fit(s, w)
	}
	return tint(fit(s, w), colRaised)
}

// washed is what shows behind an overlay: the screen faded to one light grey, as the design draws it.
func (st State) washed(lines []string) []string {
	out := make([]string, len(lines))
	for i, l := range lines {
		plain := strings.ReplaceAll(ansi.Strip(l), placeholderCells, "   ")
		out[i] = st.paint(colBorder, plain)
	}
	return out
}

// panelWidth is the agents panel's width, docked or floating.
const panelWidth = 46

// panelDocks: the window is wide enough for the panel beside the main view (the design's 160 columns), and ⇥ has not
// hidden it.
func panelDocks(st State, w int) bool { return w >= 160 && !st.App.PanelHidden }

// layer marks an overlay's lines, so a click outside it closes it.
func layer(st State, name string, lines []string) []string {
	for i, l := range lines {
		lines[i] = st.zone("layer:"+name, l)
	}
	return lines
}

// center puts s in the middle of w cells.
func center(s string, w int) string {
	pad := max(0, (w-lipgloss.Width(s))/2)
	return fit(strings.Repeat(" ", pad)+s, w)
}

// spread lays out a left, centre and right part on one line of w cells, with equal room on either side of the centre
// part, as the design's top lines are laid out.
func spread(left, mid, right string, w int) string {
	lw, mw, rw := lipgloss.Width(left), lipgloss.Width(mid), lipgloss.Width(right)
	start := lw + max(2, (w-lw-mw-rw)/2)
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
		parts = append(parts, st.hint(p))
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
		out = append(out, p("│")+st.surface(l, w-2)+p("│"))
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
	if st.ChatParent != "" {
		return st.ChatParent // the parent chosen; a conversation another one had is handed to it
	}
	if st.Chat != nil && st.Chat.Parent != "" {
		return st.Chat.Parent
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

// panelJobs are the panel's helpers: the ones at work, then the last few that finished, so a finished helper's
// thread is still one ⏎ away.
func panelJobs(v *view.View) []view.Row {
	out := activeJobs(v)
	finished := 0
	for _, r := range v.Rows("Jobs") {
		if r.Label != "" && strings.Contains(finishedLabels, r.Label) && finished < 5 {
			out = append(out, r)
			finished++
		}
	}
	return out
}

// finishedWord is how the panel says a finished helper ended.
func finishedWord(st State, r view.Row) string {
	switch r.Label {
	case "Verified":
		return st.paint(colGreen, "finished · accepted")
	case "Unverified":
		return st.paint(colAmber, "finished · not accepted yet")
	case "Failed":
		return st.paint(colRed, "failed")
	}
	return st.faint("stopped")
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
	if w < 60 || h < 18 {
		lines := make([]string, h)
		for i := range lines {
			lines[i] = fit("", w)
		}
		lines[0] = fit(" rig · window too small (need 60×18); q quits", w)
		return lines
	}
	if st.App.Splash > 0 {
		return splashScreen(v, st, w, h)
	}
	// a row of room above the top bar and below the footer, so neither sits on the window's edge
	if st.App.Term {
		// the terminal split: the screen above, the shell below a rule, a third of the height
		th := termRows(h)
		body := appBody(v, st, w, h-2-th-1)
		return append(append(append([]string{fit("", w)}, body...), termSplit(st, w, th)...), fit("", w))
	}
	return append(append([]string{fit("", w)}, appBody(v, st, w, h-2)...), fit("", w))
}

// appBody is the screen inside AppFrame's margin.
func appBody(v *view.View, st State, w, h int) []string {
	a := st.App
	confirm := func(lines []string) []string {
		switch {
		case st.Mode == ModeConfirm:
			lines[len(lines)-1] = confirmLine(st, w)
		case st.Message != "" && problem(st.Message):
			lines[len(lines)-1] = center(st.paint(colAmber, clean(st.Message)), w)
		}
		return lines
	}
	if a.Splash > 0 {
		return splashScreen(v, st, w, h)
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
		keys := settingsKeys(SettingsSections[a.Settings.Section])
		if a.Settings.Pick != "" {
			keys = [][2]string{{"⏎", "apply " + a.Settings.Pick}, {"←→", "choose"}, {"esc", "cancel"}}
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
	if a.Search != nil {
		lines = []string{fit(rigBarLeft(st, v)+st.faint(" › search"), w), fit("", w)} // the design's search is a page of its own
	}
	if a.Search == nil && a.Thread == "" && a.Tab == 1 && st.Chat == nil && st.ChatE == "" {
		lines = []string{rigBarLeft(st, v) + "", fit("", w)} // the design's first conversation has the mark and repo only
		lines[0] = fit(lines[0], w)
	}
	// The conversation, changes and code screens end in the design's full-width footer under a rule.
	previewing := a.Tab == 3 && a.Preview && previewKind(a.File) != "" && a.FileErr == ""
	ruled := a.Search == nil && a.Thread == "" && !previewing && (a.Tab == 2 || a.Tab == 3 || st.Chat != nil || st.ChatE != "")
	body := h - 3
	if ruled {
		body = h - 4
	}
	// 160 columns and up, the panel stays docked beside the main view, as in the window; ⇥ hides it
	docked := panelDocks(st, w) && a.Search == nil && !previewing
	mw := w
	if docked {
		mw = w - panelWidth
	}
	var main []string
	var keys [][2]string
	footer := ""
	switch {
	case a.Search != nil:
		main, footer = searchScreen(st, w, body)
	case a.Thread != "":
		main, keys = threadBody(st, mw, body)
	case a.Tab == 2:
		main, keys = changesScreen(v, st, mw, body)
		footer = splitHints(st, keys, AppRightKeys, w)
	case a.Tab == 3 && a.Preview && previewKind(a.File) != "" && a.FileErr == "":
		// the design's preview is a page of its own: the file rendered in the middle, its keys at the foot
		lines = []string{fit(rigBarLeft(st, v)+st.faint(" › preview"), w), fit("", w)}
		cw := min(appColumn, w-8)
		main = indentAll(previewLines(st, a, cw, body), (w-cw)/2, w)
		for len(main) < body {
			main = append(main, fit("", w)) // the keys stay at the foot of the window
		}
		main = main[:body]
		keys = [][2]string{{"⇥", "next file"}, {"s", "source"}, {"/", "filter rows"}, {"o", "open in app"}, {"?", "ask " + appParent(st)}}
		if a.Finding {
			keys = [][2]string{{"⏎", "keep"}, {"esc", "all rows"}}
		}
	case a.Tab == 3:
		main, keys = codeScreen(v, st, mw, body)
		footer = splitHints(st, keys, AppRightKeys, w)
	case st.Chat == nil && st.ChatE == "":
		main, keys = emptyBody(v, st, mw, body)
	default:
		main, keys = conversationBody(v, st, mw, body)
		footer = splitHints(st, keys, AppRightKeys, w)
	}
	if docked {
		side := layer(st, "dock", agentsPanel(v, st, panelWidth, len(main), false))
		for i := range main {
			main[i] = fit(main[i], mw) + side[i]
		}
	}

	var modal []string
	modalY := 3
	if a.Palette != nil {
		modal = layer(st, "palette", paletteBox(v, st, min(76, w-8)))
		keys = [][2]string{{"↑↓", "move"}, {"⏎", "open"}, {"esc", "close"}}
	}
	if a.NewJob != nil && a.Palette == nil {
		modal, modalY = layer(st, "newjob", newJobBox(st, *a.NewJob, min(84, w-8))), 5
		keys = nil
	}
	if a.Help {
		hw := min(64, w-8)
		overlay(main, layer(st, "help", helpBox(st, hw)), (w-hw)/2, 2)
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
	case strings.HasPrefix(st.Message, "copied "):
		lines = append(lines, center(st.paint(colGreen, "✓ "+st.Message), w))
	case footer != "" && !a.Panel && a.Palette == nil && a.NewJob == nil && !a.Help:
		lines = append(lines, footer)
	default:
		lines = append(lines, keyHints(st, keys, w))
	}
	for len(lines) < h {
		lines = append(lines, fit("", w))
	}
	lines = lines[:h]
	if len(modal) > 0 && st.Mode != ModeConfirm {
		// the design's palette and helper card float on a faded screen
		lines = st.washed(lines)
		lines[h-1] = fit("", w)
		mw := lipgloss.Width(modal[0])
		overlay(lines, modal, (w-mw)/2, modalY)
	}
	if a.Panel && !docked && st.Mode != ModeConfirm {
		// under 160 columns the design's panel floats: a card over the faded screen, a row and two columns clear of
		// its edges
		pw := min(panelWidth, w/2)
		copy(lines[1:], st.washed(lines[1:]))
		overlay(lines, layer(st, "panel", agentsPanel(v, st, pw, h-3, true)), w-pw-2, 2)
	}
	return lines
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
	left += "   " + st.zone("row:open:parent", st.tile(appParent(st))+" "+appParent(st)) // a click switches parent
	tab := func(n int, name, extra string) string {
		if (n == 1 && a.NewChat) || (n == 2 && a.NewJobs) {
			extra = strings.TrimSpace(st.paint(colAccent, "●") + " " + extra) // new since you looked
		}
		label := st.faint(fmt.Sprint(n)) + " " + name
		if a.Tab == n && st.Styled {
			on := lipgloss.NewStyle().Foreground(lipgloss.Color(colText)).Bold(true).Underline(true)
			label = on.Foreground(lipgloss.Color(colFaint)).Render(fmt.Sprint(n)) + on.Render(" "+name)
		} else if a.Tab == n {
			label = fmt.Sprint(n) + " [" + name + "]"
		} else {
			label = st.faint(fmt.Sprint(n)) + " " + st.dim(name)
		}
		return st.zone(fmt.Sprintf("tab:%d", n), label+extra)
	}
	changes := ""
	if n := len(doneJobs(v)) + countLabel(v, "Unverified"); n > 0 {
		changes = " " + st.paint(colGreen, fmt.Sprint(n))
	}
	mid := tab(1, "conversation", "") + "     " + tab(2, "changes", changes) + "     " + tab(3, "code", "")
	working := len(activeJobs(v))
	panel := "panel"
	if panelDocks(st, w) {
		panel = "hide panel"
	}
	// the window's header ends in Settings; here a click on it opens them, as /settings and ⌃k › settings do
	right := st.zone("key:⌃k", st.dim("⌃k")) + "   " + st.hint([2]string{"⇥", panel}) + "   " +
		st.zone("row:open:settings", st.dim("settings")) + "   "
	if working > 0 {
		busy := fmt.Sprintf("%d working", working)
		if lipgloss.Width(left)+lipgloss.Width(mid)+lipgloss.Width(right)+len(busy)+12 > w {
			busy = fmt.Sprint(working) // narrow: the count alone keeps the tabs on the line
		}
		right = st.zone("key:⇥", st.paint(colAccent, "●")+" "+st.dim(busy)) + "   " + right
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
	return roundBox(st, boxRows(st, w, placeholder), w, lit)
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
		// the design's "claude · working in sandbox · 4m": where the parent works, and for how long this turn
		state = "working"
		if st.Workspace != nil && st.Workspace.Context != "" {
			state += " in sandbox"
		}
		for i := len(st.Chat.Entries) - 1; i >= 0; i-- {
			if e := st.Chat.Entries[i]; e.Role == "user" {
				if at, err := time.Parse(time.RFC3339, e.At); err == nil && !st.Now.IsZero() && !st.Now.Before(at) {
					state += " · " + elapsed(st.Now.Sub(at))
				}
				break
			}
		}
	}
	lines = append(lines, st.tile(parent)+"  "+st.bold(fit(title, cw-5)))
	if st.Chat.Plan {
		state = st.paint(colAccent, "plan mode") + st.faint(" · "+state)
	} else if st.Chat.Mode == "edits" {
		state = st.paint(colAmber, "auto-edit") + st.faint(" · "+state)
	}
	if pct := st.Chat.ContextPct; pct != nil && *pct >= 0 {
		note := fmt.Sprintf("context %d%%", *pct)
		if *pct >= 80 {
			state += st.faint(" · ") + st.paint(colAmber, note)
		} else {
			state += st.faint(" · " + note)
		}
	}
	lines = append(lines, "     "+st.faint(parent+" · ")+state)
	lines = append(lines, "", "")
	// the transcript's lines are made once for each transcript, width and theme: a long conversation scrolls at once
	a := st.App
	key := fmt.Sprintf("%p|%d|%d|%s|%s|%v|%v", st.Chat, len(st.Chat.Entries), cw, colText, colGround, st.Styled, st.Logos)
	if a.chatKey != key {
		a.chatKey, a.chatLines = key, entryLines(st, st.Chat.Entries, cw)
	}
	lines = append(lines, markMatches(st, a.chatLines, a.ChatFind)...)
	if st.Chat.State == "running" && strings.TrimSpace(st.Chat.Partial) != "" {
		lines = append(lines, entryLines(st, []view.ChatEntry{{Role: "assistant", Text: st.Chat.Partial}}, cw)...)
	}
	if st.Chat.Parent != "" && parent != st.Chat.Parent && st.Chat.State != "running" {
		// switched, not yet sent: the next message hands the conversation over
		lines = append(lines, center(st.faint("── "+parent+" takes over from "+st.Chat.Parent+" with your next message ──"), cw), "")
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
		lines = append(lines, queuedRows(st, cw)...)
	}
	if st.Prompt != "" {
		keys = [][2]string{{"⏎", "send"}, {"⌥⏎", "new line"}, {"esc", "clear"}, {"⇥", "agents"}, {"⌃k", "commands"}}
	}
	box := append(attachedRows(st, cw), replyBox(st, cw, "Reply to "+parent, false)...)
	if nextBarOn(st) {
		box = append(nextBar(st, cw), box...) // what to do with the files the last turn changed
		keys = append([][2]string{{"g", "review"}, {"c", "commit"}, {"u", "undo"}}, keys...)
	}
	if a.ChatFind != nil {
		box = chatFindRow(st, cw)
		keys = [][2]string{{"⏎", "next"}, {"↑", "previous"}, {"esc", "close"}}
	}
	if menu := slashMenu(st, cw); len(menu) > 0 && a.ChatFind == nil {
		box = append(menu, box...)
		keys = [][2]string{{"↑↓", "choose"}, {"⇥", "complete"}, {"⏎", "run"}, {"esc", "clear"}}
	}
	room := h - len(box) - 1
	st.App.ChatMax = max(0, len(lines)-room)
	shown := lastLines(lines, st.ChatUp, cw, room)
	out := indentAll(append(append(shown, box...), ""), x, w)
	return out[:h], keys
}

// entryLines is the conversation's entries as lines: your messages as bubbles, the parent's answers, tool calls and errors.
func entryLines(st State, entries []view.ChatEntry, cw int) []string {
	var lines []string
	several := false // more than one parent in this conversation: each answer says whose it is
	for _, e := range entries {
		several = several || e.Role == "switch"
	}
	last := ""
	lastErr := -1
	for i, e := range entries {
		if e.Role == "error" {
			lastErr = i
		}
	}
	for i, e := range entries {
		role := e.Role
		if role == "tool" || role == "result" || role == "result-error" || role == "diff" {
			role = last // a tool call and its answer do not end the parent's answer
		}
		if e.Role == "assistant" && several && last != "assistant" && e.Parent != "" {
			lines = append(lines, st.tile(e.Parent)+"  "+st.faint(e.Parent))
		}
		last = role
		switch e.Role {
		case "switch":
			from, to, _ := strings.Cut(e.Text, " → ")
			lines = append(lines, center(st.faint("── "+to+" takes over from "+from+" ──"), cw), "")
			continue
		case "user":
			lines = append(lines, bubble(st, e.Text, cw)...)
		case "assistant":
			for _, raw := range strings.Split(strings.TrimRight(e.Text, "\n"), "\n") {
				for _, l := range wrap(plainMarkdown(sanitize(raw)), cw, "") {
					lines = append(lines, st.dim(l))
				}
			}
		case "tool":
			lines = append(lines, toolLineWith(st, e.Text, cw, toolProgress(st, e)))
			continue
		case "diff":
			lines = append(lines, diffLines(st, e.Text, cw)...)
			continue
		case "summary":
			// what /compact carried over from the conversation before, with its lines kept
			lines = append(lines, center(st.faint("── summary carried over ──"), cw))
			for _, raw := range strings.Split(strings.TrimRight(e.Text, "\n"), "\n") {
				for _, l := range wrap(plainMarkdown(sanitize(raw)), cw-4, "") {
					lines = append(lines, "  "+st.dim(l))
				}
			}
			lines = append(lines, center(st.faint("──"), cw), "")
			continue
		case "result", "result-error":
			colour := ""
			if e.Role == "result-error" {
				colour = colAmber
			}
			lines = append(lines, resultLine(st, e.Text, cw, colour))
			continue
		case "error":
			for _, l := range wrap(e.Text, cw-2, "") {
				lines = append(lines, st.paint(colAmber, "! "+l))
			}
			if i == lastErr {
				lines = append(lines, st.faint("  /doctor shows what is wrong with the project and fixes what it can"))
			}
		default:
			for _, l := range wrap(clean(e.Text), cw-4, "") {
				lines = append(lines, st.faint("  "+l))
			}
			continue
		}
		lines = append(lines, "")
	}
	return lines
}

// toolLine is a tool the parent called, as its own screen draws one: a bullet, the tool's name, what it works on.
func toolLine(st State, text string, cw int) string {
	return toolLineWith(st, text, cw, "")
}

// toolLineWith is a tool line with a suffix after the detail: the progress of one still running.
func toolLineWith(st State, text string, cw int, suffix string) string {
	name, detail, _ := strings.Cut(clean(text), " ")
	room := max(4, cw-6-lipgloss.Width(name)-lipgloss.Width(suffix))
	return "  " + st.paint(colAccent, "●") + " " + st.bold(name) + st.faint(" "+fit(detail, room)) + st.paint(colAccent, suffix)
}

// diffLines is a file the parent's turn changed, as its own screen shows an edit: the file with its counts, then the
// changed lines with the removed ones red and the added ones green.
func diffLines(st State, text string, cw int) []string {
	head, body, _ := strings.Cut(text, "\n")
	path, counts, _ := strings.Cut(head, " +")
	added, deleted, _ := strings.Cut(counts, " −")
	out := []string{"  " + st.paint(colAccent, "●") + " " + st.bold(path) + "  " + st.paint(colGreen, "+"+added) + " " + st.paint(colRed, "−"+deleted)}
	add, del := tints()
	for _, raw := range strings.Split(body, "\n") {
		if raw == "" {
			continue
		}
		switch {
		case strings.HasPrefix(raw, "@@"):
			out = append(out, st.faint("      ⋯"))
		case strings.HasPrefix(raw, "+"):
			out = append(out, tintLine(st, st.paint(colGreen, fit("    + "+sanitize(raw[1:]), cw-2)), add))
		case strings.HasPrefix(raw, "-"):
			out = append(out, tintLine(st, st.paint(colRed, fit("    − "+sanitize(raw[1:]), cw-2)), del))
		case strings.HasPrefix(raw, "…") || raw == "binary file":
			out = append(out, st.faint("    "+raw))
		default:
			out = append(out, st.dim(fit("      "+sanitize(strings.TrimPrefix(raw, " ")), cw-2)))
		}
	}
	return out
}

// tintLine puts a background behind a coloured line, as the diffs elsewhere do.
func tintLine(st State, line, bg string) string {
	if st.Styled && bg != "" {
		return tint(line, bg)
	}
	return line
}

// resultLine is what the tool answered, under it: its first line, in amber when the tool failed.
func resultLine(st State, text string, cw int, colour string) string {
	body := fit(clean(text), max(4, cw-8))
	if colour != "" {
		return "    " + st.faint("⎿") + " " + st.paint(colour, body)
	}
	return "    " + st.faint("⎿ "+body)
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
	about := appParent(st) + " applied " + clean(r.Title)
	hints := "2 changes   u undo"
	line2 := "  " + st.paint(colAccent, "●") + " " + st.dim(fit(about, max(8, cw-12-len(hints)))) + "     " + st.faint(hints)
	return roundBox(st, []string{st.zone("row:card:"+r.ID, line1), "", st.zone("row:card:"+r.ID, line2)}, cw, false)
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
	return append(out, "", st.zone("row:answer:allow", chip("allow", !deny))+"   "+st.zone("row:answer:deny", chip("deny", deny))+"    "+
		st.faint("or type below"), "")
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
	lines = append(lines, attachedRows(st, cw)...)
	lines = append(lines, roundBox(st, boxRows(st, cw, "ask "+parent+" anything about "+repo), cw, true)...)
	if menu := slashMenu(st, cw); len(menu) > 0 {
		lines = append(lines, menu...)
	}
	lines = append(lines, "", "")
	for i, s := range Suggestions {
		lines = append(lines, st.zone(fmt.Sprintf("row:suggest:%d", i), st.dim(fmt.Sprint(i+1))+"  "+s))
	}
	lines = append(lines, "", "")
	if st.App.Facts != "" {
		lines = append(lines, st.faint("rig looked at the repo: "+st.App.Facts))
	}
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
	keys := [][2]string{{"⏎", "send"}, {"1-4", "suggestion"}, {"@", "add file"}, {"⌃k", "commands"}, {"?", "more"}}
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
	lines := []string{spread("   "+logo+"   "+st.bold("rig"), "", "", w), fit("", w)} // one step: no counting dots
	cw := min(60, w-8)
	body := []string{"", "", st.bold("Who do you want to talk to?"), st.dim("Pick one: it reads your project and does the work you ask for."), "", ""}
	items := WelcomeItems(st)
	for i, it := range items {
		if !it.Installed {
			continue
		}
		row := "   " + st.tile(it.Name) + "  " + it.Name
		if i == a.WelcomeSel {
			row = " " + st.bold("›") + " " + st.tile(it.Name) + "  " + st.bold(it.Name)
			model := "auto"
			if it.Name == st.ChatParent && st.ChatModel != "" {
				model = st.ChatModel // the model its own settings name, as the design shows on the chosen row
			}
			right := st.faint(model+" model") + " "
			row += strings.Repeat(" ", max(2, cw-2-lipgloss.Width(row)-lipgloss.Width(right))) + right
			for _, l := range roundBox(st, []string{row}, cw, true) {
				body = append(body, st.zone(fmt.Sprintf("row:welcome:%d", i), l))
			}
		} else {
			body = append(body, st.zone(fmt.Sprintf("row:welcome:%d", i), fit(row, cw)))
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
func agentsPanel(v *view.View, st State, pw, h int, float bool) []string {
	iw := pw - 5
	if float {
		iw = pw - 6 // a card: its border, and the same room inside
	}
	parent := appParent(st)
	head := func(title, right string) string {
		t := st.bold(title)
		return t + strings.Repeat(" ", max(1, iw-lipgloss.Width(t)-lipgloss.Width(right))) + st.faint(right)
	}
	lines := []string{""}
	var applied []string
	commands := 0
	if st.Today != nil {
		for _, r := range st.Today.Rows {
			switch r.State {
			case "applied":
				undo := ""
				if len(applied) == 0 {
					undo = st.hint([2]string{"u", "undo"})
				}
				top := " " + st.tile(r.Worker) + "  " + fit(clean(r.Title), iw-14)
				if len(applied) < 4 {
					applied = append(applied, top+strings.Repeat(" ", max(1, iw-lipgloss.Width(top)-lipgloss.Width(undo)))+undo,
						"      "+st.paint(colGreen, fit(r.Worker+" · applied by "+parent+" · "+ago(st.Now, r.At), iw-6)))
				}
			case "command":
				commands++
			}
		}
	}
	lines = append(lines, st.zone("key:a", head("Done today", fmt.Sprintf("%d · all ›", len(applied)/2))))
	if len(applied) == 0 {
		lines = append(lines, st.faint("  nothing applied yet"))
	}
	lines = append(lines, applied...)
	lines = append(lines, "", head("Agents", "started by "+parent))
	parentRow := " " + st.tile(parent) + "  " + st.bold(parent) + "  " + st.faint("parent · in the conversation")
	if st.Styled {
		parentRow = tint(fit(parentRow, iw), colSelBg)
	}
	lines = append(lines, parentRow)
	jobs, working := panelJobs(v), len(activeJobs(v))
	sel := clampSel(st.App.PanelSel, len(jobs))
	job := func(i int, r view.Row) {
		worker := r.Worker
		if worker == "" {
			worker = "helper"
		}
		name := worker
		if i == sel {
			name = st.bold(worker)
		}
		top := " " + st.tile(worker) + "  " + name + "  " + st.faint(fit(clean(r.Title), max(4, iw-lipgloss.Width(worker)-14)))
		top = fit(top, iw-6) + st.faint("  › ")
		activity := "      " + st.paint(colAccent, fit(strings.TrimPrefix(clean(r.Second), "permission: "), iw-10))
		if r.Ask {
			activity = "      " + st.paint(colAmber, fit("asks: "+strings.TrimPrefix(clean(r.Second), "permission: "), iw-10))
		}
		if i >= working {
			activity = "      " + finishedWord(st, r)
		}
		if r.Label == "Paused" {
			activity = "      " + st.faint("paused · p lets it go on")
		}
		group := []string{top, activity}
		if i == sel {
			group = roundBox(st, group, iw, true)
		}
		for _, l := range group {
			lines = append(lines, st.zone(fmt.Sprintf("row:panel:%d", i), fit(l, iw)))
		}
	}
	for i, r := range jobs[:working] {
		job(i, r)
	}
	if working == 0 {
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
	if len(jobs) > working {
		// the helpers that finished: their threads stay one ⏎ away
		lines = append(lines, "", head("Finished", "⏎ its thread"))
		for i := working; i < len(jobs); i++ {
			job(i, jobs[i])
		}
	}
	var foot []string
	if commands > 0 {
		word := "commands"
		if commands == 1 {
			word = "command"
		}
		foot = append(foot, st.faint(fmt.Sprintf("%s allowed %d %s today", parent, commands, word)))
	}
	if st.Workspace != nil && st.Workspace.Context != "" {
		foot = append(foot, st.paint(colGreen, "●")+" "+st.faint("sandbox "+lastWord(st.Workspace.Context)))
	}
	shut := "hide"
	if float {
		shut = "close"
	}
	keys := st.hint([2]string{"⏎", "thread"}) + "   " + st.hint([2]string{"p", "pause"}) + "   " + st.hint([2]string{"s", "stop"}) + "   " + st.hint([2]string{"⇥", shut})
	room := h - len(foot) - 2
	if float {
		room -= 2
	}
	if len(lines) > room {
		lines = lines[:room]
	}
	for len(lines) < room {
		lines = append(lines, "")
	}
	lines = append(lines, foot...)
	if float {
		// the design's floating card: rounded, over the faded screen, its keys under a rule at its foot
		var in []string
		for _, l := range lines {
			in = append(in, "  "+fit(l, iw)+"  ")
		}
		in = append(in, st.paint(colBorder, strings.Repeat("─", pw-2)), "  "+keys)
		return roundBox(st, in, pw, false)
	}
	edge := st.paint(colBorder, "│")
	out := make([]string, 0, h)
	for _, l := range lines {
		out = append(out, edge+st.surface("  "+fit(l, iw)+"  ", pw-1))
	}
	out = append(out, edge+st.surface(st.paint(colBorder, strings.Repeat("─", pw-1)), pw-1), edge+st.surface("  "+keys, pw-1))
	return out
}

// paletteItems are Ctrl-K's rows for a query: ask the parent, matching files, matching commands.
func paletteItems(st State) []paletteItem {
	q := strings.ToLower(strings.TrimSpace(st.App.Palette.Query))
	var out []paletteItem
	if st.App.Palette.Model {
		return modelItems(st, q)
	}
	if st.App.Palette.Effort {
		return effortItems(st, appParent(st), q)
	}
	if st.App.Palette.Resume {
		return resumeItems(st, q)
	}
	if st.App.Palette.Rewind {
		return rewindItems(st, q)
	}
	if st.App.Palette.Fork {
		return forkItems(st, q)
	}
	if st.App.Palette.Running {
		return runningItems(st, q)
	}
	if st.App.Palette.Doctor {
		return doctorItems(st, q)
	}
	if st.App.Palette.SelfTest {
		return selftestItems(st, q)
	}
	if st.App.Palette.Symbols {
		return symbolItems(st, q)
	}
	if st.App.Palette.Branch {
		return branchItems(st, q)
	}
	if st.App.Palette.Problems {
		return problemItems(st, q)
	}
	if st.App.Palette.Tasks {
		return taskPaletteItems(st, q)
	}
	if st.App.Palette.Log {
		return logItems(st, q)
	}
	if strings.HasPrefix(q, "#") {
		return workspaceSymbolItems(st, q[1:]) // the repository's symbols
	}
	if q == "" {
		out = append(out, recentItems(st, st.App.Palette.Attach)...) // the files opened lately first
	}
	if st.App.Palette.Attach {
		find, span := q, ""
		if name, rng, ok := strings.Cut(q, ":"); ok && lineSpan.MatchString(rng) {
			find, span = name, ":"+rng // "auth.py:10-40": just those lines
		}
		for _, f := range st.App.Files {
			if find == "" || strings.Contains(strings.ToLower(f), find) {
				dir, name := "", f
				if i := strings.LastIndex(f, "/"); i >= 0 {
					dir, name = f[:i], f[i+1:]
				}
				if span != "" {
					dir = strings.TrimPrefix(dir+" · lines "+span[1:], " · ")
				}
				if out = append(out, paletteItem{"add a file", name + span, dir, "attach:" + f + span}); len(out) >= 10 {
					break
				}
			}
		}
		return out
	}
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
	// the design lists commands by name only; undo last apply says what it would undo
	command := func(c [3]string) paletteItem {
		it := paletteItem{"commands", c[0], "", c[2]}
		if c[2] == "undo-last" {
			if r := lastApplied(st); r != nil {
				it.Detail = r.Worker + " · " + clean(r.Title)
			}
		}
		return it
	}
	matched := 0
	for _, c := range PaletteCommands {
		if q == "" || strings.Contains(c[0], q) {
			out = append(out, command(c))
			matched++
		}
	}
	// the parent's own skills and commands, when the query names one: ⏎ puts it in the box
	skills := 0
	for _, c := range st.ParentCommands {
		if q != "" && skills < 4 && strings.Contains(strings.ToLower(c.Name), q) {
			name := c.Name
			if c.Skill && Conventions[st.ChatParent].Dollar {
				name = "$" + strings.TrimPrefix(name, "/") // codex's own way to name a skill
			}
			out = append(out, paletteItem{appParent(st) + " skills", name, c.Help, "slash:" + name})
			skills++
		}
	}
	if matched == 0 {
		for _, c := range PaletteCommands[:5] { // nothing matches: the usual ones, as the design shows them
			out = append(out, command(c))
		}
	}
	return out
}

// lastApplied is the newest work applied today, the one undo last apply would undo.
func lastApplied(st State) *struct {
	Worker, Title, JobID string
} {
	if st.Today == nil {
		return nil
	}
	for _, r := range st.Today.Rows {
		if r.State == "applied" {
			return &struct{ Worker, Title, JobID string }{r.Worker, r.Title, r.JobID}
		}
	}
	return nil
}

// PaletteCommands are Ctrl-K's commands: label, detail, action.
var PaletteCommands = [][3]string{
	{"undo last apply", "revert the newest applied work", "undo-last"},
	{"hold all applies", "keep every helper's work from being applied", "hold-all"},
	{"switch parent", "choose who you talk to", "parent"},
	{"ship changes", "push, or open a pull request", "ship"},
	{"new conversation", "start again with the parent", "new"},
	{"queue a task", "park it for the parent to pick", "queue"},
	{"change theme", "rig, light, dark, material, dracula", "theme"},
	{"change model", "the model the parent talks with", "model"},
	{"resume a conversation", "open an earlier one again (/resume)", "resume"},
	{"fork the conversation", "branch from a turn into a new conversation (/fork)", "fork"},
	{"rewind files", "put files back as they were before a turn (/rewind)", "rewind"},
	{"search the code", "text in every file (⌃f)", "search"},
	{"find references", "every use of the name at the cursor (F7)", "references"},
	{"test every parent (selftest)", "tiny real turns per installed parent: answer, reasoning level, model, plan, auto-edit, stop (/selftest)", "selftest"},
	{"what is wrong (doctor)", "problems with this project in plain words, with the fix for each Rig can run (/doctor)", "doctor"},
	{"what is running", "the parent's tool call, the helpers at work, the terminal (/running)", "running"},
	{"run a task", "a script, make or just target, or the tests, in the terminal", "tasks"},
	{"run the test at the cursor", "go, pytest, jest, vitest or cargo (F9)", "test"},
	{"format document", "gofmt, prettier, ruff, black, rustfmt or shfmt (⌥⇧f)", "format"},
	{"file history", "the commits that changed the open file (l)", "history"},
	{"done today", "what was applied, undo and redo", "today"},
	{"settings", "theme, agents, routing, sandbox, toml", "settings"},
	{"start the sandbox", "rig env up", "env-up"},
	{"stop a helper", "from the agents panel", "panel"},
	{"the board", "jobs, queue, workspace and evidence", "board"},
	{"help", "every key", "help"},
	{"quit", "leave rig shell", "quit"},
}

func paletteBox(v *view.View, st State, pw int) []string {
	iw := pw - 6
	text := "  " + st.faint("›") + " " + st.App.Palette.Query + "▏"
	if st.App.Palette.Query == "" {
		text = "  " + st.faint("›") + " ▏" + st.faint("search files and commands, # a symbol, or ask "+appParent(st))
		if st.App.Palette.Attach {
			text = "  " + st.faint("@") + " ▏" + st.faint("a file to add to your message")
		}
		if st.App.Palette.Model {
			text = "  " + st.faint("›") + " ▏" + st.faint("a model for "+appParent(st)+", or type its name; reasoning is one row down")
		}
		if st.App.Palette.Effort {
			text = "  " + st.faint("›") + " ▏" + st.faint("how much "+appParent(st)+" reasons: low, medium, high…")
		}
		if st.App.Palette.SelfTest {
			text = "  " + st.faint("✓") + " ▏" + st.faint("does each installed parent work through Rig? ⏎ on the first row runs the tests")
		}
		if st.App.Palette.Doctor {
			text = "  " + st.faint("✚") + " ▏" + st.faint("what is wrong with this project: ⏎ fixes it where Rig can, else says what to do")
		}
		if st.App.Palette.Running {
			text = "  " + st.faint("⟳") + " ▏" + st.faint("what is running: ⏎ goes to it, x stops it")
		}
		if st.App.Palette.Fork {
			text = "  " + st.faint("⑂") + " ▏" + st.faint("a turn to branch from: the new conversation starts as a copy up to it")
		}
		if st.App.Palette.Rewind {
			text = "  " + st.faint("↶") + " ▏" + st.faint("a turn to put back: its files return to how they were before it")
		}
		if st.App.Palette.Resume {
			text = "  " + st.faint("⟲") + " ▏" + st.faint("an earlier conversation to open again: type to search")
		}
		if st.App.Palette.Symbols {
			text = "  " + st.faint("›") + " ▏" + st.faint("a function or type in "+filepath.Base(st.App.File))
		}
		if st.App.Palette.Branch {
			text = "  " + st.faint("⎇") + " ▏" + st.faint("a branch to switch to, or a new branch's name")
		}
		if st.App.Palette.Problems {
			text = "  " + st.faint("✕") + " ▏" + st.faint("a problem a helper's check found: ⏎ opens it, ⇥ asks "+appParent(st)+" to fix it")
		}
		if st.App.Palette.Tasks {
			text = "  " + st.faint("$") + " ▏" + st.faint("a task to run in the terminal, or type a command")
		}
		if st.App.Palette.Log {
			text = "  " + st.faint("⎇") + " ▏" + st.faint("a commit that changed "+filepath.Base(st.App.File)+", to see its change")
		}
	}
	lines := []string{text, ""}
	items := paletteItems(st)
	sel := clampSel(st.App.Palette.Sel, len(items))
	section := ""
	// twelve rows show; moving past them scrolls, so the chosen row is always one of them
	start := 0
	if sel >= 12 {
		start = sel - 11
	}
	for i := start; i < len(items) && i < start+12; i++ {
		it := items[i]
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
			row = fit(row, iw)
			if st.Styled {
				row = tint(lipgloss.NewStyle().Bold(true).Render(row), colSelBg)
			} else {
				row = ">" + row[1:]
			}
		}
		lines = append(lines, st.zone(fmt.Sprintf("row:palette:%d", i), row))
	}
	return roundBox(st, indentAll(append([]string{""}, append(lines, "")...), 2, pw-2), pw, false)
}

// newJobBox is the design's "claude started codex" card: the task, why and where it runs, what finishes it, how far it
// has got, and its keys with the one primary button.
func newJobBox(st State, r view.Row, dw int) []string {
	parent := appParent(st)
	worker := r.Worker
	if worker == "" {
		worker = "helper"
	}
	iw := dw - 6
	lines := []string{"", st.tile(parent) + " " + st.faint("→") + " " + st.tile(worker) + "  " + st.bold(parent+" started "+worker) + "  " + st.faint("just now"), ""}
	for _, l := range wrap(clean(r.Title), iw-4, "") {
		lines = append(lines, tintIf(st, "  "+fit(l, iw-4)+"  ", colSelBg))
	}
	lines = append(lines, "")
	if why := clean(r.Explanation); why != "" {
		lines = append(lines, st.faint("why    ")+fit(why, iw-7))
	}
	where := "worktree " + r.ID
	if st.Workspace != nil && st.Workspace.Context != "" {
		where = "sandbox " + lastWord(st.Workspace.Context) + st.faint(" · ") + where
	}
	lines = append(lines, st.faint("where  ")+where, st.faint("done   ")+"when its checks pass"+st.faint(" · then "+parent+" applies it"), "")
	now := strings.ToLower(clean(r.Label))
	if r.Second != "" {
		now = clean(r.Second)
	}
	lines = append(lines, st.paint(colGreen, "✓ admitted")+"  "+st.paint(colGreen, "✓ worktree")+"  "+st.bold("● "+fit(now, 30))+"  "+st.faint("○ checks  ○ applied"), "")
	keys := st.hint([2]string{"t", "tell " + parent}) + "   " + st.hint([2]string{"s", "stop"}) + "   " + st.hint([2]string{"esc", "close"})
	button := " ⏎ watch thread "
	if st.Styled {
		button = lipgloss.NewStyle().Background(lipgloss.Color(colText)).Foreground(lipgloss.Color(colGround)).Bold(true).Render(button)
	} else {
		button = "[⏎ watch thread]"
	}
	foot := keys + strings.Repeat(" ", max(2, iw-lipgloss.Width(keys)-lipgloss.Width(button))) + st.zone("key:⏎", button)
	box := roundBox(st, append(indentAll(lines, 2, iw+2), st.paint(colBorder, strings.Repeat("─", iw+4)), "  "+foot), dw, false)
	return box
}

// tintIf is tint where the screen has colour.
func tintIf(st State, s, bg string) string {
	if !st.Styled {
		return s
	}
	return tint(s, bg)
}

// helpBox lists the app's keys.
func helpBox(st State, hw int) []string {
	rows := [][2]string{
		{"type, ⏎", "talk to the parent"},
		{"⌥⏎ ⌃j", "a new line in your message (the wheel over a long one scrolls it)"},
		{"↑ ↓", "the lines of your message; in an empty box, what you sent"},
		{"← →", "move in the box (⌥ a word, ⌃a ⌃e its ends, ⌃w a word back)"},
		{"⇥", "the agents panel: done today, helpers, the queue"},
		{"/model", "the model and reasoning level the parent talks with (/effort LEVEL for the level)"},
		{"/plan", "the parent plans and changes nothing (/plan TEXT, /plan go, /plan off)"},
		{"/cost /context /mcp", "what the conversation used, how full the context is, the MCP servers"},
		{"/init /review", "the parent writes CLAUDE.md or AGENTS.md; reviews the current changes"},
		{"/memory", "open CLAUDE.md or AGENTS.md in the code tab"},
		{"!command", "run a command in the terminal split"},
		{"⌃k", "commands, files, # a symbol of the repository, or ask the parent"},
		{"F12 F7", "in the code tab: go to a definition, find references"},
		{"⌥z", "in the code tab: wrap long lines"},
		{"F9 ⌥⇧f l", "in the code tab: run the test at the cursor, format the file, its history"},
		{"1 2 3", "conversation, changes, code (with the box empty)"},
		{"/", "commands: /parent /queue /new /stop /theme /quit"},
		{"1 2", "allow or deny a request the conversation shows"},
		{"PgUp PgDn", "scroll the conversation"},
		{"click", "does what its key would; the wheel scrolls"},
		{"drag", "selects text; letting go copies it"},
		{"⇧ ← →", "select in the box; a click there places the cursor"},
		{"esc", "close, or clear the box"},
		{"⌃c", "clear the box, or quit"},
		{"⌃q", "quit, from anywhere (asks if edits are not saved)"},
		{"⌃d", "quit, with the box empty"},
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
	if t := st.ThreadView; t != nil && t.JobID == st.App.Thread {
		lines = []string{"", " " + st.tile(t.Worker) + "  " + st.bold(t.Worker) + "  " + st.faint(fit(clean(t.Title), max(4, cw-lipgloss.Width(t.Worker)-6)))}
		if t.Running {
			lines = append(lines, "      "+st.paint(colAccent, "working · started by "+t.Parent))
		} else {
			lines = append(lines, "      "+st.faint(t.Ending+" · started by "+t.Parent))
		}
		lines = append(lines, "")
		a := st.App
		if key := fmt.Sprintf("%p|%d|%s|%v", t, cw, colText, st.Styled); a.logKey != key {
			a.logKey, a.logLines = key, threadLines(st, t, cw)
		}
		all := a.logLines
		a.LogMax = max(0, len(all)-(h-len(lines)))
		lines = append(lines, lastLines(all, st.LogUp, cw, h-len(lines))...)
		for len(lines) < h {
			lines = append(lines, "")
		}
		keys := [][2]string{{"esc", "back"}, {"⇥", "agents"}}
		if t.Running {
			keys = [][2]string{{"esc", "back"}, {"p", "pause"}, {"s", "stop"}, {"⇥", "agents"}}
		}
		if st.LogUp > 0 {
			keys = append([][2]string{{"G", "newest"}}, keys...)
		}
		return indentAll(lines[:h], (w-cw)/2, w), keys
	}
	switch {
	case st.Log == nil && st.LogE != "":
		lines = append(lines, st.paint(colAmber, "unavailable: "+clean(st.LogE)))
	case st.Log == nil:
		lines = append(lines, st.faint("loading…"))
	default:
		// the whole log, wrapped, with the newest at the foot; the wheel and PgUp/PgDn scroll it (LogUp lines up)
		a := st.App
		if key := fmt.Sprintf("%p|%d|%s|%v", st.Log, cw, colText, st.Styled); a.logKey != key {
			a.logKey, a.logLines = key, nil
			for _, l := range strings.Split(strings.TrimRight(st.Log.Text, "\n"), "\n") {
				for _, part := range strings.Split(ansi.Wrap(sanitize(l), cw, " "), "\n") {
					a.logLines = append(a.logLines, st.dim(part))
				}
			}
		}
		all := a.logLines
		st.App.LogMax = max(0, len(all)-(h-len(lines)))
		lines = append(lines, lastLines(all, st.LogUp, cw, h-len(lines))...)
	}
	for len(lines) < h {
		lines = append(lines, "")
	}
	keys := [][2]string{{"esc", "back"}, {"s", "stop"}, {"⇥", "agents"}}
	if st.LogUp > 0 {
		keys = append([][2]string{{"G", "newest"}}, keys...)
	}
	return indentAll(lines[:h], (w-cw)/2, w), keys
}

// threadLines is a helper's thread as a conversation: who said what to whom, its work in between, wrapped to cw.
func threadLines(st State, t *view.JobThread, cw int) []string {
	var out []string
	wrap := func(text string, paint func(string) string, lead string) {
		for _, l := range strings.Split(strings.TrimRight(text, "\n"), "\n") {
			for _, part := range strings.Split(ansi.Wrap(sanitize(l), cw-6, " "), "\n") {
				out = append(out, lead+paint(part))
			}
		}
	}
	plain := func(s string) string { return s }
	who := func(name, about string) {
		if len(out) > 0 {
			out = append(out, "")
		}
		out = append(out, " "+st.tile(name)+"  "+st.bold(name)+"  "+st.faint(about))
	}
	last := ""
	for _, e := range t.Entries {
		switch e.Kind {
		case "brief":
			who(e.Who, "→ "+t.Worker+" · the task")
			wrap(e.Text, plain, "      ")
		case "message":
			note := "→ " + t.Worker
			if e.Unread {
				note += " · not read yet"
			}
			who(e.Who, note)
			wrap(e.Text, plain, "      ")
		case "ask":
			who(e.Who, "asks "+t.Parent)
			wrap(e.Text, func(s string) string { return st.paint(colAmber, s) }, "      ")
		case "result":
			who(e.Who, "→ "+t.Parent+" · reported")
			wrap(e.Text, plain, "      ")
		default:
			if last != "activity" {
				who(e.Who, "working")
			}
			wrap(e.Text, st.dim, "      · ")
		}
		last = e.Kind
	}
	if t.Ending != "" {
		out = append(out, "", "      "+st.faint("── "+t.Ending))
	}
	if len(out) == 0 {
		out = append(out, st.faint("      nothing yet"))
	}
	return out
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

// splashScreen is the design's rig mark while rig shell opens: the stem is the parent, the three dots its helpers,
// pulsing in turn, left to right; then the wordmark and what is happening. Ghostty and kitty draw the mark itself.
func splashScreen(v *view.View, st State, w, h int) []string {
	a := st.App
	dots := []string{"#6d4aff", "#0d9488", "#db2777"}
	pulse := (a.Splash / 3) % 3
	dot := func(i int) string {
		if i == pulse {
			return st.paint(dots[i], "●")
		}
		return st.paint(colBorder, "●")
	}
	mark := []string{ // the stem with its arc, in blocks, and the dots on the arc's level
		st.bold("██ ▄▄▄▄▄") + "  " + dot(0) + " " + dot(1) + " " + dot(2),
		st.bold("██▀▀    "),
		st.bold("██      "),
		st.bold("██      "),
	}
	if st.Logos && st.Styled {
		if rows := markRowsFor(st.Theme); rows != nil {
			mark = rows
		}
	}
	status := "starting the local API"
	if a.Repo != "" {
		status = "opening " + appProject(v, a)
	}
	frame := spinFrames[a.Splash%len(spinFrames)]
	body := append(append([]string(nil), mark...), "", "", st.bold("rig"), "", st.faint(string(frame)+" "+status))
	top := max(0, (h-len(body))/2-1)
	lines := make([]string, 0, h)
	for i := 0; i < top; i++ {
		lines = append(lines, fit("", w))
	}
	markW := lipgloss.Width(mark[0])
	for i, l := range body {
		pad := (w - lipgloss.Width(l)) / 2
		if i < len(mark) {
			pad = (w - markW) / 2 // the mark's rows share one left edge
		}
		lines = append(lines, fit(strings.Repeat(" ", max(0, pad))+l, w))
	}
	for len(lines) < h {
		lines = append(lines, fit("", w))
	}
	return lines[:h]
}
