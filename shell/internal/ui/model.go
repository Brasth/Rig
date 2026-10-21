package ui

import (
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"os/exec"
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"

	"github.com/Brasth/Rig/shell/internal/api"
	"github.com/Brasth/Rig/shell/internal/term"
	"github.com/Brasth/Rig/shell/internal/view"
)

// Source is what the model needs from the API client. *api.Client satisfies it.
type Source interface {
	Snapshot(start, rows int, timeout time.Duration) (*view.Snapshot, error)
	Act(method string, params map[string]any, timeout time.Duration) (string, error)
	Workspace(deep bool, timeout time.Duration) (*view.Workspace, error)
	Fleet(timeout time.Duration) (*view.Panel, error)
	Settings(timeout time.Duration) (*view.Panel, error)
	JobEvidence(id string, timeout time.Duration) (*view.Evidence, error)
	Recover(confirmID string, timeout time.Duration) (string, error)
	Log(id string, lines int, timeout time.Duration) (*view.LogReply, error)
	JobThread(id string, timeout time.Duration) (*view.JobThread, error)
	JobProblems(id string, timeout time.Duration) (*view.JobProblems, error)
	Shell(id string, timeout time.Duration) (*view.ShellPlan, error)
	Parent(name string, timeout time.Duration) (*view.ShellPlan, error)
	ChatStatus(timeout time.Duration) (*view.ChatStatus, error)
	ChatCommands(parent string, timeout time.Duration) (*view.ParentCommands, error)
	ChatModels(parent string, timeout time.Duration) (*view.ParentModels, error)
	ChatTranscript(session string, tail int, timeout time.Duration) (*view.ChatTranscript, error)
	ChatSend(message, session, parent string, timeout time.Duration) (*view.ChatStarted, error)
	ChatCancel(session string, timeout time.Duration) error
	ChatFork(session string, turn int, timeout time.Duration) (*view.ChatForked, error)
	ChatRewind(session string, turn int, path string, dry, undo bool, timeout time.Duration) (*view.ChatRewound, error)
	ChatAnswer(session, askID, decision string, timeout time.Duration) error
	ChatSetParent(name string, timeout time.Duration) error
	Theme(timeout time.Duration) (*view.ThemeReply, error)
	JobDiff(id string, timeout time.Duration) (*view.JobDiff, error)
	AppSettings(timeout time.Duration) (*view.AppSettings, error)
	Today(timeout time.Duration) (*view.Today, error)
	ShipStatus(timeout time.Duration) (*view.ShipStatus, error)
	SetSetting(key, value, scope string, timeout time.Duration) (*view.AppSettings, error)
	SetTheme(theme, scope string, timeout time.Duration) (*view.ThemeReply, error)
}

const (
	pollEvery      = time.Second
	workspaceEvery = 5 * time.Second
	logEvery       = 2 * time.Second
	chatEvery      = time.Second
	chatLiveEvery  = 300 * time.Millisecond // while the parent answers, so its reply streams in
	spinEvery      = 100 * time.Millisecond
	chatStatEvery  = 10 * time.Second
	logFetchRows   = 200
	callTimeout    = 5 * time.Second
	snapshotRows   = 200
	finishedLabels = "Verified Unverified Failed Cancelled"
)

type tickMsg struct{}

type snapshotMsg struct {
	snap *view.Snapshot
	err  error
	at   time.Time
}

type workspaceMsg struct {
	ws  *view.Workspace
	err error
	at  time.Time
}

type fleetMsg struct {
	panel *view.Panel
	err   error
	at    time.Time
}

type settingsMsg struct {
	panel *view.Panel
	err   error
}

type evidenceMsg struct {
	ev  *view.Evidence
	err error
	id  string
}

type logMsg struct {
	log    *view.LogReply
	thread *view.JobThread // a helper's thread, when the server has job.thread
	err    error
	at     time.Time
}

// Pane is a program running on an emulated terminal. *term.Pane is the real one; tests use a fake.
type Pane interface {
	Resize(cols, rows int)
	Write([]byte)
	AppCursor() bool
	Lines(styled, showCursor bool) []string
	LinesFrom(up int, styled, showCursor bool) []string // scrolled up into the scrollback
	Scrollback() int
	Updates() <-chan struct{}
	Exited() (bool, error)
	Close()
}

type paneMsg struct{ p Pane }

type panePlanMsg struct {
	plan *view.ShellPlan
	err  error
}

func spawnPane(argv []string, dir string, env []string, cols, rows int) (Pane, error) {
	p, err := term.Start(argv, dir, env, cols, rows)
	if err != nil {
		return nil, err
	}
	return p, nil
}

type chatStatusMsg struct {
	status *view.ChatStatus
	err    error
}

type chatMsg struct {
	chat *view.ChatTranscript
	err  error
}

type chatSentMsg struct {
	started *view.ChatStarted
	err     error
	sent    string
	quiet   bool // a command that starts no turn and leaves the box alone (the mode key)
}

type chatCancelMsg struct{ err error }

type chatAnsweredMsg struct{ err error }

type parentSetMsg struct {
	name string
	err  error
}

type spinMsg struct{}

type themeMsg struct {
	reply *view.ThemeReply
	err   error
	saved bool
}

type shellPlanMsg struct {
	plan *view.ShellPlan
	err  error
}

type shellDoneMsg struct{ err error }

type recoverMsg struct {
	text string
	err  error
}

type actionMsg struct {
	text    string
	err     error
	enqueue bool
}

// Model is the Bubble Tea model for the board pane.
type Model struct {
	src        Source
	st         State
	view       *view.View
	w, h       int
	fetching   bool
	wsBusy     bool
	wsLast     time.Time
	flBusy     bool
	flLast     time.Time
	lgBusy     bool
	lgLast     time.Time
	chBusy     bool
	chLast     time.Time
	csBusy     bool
	csLast     time.Time
	dkBusy     bool
	stBusy     bool
	stLast     time.Time
	dkLast     time.Time
	pane       Pane
	paneName   string
	paneFocus  bool
	parentName string // the parent CLI `rig shell --parent` asked for; empty means the project's own
	spawn      func(argv []string, dir string, env []string, cols, rows int) (Pane, error)
	chatID     string // the session the Chat tab follows; empty until one exists
	inputFor   string // "chat" while the input line is a chat message, else enqueue
	// exec hands the terminal to a command and returns when it exits; tests replace it.
	exec        func(argv []string, done func(error) tea.Msg) tea.Cmd
	lastOK      time.Time
	now         func() time.Time
	newKey      func() string
	selID       map[string]string
	draftKey    string
	pendingX    string // method for a confirmed stop: job.cancel or queue.cancel
	pendingI    string // id for a confirmed stop
	pickerSeen  bool   // the picker opened by itself once, for a project with no parent
	chatAdopted bool   // a conversation was picked up, or one was left on purpose: none is picked up again
	framed      bool   // the window has room for the mockup's border around the board
	winW, winH  int    // the window; the app uses all of it
	dfBusy      bool
	dfLast      time.Time
	lastAsk     string // the request the bell last rang for
	tdLast      time.Time
	ringer      func()                   // rings the terminal bell; tests replace it
	notifier    func(title, text string) // a desktop notice through the terminal (OSC 9 and 777); tests replace it
	splashFrom  time.Time                // when the splash began
	seenApplied map[string]bool          // the applied work done today already known, so only new applies notify
	seenJobs    map[string]bool
	themeFixed  bool // rig shell --theme chose the theme for this board; the saved one is not loaded
	messaging   bool // m was pressed under a request: a and d type again until the message is sent
	spinning    bool // the spinner's tick is scheduled
	chatStat    *view.ChatStatus
	cmdsFor     string    // the parent whose skills and commands the / menu has
	modelsAt    time.Time // when the parent's models were last listed
	edited      string    // the box as the editor last left it (app_edit.go)
	history     []string  // what was sent from the box, oldest first, for ↑
	histAt      int       // the message ↑ brought back; -1 while typing
	histDraft   string    // what was being typed before ↑
	histFor     string    // the conversation history was seeded from
	zones       []hitZone // where the last frame's clickable places are (mouse.go)
	plain       []string  // the last frame's text, for copying a selection
	press, sel  *textSel  // where the button went down, and the text dragged over since
	clip        string    // what the code editor copied or cut, for ⌃v
	codeDrag    bool      // the button went down in a file being edited: dragging selects
	boxDrag     bool      // the button went down in the reply box: dragging selects its text
	boxFrom     int       // where in the box's text it went down
	clickAt     time.Time // the last press, for a double click
	clickN      int       // presses in a row at one place: 2 a double click, 3 a triple one
	jumpBack    []jumpTo  // where go to definition came from, for ⌃t
	clicks      int
	clickX      int
	clickY      int
	hoverX      int    // the pointer, for the zone drawn underlined
	hoverKey    string // the hover lookup on its way: name@x,y
	probLast    time.Time
	hoverY      int
}

// NewModel returns a model that polls src.
func NewModel(src Source, ascii bool) *Model {
	st := NewState()
	st.Loading, st.ASCII, st.Styled = true, ascii, true
	st.WideSession = sessionGeometry(120, 36).ok
	return &Model{src: src, st: st, w: 120, h: 36, now: time.Now, newKey: randomKey, selID: map[string]string{}, exec: execProcess, spawn: spawnPane,
		hoverX: -1, hoverY: -1, histAt: -1}
}

func execProcess(argv []string, done func(error) tea.Msg) tea.Cmd {
	cmd := exec.Command(argv[0], argv[1:]...)
	return tea.ExecProcess(cmd, func(err error) tea.Msg { return done(err) })
}

func randomKey() string {
	b := make([]byte, 8)
	_, _ = rand.Read(b)
	return "shell-" + hex.EncodeToString(b)
}

