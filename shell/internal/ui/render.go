package ui

import (
	"fmt"
	"strings"
	"time"

	"github.com/charmbracelet/lipgloss"

	"github.com/Brasth/Rig/shell/internal/view"
)

const (
	minWidth, minHeight = 40, 8
	splitMinWidth       = 100
	stackMinHeight      = 24
	twoLineMinBody      = 12
)

// Mode is what the keyboard is doing right now.
type Mode int

const (
	ModeNormal Mode = iota
	ModeConfirm
	ModeInput
)

// State is everything Render needs besides the view. It holds no I/O.
type State struct {
	ParentCommands []SlashCommand // the parent's skills and commands, in the / menu after Rig's
	ModelList      []ModelChoice  // every model the parent offers, for /model
	ModelsFor      string         // the parent ModelList is for; "loading:<parent>" while it is asked
	Mouse          bool           // clickable places are marked as zones (mouse.go)
	Logos          bool           // agents drawn as their logo tiles with kitty's image placeholders (gfx.go)
	Tab            string
	Selected       map[string]int
	Offsets        map[string]int
	DetailOpen     bool
	DetailOff      int
	Mode           Mode
	Prompt         string // confirm question or input draft
	PromptBack     int    // the app's box: how many runes of the draft are after the cursor
	PromptTop      int    // the wheel scrolled the box to this row, while the cursor is where it was (PromptTopCaret)
	PromptTopAt    int
	PromptScrolled bool
	PromptSel      bool // and some of it is selected, from PromptAnchor to the cursor
	PromptAnchor   int
	Message        string
	Stale          string // set when the last refresh failed; shown until one succeeds
	Loading        bool
	ASCII          bool
	Workspace      *view.Workspace
	WorkspaceE     string // why the workspace panel could not load
	Fleet          *view.Panel
	FleetE         string
	Settings       *view.Panel
	SettingsE      string
	PaneLines      []string // the Session pane's screen, already styled by the emulator
	PaneTitle      string
	PaneFocus      bool
	PaneRunning    bool
	ChatOn         bool   // the chat experiment is on: the Chat tab exists
	ChatParent     string // who answers a new conversation (chat.status); empty when no parent CLI is installed
	Chat           *view.ChatTranscript
	ChatE          string
	ChatUp         int    // lines scrolled back from the newest
	InputLabel     string // what the input line is for; empty means enqueue
	Log            *view.LogReply
	ThreadView     *view.JobThread // the helper's thread with the parent, when the server gives it
	LogE           string
	LogUp          int // lines scrolled back from the newest; 0 follows the tail
	Evidence       *view.Evidence
	EvidenceE      string
	EvidenceID     string
	EvidenceOpen   bool           // the evidence drawer replaces the board body
	HelpOpen       bool           // the key list replaces the board body
	WideSession    bool           // the window fits the Session screen, whose middle column is the chat: no separate Chat tab
	DockEvidence   *view.Evidence // the selected job's evidence in the Session screen's dock
	DockID         string
	DockE          string
	Styled         bool               // apply colour and reverse video to list rows; golden tests leave it off
	ChatModel      string             // the parent's model, for the title "Parent · claude opus"
	ChatEffort     string             // the reasoning level chosen for the parent, shown after the model
	ChatSessions   []view.ChatSession // the conversations of this repository, the one used last first, for /resume
	Theme          string             // the colour theme in use; empty is the default, rig
	ThemeScope     string             // where it is kept: "all" repos, this "repo", "default" or "this board" (rig shell --theme)
	Focus          string             // the Session screen's pane with the keyboard: "" (the jobs, as the mockup opens), "parent" or "workspace"
	Picker         *Picker            // the parent picker, while it is open
	App            *AppState          // the app (the design's ADE screens); nil while the board shows
	AppSettings    *view.AppSettings
	Today          *view.Today      // what helpers applied today
	Ship           *view.ShipStatus // what is ahead of the upstream branch
	ShipE          string
	SlashSel       int       // the chosen line of the / command list
	Now            time.Time // the clock, for the working spinner and its elapsed time; zero in golden tests
}

// Picker is the parent picker: every parent the chat can run, the installed ones first.
type Picker struct {
	Items   []PickItem
	Sel     int
	Current string // the parent in use
}

// PickItem is one parent CLI in the picker.
type PickItem struct {
	Name      string
	Installed bool
}

// SlashCommand is one of the ask box's / commands.
type SlashCommand struct {
	Name, Args, Help string
	Parent           bool // the parent's own skill or command, sent to it
	Skill            bool // a skill, which $name also names (as in codex's box)
}

// softCommands are Rig's commands a parent's own skill or command of the same name replaces, as its own screen would
// run it: the parent's /review is the user's, not Rig's.
var softCommands = map[string]bool{"/init": true, "/review": true, "/security-review": true}

