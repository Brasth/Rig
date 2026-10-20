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

	"github.com/Brasth/Rig/shell/internal/api"
	"github.com/Brasth/Rig/shell/internal/view"
)

// Source is what the model needs from the API client. *api.Client satisfies it.
type Source interface {
	Snapshot(start, rows int, timeout time.Duration) (*view.Snapshot, error)
	Act(method string, params map[string]any, timeout time.Duration) (string, error)
	Workspace(deep bool, timeout time.Duration) (*view.Workspace, error)
	Fleet(timeout time.Duration) (*view.Panel, error)
	JobEvidence(id string, timeout time.Duration) (*view.Evidence, error)
	Recover(confirmID string, timeout time.Duration) (string, error)
	Log(id string, lines int, timeout time.Duration) (*view.LogReply, error)
	Shell(id string, timeout time.Duration) (*view.ShellPlan, error)
	ChatStatus(timeout time.Duration) (*view.ChatStatus, error)
	ChatTranscript(session string, tail int, timeout time.Duration) (*view.ChatTranscript, error)
	ChatSend(message, session string, timeout time.Duration) (*view.ChatStarted, error)
	ChatCancel(session string, timeout time.Duration) error
}

const (
	pollEvery      = time.Second
	workspaceEvery = 5 * time.Second
	logEvery       = 2 * time.Second
	chatEvery      = time.Second
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

type evidenceMsg struct {
	ev  *view.Evidence
	err error
	id  string
}

type logMsg struct {
	log *view.LogReply
	err error
	at  time.Time
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
}

type chatCancelMsg struct{ err error }

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
	src      Source
	st       State
	view     *view.View
	w, h     int
	fetching bool
	wsBusy   bool
	wsLast   time.Time
	flBusy   bool
	flLast   time.Time
	lgBusy   bool
	lgLast   time.Time
	chBusy   bool
	chLast   time.Time
	csBusy   bool
	csLast   time.Time
	chatID   string // the session the Chat tab follows; empty until one exists
	inputFor string // "chat" while the input line is a chat message, else enqueue
	// exec hands the terminal to a command and returns when it exits; tests replace it.
	exec     func(argv []string, done func(error) tea.Msg) tea.Cmd
	lastOK   time.Time
	now      func() time.Time
	newKey   func() string
	selID    map[string]string
	draftKey string
	pendingX string // method for a confirmed stop: job.cancel or queue.cancel
	pendingI string // id for a confirmed stop
}