func (m *Model) Init() tea.Cmd {
	if m.appOn() {
		cmds := []tea.Cmd{m.fetch(), tick(), m.fetchTheme(), m.fetchAppSettings()}
		if m.st.App.Splash > 0 {
			cmds = append(cmds, splashTick())
		}
		return tea.Batch(cmds...)
	}
	return tea.Batch(m.fetch(), tick(), m.fetchTheme())
}

// fetchTheme loads the saved theme once at the start (a server that predates themes keeps the default).
func (m *Model) fetchTheme() tea.Cmd {
	if m.themeFixed {
		return nil
	}
	src := m.src
	return func() tea.Msg {
		reply, err := src.Theme(callTimeout)
		return themeMsg{reply: reply, err: err}
	}
}

// SetTheme draws this board in the named theme without saving it (rig shell --theme); false for an unknown name.
func (m *Model) SetTheme(name string) bool {
	if !ApplyTheme(name) {
		return false
	}
	m.st.Theme, m.st.ThemeScope, m.themeFixed = name, "this board", true
	return true
}

// switchTheme applies a theme at once and keeps it where the current one is kept: this repo, or all repos.
func (m *Model) switchTheme(name, scope string) tea.Cmd {
	if !ApplyTheme(name) {
		m.st.Message = "no theme called " + name + "; the themes are " + strings.Join(ThemeNames(), ", ")
		return nil
	}
	if scope != "repo" {
		scope = "all"
	}
	m.st.Theme, m.themeFixed = name, false
	src := m.src
	return func() tea.Msg {
		reply, err := src.SetTheme(name, scope, callTimeout)
		return themeMsg{reply: reply, err: err, saved: true}
	}
}

// themeKey is the Settings tab's Theme row: ←→ change the theme, g keeps it for all repos or only this one.
func (m *Model) themeKey(key string) (tea.Cmd, bool) {
	names := ThemeNames()
	at := 0
	for i, n := range names {
		if n == m.st.Theme {
			at = i
		}
	}
	switch key {
	case "left", "h":
		return m.switchTheme(names[(at+len(names)-1)%len(names)], m.st.ThemeScope), true
	case "right", "l":
		return m.switchTheme(names[(at+1)%len(names)], m.st.ThemeScope), true
	case "g":
		scope := "repo"
		if m.st.ThemeScope == "repo" {
			scope = "all"
		}
		return m.switchTheme(names[at], scope), true
	}
	return nil, false
}

func tick() tea.Cmd { return tea.Tick(pollEvery, func(time.Time) tea.Msg { return tickMsg{} }) }

func (m *Model) fetch() tea.Cmd {
	if m.fetching {
		return nil
	}
	m.fetching = true
	src := m.src
	return func() tea.Msg {
		snap, err := src.Snapshot(0, snapshotRows, callTimeout)
		return snapshotMsg{snap: snap, err: err, at: time.Now()}
	}
}

// fetchWorkspace loads the workspace panel. It runs only while that tab is
// showing, at most once per interval unless forced, so dc-cli is not polled
// for a panel nobody is looking at.
func (m *Model) fetchWorkspace(force bool) tea.Cmd {
	if !m.showsWorkspace() || m.wsBusy || (!force && m.now().Sub(m.wsLast) < workspaceEvery) {
		return nil
	}
	m.wsBusy = true
	src := m.src
	return func() tea.Msg {
		ws, err := src.Workspace(true, 15*time.Second)
		return workspaceMsg{ws: ws, err: err, at: time.Now()}
	}
}

// fetchFleet loads the fleet panel only while its tab is showing, like the workspace panel.
func (m *Model) fetchFleet(force bool) tea.Cmd {
	if m.st.Tab != EnvTab || m.flBusy || (!force && m.now().Sub(m.flLast) < workspaceEvery) {
		return nil
	}
	m.flBusy = true
	src := m.src
	return func() tea.Msg {
		p, err := src.Fleet(20 * time.Second)
		return fleetMsg{panel: p, err: err, at: time.Now()}
	}
}

// composite is true while the Session tab shows the mockup's three columns and dock.
func (m *Model) composite() bool {
	return m.st.Tab == SessionTab && sessionGeometry(m.w, m.h).ok
}

// paneLive: a program is running in the Session pane (one that exited gives the middle column back to the chat).
func (m *Model) paneLive() bool {
	if m.pane == nil {
		return false
	}
	done, _ := m.pane.Exited()
	return !done
}

// chatInSession: the Session screen's middle column is the conversation with the parent, as in the mockup.
func (m *Model) chatInSession() bool { return m.composite() && m.st.ChatOn && !m.paneLive() }

// chatAsk is the parent's permission request a/d answer: shown in the conversation (the Chat tab, or the Session
// screen's middle column) and still waiting. It comes before a worker's ASK, which the Jobs list also shows.
func (m *Model) chatAsk() *view.ChatAsk {
	if m.st.Chat == nil || m.st.Chat.Ask == nil || !(m.st.Tab == ChatTab || m.chatInSession()) {
		return nil
	}
	return m.st.Chat.Ask
}

// startChatInput opens the mockup's "› Ask the parent" box.
func (m *Model) startChatInput() {
	m.st.Mode, m.st.InputLabel, m.inputFor, m.st.Message = ModeInput, "chat", "chat", ""
}

// autoFocus keeps the Session screen's ask line ready for typing once Tab (or i, m, /) gave it the keyboard: whenever nothing
// else holds the keyboard (a confirmation, the help list, the picker, board keys), the box does.
func (m *Model) autoFocus() {
	if !m.chatInSession() {
		if m.st.Mode == ModeInput && m.inputFor == "chat" && m.st.Tab == SessionTab && !m.paneLive() && m.st.Prompt == "" {
			m.st.Mode, m.st.InputLabel, m.inputFor = ModeNormal, "", "" // the pane took the column: the board's keys again
		}
		return
	}
	if m.st.Mode == ModeNormal && m.st.Focus == "parent" && !m.st.HelpOpen && !m.st.EvidenceOpen && m.st.Picker == nil && !m.paneFocus {
		m.st.Mode, m.st.InputLabel, m.inputFor = ModeInput, "chat", "chat"
	}
}

// openPicker shows the parent picker: the installed parents first, in the order Rig prefers them.
func (m *Model) openPicker() {
	names := []string{"claude", "codex", "grok", "opencode", "omp", "pi", "agy"}
	installed := map[string]bool{}
	if m.chatStat != nil {
		if len(m.chatStat.Parents) > 0 {
			known := map[string]bool{}
			for _, n := range m.chatStat.Parents {
				known[n] = true
			}
			var kept []string
			for _, n := range names {
				if known[n] {
					kept = append(kept, n)
					delete(known, n)
				}
			}
			for _, n := range m.chatStat.Parents {
				if known[n] {
					kept = append(kept, n)
				}
			}
			names = kept
		}
		for _, n := range m.chatStat.Available {
			installed[n] = true
		}
	}
	pk := &Picker{Current: m.st.ChatParent}
	for _, pass := range []bool{true, false} {
		for _, n := range names {
			if installed[n] == pass {
				pk.Items = append(pk.Items, PickItem{Name: n, Installed: pass})
			}
		}
	}
	for i, it := range pk.Items {
		if it.Name == pk.Current {
			pk.Sel = i
		}
	}
	m.st.Picker = pk
	if m.st.Mode == ModeInput && m.inputFor == "chat" {
		m.st.Mode, m.st.InputLabel, m.inputFor = ModeNormal, "", ""
	}
}

// pickerKey moves through the picker; Enter saves the choice as the project's parent.
func (m *Model) pickerKey(k tea.KeyMsg) (tea.Model, tea.Cmd) {
	pk := m.st.Picker
	switch k.String() {
	case "up", "k", "shift+tab":
		pk.Sel = clampSel(pk.Sel-1, len(pk.Items))
	case "down", "j", "tab":
		pk.Sel = clampSel(pk.Sel+1, len(pk.Items))
	case "esc", "q":
		m.st.Picker = nil
	case "enter":
		if len(pk.Items) == 0 {
			m.st.Picker = nil
			return m, nil
		}
		item := pk.Items[pk.Sel]
		if !item.Installed {
			m.st.Message = item.Name + " is not installed here; choose an installed one"
			return m, nil
		}
		m.st.Picker = nil
		m.st.Message = "saving " + item.Name + " as the parent…"
		src, name := m.src, item.Name
		return m, func() tea.Msg { return parentSetMsg{name: name, err: src.ChatSetParent(name, callTimeout)} }
	}
	return m, nil
}