// SlashCommands are what the ask box understands besides a message for the parent.
var SlashCommands = []SlashCommand{
	{"/parent", "", "choose the parent CLI", false, false},
	{"/queue", " TEXT", "park a task in the queue instead", false, false},
	{"/new", "", "start a new conversation", false, false},
	{"/stop", "", "stop the parent's running turn", false, false},
	{"/jobs", "", "the Jobs tab", false, false},
	{"/env", "", "the container, ports and worktrees", false, false},
	{"/help", "", "what you can do, in plain words (and every key)", false, false},
	{"/theme", " NAME", "rig, light, dark, material or dracula", false, false},
	{"/settings", "", "theme, agents, routing, sandbox", false, false},
	{"/plan", " [TEXT|off|go]", "the parent plans and changes nothing; off or go ends it", false, false},
	{"/cost", "", "what this conversation has used so far", false, false},
	{"/context", "", "how full the parent's context is", false, false},
	{"/mcp", "", "the MCP servers the parent has", false, false},
	{"/memory", "", "open CLAUDE.md or AGENTS.md to edit", false, false},
	{"/remember", " NOTE", "keep NOTE in .rig/notes.md: it goes with every conversation's first message", false, false},
	{"/notes", "", "open the notes the parent is told (.rig/notes.md)", false, false},
	{"/init", "", "the parent writes CLAUDE.md or AGENTS.md for this project", false, false},
	{"/review", " [FOCUS]", "the parent reviews the current changes", false, false},
	{"/security-review", " [FOCUS]", "the parent reviews them for security problems", false, false},
	{"/clear", "", "start a new conversation", false, false},
	{"/compact", " [FOCUS]", "continue in a fresh conversation from the parent's own summary", false, false},
	{"/resume", "", "open an earlier conversation again", false, false},
	{"/selftest", "", "test that each installed parent works through Rig (runs a few tiny real turns)", false, false},
	{"/helpers", "", "choose which other assistants the parent can hand work to", false, false},
	{"/doctor", "", "what is wrong with this project, and fix what Rig can", false, false},
	{"/running", "", "what is running: the parent's tool call, the helpers, the terminal", false, false},
	{"/mode", " [ask|edits|plan]", "how much the parent may do without asking (⇧Tab goes round)", false, false},
	{"/export", " [FILE]", "write this conversation as markdown (default .rig/exports/ID.md)", false, false},
	{"/fork", "", "branch from a turn into a new conversation", false, false},
	{"/rewind", "", "put files back as they were before a turn", false, false},
	{"/model", "", "the model the parent talks with (or /model NAME [LEVEL])", false, false},
	{"/effort", " LEVEL", "how much the parent reasons: low, medium, high…", false, false},
	{"/quit", "", "leave rig shell", false, false},
}

// Convention is how a parent's own box names its skills, so the box here works as the one its users know.
type Convention struct {
	Dollar      bool   // $name mentions a skill anywhere in a message, and skills are not in the / menu (codex)
	SkillPrefix string // the / name a skill goes by after its prefix: /skill:name in pi and omp
}

// Conventions are the parents whose box differs from claude's, where / lists commands and skills alike.
var Conventions = map[string]Convention{
	"codex": {Dollar: true},
	"pi":    {SkillPrefix: "skill:"},
	"omp":   {SkillPrefix: "skill:"},
}

// slashes is the commands a draft that starts with / could be, until it has a space: Rig's own, then the parent's
// skills and commands, named as the parent's own box names them.
func (st State) slashes() []SlashCommand {
	prompt := st.Prompt
	conv := Conventions[st.ChatParent]
	if word := mentionWord(prompt); word != "" && conv.Dollar {
		// a $ word anywhere in the message names one of the parent's skills, as codex's box does
		var out []SlashCommand
		for _, c := range st.ParentCommands {
			name := "$" + strings.TrimPrefix(c.Name, "/"+conv.SkillPrefix)
			if c.Skill && (strings.HasPrefix(name, word) || (len(word) > 2 && strings.Contains(name, word[1:]))) {
				out = append(out, SlashCommand{Name: name, Help: c.Help, Parent: true, Skill: true})
			}
		}
		return out
	}
	if !strings.HasPrefix(prompt, "/") || strings.Contains(prompt, " ") {
		return nil
	}
	var out []SlashCommand
	own := map[string]bool{}
	for _, c := range st.ParentCommands {
		own[c.Name] = true
	}
	for _, c := range append(append([]SlashCommand(nil), SlashCommands...), st.ParentCommands...) {
		if c.Skill && conv.Dollar {
			continue // codex names its skills with $, not /
		}
		if !c.Parent && softCommands[c.Name] && own[c.Name] {
			continue // the parent's own /review, say, is the one that runs
		}
		if strings.HasPrefix(c.Name, prompt) || (c.Parent && strings.Contains(c.Name, prompt[1:]) && len(prompt) > 2) {
			out = append(out, c)
		}
	}
	return out
}

// mentionWord is the $ word being typed at the end of the box, or "".
func mentionWord(prompt string) string {
	if prompt == "" || strings.HasSuffix(prompt, " ") {
		return ""
	}
	word := prompt[strings.LastIndexAny(prompt, " \n")+1:]
	if !strings.HasPrefix(word, "$") {
		return ""
	}
	return word
}

// completeSlash is the box once a menu row is chosen: a $ skill takes the place of the word typed, a / command the
// whole box, with a space where arguments follow.
func completeSlash(prompt string, c SlashCommand) string {
	if strings.HasPrefix(c.Name, "$") {
		return strings.TrimSuffix(prompt, mentionWord(prompt)) + c.Name + " "
	}
	if c.Args != "" {
		return c.Name + " "
	}
	return c.Name
}

// typingChat: the ask box has the keyboard.
func typingChat(st State) bool { return st.Mode == ModeInput && st.InputLabel == "chat" }

// problem: a message reports something that went wrong, so it is shown in amber.
func problem(msg string) bool {
	low := strings.ToLower(msg)
	for _, w := range []string{"fail", "cannot", "could not", "unavailable", "error", "no parent", "not on path", "unknown"} {
		if strings.Contains(low, w) {
			return true
		}
	}
	return false
}

var spinFrames = []rune("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏")