// NewModel returns a model that polls src.
func NewModel(src Source, ascii bool) *Model {
	st := NewState()
	st.Loading, st.ASCII, st.Styled = true, ascii, true
	return &Model{src: src, st: st, w: 120, h: 36, now: time.Now, newKey: randomKey, selID: map[string]string{}, exec: execProcess}
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

func (m *Model) Init() tea.Cmd { return tea.Batch(m.fetch(), tick()) }

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
	if m.st.Tab != WorkspaceTab || m.wsBusy || (!force && m.now().Sub(m.wsLast) < workspaceEvery) {
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
	if m.st.Tab != FleetTab || m.flBusy || (!force && m.now().Sub(m.flLast) < workspaceEvery) {
		return nil
	}
	m.flBusy = true
	src := m.src
	return func() tea.Msg {
		p, err := src.Fleet(20 * time.Second)
		return fleetMsg{panel: p, err: err, at: time.Now()}
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
	if m.st.Tab != LogsTab || m.lgBusy || (!force && m.now().Sub(m.lgLast) < logEvery) {
		return nil
	}
	m.lgBusy = true
	src, id := m.src, m.selectedJobID()
	return func() tea.Msg {
		l, err := src.Log(id, logFetchRows, callTimeout)
		return logMsg{log: l, err: err, at: time.Now()}
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
	if m.st.Tab != ChatTab || m.chBusy || m.chatID == "" || (!force && m.now().Sub(m.chLast) < chatEvery) {
		return nil
	}
	m.chBusy = true
	src, id := m.src, m.chatID
	return func() tea.Msg {
		c, err := src.ChatTranscript(id, 300, callTimeout)
		return chatMsg{chat: c, err: err}
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

func (m *Model) rows() []view.Row { return m.view.Rows(m.st.Tab) }

func (m *Model) selected() (view.Row, bool) {
	rows := m.rows()
	if len(rows) == 0 {
		return view.Row{}, false
	}
	return rows[clampSel(m.st.Selected[m.st.Tab], len(rows))], true
}

func (m *Model) remember() {
	if row, ok := m.selected(); ok {
		m.selID[m.st.Tab] = row.ID
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
	switch msg := msg.(type) {
	case tea.WindowSizeMsg:
		m.w, m.h = msg.Width, msg.Height
	case tickMsg:
		return m, tea.Batch(m.fetch(), m.fetchWorkspace(false), m.fetchFleet(false), m.fetchLog(false), m.fetchChatStatus(), m.fetchChat(false), tick())
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
		if m.chatID == "" && len(msg.status.Sessions) > 0 {
			m.chatID = msg.status.Sessions[0].ID
		}
	case chatMsg:
		m.chBusy, m.chLast = false, m.now()
		if msg.err != nil {
			m.st.ChatE = msg.err.Error()
			return m, nil
		}
		m.st.Chat, m.st.ChatE = msg.chat, ""
	case chatSentMsg:
		if msg.err != nil {
			m.st.InputLabel, m.inputFor = "chat", "chat"
			m.st.Mode = ModeInput // keep the draft, as a failed enqueue does
			m.st.Message = "Chat send failed: " + msg.err.Error() + "; draft retained"
			return m, nil
		}
		m.chatID, m.st.Prompt, m.st.ChatUp = msg.started.Session, "", 0
		m.st.Message = "sent to " + clean(msg.started.Parent)
		m.chLast = time.Time{}
		return m, m.fetchChat(true)
	case chatCancelMsg:
		if msg.err != nil {
			m.st.Message = "Stop failed: " + msg.err.Error()
		} else {
			m.st.Message = "turn stopped"
		}
		m.chLast = time.Time{}
		return m, m.fetchChat(true)
	case logMsg:
		m.lgBusy, m.lgLast = false, m.now()
		if msg.err != nil {
			m.st.LogE = msg.err.Error()
			return m, nil
		}
		m.st.Log, m.st.LogE = msg.log, ""
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
		return m.key(msg)
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
	if k.Type == tea.KeyCtrlC {
		return m, tea.Quit
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
	if k.String() == "c" && m.st.Tab == ChatTab {
		m.st.Mode, m.st.InputLabel, m.inputFor, m.st.Message = ModeInput, "chat", "chat", ""
		return m, nil
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
	case "tab", "shift+tab":
		step := 1
		if k.String() == "shift+tab" {
			step = len(TabList(m.st)) - 1
		}
		tabs := TabList(m.st)
		for i, t := range tabs {
			if t == m.st.Tab {
				m.st.Tab = tabs[(i+step)%len(tabs)]
				break
			}
		}
		m.st.DetailOpen, m.st.DetailOff, m.st.Message = false, 0, ""
		m.st.EvidenceOpen = false
		return m, tea.Batch(m.fetchWorkspace(true), m.fetchFleet(true), m.fetchLog(true), m.fetchChat(true))
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
		if m.st.Tab == ChatTab {
			m.chatID, m.st.Chat, m.st.ChatUp = "", nil, 0
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
		m.st.DetailOpen, m.st.EvidenceOpen, m.st.Message = false, false, ""
	case "v":
		row, ok := m.selected()
		if !ok || m.st.Tab != "Jobs" {
			return m, nil
		}
		m.st.EvidenceOpen, m.st.EvidenceID, m.st.Evidence, m.st.EvidenceE, m.st.DetailOff = true, row.ID, nil, "", 0
		return m, m.fetchEvidence(row.ID)
	case "R":
		return m, m.askRecover()
	case "r":
		m.st.Message = "refreshing"
		cmds := []tea.Cmd{m.fetch(), m.fetchWorkspace(true), m.fetchFleet(true), m.fetchLog(true)}
		if m.st.EvidenceOpen {
			cmds = append(cmds, m.fetchEvidence(m.st.EvidenceID))
		}
		return m, tea.Batch(cmds...)
	case "a", "d":
		row, ok := m.selected()
		if !ok || m.st.Tab != "Jobs" || !row.Ask {
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
	case "e":
		m.st.Mode, m.st.Message = ModeInput, ""
		if m.draftKey == "" {
			m.draftKey = m.newKey()
		}
	}
	return m, nil
}

func (m *Model) move(delta int) {
	rows := m.rows()
	if len(rows) == 0 {
		return
	}
	m.st.Selected[m.st.Tab] = clampSel(m.st.Selected[m.st.Tab]+delta, len(rows))
	m.st.DetailOff = 0
	m.remember()
	eh := 1
	if (m.st.Tab == "Jobs" || m.st.Tab == "Queue") && m.h-5 >= twoLineMinBody {
		eh = 2
	}
	m.st.Offsets[m.st.Tab] = WindowStart(m.st.Offsets[m.st.Tab], m.st.Selected[m.st.Tab], m.h-5, eh, len(rows))
}

// scrollsPanel is true when j/k scroll a panel instead of moving a list selection.
func (m *Model) scrollsPanel() bool {
	return m.st.EvidenceOpen || m.st.Tab == WorkspaceTab || m.st.Tab == FleetTab
}

// askRecover confirms dc-cli's proposed fix. Only a plan the server marked safe is offered; anything
// else is shown as text on the Workspace tab for the owner to run themselves.
func (m *Model) askRecover() tea.Cmd {
	plan := (*view.RecoveryPlan)(nil)
	if m.st.Workspace != nil {
		plan = m.st.Workspace.Recovery
	}
	if m.st.Tab != WorkspaceTab || plan == nil || plan.Ready {
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

func (m *Model) askStop() tea.Cmd {
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
	if !ok || (m.st.Tab != "Jobs" && m.st.Tab != "Queue") {
		return nil
	}
	if m.st.Tab == "Jobs" && strings.Contains(finishedLabels, row.Label) && row.Label != "" {
		m.st.Message = row.ID + " already finished"
		return nil
	}
	m.pendingX, m.pendingI = "job.cancel", row.ID
	if m.st.Tab == "Queue" {
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
		if method == "chat.cancel" {
			m.st.Message = "stopping " + id
			src := m.src
			return m, func() tea.Msg { return chatCancelMsg{err: src.ChatCancel(id, callTimeout)} }
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
	switch k.Type {
	case tea.KeyEsc:
		m.st.Mode, m.st.Prompt, m.st.InputLabel, m.inputFor = ModeNormal, "", "", ""
	case tea.KeyEnter:
		text := strings.TrimSpace(m.st.Prompt)
		if text == "" {
			return m, nil
		}
		m.st.Mode = ModeNormal
		if m.inputFor == "chat" {
			m.st.InputLabel, m.inputFor = "", ""
			src, session := m.src, m.chatID
			return m, func() tea.Msg {
				started, err := src.ChatSend(text, session, 10*time.Second)
				return chatSentMsg{started: started, err: err}
			}
		}
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

func (m *Model) View() string {
	lines := RenderLines(m.view, m.st, m.w, m.h)
	return strings.Join(Decorate(lines), "\n")
}

// IsNotRunning reports whether err means no API server answered.
func IsNotRunning(err error) bool { return errors.Is(err, api.ErrNotRunning) }