// runSlash runs a / command typed into the ask box. ok is false for text that is not one of them.
func (m *Model) runSlash(text string) (tea.Cmd, bool) {
	name, rest, _ := strings.Cut(text, " ")
	rest = strings.TrimSpace(rest)
	clear := func() { m.st.Prompt, m.st.SlashSel = "", 0 }
	switch name {
	case "/parent":
		clear()
		m.openPicker()
	case "/queue":
		if rest == "" {
			m.st.Prompt = "/queue "
			m.st.Message = "type the task after /queue"
			return nil, true
		}
		// the mockup's "/queue to park a task": the same queue.add as e, never sent to the parent
		if m.draftKey == "" {
			m.draftKey = m.newKey()
		}
		m.st.Mode, m.st.InputLabel, m.inputFor = ModeNormal, "", ""
		return m.act("queue.add", map[string]any{"text": rest, "idempotency_key": m.draftKey}, true), true
	case "/fork":
		clear()
		m.st.Mode, m.st.InputLabel, m.inputFor = ModeNormal, "", ""
		if !m.appOn() {
			m.st.Message = "/fork is in the app: rig shell"
			return nil, true
		}
		return m.openFork(), true
	case "/rewind":
		clear()
		m.st.Mode, m.st.InputLabel, m.inputFor = ModeNormal, "", ""
		if !m.appOn() {
			m.st.Message = "/rewind is in the app: rig shell"
			return nil, true
		}
		return m.openRewind(), true
	case "/resume":
		clear()
		m.st.Mode, m.st.InputLabel, m.inputFor = ModeNormal, "", ""
		if !m.appOn() {
			m.st.Message = "/resume is in the app: rig shell"
			return nil, true
		}
		return m.openResume(), true
	case "/memory":
		clear()
		m.st.Mode, m.st.InputLabel, m.inputFor = ModeNormal, "", ""
		if !m.appOn() {
			m.st.Message = "/memory is in the app: rig shell"
			return nil, true
		}
		return m.openMemory(), true
	case "/new", "/clear":
		clear()
		m.chatID, m.st.Chat, m.st.ChatUp, m.chatAdopted = "", nil, 0, true
		m.st.Message = "new conversation: the next message starts it"
	case "/stop":
		clear()
		if m.chatID == "" || m.st.Chat == nil || m.st.Chat.State != "running" {
			m.st.Message = "the parent is not working on anything"
			return nil, true
		}
		m.st.Message = "stopping " + m.chatID
		src, id := m.src, m.chatID
		return func() tea.Msg { return chatCancelMsg{err: src.ChatCancel(id, callTimeout)} }, true
	case "/jobs", "/env":
		clear()
		m.st.Mode, m.st.InputLabel, m.inputFor = ModeNormal, "", ""
		if name == "/jobs" {
			return m.goTab("Jobs"), true
		}
		return m.goTab(EnvTab), true
	case "/help":
		clear()
		m.st.Mode, m.st.InputLabel, m.inputFor = ModeNormal, "", ""
		m.st.HelpOpen = true
	case "/theme":
		clear()
		if rest == "" {
			m.st.Mode, m.st.InputLabel, m.inputFor = ModeNormal, "", ""
			return m.goTab(SettingsTab), true // the Theme row, where ←→ shows each one
		}
		return m.switchTheme(strings.ToLower(rest), m.st.ThemeScope), true
	case "/model":
		clear()
		m.st.Mode, m.st.InputLabel, m.inputFor = ModeNormal, "", ""
		if !m.appOn() {
			m.st.Message = "/model is in the app: rig shell"
			return nil, true
		}
		if rest != "" {
			// /model NAME, or /model NAME LEVEL for both at once
			name, level, _ := strings.Cut(rest, " ")
			if level = strings.TrimSpace(level); level != "" {
				return tea.Batch(m.setModel(name), m.setEffort(level)), true
			}
			return m.setModel(rest), true
		}
		return m.runPalette(paletteItem{Action: "model"}, ""), true
	case "/effort":
		clear()
		m.st.Mode, m.st.InputLabel, m.inputFor = ModeNormal, "", ""
		if !m.appOn() {
			m.st.Message = "/effort is in the app: rig shell"
			return nil, true
		}
		if rest != "" {
			return m.setEffort(rest), true
		}
		a := m.st.App
		a.Settings, a.Palette = nil, &Palette{Effort: true}
		return tea.Batch(m.fetchAppSettings()), true
	case "/settings":
		clear()
		m.st.Mode, m.st.InputLabel, m.inputFor = ModeNormal, "", ""
		if m.appOn() {
			return m.openSettings("general"), true
		}
		return m.goTab(SettingsTab), true
	case "/quit":
		return tea.Quit, true
	default:
		return nil, false
	}
	return nil, true
}

// chatEntries is what the conversation already shows of a session, so a message just sent appears under it at once.
func (m *Model) chatEntries(session string) []view.ChatEntry {
	if m.st.Chat == nil || m.st.Chat.Session != session {
		return nil
	}
	return append([]view.ChatEntry(nil), m.st.Chat.Entries...)
}

// spin keeps the working spinner turning while the parent answers.
func (m *Model) spin() tea.Cmd {
	if m.spinning || m.st.Chat == nil || m.st.Chat.State != "running" {
		return nil
	}
	m.spinning = true
	return tea.Tick(spinEvery, func(time.Time) tea.Msg { return spinMsg{} })
}

// showsWorkspace: the workspace panel is on screen (the Env tab, or the Session screen's left column).
func (m *Model) showsWorkspace() bool { return m.st.Tab == EnvTab || m.composite() || m.appOn() }

// listTab is the tab whose rows the keyboard moves through: the Session screen shows the Jobs list.
func (m *Model) listTab() string {
	if m.st.Tab == SessionTab {
		return "Jobs"
	}
	return m.st.Tab
}

// dockMsg is the evidence for the job the Session screen's dock follows.
type dockMsg struct {
	ev  *view.Evidence
	err error
	id  string
}

// fetchDock loads the selected job's evidence for the dock, while the Session screen shows: at once when the
// selection changes, and at most every workspaceEvery after that.
func (m *Model) fetchDock(force bool) tea.Cmd {
	id := m.selectedJobID()
	if !(m.composite() || (m.appOn() && m.st.App.Tab == 2)) || id == "" || m.dkBusy {
		return nil
	}
	if id == m.st.DockID && !force && m.now().Sub(m.dkLast) < workspaceEvery {
		return nil
	}
	if id != m.st.DockID {
		m.st.DockEvidence, m.st.DockE, m.st.DockID = nil, "", id
	}
	m.dkBusy = true
	src := m.src
	return func() tea.Msg {
		ev, err := src.JobEvidence(id, 15*time.Second)
		return dockMsg{ev: ev, err: err, id: id}
	}
}

// fetchSettings loads the settings panel while its tab shows; it is a local read, so the interval is short.
func (m *Model) fetchSettings(force bool) tea.Cmd {
	if m.st.Tab != SettingsTab || m.stBusy || (!force && m.now().Sub(m.stLast) < workspaceEvery) {
		return nil
	}
	m.stBusy = true
	src := m.src
	return func() tea.Msg {
		p, err := src.Settings(callTimeout)
		return settingsMsg{panel: p, err: err}
	}
}

// selectedJobID is the Jobs-tab selection: what the Logs tab follows and what a shell targets.
func (m *Model) selectedJobID() string {
	if m.view == nil {
		return ""
	}
	rows := m.view.Rows("Jobs")
	if len(rows) == 0 {
		return ""
	}
	return rows[clampSel(m.st.Selected["Jobs"], len(rows))].ID
}

// fetchLog follows the selected job's log, only while the Logs tab is showing.
func (m *Model) fetchLog(force bool) tea.Cmd {
	thread := m.appOn() && m.st.App.Thread != ""
	if !(m.st.Tab == LogsTab || thread) || m.lgBusy || (!force && m.now().Sub(m.lgLast) < logEvery) {
		return nil
	}
	m.lgBusy = true
	src, id, rows := m.src, m.selectedJobID(), logFetchRows
	if thread {
		id, rows = m.st.App.Thread, 500 // the most log.follow gives: a thread scrolls back through it
	}
	return func() tea.Msg {
		if thread {
			// the thread as a conversation; an older server without job.thread shows the helper's log instead
			if t, err := src.JobThread(id, callTimeout); err == nil && t != nil {
				return logMsg{thread: t, log: &view.LogReply{}, at: time.Now()}
			}
		}
		l, err := src.Log(id, rows, callTimeout)
		return logMsg{log: l, err: err, at: time.Now()}
	}
}

type parentCmdsMsg struct {
	parent string
	list   []SlashCommand
	err    error
}

// fetchParentCommands asks for the parent's own skills and commands, once for each parent.
func (m *Model) fetchParentCommands(parent string) tea.Cmd {
	m.cmdsFor = parent
	src := m.src
	return func() tea.Msg {
		got, err := src.ChatCommands(parent, callTimeout)
		if err != nil || got == nil {
			return parentCmdsMsg{parent: parent, err: err} // an older server has no chat.commands: Rig's own only
		}
		taken := map[string]bool{}
		for _, c := range SlashCommands {
			taken[c.Name] = !softCommands[c.Name]
		}
		var list []SlashCommand
		for _, c := range got.Commands {
			name := "/" + c.Name
			if c.Kind == "skill" {
				name = "/" + Conventions[parent].SkillPrefix + c.Name // pi and omp call a skill /skill:name
			}
			if !taken[name] {
				taken[name] = true
				list = append(list, SlashCommand{Name: name, Args: " …", Help: c.Kind + " · " + c.Help, Parent: true, Skill: c.Kind == "skill"})
			}
		}
		return parentCmdsMsg{parent: parent, list: list}
	}
}

// fetchChatStatus learns whether the experiment is on (and which session is newest). Cheap, and the only call a
// board makes for the experiment while its tab is not showing.
func (m *Model) fetchChatStatus() tea.Cmd {
	if m.csBusy || m.now().Sub(m.csLast) < chatStatEvery {
		return nil
	}
	m.csBusy = true
	src := m.src
	return func() tea.Msg {
		s, err := src.ChatStatus(callTimeout)
		return chatStatusMsg{status: s, err: err}
	}
}

func (m *Model) fetchChat(force bool) tea.Cmd {
	every := chatEvery
	if m.st.Chat != nil && m.st.Chat.State == "running" {
		every = chatLiveEvery
	}
	if !(m.st.Tab == ChatTab || m.chatInSession() || m.appOn()) || m.chBusy || m.chatID == "" || (!force && m.now().Sub(m.chLast) < every) {
		return nil
	}
	m.chBusy = true
	src, id := m.src, m.chatID
	return func() tea.Msg {
		c, err := src.ChatTranscript(id, 300, callTimeout)
		return chatMsg{chat: c, err: err}
	}
}

// paneSize is the room the Session tab gives a program: the middle column, or the whole body under the rule.
func (m *Model) paneSize() (cols, rows int) {
	if cols, rows, ok := SessionPaneSize(m.w, m.h); ok {
		return cols, max(2, rows)
	}
	return m.w, max(2, m.h-5)
}