// workingLine is the spinner under a running turn, with how long the parent has been at it.
func workingLine(st State) string {
	frame := spinFrames[0]
	line := "working…"
	if !st.Now.IsZero() {
		frame = spinFrames[(st.Now.UnixMilli()/100)%int64(len(spinFrames))]
		for i := len(st.Chat.Entries) - 1; i >= 0; i-- {
			e := st.Chat.Entries[i]
			if e.Role != "user" {
				continue
			}
			if at, err := time.Parse(time.RFC3339, e.At); err == nil && !st.Now.Before(at) {
				line += " " + elapsed(st.Now.Sub(at))
			}
			break
		}
	}
	line = " " + string(frame) + " " + line + " · /stop stops it"
	if st.Styled {
		return lipgloss.NewStyle().Foreground(lipgloss.Color(colAccent)).Render(line)
	}
	return line
}

func elapsed(d time.Duration) string {
	sec := int(d.Seconds())
	if sec < 60 {
		return fmt.Sprintf("%ds", sec)
	}
	return fmt.Sprintf("%dm %02ds", sec/60, sec%60)
}

// NewState returns a state on the first tab.
func NewState() State {
	return State{Tab: "Jobs", Selected: map[string]int{}, Offsets: map[string]int{}}
}

// Tabs are the board tabs in the order the mockup shows them. Logs is a screen under Jobs (key l), not a tab.
var Tabs = []string{SessionTab, "Jobs", "Queue", "Workflows", EnvTab, SettingsTab}

// SessionTab is the parent's seat: it hosts one interactive program (the parent CLI, or a shell in the
// container) on an emulated terminal.
const SessionTab = "Session"

// EnvTab shows the workspace (container, ports, worktrees, doctor) and the fleet of containers on the engine.
const EnvTab = "Env"

// SettingsTab shows what the project's configuration makes effective. It is read-only.
const SettingsTab = "Settings"

// ChatTab is the EXPERIMENT chat with a headless parent; it exists only while the experiment is on.
const ChatTab = "Chat"

// TabList is the tabs to show: the board tabs, plus Chat while the experiment is on.
func TabList(st State) []string {
	if st.ChatOn && !st.WideSession {
		return append(append([]string(nil), Tabs...), ChatTab)
	}
	return Tabs
}

// LogsTab follows the selected job's log as plain text, so the board works without tmux.
const LogsTab = "Logs"

// ValidTab is true for the board tabs and for the Logs screen.
func ValidTab(name string) bool {
	if name == LogsTab {
		return true
	}
	for _, t := range Tabs {
		if t == name {
			return true
		}
	}
	return false
}

// TabAlias maps the names earlier versions used (and the ones people type) to a current tab.
func TabAlias(name string) string {
	switch name {
	case "Workspace", "Fleet":
		return EnvTab
	case "Term":
		return SessionTab
	}
	return name
}

func glyph(r view.Row) string {
	if r.Glyph == "" {
		return "·"
	}
	return r.Glyph
}

// Render draws one frame at w by h cells as text. Colour appears only when
// st.Styled is set, so frames are comparable as plain text.
func Render(v *view.View, st State, w, h int) string {
	return strings.Join(RenderLines(v, st, w, h), "\n")
}

// RenderLines is Render before joining, for callers that decorate lines.
func RenderLines(v *view.View, st State, w, h int) []string {
	out := frame(v, st, w, h)
	if st.ASCII {
		for i := range out {
			out[i] = asciiOnly(out[i])
		}
	}
	return out
}