// waitPane returns a command that reports the next change of the pane's screen.
func waitPane(p Pane) tea.Cmd {
	return func() tea.Msg {
		<-p.Updates()
		return paneMsg{p: p}
	}
}

// askPane fetches the server's command for the parent CLI or for a container shell.
func (m *Model) askPane(parent bool) tea.Cmd {
	src, parentName := m.src, m.parentName
	return func() tea.Msg {
		var plan *view.ShellPlan
		var err error
		if parent {
			plan, err = src.Parent(parentName, callTimeout)
		} else {
			plan, err = src.Shell("", callTimeout)
		}
		return panePlanMsg{plan: plan, err: err}
	}
}

// startPane runs the plan in the Session tab. A finished pane is replaced; a running one is never replaced silently.
func (m *Model) startPane(plan *view.ShellPlan) tea.Cmd {
	if m.pane != nil {
		if done, _ := m.pane.Exited(); !done {
			m.st.Message = "a program is already running here; Enter types into it, x closes it"
			return nil
		}
		m.pane.Close()
		m.pane = nil
	}
	cols, rows := m.paneSize()
	p, err := m.spawn(plan.Argv, plan.Dir, plan.Env, cols, rows)
	if err != nil {
		m.st.Message = "Could not start: " + clean(err.Error())
		return nil
	}
	label := plan.Label
	if label == "" {
		label = "program"
	}
	m.pane, m.paneName, m.paneFocus = p, label, true
	m.st.Message = ""
	return waitPane(p)
}

// Close ends a program still running in the Session pane. Call it when the board exits.
func (m *Model) Close() {
	if m.pane != nil {
		m.pane.Close()
	}
}

// syncPane copies the pane's screen into the render state.
func (m *Model) syncPane() {
	m.st.PaneFocus = m.paneFocus
	m.st.PaneLines, m.st.PaneTitle, m.st.PaneRunning = nil, "", false
	if m.pane == nil {
		return
	}
	done, err := m.pane.Exited()
	m.st.PaneRunning = !done
	if m.appOn() && m.st.App.Term {
		a := m.st.App
		a.TermUp = max(0, min(a.TermUp, m.pane.Scrollback()))
		m.st.PaneLines = m.pane.LinesFrom(a.TermUp, true, a.TermFocus && !done)
	} else {
		m.st.PaneLines = m.pane.Lines(true, m.paneFocus && !done)
	}
	switch {
	case done && err != nil:
		m.st.PaneTitle = m.paneName + " · exited: " + err.Error()
	case done:
		m.st.PaneTitle = m.paneName + " · exited"
	case m.paneFocus:
		m.st.PaneTitle = m.paneName + " · typing (Ctrl-] returns to the board)"
	default:
		m.st.PaneTitle = m.paneName + " · running (Enter to type)"
	}
}

func (m *Model) askShell() tea.Cmd {
	id := ""
	if m.st.Tab == "Jobs" {
		id = m.selectedJobID()
	}
	src := m.src
	return func() tea.Msg {
		plan, err := src.Shell(id, callTimeout)
		return shellPlanMsg{plan: plan, err: err}
	}
}

func (m *Model) fetchEvidence(id string) tea.Cmd {
	src := m.src
	return func() tea.Msg {
		ev, err := src.JobEvidence(id, 15*time.Second)
		return evidenceMsg{ev: ev, err: err, id: id}
	}
}

func (m *Model) recover(id string) tea.Cmd {
	src := m.src
	return func() tea.Msg {
		text, err := src.Recover(id, 5*time.Minute)
		return recoverMsg{text: text, err: err}
	}
}

func (m *Model) act(method string, params map[string]any, enqueue bool) tea.Cmd {
	src := m.src
	return func() tea.Msg {
		text, err := src.Act(method, params, callTimeout)
		return actionMsg{text: text, err: err, enqueue: enqueue}
	}
}

func (m *Model) rows() []view.Row { return m.view.Rows(m.listTab()) }

func (m *Model) selected() (view.Row, bool) {
	rows := m.rows()
	if len(rows) == 0 {
		return view.Row{}, false
	}
	return rows[clampSel(m.st.Selected[m.listTab()], len(rows))], true
}

func (m *Model) remember() {
	if row, ok := m.selected(); ok {
		m.selID[m.listTab()] = row.ID
	}
}

// reselect keeps each tab on the same item across refreshes when it still exists.
func (m *Model) reselect() {
	for _, tab := range Tabs {
		rows := m.view.Rows(tab)
		if id := m.selID[tab]; id != "" {
			for i, r := range rows {
				if r.ID == id {
					m.st.Selected[tab] = i
				}
			}
		}
		m.st.Selected[tab] = clampSel(m.st.Selected[tab], len(rows))
	}
}

func (m *Model) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	model, cmd := m.update(msg)
	if m.appOn() {
		return model, cmd
	}
	m.autoFocus()
	return model, cmd
}