func frame(v *view.View, st State, w, h int) []string {
	if w < minWidth || h < minHeight {
		lines := make([]string, h)
		if h > 0 {
			lines[0] = fit(fmt.Sprintf("Rig · window too small (need %d×%d); q quits", minWidth, minHeight), w)
		}
		for i := 1; i < h; i++ {
			lines[i] = fit("", w)
		}
		return lines
	}
	body := h - 5
	st.WideSession = sessionGeometry(w, h).ok
	lines := []string{headerLine(v, st, w), tabLine(v, st, w)}
	if st.HelpOpen {
		lines = append(lines, ruleS(st, "Keys", w))
		lines = append(lines, helpLines(w, body)...)
		lines = append(lines, messageLine(st, w), footerLine(nil, 0, st, w))
		for len(lines) < h {
			lines = append(lines, fit("", w))
		}
		return lines[:h]
	}
	if st.EvidenceOpen {
		lines = append(lines, ruleS(st, "Evidence › "+sanitize(st.EvidenceID), w))
		lines = append(lines, evidenceLines(st, w, body)...)
		lines = append(lines, messageLine(st, w), footerLine(nil, 0, st, w))
		for len(lines) < h {
			lines = append(lines, fit("", w))
		}
		return lines[:h]
	}
	if st.Tab == SessionTab && sessionGeometry(w, h).ok {
		return sessionFrame(v, st, w, h)
	}
	if st.Tab == SessionTab {
		lines = append(lines, ruleS(st, termTitle(st), w))
		lines = append(lines, termBody(st, w, body)...)
		lines = append(lines, messageLine(st, w), footerLine(nil, 0, st, w))
		for len(lines) < h {
			lines = append(lines, fit("", w))
		}
		return lines[:h]
	}
	if st.Tab == ChatTab {
		lines = append(lines, ruleS(st, chatTitle(st), w))
		lines = append(lines, chatBody(st, w, body)...)
		lines = append(lines, messageLine(st, w), footerLine(nil, 0, st, w))
		for len(lines) < h {
			lines = append(lines, fit("", w))
		}
		return lines[:h]
	}
	if st.Tab == LogsTab {
		lines = append(lines, ruleS(st, logTitle(st), w))
		lines = append(lines, logLines(st, w, body)...)
		lines = append(lines, messageLine(st, w), footerLine(nil, 0, st, w))
		for len(lines) < h {
			lines = append(lines, fit("", w))
		}
		return lines[:h]
	}
	if st.Tab == SettingsTab {
		lines = append(lines, ruleS(st, "Theme", w))
		lines = append(lines, themeLines(st, w)...)
		lines = append(lines, ruleS(st, panelTitle(st), w))
		lines = append(lines, panelBody(st, w, body-len(themeLines(st, w))-1)...)
		lines = append(lines, messageLine(st, w), footerLine(nil, 0, st, w))
		for len(lines) < h {
			lines = append(lines, fit("", w))
		}
		return lines[:h]
	}
	if st.Tab == EnvTab {
		lines = append(lines, ruleS(st, panelTitle(st), w))
		lines = append(lines, panelBody(st, w, body)...)
		lines = append(lines, messageLine(st, w), footerLine(nil, 0, st, w))
		for len(lines) < h {
			lines = append(lines, fit("", w))
		}
		return lines[:h]
	}
	rows := v.Rows(st.Tab)
	sel := clampSel(st.Selected[st.Tab], len(rows))
	split := w >= splitMinWidth
	stacked := !split && h >= stackMinHeight
	eh := 1
	if (st.Tab == "Jobs" || st.Tab == "Queue") && body >= twoLineMinBody {
		eh = 2
	}

	switch {
	case st.DetailOpen && !split && !stacked:
		lines = append(lines, ruleS(st, fmt.Sprintf("%s › %s", st.Tab, selID(rows, sel)), w))
		lines = append(lines, detailLines(rows, sel, st.DetailOff, w, body)...)
	case split:
		lw := (w*52 + 50) / 100
		lw = max(52, min(72, lw))
		dw := w - lw - 1
		lines = append(lines, ruleS(st, rangeText(st.Tab, rows, st.Offsets[st.Tab], sel, body, eh), lw)+sepTop(st)+ruleS(st, "", dw))
		list := listLines(rows, sel, st.Offsets[st.Tab], lw, body, eh, st.Styled, st.Tab == "Jobs")
		det := detailLines(rows, sel, st.DetailOff, dw, body)
		for i := 0; i < body; i++ {
			lines = append(lines, list[i]+bar(st)+det[i])
		}
	case stacked:
		lb := (body*60 + 50) / 100
		lines = append(lines, ruleS(st, rangeText(st.Tab, rows, st.Offsets[st.Tab], sel, lb, eh), w))
		lines = append(lines, listLines(rows, sel, st.Offsets[st.Tab], w, lb, eh, st.Styled, st.Tab == "Jobs")...)
		lines = append(lines, ruleS(st, "Detail", w))
		lines = append(lines, detailLines(rows, sel, st.DetailOff, w, body-lb-1)...)
	default:
		lines = append(lines, ruleS(st, rangeText(st.Tab, rows, st.Offsets[st.Tab], sel, body, eh), w))
		lines = append(lines, listLines(rows, sel, st.Offsets[st.Tab], w, body, eh, st.Styled, st.Tab == "Jobs")...)
	}
	lines = append(lines, messageLine(st, w), footerLine(rows, sel, st, w))
	for len(lines) < h {
		lines = append(lines, fit("", w))
	}
	return lines[:h]
}

func clampSel(sel, n int) int {
	if n == 0 {
		return 0
	}
	return max(0, min(sel, n-1))
}

func selID(rows []view.Row, sel int) string {
	if len(rows) == 0 {
		return ""
	}
	return rows[sel].ID
}

func headerLine(v *view.View, st State, w int) string {
	if v == nil {
		msg := " Rig · loading…"
		if st.Stale != "" {
			msg = " Rig · no snapshot yet"
		}
		return fit(msg, w)
	}
	hd := v.Header
	health := fmt.Sprintf("! %d need attention  ● %d running  slots %d/%d", hd.Attention, hd.Running, hd.Slots, hd.Cap)
	if w < 60 {
		health = fmt.Sprintf("!%d ●%d %d/%d", hd.Attention, hd.Running, hd.Slots, hd.Cap)
	}
	title := " Rig · " + clean(hd.Project)
	if st.Workspace != nil && st.Workspace.Context != "" && w >= 100 {
		title += " · " + clean(st.Workspace.Context)
	}
	left := strings.TrimRight(fit(title, max(1, w-lipgloss.Width(health)-3)), " ")
	gap := max(2, w-lipgloss.Width(left)-lipgloss.Width(health)-1)
	return fit(left+strings.Repeat(" ", gap)+health, w)
}

// tabShort are distinct three-letter forms for a narrow tab bar ("Wor" would be both Workflows and Workspace).
var tabShort = map[string]string{"Jobs": "Job", "Queue": "Que", "Workflows": "Flw", EnvTab: "Env", SettingsTab: "Set", LogsTab: "Log", ChatTab: "Cht", SessionTab: "Ses"}

func tabLine(v *view.View, st State, w int) string {
	if line := tabLineWith(v, st, false); lipgloss.Width(line) <= w {
		return fit(line, w)
	}
	return fit(tabLineWith(v, st, true), w)
}

// tabLineWith builds the tab bar; short abbreviates the inactive tabs so the active one is never cut off.
func tabLineWith(v *view.View, st State, short bool) string {
	var parts []string
	active := st.Tab
	if active == LogsTab {
		active = "Jobs" // the log is a screen under Jobs
	}
	for _, name := range TabList(st) {
		label := name
		if short && name != active {
			label = tabShort[name]
		}
		if v != nil {
			for _, t := range v.Tabs {
				if t.Name != name {
					continue
				}
				switch {
				case name == "Workflows": // the mockup counts jobs and the queue, not workflows
				case t.Attention > 0:
					label += fmt.Sprintf(" !%d", t.Attention)
				case t.Count > 0:
					label += fmt.Sprintf(" %d", t.Count)
				}
			}
		}
		if name == SettingsTab && st.Settings != nil && st.Settings.Attention {
			label += " !"
		}
		if name == active {
			label = "[" + label + "]"
		} else {
			label = " " + label + " "
		}
		parts = append(parts, label)
	}
	return strings.Join(parts, " ")
}

func rule(title string, w int) string {
	if w <= 0 {
		return ""
	}
	if title == "" {
		return strings.Repeat("─", w)
	}
	head := "─ " + title + " "
	if lipgloss.Width(head) >= w {
		return fit(head, w)
	}
	return head + strings.Repeat("─", w-lipgloss.Width(head))
}

func rangeText(tab string, rows []view.Row, off, sel, capRows, eh int) string {
	n := len(rows)
	if n == 0 {
		return tab
	}
	start, end := windowRange(rows, off, sel, capRows, eh, tab == "Jobs")
	text := fmt.Sprintf("%s %d-%d/%d", tab, start+1, end, n)
	if tab == "Jobs" {
		text += " · attention first"
	}
	return text
}

func windowStart(off, sel, per, n int) int {
	off = max(0, min(off, max(0, n-per)))
	if sel < off {
		off = sel
	}
	if sel >= off+per {
		off = sel - per + 1
	}
	return max(0, off)
}

// WindowStart exposes the viewport rule so the model keeps offsets in step.
func WindowStart(off, sel, bodyRows, eh, n int) int {
	return windowStart(off, sel, max(1, bodyRows/eh), n)
}

// firstFinished is the index where the "Finished" group starts, or -1 when the list has no such boundary
// (everything is finished, or nothing is).
func firstFinished(rows []view.Row) int {
	for i, r := range rows {
		for _, name := range strings.Fields(finishedLabels) {
			if r.Label == name {
				if i == 0 {
					return -1
				}
				return i
			}
		}
	}
	return -1
}

// windowRange is the rows [start, end) that fit capRows lines, counting the one-line "Finished" divider
// before the first finished row, and always including the selected row.
func windowRange(rows []view.Row, off, sel, capRows, eh int, groups bool) (int, int) {
	n := len(rows)
	fs := -1
	if groups {
		fs = firstFinished(rows)
	}
	start := windowStart(off, sel, max(1, capRows/eh), n)
	for {
		used, end := 0, start
		for i := start; i < n; i++ {
			need := eh
			if i == fs {
				need++
			}
			if used+need > capRows {
				break
			}
			used += need
			end = i + 1
		}
		if sel < end || start >= sel {
			return start, max(end, min(n, start+1))
		}
		start++
	}
}

func listLines(rows []view.Row, sel, off, w, capRows, eh int, styled, groups bool) []string {
	out := make([]string, 0, capRows)
	if len(rows) == 0 {
		out = append(out, fit("  Nothing here yet.", w))
	} else {
		start, end := windowRange(rows, off, sel, capRows, eh, groups)
		fs := -1
		if groups {
			fs = firstFinished(rows)
		}
		for i := start; i < end; i++ {
			if i == fs {
				div := fit("  Finished", w)
				if styled {
					div = muted(div)
				}
				out = append(out, div)
			}
			out = append(out, entry(rows[i], i == sel, w, eh, styled)...)
		}
	}
	for len(out) < capRows {
		out = append(out, fit("", w))
	}
	return out[:capRows]
}

func entry(r view.Row, selected bool, w, eh int, styled bool) []string {
	marker := " "
	if selected {
		marker = ">"
	}
	label := fit(glyph(r)+" "+clean(r.Label), 13)
	prefixPlain := fmt.Sprintf("%s %s  ", marker, label)
	room := w - lipgloss.Width(prefixPlain)
	title := clean(r.Title)
	if r.Summary != "" {
		title = fit(title, max(1, room-lipgloss.Width(r.Summary)-2)) + "  " + r.Summary
	}
	rest := strings.TrimRight(title, " ")
	if w >= 70 && r.ID != "" {
		tag := abbrevTail(clean(r.ID), 12)
		used := lipgloss.Width(prefixPlain) + lipgloss.Width(rest)
		if used+2+lipgloss.Width(tag) <= w {
			rest += strings.Repeat(" ", w-used-lipgloss.Width(tag)) + tag
		}
	}
	rest = fit(rest, room)
	second := ""
	if eh == 2 {
		second = fit("    "+clean(r.Second), w)
	}
	if styled {
		if selected {
			return selectedRow(marker, label, r.Tone, rest, second, eh)
		}
		lines := []string{tone(r.Tone).Render(marker + " " + label + "  " + rest)} // the mockup colours the whole row
		if eh == 2 {
			lines = append(lines, muted(second))
		}
		return lines
	}
	lines := []string{marker + " " + label + "  " + rest}
	if eh == 2 {
		lines = append(lines, second)
	}
	return lines
}

func abbrevTail(s string, n int) string {
	r := []rune(s)
	if len(r) <= n {
		return s
	}
	return "…" + string(r[len(r)-n+1:])
}

func detailLines(rows []view.Row, sel, off, w, h int) []string {
	out := make([]string, 0, h)
	if len(rows) == 0 || h <= 0 {
		for len(out) < h {
			out = append(out, fit("", w))
		}
		return out
	}
	r := rows[sel]
	var all []string
	all = append(all, wrap(glyph(r)+" "+r.Label, w-2, "  ")...)
	if r.Explanation != "" {
		all = append(all, wrap(r.Explanation, w-2, "  ")...)
	}
	all = append(all, "")
	for _, line := range r.Detail {
		all = append(all, wrap(line, w-2, "  ")...)
	}
	off = max(0, min(off, max(0, len(all)-h)))
	for _, l := range all[off:] {
		if len(out) == h {
			break
		}
		out = append(out, fit(" "+l, w))
	}
	for len(out) < h {
		out = append(out, fit("", w))
	}
	return out
}