func (m *Model) update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.WindowSizeMsg:
		// the board fills the window inside the mockup's thin border; the side columns keep their widths and the chat grows
		m.winW, m.winH = msg.Width, msg.Height
		if m.st.Logos {
			ResendLogos() // a resize may be tmux re-attaching to another terminal, which has not seen the tiles
		}
		m.w, m.h, m.framed = msg.Width, msg.Height, false
		if msg.Width >= sessionMinW+2 && msg.Height >= sessionMinH+2 {
			m.w, m.h, m.framed = msg.Width-2, msg.Height-2, true
		}
		m.st.WideSession = sessionGeometry(m.w, m.h).ok
		if m.pane != nil {
			if m.appOn() && m.st.App.Term {
				m.pane.Resize(m.termSize())
			} else {
				m.pane.Resize(m.paneSize())
			}
		}
	case panePlanMsg:
		switch {
		case msg.err != nil:
			m.st.Message = "Could not start: " + clean(msg.err.Error())
		case !msg.plan.Available || len(msg.plan.Argv) == 0:
			m.st.Message = "Cannot start: " + clean(msg.plan.Reason)
		default:
			return m, m.startPane(msg.plan)
		}
	case paneMsg:
		if msg.p != m.pane {
			return m, nil // a pane that was closed or replaced
		}
		if done, err := m.pane.Exited(); done {
			m.paneFocus = false
			if err != nil {
				m.st.Message = m.paneName + " exited: " + clean(err.Error())
			} else {
				m.st.Message = m.paneName + " exited"
			}
			return m, nil
		}
		return m, waitPane(m.pane)
	case tickMsg:
		return m, tea.Batch(m.fetch(), m.fetchWorkspace(false), m.fetchFleet(false), m.fetchSettings(false), m.fetchLog(false), m.fetchDock(false), m.fetchChatStatus(), m.fetchChat(false), m.fetchDiff(false), m.fetchTodayEvery(), m.fetchProblems(false), tick())
	case chatStatusMsg:
		m.csBusy, m.csLast = false, m.now()
		if msg.err != nil || msg.status == nil || !msg.status.Enabled {
			m.st.ChatOn = false // off, or a server that predates the experiment: no tab
			if m.st.Tab == ChatTab {
				m.st.Tab = "Jobs"
			}
			return m, nil
		}
		m.st.ChatOn = true
		m.chatStat = msg.status
		m.st.ChatSessions = msg.status.Sessions
		m.st.ChatParent, m.st.ChatModel, m.st.ChatEffort = msg.status.Parent, msg.status.Model, msg.status.Effort
		if m.parentName != "" && m.parentName != msg.status.Parent {
			m.st.ChatParent, m.st.ChatModel, m.st.ChatEffort = m.parentName, "", "" // its model is in its own settings, not read for this one
		}
		if !m.pickerSeen && msg.status.Configured == "" && m.parentName == "" && m.st.Tab == SessionTab {
			m.pickerSeen = true // the first time only: a project with no parent of its own chooses one here
			if m.appOn() {
				m.openWelcome()
				m.st.App.FirstRun = true
			} else {
				m.openPicker()
			}
		}
		// When rig shell opens, pick up the newest conversation, whichever parent had it last: the parent in use takes it
		// over with the next message. Only then: after a new conversation, an old one must not come back.
		if m.chatID == "" && !m.chatAdopted && len(msg.status.Sessions) > 0 {
			m.chatID, m.chatAdopted = msg.status.Sessions[0].ID, true
		}
		if m.appOn() && m.st.ChatParent != "" && m.st.ChatParent != m.cmdsFor {
			return m, m.fetchParentCommands(m.st.ChatParent) // the / menu offers this parent's skills and commands
		}
	case problemsMsg:
		if m.appOn() && msg.err == nil && msg.p != nil {
			m.st.App.Problems = msg.p
		}
	case formatMsg:
		return m, m.afterFormat(msg)
	case gitLogMsg:
		if m.appOn() {
			m.st.App.FileLog, m.st.App.FileLogFor = append([]gitCommit{}, msg.commits...), msg.file
		}
	case symIndexMsg:
		if m.appOn() {
			m.st.App.SymIndex = &symIndex{at: m.now(), items: msg.items}
		}
	case hoverMsg:
		if m.appOn() && msg.key == m.hoverKey && msg.info != nil {
			m.st.App.Hover = msg.info
		}
	case definitionMsg:
		if m.appOn() {
			return m, m.afterDefinition(msg)
		}
	case branchesMsg:
		if m.appOn() {
			m.st.App.Branches, m.st.App.RemoteBranches = msg.local, msg.remote
		}
	case gitStatusMsg:
		if m.appOn() {
			m.st.App.Git = msg.state
			if msg.state.branch != "" {
				m.st.App.Branch = msg.state.branch
			}
		}
	case blameMsg:
		if a := m.st.App; m.appOn() && msg.file == a.File {
			if msg.err != nil {
				a.BlameFor, a.BlameLines = msg.file, nil // not in git yet, or no git: no blame to show
				if a.Blame {
					a.Blame, m.st.Message = false, "no blame for "+msg.file+": "+clean(msg.err.Error())
				}
			} else {
				a.BlameFor, a.BlameLines = msg.file, msg.lines
			}
		}
	case gutterMsg:
		if a := m.st.App; m.appOn() && msg.file == a.File {
			a.GutterFor, a.Gutter = msg.file, msg.marks
		}
	case gitDoneMsg:
		if m.appOn() {
			return m, m.afterGit(msg)
		}
	case gitDiffMsg:
		if m.appOn() {
			if old := m.st.App.GitDiff; old != nil && msg.view != nil && old.path == msg.view.path && old.staged == msg.view.staged && old.commit == msg.view.commit {
				msg.view.off, msg.view.hunk = old.off, old.hunk // a refresh keeps the place
			}
			m.st.App.GitDiff = msg.view
		}
	case modelsMsg:
		m.afterModels(msg)
	case parentCmdsMsg:
		if msg.err == nil && msg.parent == m.st.ChatParent {
			m.st.ParentCommands = msg.list
		}
	case chatMsg:
		m.chBusy, m.chLast = false, m.now()
		if a := m.st.App; m.appOn() && msg.err == nil && msg.chat != nil {
			n := len(msg.chat.Entries)
			if a.Tab == 1 && a.Thread == "" && a.Palette == nil && !a.Panel {
				a.SeenChat, a.NewChat = n, false
			} else if n > a.SeenChat && a.SeenChat > 0 {
				a.NewChat = true
			} else if a.SeenChat == 0 {
				a.SeenChat = n
			}
		}
		if msg.err != nil {
			m.st.ChatE = msg.err.Error()
			return m, nil
		}
		if msg.chat != nil && msg.chat.Session != "" && msg.chat.Session != m.chatID {
			return m, nil // the conversation was left (a switch of parent, a new one) while this was on its way
		}
		prev := m.st.Chat
		m.st.Chat, m.st.ChatE = msg.chat, ""
		if m.appOn() {
			m.rememberChat()
			return m, tea.Batch(m.spin(), m.bell(), m.afterTurn(prev, msg.chat), m.drainOutbox())
		}
		return m, m.spin()
	case splashTickMsg:
		if a := m.st.App; a != nil && a.Splash > 0 {
			if m.splashDone() {
				a.Splash = 0
				return m, nil
			}
			a.Splash++
			return m, splashTick()
		}
	case todayMsg:
		m.tdLast = m.now()
		if msg.err == nil {
			m.st.Today = msg.t
			return m, m.notifyApplied(msg.t)
		}
	case shipMsg:
		m.st.Ship, m.st.ShipE = msg.s, ""
		if msg.err != nil {
			m.st.ShipE = msg.err.Error()
		}
	case appliedMsg:
		if msg.err != nil {
			m.st.Message = "Could not do that: " + msg.err.Error()
		} else {
			m.st.Message = msg.text
		}
		m.fetching = false
		if msg.err == nil && msg.tell != "" {
			return m, tea.Batch(m.fetchToday(), m.fetchShip(), m.fetch(), m.tellParent(msg.tell))
		}
		return m, tea.Batch(m.fetchToday(), m.fetchShip(), m.fetch())
	case noteSentMsg:
		if msg.err != nil || msg.started == nil {
			m.st.Message = "undone, but " + appParent(m.st) + " could not be told"
			if msg.err != nil {
				m.st.Message += ": " + msg.err.Error()
			}
			return m, nil
		}
		m.chatID, m.chLast = msg.started.Session, time.Time{}
		m.st.Message = "undone; " + appParent(m.st) + " is handling anything built on it"
		return m, tea.Batch(m.fetchChat(true), m.spin())
	case modelSetMsg:
		return m, m.afterModelSet()
	case appSettingsMsg:
		if msg.err != nil && m.st.AppSettings == nil && IsNotRunning(msg.err) {
			// the first load, while rig shell is still starting the API server: ask again shortly, quietly
			fetch := m.fetchAppSettings()
			return m, tea.Tick(time.Second, func(time.Time) tea.Msg { return fetch() })
		}
		if msg.err != nil {
			m.st.Message = "Could not save the setting: " + msg.err.Error()
			if m.st.AppSettings == nil {
				m.st.Message = "Could not load the settings: " + msg.err.Error()
			}
			return m, nil
		}
		m.st.AppSettings = msg.s
	case settingsEditedMsg:
		return m, m.fetchAppSettings()
	case diffMsg:
		m.dfBusy, m.dfLast = false, m.now()
		if m.appOn() {
			if msg.id != m.st.App.DiffID {
				m.st.App.DiffFile, m.st.App.DiffOff = 0, 0
			}
			m.st.App.Diff, m.st.App.DiffID = msg.diff, msg.id
			if msg.err != nil || msg.diff == nil {
				m.st.App.Diff = &view.JobDiff{JobID: msg.id}
			}
		}
	case changedMsg:
		if m.appOn() {
			m.st.App.Changed, m.st.App.Added, m.st.App.ChangedLines = msg.files, msg.added, msg.lines
		}
	case searchMsg:
		if m.appOn() && m.st.App.Search != nil && m.st.App.Search.key() == msg.query {
			m.st.App.Search.Results, m.st.App.Search.Ran = msg.hits, msg.query
		}
	case reopenMsg:
		if m.appOn() && m.st.App.File == msg.path {
			return m, m.openFile(msg.path)
		}
	case filesMsg:
		if m.appOn() {
			m.st.App.Files = msg.files
		}
	case fileMsg:
		if m.appOn() && msg.path == m.st.App.File {
			a := m.st.App
			a.FileLines, a.FileErr, a.ImageCols, a.ImageRows = msg.lines, "", 0, 0
			a.FileCRLF, a.FileTruncated, a.Dirty = msg.crlf, msg.truncated, false
			a.SavedID, a.Undo, a.Redo, a.EdSel = linesID(msg.lines), nil, nil, false
			if msg.err != nil {
				a.FileErr = msg.err.Error()
			}
			cmds := []tea.Cmd{m.fetchGutter(), m.fetchBlame()} // the gutter, and blame for the cursor's line
			if msg.err == nil && a.Conflict == nil && !a.Editing && len(conflicts(msg.lines)) > 0 {
				a.Conflict = &conflictView{path: msg.path} // git left conflict markers: resolve them side by side
			}
			if to := a.pendingAt; to != nil && to.file == msg.path && msg.err == nil {
				a.pendingAt = nil
				m.goTo(to.at) // go to definition: the cursor on the name
			}
			if msg.image != nil && m.st.Logos && msg.w > 0 && msg.h > 0 {
				// shown inline: as wide as the column allows, as tall as its shape needs (a cell is about twice as tall as wide)
				cols := min(84, m.w-8, msg.w/8+1)
				rows := max(1, cols*msg.h/msg.w/2)
				if limit := max(4, m.h-10); rows > limit {
					rows, cols = limit, max(1, limit*2*msg.w/msg.h)
				}
				if SendImage(msg.image, cols, rows) {
					a.ImageCols, a.ImageRows = cols, rows
				}
			}
			return m, tea.Batch(cmds...) // the lines changed since the last commit, and who wrote each
		}
	case themeMsg:
		switch {
		case msg.err != nil && msg.saved:
			m.st.Message = "Could not save the theme: " + msg.err.Error()
		case msg.err != nil || msg.reply == nil:
			// a server without themes, or none saved: keep the default
		case msg.saved:
			m.st.ThemeScope = msg.reply.Scope
			where := map[string]string{"repo": "this repo (.rig/harness.toml)", "all": "all repos (~/.rig/config.toml)"}[msg.reply.Scope]
			m.st.Message = "theme " + msg.reply.Theme + " for " + where
		case m.appOn() && msg.reply.Scope == "default":
			m.st.ThemeScope = "default" // nothing saved: the app keeps the design's light
		case !m.themeFixed && ApplyTheme(msg.reply.Theme):
			m.st.Theme, m.st.ThemeScope = msg.reply.Theme, msg.reply.Scope
		}
	case spinMsg:
		m.spinning = false
		return m, tea.Batch(m.spin(), m.fetchChat(false))
	case parentSetMsg:
		if msg.err != nil {
			m.st.Message = "Could not save the parent: " + msg.err.Error()
			return m, nil
		}
		was := appParent(m.st)
		m.parentName, m.st.ChatParent, m.st.ChatModel, m.st.ChatEffort = msg.name, msg.name, "", ""
		if m.chatStat != nil {
			m.chatStat.Configured = msg.name
		}
		m.st.Message = "parent: " + msg.name + " (saved in .rig/harness.toml)"
		if m.chatID != "" && m.st.Chat != nil && m.st.Chat.Parent != msg.name {
			// the conversation goes on: the new parent is told what was said with your next message
			m.st.Message = msg.name + " takes this conversation over from " + m.st.Chat.Parent + " with your next message"
		}
		_ = was
	case chatSentMsg:
		if m.appOn() {
			m.st.App.Sending = false
		}
		if msg.err == nil && msg.started == nil {
			msg.err = errors.New("the server sent no session")
		}
		if msg.err != nil {
			if m.appOn() {
				m.takeBackOutbox("the send failed") // what was waiting behind it comes back to the box too
			}
			m.st.InputLabel, m.inputFor = "chat", "chat"
			m.st.Mode = ModeInput // keep the draft, as a failed enqueue does
			m.st.Prompt = msg.sent
			m.st.Message = "Chat send failed: " + msg.err.Error() + "; draft retained"
			return m, nil
		}
		if msg.quiet {
			m.chatID, m.st.Message, m.chLast = msg.started.Session, "", time.Time{}
			return m, m.fetchChat(true)
		}
		m.chatID, m.st.Prompt, m.st.ChatUp = msg.started.Session, "", 0
		m.st.Message = ""
		m.chLast = time.Time{}
		m.st.Chat = &view.ChatTranscript{Session: msg.started.Session, Parent: msg.started.Parent, State: "running",
			Entries: append(m.chatEntries(msg.started.Session), view.ChatEntry{Role: "user", Text: msg.sent, At: m.now().UTC().Format(time.RFC3339)})}
		return m, tea.Batch(m.fetchChat(true), m.spin())
	case chatAnsweredMsg:
		if msg.err != nil {
			m.st.Message = "Answer failed: " + msg.err.Error()
		}
		m.chLast = time.Time{}
		return m, m.fetchChat(true)
	case forkedMsg:
		return m, m.onForked(msg)
	case rewindPlanMsg:
		return m, m.onRewindPlan(msg)
	case rewoundMsg:
		return m, m.onRewound(msg)
	case chatCancelMsg:
		if msg.err != nil {
			m.st.Message = "Stop failed: " + msg.err.Error()
		} else {
			m.st.Message = "turn stopped"
			if m.appOn() {
				m.takeBackOutbox("the turn was stopped")
			}
		}
		m.chLast = time.Time{}
		return m, m.fetchChat(true)
	case settingsMsg:
		m.stBusy, m.stLast = false, m.now()
		if msg.err != nil {
			m.st.SettingsE = msg.err.Error() // keep the last good panel, say why it is stale
			return m, nil
		}
		m.st.Settings, m.st.SettingsE = msg.panel, ""
	case dockMsg:
		m.dkBusy, m.dkLast = false, m.now()
		if msg.id != m.st.DockID {
			return m, m.fetchDock(false) // the selection moved on while this was loading
		}
		if m.startingUp(msg.err) {
			m.dkLast = time.Time{} // still loading, not a failure: try again on the next tick
			return m, nil
		}
		if msg.err != nil {
			m.st.DockE = msg.err.Error()
			return m, nil
		}
		m.st.DockEvidence, m.st.DockE = msg.ev, ""
	case logMsg:
		m.lgBusy, m.lgLast = false, m.now()
		if msg.err != nil {
			m.st.LogE = msg.err.Error()
			return m, nil
		}
		m.st.Log, m.st.LogE, m.st.ThreadView = msg.log, "", msg.thread
	case shellPlanMsg:
		switch {
		case msg.err != nil:
			m.st.Message = "Shell failed: " + msg.err.Error()
		case !msg.plan.Available || len(msg.plan.Argv) == 0:
			m.st.Message = "No shell: " + clean(msg.plan.Reason)
		default:
			m.st.Message = "shell in " + clean(msg.plan.Workspace) + " (exit to return)"
			return m, m.exec(msg.plan.Argv, func(err error) tea.Msg { return shellDoneMsg{err: err} })
		}
	case shellDoneMsg:
		m.st.Message = "shell closed"
		if msg.err != nil {
			m.st.Message = "shell closed: " + msg.err.Error()
		}
		return m, tea.Batch(m.fetch(), m.fetchWorkspace(true))
	case fleetMsg:
		m.flBusy, m.flLast = false, m.now()
		if msg.err != nil {
			m.st.FleetE = msg.err.Error()
			return m, nil
		}
		m.st.Fleet, m.st.FleetE = msg.panel, ""
	case evidenceMsg:
		if !m.st.EvidenceOpen || msg.id != m.st.EvidenceID {
			return m, nil // the owner moved on
		}
		if msg.err != nil {
			m.st.EvidenceE = msg.err.Error()
			return m, nil
		}
		m.st.Evidence, m.st.EvidenceE = msg.ev, ""
	case recoverMsg:
		if msg.err != nil {
			m.st.Message = "Recovery failed: " + msg.err.Error()
		} else {
			m.st.Message = msg.text
		}
		return m, tea.Batch(m.fetchWorkspace(true), m.fetch())
	case workspaceMsg:
		m.wsBusy, m.wsLast = false, m.now()
		if m.startingUp(msg.err) && m.st.Workspace == nil {
			m.wsLast = time.Time{} // the API server is still starting: keep "Loading", retry on the next tick
			return m, nil
		}
		if msg.err != nil {
			// Keep the last good panel on screen; say why it is stale.
			m.st.WorkspaceE = msg.err.Error()
			return m, nil
		}
		m.st.Workspace, m.st.WorkspaceE = msg.ws, ""
	case snapshotMsg:
		m.fetching = false
		m.st.Loading = false
		if msg.err != nil {
			age := "no snapshot yet"
			if !m.lastOK.IsZero() {
				age = fmt.Sprintf("snapshot %.1fs old", m.now().Sub(m.lastOK).Seconds())
			}
			m.st.Stale = fmt.Sprintf("refresh failed: %v (%s)", msg.err, age)
			return m, nil
		}
		m.st.Stale, m.lastOK = "", msg.at
		m.view = &msg.snap.View
		m.reselect()
		if a := m.st.App; m.appOn() {
			// a helper that finished while the changes tab was not up: a dot on the tab until you look
			if a.SeenJobs == nil {
				a.SeenJobs = map[string]bool{}
			}
			for _, r := range m.view.Rows("Jobs") {
				done := r.Label != "" && strings.Contains(finishedLabels, r.Label)
				if done && !a.SeenJobs[r.ID] {
					if a.Tab == 2 || len(a.SeenJobs) == 0 {
						a.SeenJobs[r.ID] = true // on the tab, or the first snapshot: seen
					} else {
						a.NewJobs = true
					}
				}
			}
			if a.Tab == 2 {
				for _, r := range m.view.Rows("Jobs") {
					a.SeenJobs[r.ID] = true
				}
				a.NewJobs = false
			}
		}
		m.noticeNewJobs()
		if m.appOn() {
			return m, m.bell()
		}
	case actionMsg:
		switch {
		case msg.err != nil && msg.enqueue:
			m.st.Mode = ModeInput // keep the draft so nothing typed is lost
			m.st.Message = "Enqueue failed: " + msg.err.Error() + "; draft retained"
		case msg.err != nil:
			m.st.Message = "Action failed: " + msg.err.Error()
		case msg.enqueue:
			m.draftKey, m.st.Prompt, m.st.Message = "", "", "Queued: "+firstLine(msg.text)
		default:
			m.st.Message = firstLine(msg.text)
		}
		m.fetching = false
		return m, m.fetch()
	case tea.KeyMsg:
		m.sel = nil // a key ends a selection, as in a terminal
		return m.key(msg)
	case tea.MouseMsg:
		return m.mouse(msg)
	}
	return m, nil
}