func messageLine(st State, w int) string {
	switch st.Mode {
	case ModeConfirm:
		return fit(" "+st.Prompt+"  y yes · n no", w)
	case ModeInput:
		label := "enqueue"
		if st.InputLabel != "" {
			label = st.InputLabel
		}
		return fit(" "+label+" › "+st.Prompt+"▏", w)
	}
	switch {
	case st.Stale != "":
		return fit(" "+clean(st.Stale), w)
	case st.Message != "":
		return fit(" "+clean(st.Message), w)
	}
	return fit("", w)
}

// helpKeys is every key, grouped, for the ? screen; the footer shows only the few a row of text has room for.
var helpKeys = [][2]string{
	{"type, Enter", "session: ask the parent (the box always has the keyboard)"},
	{"/", "session: commands: /parent /queue /new /stop /jobs /env /help /quit"},
	{"Esc", "session: clear the box, then board keys (Enter or i types again)"},
	{"PgUp, PgDn", "session: scroll the conversation"},
	{"Tab, Shift-Tab", "next or previous tab"},
	{"j, k", "move (scroll on panels, logs and the evidence drawer)"},
	{"Enter, Esc", "open or close details"},
	{"a, d", "allow or deny the selected worker's prompt"},
	{"e", "park a task in the queue (Enter saves, Esc discards)"},
	{"v", "evidence drawer for the selected job"},
	{"w", "workspace: container, ports, worktrees, doctor"},
	{"l", "jobs: follow the selected job's log (Esc returns)"},
	{"s", "shell in the selected job's container"},
	{"x", "stop the selected job or queue item, after a confirmation"},
	{"r", "refresh"},
	{"R", "workspace: apply the proposed fix, after a confirmation"},
	{"G", "logs: follow the tail again"},
	{"?", "this list"},
	{"q", "quit"},
}

func helpLines(w, h int) []string {
	out := make([]string, 0, h)
	for _, kv := range helpKeys {
		out = append(out, fit(fmt.Sprintf(" %-16s %s", kv[0], kv[1]), w))
	}
	out = append(out, fit("", w), fit(" Esc or ? closes this list.", w))
	for len(out) < h {
		out = append(out, fit("", w))
	}
	return out[:h]
}

func footerLine(rows []view.Row, sel int, st State, w int) string {
	keys := []string{"Tab pane", "Enter details", "e enqueue", "v evidence", "w workspace", "? help", "q quit"}
	if st.Tab == EnvTab {
		keys = []string{"Tab pane", "j/k scroll", "r refresh", "? help", "q quit"}
		if st.Workspace != nil && st.Workspace.Recovery != nil && st.Workspace.Recovery.Safe && !st.Workspace.Recovery.Ready {
			keys = []string{"Tab pane", "j/k scroll", "R recover", "r refresh", "? help", "q quit"}
		}
	}
	if st.Tab == SettingsTab {
		keys = []string{"←→ theme", "g where", "j/k scroll", "rest read-only: edit .rig/harness.toml", "? help", "q quit"}
	}
	if st.Tab == LogsTab {
		keys = []string{"Esc back to jobs", "k/j scroll", "G follow", "s shell", "r refresh", "q quit"}
	}
	if st.Tab == SessionTab {
		keys = []string{"Tab pane", "p parent CLI", "s container shell", "Enter type in it", "x close", "q quit"}
		if st.PaneFocus {
			keys = []string{"Ctrl-] back to the board (every other key goes to the program)"}
		}
	}
	if st.Tab == ChatTab {
		keys = []string{"Tab pane", "c write", "N new session", "x stop", "k/j scroll", "G follow", "q quit"}
	}
	if st.EvidenceOpen {
		keys = []string{"Esc close", "j/k scroll", "r refresh", "q quit"}
	}
	if st.HelpOpen {
		keys = []string{"Esc close", "q quit"}
	}
	if st.Mode == ModeInput {
		keys = []string{"Enter queue it", "Esc discard"}
	} else if st.Mode == ModeConfirm {
		keys = []string{"y confirm", "n cancel"}
	} else if len(rows) > 0 && st.Tab == "Jobs" && rows[sel].Ask {
		keys = append([]string{"a allow", "d deny"}, keys...)
	}
	return footerWith(keys, st, w)
}

// footerWith lays out the key hints on one line.
func footerWith(keys []string, st State, w int) string {
	return fit(" "+strings.Join(keys, "  "), w)
}

func termTitle(st State) string {
	if st.PaneTitle == "" {
		return "Session"
	}
	return "Session › " + sanitize(st.PaneTitle)
}

// termBody is the pane's screen, or the help shown while nothing runs.
func termBody(st State, w, h int) []string {
	out := make([]string, 0, h)
	if len(st.PaneLines) == 0 {
		for _, l := range []string{
			" Run an interactive program here, with no tmux:",
			"",
			"   p  the parent CLI for this project",
			"   s  a shell in this project's container (dc exec --no-start)",
			"",
			" Enter types into it; Ctrl-] returns to the board; x closes it.",
		} {
			out = append(out, fit(l, w))
		}
	} else {
		for i := 0; i < h && i < len(st.PaneLines); i++ {
			out = append(out, fit(st.PaneLines[i], w))
		}
	}
	for len(out) < h {
		out = append(out, fit("", w))
	}
	return out
}