func firstLine(s string) string {
	for _, l := range strings.Split(s, "\n") {
		if l = strings.TrimSpace(l); l != "" {
			return l
		}
	}
	return ""
}

func (m *Model) key(k tea.KeyMsg) (tea.Model, tea.Cmd) {
	if m.appOn() {
		return m.appKey(k)
	}
	// While typing into the pane every key belongs to its program, Ctrl-C included, except the one way back.
	if m.paneFocus && m.pane != nil && m.st.Tab == SessionTab {
		if k.Type == tea.KeyCtrlCloseBracket {
			m.paneFocus = false
			m.st.Message = "back on the board; Enter types into " + m.paneName + " again"
			return m, nil
		}
		m.pane.Write(term.KeyBytes(k, m.pane.AppCursor()))
		return m, nil
	}
	if k.Type == tea.KeyCtrlQ {
		return m, tea.Quit
	}
	if k.Type == tea.KeyCtrlC {
		if m.st.Mode == ModeInput && m.st.Prompt != "" {
			m.st.Prompt, m.st.SlashSel = "", 0 // as in codex: Ctrl-C clears a draft first, and quits on an empty box
			return m, nil
		}
		return m, tea.Quit
	}
	if m.st.Picker != nil {
		return m.pickerKey(k)
	}
	switch m.st.Mode {
	case ModeInput:
		return m.inputKey(k)
	case ModeConfirm:
		return m.confirmKey(k)
	}
	// A paste, or several characters arriving as one key when the terminal has no
	// bracketed paste, must never run as commands.
	if k.Paste || (k.Type == tea.KeyRunes && len(k.Runes) > 1) {
		m.st.Message = "Press e before pasting queue text"
		return m, nil
	}
	if m.st.Tab == SessionTab {
		switch k.String() {
		case "p":
			return m, m.askPane(true)
		case "s":
			return m, m.askPane(false)
		case "enter":
			switch {
			case m.paneLive():
				m.paneFocus = true
				m.st.Message = ""
			case m.chatInSession() && m.st.Focus == "":
				if _, ok := m.selected(); ok { // the mockup's "Enter details": the Jobs tab, opened on this job
					cmd := m.goTab("Jobs")
					m.st.DetailOpen = true
					return m, cmd
				}
			case m.chatInSession():
				m.st.Focus = "parent"
				m.startChatInput()
			}
			return m, nil
		case "i", "/", "m":
			if m.chatInSession() {
				m.st.Focus = "parent"
				m.startChatInput()
				if k.String() == "/" {
					m.st.Prompt = "/"
				}
				return m, nil
			}
		case "tab":
			if m.composite() { // the mockup's "Tab pane": the jobs, then the ask line, then the workspace
				m.st.Focus = map[string]string{"": "parent", "parent": "workspace", "workspace": ""}[m.st.Focus]
				m.st.Message = ""
				if m.st.Focus == "parent" && !m.chatInSession() {
					m.st.Focus = "workspace"
				}
				return m, nil
			}
		case "shift+tab":
			if m.composite() {
				return m, m.goTab("Jobs") // the next tab; Tab itself moves between the panes here
			}
		}
		if m.composite() && m.st.Focus == "workspace" {
			switch k.String() {
			case "u":
				m.pendingX, m.pendingI = "env.up", "up"
				m.st.Mode, m.st.Prompt = ModeConfirm, "Start this project's container (rig env up)?"
				return m, nil
			case "x":
				return m, m.askShell()
			case "d":
				return m, m.goTab(EnvTab) // the full doctor report and the proposed fix
			}
		}
	}
	if k.String() == "c" && (m.st.Tab == ChatTab || m.chatInSession()) {
		m.startChatInput()
		return m, nil
	}
	if m.st.HelpOpen {
		switch k.String() {
		case "?", "esc":
			m.st.HelpOpen = false
		case "q":
			return m, tea.Quit
		}
		return m, nil
	}
	if m.st.Tab == SettingsTab {
		if cmd, ok := m.themeKey(k.String()); ok {
			return m, cmd
		}
	}
	if m.view == nil {
		if k.String() == "q" {
			return m, tea.Quit
		}
		return m, nil
	}
	switch k.String() {
	case "q":
		return m, tea.Quit
	case "?":
		m.st.HelpOpen = true
		return m, nil
	case "w":
		return m, m.goTab(EnvTab)
	case "tab", "shift+tab":
		step := 1
		if k.String() == "shift+tab" {
			step = len(TabList(m.st)) - 1
		}
		tabs := TabList(m.st)
		here := m.st.Tab
		if here == LogsTab {
			here = "Jobs" // the log is a screen under Jobs
		}
		for i, t := range tabs {
			if t == here {
				return m, m.goTab(tabs[(i+step)%len(tabs)])
			}
		}
		return m, m.goTab(SessionTab) // on a tab the strip no longer lists (Chat, once the window is wide)
	case "l":
		if m.st.Tab != "Jobs" {
			return m, nil
		}
		return m, m.goTab(LogsTab)
	case "j", "down":
		if m.st.Tab == ChatTab {
			m.st.ChatUp = max(0, m.st.ChatUp-1)
			return m, nil
		}
		if m.st.Tab == LogsTab && !m.st.EvidenceOpen {
			m.st.LogUp = max(0, m.st.LogUp-1)
			return m, nil
		}
		if m.scrollsPanel() {
			m.st.DetailOff++
			return m, nil
		}
		m.move(1)
		return m, m.fetchDock(false)
	case "k", "up":
		if m.st.Tab == ChatTab {
			m.st.ChatUp++
			return m, nil
		}
		if m.st.Tab == LogsTab && !m.st.EvidenceOpen {
			m.st.LogUp++
			return m, nil
		}
		if m.scrollsPanel() {
			m.st.DetailOff = max(0, m.st.DetailOff-1)
			return m, nil
		}
		m.move(-1)
		return m, m.fetchDock(false)
	case "pgdown":
		if m.st.Tab == LogsTab {
			m.st.LogUp = max(0, m.st.LogUp-8)
			return m, nil
		}
		m.st.DetailOff += 8
	case "pgup":
		if m.st.Tab == LogsTab {
			m.st.LogUp += 8
			return m, nil
		}
		m.st.DetailOff = max(0, m.st.DetailOff-8)
	case "G":
		m.st.LogUp, m.st.ChatUp = 0, 0
	case "N":
		if m.st.Tab == ChatTab || m.chatInSession() {
			m.chatID, m.st.Chat, m.st.ChatUp, m.chatAdopted = "", nil, 0, true
			m.st.Message = "next message starts a new session"
		}
	case "s":
		return m, m.askShell()
	case "enter":
		if _, ok := m.selected(); ok {
			m.st.DetailOpen = !m.st.DetailOpen
			m.st.DetailOff = 0
		}
	case "esc":
		if m.st.Tab == LogsTab && !m.st.EvidenceOpen {
			return m, m.goTab("Jobs")
		}
		m.st.DetailOpen, m.st.EvidenceOpen, m.st.Message = false, false, ""
	case "v":
		row, ok := m.selected()
		if !ok || m.listTab() != "Jobs" {
			return m, nil
		}
		m.st.EvidenceOpen, m.st.EvidenceID, m.st.Evidence, m.st.EvidenceE, m.st.DetailOff = true, row.ID, nil, "", 0
		return m, m.fetchEvidence(row.ID)
	case "R":
		return m, m.askRecover()
	case "r":
		m.st.Message = "refreshing"
		cmds := []tea.Cmd{m.fetch(), m.fetchWorkspace(true), m.fetchFleet(true), m.fetchSettings(true), m.fetchLog(true)}
		if m.st.EvidenceOpen {
			cmds = append(cmds, m.fetchEvidence(m.st.EvidenceID))
		}
		return m, tea.Batch(cmds...)
	case "a", "d":
		if m.chatAsk() != nil {
			return m.answerAsk(k.String())
		}
		row, ok := m.selected()
		if !ok || m.listTab() != "Jobs" || !row.Ask {
			return m, nil
		}
		method, word := "job.allow", "allow"
		if k.String() == "d" {
			method, word = "job.deny", "deny"
		}
		m.st.Message = word + " requested " + row.ID
		return m, m.act(method, map[string]any{"id": row.ID}, false)
	case "x":
		return m, m.askStop()
	case "X":
		if m.composite() {
			return m, m.askClosePane()
		}
	case "e":
		m.st.Mode, m.st.Message = ModeInput, ""
		if m.draftKey == "" {
			m.draftKey = m.newKey()
		}
	}
	return m, nil
}

// SetParent names the parent CLI to talk to (`rig shell --parent`); empty means the project's configured one.
func (m *Model) SetParent(name string) { m.parentName = name }

// SetTab opens the board on a tab; the first refresh loads whatever it needs.
func (m *Model) SetTab(name string) { m.st.Tab = name }

// goTab shows a tab and starts whatever it needs loaded.
func (m *Model) goTab(name string) tea.Cmd {
	m.st.Tab = name
	m.st.Focus = ""
	m.st.DetailOpen, m.st.DetailOff, m.st.Message = false, 0, ""
	m.st.EvidenceOpen = false
	return tea.Batch(m.fetchWorkspace(true), m.fetchFleet(true), m.fetchSettings(true), m.fetchLog(true), m.fetchDock(true), m.fetchChat(true))
}

func (m *Model) move(delta int) {
	rows := m.rows()
	if len(rows) == 0 {
		return
	}
	tab := m.listTab()
	m.st.Selected[tab] = clampSel(m.st.Selected[tab]+delta, len(rows))
	m.st.DetailOff = 0
	m.remember()
	eh := 1
	if (tab == "Jobs" || tab == "Queue") && m.h-5 >= twoLineMinBody {
		eh = 2
	}
	m.st.Offsets[tab] = WindowStart(m.st.Offsets[tab], m.st.Selected[tab], m.h-5, eh, len(rows))
}

// scrollsPanel is true when j/k scroll a panel instead of moving a list selection.
func (m *Model) scrollsPanel() bool {
	return m.st.EvidenceOpen || m.st.Tab == EnvTab || m.st.Tab == SettingsTab
}

// askRecover confirms dc-cli's proposed fix. Only a plan the server marked safe is offered; anything
// else is shown as text on the Workspace tab for the owner to run themselves.
func (m *Model) askRecover() tea.Cmd {
	plan := (*view.RecoveryPlan)(nil)
	if m.st.Workspace != nil {
		plan = m.st.Workspace.Recovery
	}
	if m.st.Tab != EnvTab || plan == nil || plan.Ready {
		return nil
	}
	if !plan.Safe {
		m.st.Message = "Rig does not apply " + clean(plan.Apply) + "; run: " + clean(plan.Command)
		return nil
	}
	m.pendingX, m.pendingI = "env.recover", plan.ID
	m.st.Mode = ModeConfirm
	m.st.Prompt = fmt.Sprintf("Apply %s (%s)?", clean(plan.Apply), clean(plan.Summary))
	return nil
}

// askClosePane confirms ending the program in the Session pane.
func (m *Model) askClosePane() tea.Cmd {
	if m.pane == nil {
		m.st.Message = "nothing is running here"
		return nil
	}
	m.pendingX, m.pendingI = "pane.close", "pane"
	m.st.Mode, m.st.Prompt = ModeConfirm, "Close "+m.paneName+" (this ends the program)?"
	return nil
}

func (m *Model) askStop() tea.Cmd {
	if m.st.Tab == SessionTab && !m.composite() {
		return m.askClosePane() // the narrow Session tab is only the program: x closes it
	}
	if m.st.Tab == ChatTab {
		if m.chatID == "" || m.st.Chat == nil || m.st.Chat.State != "running" {
			m.st.Message = "no turn is running"
			return nil
		}
		m.pendingX, m.pendingI = "chat.cancel", m.chatID
		m.st.Mode, m.st.Prompt = ModeConfirm, "Stop the running chat turn "+m.chatID+"?"
		return nil
	}
	row, ok := m.selected()
	tab := m.listTab()
	if !ok || (tab != "Jobs" && tab != "Queue") {
		return nil
	}
	if tab == "Jobs" && strings.Contains(finishedLabels, row.Label) && row.Label != "" {
		m.st.Message = row.ID + " already finished"
		return nil
	}
	m.pendingX, m.pendingI = "job.cancel", row.ID
	if tab == "Queue" {
		m.pendingX = "queue.cancel"
	}
	m.st.Mode = ModeConfirm
	m.st.Prompt = fmt.Sprintf("Stop %s %s?", row.ID, clean(row.Title))
	return nil
}