func chatTitle(st State) string {
	if st.Chat != nil {
		c := st.Chat
		title := "Chat (experiment) › " + sanitize(c.Session) + " · " + sanitize(c.Parent) + " · " + sanitize(c.State)
		if c.Note != "" {
			title += " (" + sanitize(c.Note) + ")"
		}
		if st.ChatUp > 0 {
			title += fmt.Sprintf(" · %d lines back", st.ChatUp)
		}
		return title
	}
	return "Chat (experiment)"
}

// chatBody renders the transcript: you, the parent, and its tool calls, wrapped, newest at the bottom.
func chatBody(st State, w, h int) []string {
	if st.Picker != nil {
		return lastLines(pickerLines(st, w), 0, w, h)
	}
	return lastLines(append(chatAll(st, w), askLines(nil, st, w)...), st.ChatUp, w, h)
}

// lastLines is the h lines ending up lines before the newest, padded to w.
func lastLines(all []string, up, w, h int) []string {
	end := len(all) - min(up, max(0, len(all)-h))
	start := max(0, end-h)
	out := make([]string, 0, h)
	for _, line := range all[start:end] {
		out = append(out, fit(line, w))
	}
	for len(out) < h {
		out = append(out, fit("", w))
	}
	return out
}

// chatTone is how the mockup colours a transcript line: who spoke is muted, tool calls are blue, failures amber.
func chatTone(role, prefix, text string, styled bool) string {
	if !styled {
		return prefix + text
	}
	switch role {
	case "tool":
		return lipgloss.NewStyle().Foreground(lipgloss.Color(colAccent)).Render(prefix + text)
	case "error":
		return tone("attention").Render(prefix + text)
	case "system":
		return muted(prefix + text)
	}
	return muted(prefix) + text
}

// chatAll is every transcript line, wrapped to w.
func chatAll(st State, w int) []string {
	var all []string
	switch {
	case st.Chat == nil && st.ChatE != "":
		for _, l := range wrap("Chat unavailable: "+st.ChatE, w-2, "  ") {
			all = append(all, " "+l)
		}
	case st.Chat == nil:
		all = []string{" No chat session yet. Press c to write the first message; it starts a headless parent turn."}
	default:
		parent := sanitize(st.Chat.Parent)
		if st.WideSession && st.Tab == SessionTab {
			parent = "parent" // the mockup's word; the column's title names the CLI
		}
		entries := st.Chat.Entries
		if st.Chat.State == "running" && strings.TrimSpace(st.Chat.Partial) != "" {
			entries = append(append([]view.ChatEntry(nil), entries...), view.ChatEntry{Role: "assistant", Text: st.Chat.Partial})
		}
		for _, e := range entries {
			prefix := parent + " › "
			switch e.Role {
			case "user":
				prefix = "you › "
			case "tool":
				prefix = "  · "
			case "result":
				prefix = "    ⎿ "
			case "result-error":
				prefix = "    ⎿ ! "
			case "diff":
				prefix = "  ± "
			case "error":
				prefix = "! "
			case "system":
				prefix = "  – "
			}
			rows := strings.Split(strings.TrimRight(e.Text, "\n"), "\n")
			for i, raw := range rows {
				lead := prefix
				if i > 0 {
					lead = strings.Repeat(" ", lipgloss.Width(prefix))
					if e.Role == "assistant" && st.WideSession && st.Tab == SessionTab {
						lead = "  " // the mockup starts the parent's next line under its name, not its text
					}
				}
				for j, l := range wrap(sanitize(raw), max(8, w-2-lipgloss.Width(prefix)), "") {
					if j > 0 {
						lead = strings.Repeat(" ", lipgloss.Width(prefix))
					}
					all = append(all, " "+chatTone(e.Role, lead, l, st.Styled))
				}
			}
			if st.WideSession && st.Tab == SessionTab {
				all = append(all, "") // the mockup leaves a line between turns
			}
		}
		if len(st.Chat.Entries) == 0 {
			all = []string{" (empty session)"}
		}
		if st.Chat.State == "running" && st.Chat.Ask == nil {
			all = append(all, workingLine(st))
		}
		if st.ChatE != "" {
			all = append(all, ruleS(st, "stale", w))
			for _, l := range wrap("refresh failed: "+st.ChatE, w-2, "  ") {
				all = append(all, " "+l)
			}
		}
	}
	return all
}

func logTitle(st State) string {
	if st.Log != nil && st.Log.Job != nil {
		j := st.Log.Job
		title := "Logs › " + sanitize(j.ID)
		if j.Label != "" || j.Worker != "" {
			title += " · " + sanitize(strings.TrimSpace(j.Label+" "+j.Worker))
		}
		if st.LogUp > 0 {
			title += fmt.Sprintf(" · %d lines back", st.LogUp)
		}
		return title
	}
	return "Logs"
}

// logLines renders the log tail: plain text, wrapped, newest at the bottom, scrolled back by st.LogUp.
func logLines(st State, w, h int) []string {
	var all []string
	switch {
	case st.Log == nil && st.LogE != "":
		for _, l := range wrap("Logs unavailable: "+st.LogE, w-2, "  ") {
			all = append(all, " "+l)
		}
	case st.Log == nil:
		all = []string{" Loading log…"}
	case st.Log.Job == nil:
		all = []string{" No jobs yet. Output of the active job appears here."}
	default:
		for _, raw := range strings.Split(strings.TrimRight(st.Log.Text, "\n"), "\n") {
			raw = sanitize(raw) // per line: sanitize drops control characters, newlines included
			if raw == "" {
				all = append(all, "")
				continue
			}
			for _, l := range wrap(raw, w-2, "  ") {
				all = append(all, " "+l)
			}
		}
		if st.LogE != "" {
			all = append(all, ruleS(st, "stale", w))
			for _, l := range wrap("refresh failed: "+st.LogE, w-2, "  ") {
				all = append(all, " "+l)
			}
		}
	}
	end := len(all) - min(st.LogUp, max(0, len(all)-h))
	start := max(0, end-h)
	out := make([]string, 0, h)
	for _, line := range all[start:end] {
		out = append(out, fit(line, w))
	}
	for len(out) < h {
		out = append(out, fit("", w))
	}
	return out
}