func (m *Model) confirmKey(k tea.KeyMsg) (tea.Model, tea.Cmd) {
	switch k.String() {
	case "y":
		method, id := m.pendingX, m.pendingI
		m.st.Mode, m.st.Prompt, m.pendingX, m.pendingI = ModeNormal, "", "", ""
		if method == "pane.close" {
			if m.pane != nil {
				m.pane.Close()
				m.pane, m.paneFocus = nil, false
			}
			m.st.Message = "closed"
			return m, nil
		}
		if method == "chat.cancel" {
			m.st.Message = "stopping " + id
			src := m.src
			return m, func() tea.Msg { return chatCancelMsg{err: src.ChatCancel(id, callTimeout)} }
		}
		switch method {
		case "applies.undo", "applies.redo":
			if method == "applies.undo" {
				return m, m.applyThenTell(method, map[string]any{"id": id}, "undone "+id, m.undoNote(id))
			}
			return m, m.applyAct(method, map[string]any{"id": id}, "applied again "+id)
		case "applies.hold":
			return m, m.applyAct("applies.hold", map[string]any{"id": id, "on": true}, "holding "+id)
		case "applies.hold-all":
			return m, m.holdAll()
		case "file.delete":
			m.deleteFile(id)
			return m, nil
		case "git.discard":
			return m, m.discard(id)
		case "tab.discard":
			m.pendingX = "tab.discard"
			cmd := m.closeTab()
			m.pendingX = ""
			return m, cmd
		case "quit.unsaved":
			return m, tea.Quit
		case "search.replace":
			if m.st.App != nil && m.st.App.Search != nil {
				cmd := m.replaceAll(id)
				m.st.App.Search.Replace = nil
				return m, tea.Sequence(cmd, m.runSearch())
			}
		case "ship.push":
			return m, m.applyAct("ship.run", map[string]any{"mode": "push"}, "pushed")
		case "ship.pr":
			return m, m.applyAct("ship.run", map[string]any{"mode": "pr", "title": id}, "pull request opened")
		}
		if method == "install" {
			m.st.Message, m.csLast, m.csBusy = "installing…", time.Time{}, false
			return m, m.exec([]string{"sh", "-c", id}, func(err error) tea.Msg { return shellDoneMsg{err: err} })
		}
		if method == "settings.set" {
			return m, m.saveConfirmed(id)
		}
		if method == "env.down" {
			m.st.Message = "stopping the container (rig env down)"
			return m, m.exec([]string{"rig", "env", "down"}, func(err error) tea.Msg { return shellDoneMsg{err: err} })
		}
		if method == "env.up" {
			m.st.Message = "starting the container (rig env up)"
			return m, m.exec([]string{"rig", "env", "up"}, func(err error) tea.Msg { return shellDoneMsg{err: err} })
		}
		if method == "env.recover" {
			m.st.Message = "recovering: " + id
			return m, m.recover(id)
		}
		params := map[string]any{"id": id}
		if method == "job.cancel" {
			params["reason"] = "shell"
		}
		m.st.Message = "stop requested " + id
		return m, m.act(method, params, false)
	case "n", "esc":
		m.st.Mode, m.st.Prompt, m.pendingX, m.pendingI = ModeNormal, "", "", ""
	}
	return m, nil
}

func (m *Model) inputKey(k tea.KeyMsg) (tea.Model, tea.Cmd) {
	if m.inputFor == "chat" {
		return m.chatKey(k)
	}
	switch k.Type {
	case tea.KeyEsc:
		m.st.Mode, m.st.Prompt, m.st.InputLabel, m.inputFor = ModeNormal, "", "", ""
	case tea.KeyEnter:
		text := strings.TrimSpace(m.st.Prompt)
		if text == "" {
			return m, nil
		}
		m.st.Mode = ModeNormal
		return m, m.act("queue.add", map[string]any{"text": text, "idempotency_key": m.draftKey}, true)
	case tea.KeyBackspace:
		if r := []rune(m.st.Prompt); len(r) > 0 {
			m.st.Prompt = string(r[:len(r)-1])
		}
	case tea.KeyCtrlU:
		m.st.Prompt = ""
	case tea.KeyRunes, tea.KeySpace:
		m.st.Prompt += string(k.Runes)
	}
	return m, nil
}

// chatKey is the ask box: typing, Enter to send, / commands with their list, and the keys that still reach the
// board while it has the keyboard (↑↓ the jobs, PgUp/PgDn the conversation, Tab the tabs, a/d the parent's request).
func (m *Model) chatKey(k tea.KeyMsg) (tea.Model, tea.Cmd) {
	inSession := m.chatInSession()
	matches := m.st.slashes()
	if len(matches) > 0 {
		sel := clampSel(m.st.SlashSel, len(matches))
		switch k.Type {
		case tea.KeyUp:
			m.st.SlashSel = clampSel(sel-1, len(matches))
			return m, nil
		case tea.KeyDown:
			m.st.SlashSel = clampSel(sel+1, len(matches))
			return m, nil
		case tea.KeyTab:
			m.st.Prompt, m.st.SlashSel = matches[sel].Name, 0
			if matches[sel].Args != "" {
				m.st.Prompt += " "
			}
			return m, nil
		case tea.KeyEnter:
			if matches[sel].Args != "" {
				m.st.Prompt, m.st.SlashSel = matches[sel].Name+" ", 0
				return m, nil
			}
			cmd, _ := m.runSlash(matches[sel].Name)
			return m, cmd
		}
	}
	empty := m.st.Prompt == ""
	switch k.Type {
	case tea.KeyEsc:
		m.st.SlashSel = 0
		if !empty {
			m.st.Prompt = ""
			return m, nil
		}
		m.st.Mode, m.st.InputLabel, m.inputFor = ModeNormal, "", ""
		if inSession {
			m.st.Focus = "" // the board's keys, with the jobs
		}
		return m, nil
	case tea.KeyEnter:
		text := strings.TrimSpace(m.st.Prompt)
		if text == "" {
			return m, nil
		}
		if strings.HasPrefix(text, "/") {
			if cmd, ok := m.runSlash(text); ok {
				return m, cmd
			}
		}
		if !inSession {
			m.st.Mode, m.st.InputLabel, m.inputFor = ModeNormal, "", ""
		}
		m.st.Message, m.messaging = "sending…", false
		src, session, parent := m.src, m.chatID, m.parentName
		m.st.Prompt = ""
		return m, func() tea.Msg {
			started, err := src.ChatSend(text, session, parent, 10*time.Second)
			return chatSentMsg{started: started, err: err, sent: text}
		}
	case tea.KeyBackspace:
		if r := []rune(m.st.Prompt); len(r) > 0 {
			m.st.Prompt = string(r[:len(r)-1])
		}
		m.st.SlashSel = 0
		return m, nil
	case tea.KeyCtrlU:
		m.st.Prompt, m.st.SlashSel = "", 0
		return m, nil
	case tea.KeyRunes, tea.KeySpace:
		if empty && inSession && !k.Paste && !m.messaging && (k.String() == "a" || k.String() == "d" || k.String() == "m") {
			// the conversation shows a request: before anything is typed, a and d answer it and m starts a message
			if m.st.Chat != nil && m.st.Chat.Ask != nil {
				if k.String() == "m" {
					m.messaging, m.st.Message = true, "type your message to the parent"
					return m, nil
				}
				return m.answerAsk(k.String())
			}
			if job := waitingJob(m.view); job != nil {
				if k.String() == "m" {
					m.messaging, m.st.Message = true, "type your message to the parent about "+job.ID
					return m, nil
				}
				method := map[string]string{"a": "job.allow", "d": "job.deny"}[k.String()]
				m.st.Message = map[string]string{"a": "allow", "d": "deny"}[k.String()] + " requested " + job.ID
				return m, m.act(method, map[string]any{"id": job.ID}, false)
			}
		}
		m.st.Prompt += string(k.Runes)
		m.st.SlashSel = 0
		return m, nil
	}
	if !inSession {
		return m, nil
	}
	switch k.String() {
	case "up", "down":
		delta := 1
		if k.String() == "up" {
			delta = -1
		}
		m.move(delta)
		return m, m.fetchDock(false)
	case "pgup":
		m.st.ChatUp += 8
	case "pgdown":
		m.st.ChatUp = max(0, m.st.ChatUp-8)
	case "tab":
		m.st.Mode, m.st.InputLabel, m.inputFor = ModeNormal, "", ""
		m.st.Focus = "workspace"
	case "shift+tab":
		m.st.Mode, m.st.InputLabel, m.inputFor = ModeNormal, "", ""
		return m, m.goTab("Jobs")
	}
	return m, nil
}

// answerAsk allows or denies the parent's waiting request.
func (m *Model) answerAsk(key string) (tea.Model, tea.Cmd) {
	ask := m.st.Chat.Ask
	decision := "allow"
	if key == "d" {
		decision = "deny"
	}
	m.st.Message = decision + " requested: " + clean(ask.Preview)
	src, session, id := m.src, m.chatID, ask.ID
	return m, func() tea.Msg { return chatAnsweredMsg{err: src.ChatAnswer(session, id, decision, callTimeout)} }
}

func (m *Model) View() string {
	m.syncPane()
	m.st.Now = m.now()
	if m.appOn() {
		w, h := m.winW, m.winH
		if w == 0 {
			w, h = m.w, m.h
		}
		m.syncCaret()
		frame, zones := zonesOf(AppFrame(m.view, m.st, w, h), m.hoverX, m.hoverY)
		if box, bx, by := hoverBox(m.st, w); box != nil {
			overlay(frame, box, bx, by)
		}
		m.zones = zones
		m.plain = make([]string, len(frame))
		for i, l := range frame {
			m.plain[i] = strings.ReplaceAll(ansi.Strip(l), placeholderCells, "   ")
		}
		if m.sel != nil {
			paintSelection(frame, *m.sel, w)
		}
		return strings.Join(Paint(frame), "\n")
	}
	lines := Decorate(RenderLines(m.view, m.st, m.w, m.h))
	if m.framed {
		lines = Border(lines, m.w)
	}
	return strings.Join(Paint(lines), "\n")
}

// startingUp is true for the "no API server yet" error before the first snapshot has ever arrived: the board starts
// the server itself, so that is a moment of loading, not a failure to show.
func (m *Model) startingUp(err error) bool {
	return err != nil && m.lastOK.IsZero() && IsNotRunning(err)
}

// IsNotRunning reports whether err means no API server answered.
func IsNotRunning(err error) bool { return errors.Is(err, api.ErrNotRunning) }