// envSections is the Env tab: the workspace panel, then the fleet panel, each with its own failure shown in
// place so one slow or broken source never hides the other. Nil while neither has answered.
func envSections(st State) *[]view.Section {
	if st.Workspace == nil && st.Fleet == nil && st.WorkspaceE == "" && st.FleetE == "" {
		return nil
	}
	var all []view.Section
	part := func(title string, p *view.Panel, errText string) {
		switch {
		case p == nil && errText != "":
			all = append(all, view.Section{Title: title, Lines: []view.Line{{Text: title + " unavailable: " + errText}}})
		case p == nil:
			all = append(all, view.Section{Title: title, Lines: []view.Line{{Text: "Loading " + strings.ToLower(title) + "…"}}})
		default:
			all = append(all, p.Sections...)
			if errText != "" {
				all = append(all, view.Section{Title: title + " (stale)", Lines: []view.Line{{Text: "refresh failed: " + errText}}})
			}
		}
	}
	var ws *view.Panel
	if st.Workspace != nil {
		ws = &view.Panel{Sections: st.Workspace.Sections}
	}
	part("Workspace", ws, st.WorkspaceE)
	part("Fleet", st.Fleet, st.FleetE)
	return &all
}

// panelTitle is the first section's title: it labels the rule under the tab bar, so that section
// does not repeat it.
func panelTitle(st State) string {
	if st.Tab == SettingsTab {
		if st.Settings != nil && len(st.Settings.Sections) > 0 {
			return sanitize(st.Settings.Sections[0].Title)
		}
		return SettingsTab
	}
	if sections := envSections(st); sections != nil && len(*sections) > 0 {
		return sanitize((*sections)[0].Title)
	}
	return EnvTab
}

// themeLines is the Settings tab's Theme row, as the design's Settings › General draws it: the themes in a row with
// the one in use on a chip, and where it is kept.
func themeLines(st State, w int) []string {
	current := st.Theme
	if current == "" {
		current = "rig"
	}
	row := "  ›"
	for _, name := range ThemeNames() {
		chip := "  " + name + "  "
		if name == current {
			chip = " [" + name + "] "
			if st.Styled {
				chip = " " + lipgloss.NewStyle().Background(lipgloss.Color(colSelBg)).Foreground(lipgloss.Color(colText)).Bold(true).Render(" "+name+" ") + " "
			}
		} else if st.Styled {
			chip = muted(chip)
		}
		row += chip
	}
	where := map[string]string{"repo": "this repo", "all": "all repos", "this board": "this board only (--theme)"}[st.ThemeScope]
	if where == "" {
		where = "all repos (not saved yet)"
	}
	row += "   " + where + "   "
	hint := "←→ change · g all repos / this repo"
	if st.Styled {
		hint = muted(hint)
	}
	return []string{fit(row+hint, w)}
}

// panelBody renders the Env or Settings tab.
func panelBody(st State, w, h int) []string {
	if st.Tab == SettingsTab {
		return sectionLines(st.Settings.SectionsOrNil(), st.SettingsE, SettingsTab, true, st, w, h)
	}
	return sectionLines(envSections(st), "", EnvTab, true, st, w, h)
}

// evidenceLines renders the drawer for one job; every section, including the first, gets its own rule.
func evidenceLines(st State, w, h int) []string {
	var sections *[]view.Section
	if st.Evidence != nil {
		sections = &st.Evidence.Sections
	}
	return sectionLines(sections, st.EvidenceE, "Evidence", false, st, w, h)
}

func sectionLines(sections *[]view.Section, errText, name string, firstInRule bool, st State, w, h int) []string {
	var all []string
	switch {
	case sections == nil && errText != "":
		for _, l := range wrap(name+" unavailable: "+errText, w-2, "  ") {
			all = append(all, " "+l)
		}
	case sections == nil:
		all = []string{" Loading " + strings.ToLower(name) + "…"}
	default:
		for i, sec := range *sections {
			if i > 0 || !firstInRule {
				all = append(all, ruleS(st, sanitize(sec.Title), w))
			}
			for _, l := range sec.Lines {
				all = append(all, " "+tonedLine(l.Tone, sanitize(l.Text), st.Styled))
			}
		}
		if errText != "" {
			all = append(all, ruleS(st, "stale", w))
			for _, l := range wrap("refresh failed: "+errText, w-2, "  ") {
				all = append(all, " "+l)
			}
		}
	}
	off := max(0, min(st.DetailOff, max(0, len(all)-h)))
	out := make([]string, 0, h)
	for _, line := range all[off:] {
		if len(out) == h {
			break
		}
		out = append(out, fit(line, w))
	}
	for len(out) < h {
		out = append(out, fit("", w))
	}
	return out
}

func tonedLine(toneName, text string, styled bool) string {
	if styled && toneName != "" {
		if toneName == "muted" {
			return muted(text)
		}
		return tone(map[string]string{"ok": "verified", "attention": "attention", "active": "active"}[toneName]).Render(text)
	}
	return text
}
