package ui

import (
	"errors"
	"strings"
	"testing"
	"time"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/Brasth/Rig/shell/internal/view"
)

type call struct {
	method string
	params map[string]any
}

type fakeSource struct {
	ws      *view.Workspace
	wsErr   error
	wsCalls int
	snap    *view.Snapshot
	snapErr error
	actErr  error
	text    string
	calls   []call

	settings      *view.Panel
	settingsErr   error
	settingsCalls int

	fleet       *view.Panel
	fleetErr    error
	evidence    *view.Evidence
	evidenceErr error
	evidenceIDs []string
	recovered   []string
	recoverErr  error
	logReply    *view.LogReply
	logErr      error
	logIDs      []string
	shellPlan   *view.ShellPlan
	shellErr    error
	shellIDs    []string

	parentPlan  *view.ShellPlan
	parentErr   error
	parentCalls int
	parentNames []string

	chatStatus      *view.ChatStatus
	chatStatusErr   error
	chatStatusCalls int
	chatView        *view.ChatTranscript
	chatErr         error
	chatReads       []string
	chatStarted     *view.ChatStarted
	chatSendErr     error
	chatSent        [][2]string
	chatParents     []string
	chatAnswers     [][3]string
	chatAnswerErr   error
	chatCancelled   []string
	parentSet       []string
	parentSetErr    error
	theme           *view.ThemeReply
	diffs           map[string]*view.JobDiff
	appSettings     *view.AppSettings
	today           *view.Today
	shipStatus      *view.ShipStatus
	settingsSaved   [][3]string
	diffIDs         []string
	themeSaved      [][2]string
}

func (f *fakeSource) Workspace(bool, time.Duration) (*view.Workspace, error) {
	f.wsCalls++
	return f.ws, f.wsErr
}
func (f *fakeSource) Fleet(time.Duration) (*view.Panel, error) { return f.fleet, f.fleetErr }
func (f *fakeSource) Settings(time.Duration) (*view.Panel, error) {
	f.settingsCalls++
	return f.settings, f.settingsErr
}
func (f *fakeSource) JobEvidence(id string, _ time.Duration) (*view.Evidence, error) {
	f.evidenceIDs = append(f.evidenceIDs, id)
	return f.evidence, f.evidenceErr
}
func (f *fakeSource) Recover(id string, _ time.Duration) (string, error) {
	f.recovered = append(f.recovered, id)
	return "applied " + id, f.recoverErr
}
func (f *fakeSource) Log(id string, lines int, _ time.Duration) (*view.LogReply, error) {
	f.logIDs = append(f.logIDs, id)
	return f.logReply, f.logErr
}
func (f *fakeSource) Shell(id string, _ time.Duration) (*view.ShellPlan, error) {
	f.shellIDs = append(f.shellIDs, id)
	return f.shellPlan, f.shellErr
}
func (f *fakeSource) ChatStatus(time.Duration) (*view.ChatStatus, error) {
	f.chatStatusCalls++
	return f.chatStatus, f.chatStatusErr
}
func (f *fakeSource) ChatTranscript(session string, tail int, _ time.Duration) (*view.ChatTranscript, error) {
	f.chatReads = append(f.chatReads, session)
	return f.chatView, f.chatErr
}
func (f *fakeSource) ChatSend(message, session, parent string, _ time.Duration) (*view.ChatStarted, error) {
	f.chatSent = append(f.chatSent, [2]string{message, session})
	f.chatParents = append(f.chatParents, parent)
	return f.chatStarted, f.chatSendErr
}
func (f *fakeSource) ChatAnswer(session, askID, decision string, _ time.Duration) error {
	f.chatAnswers = append(f.chatAnswers, [3]string{session, askID, decision})
	return f.chatAnswerErr
}
func (f *fakeSource) JobDiff(id string, _ time.Duration) (*view.JobDiff, error) {
	f.diffIDs = append(f.diffIDs, id)
	if d, ok := f.diffs[id]; ok {
		return d, nil
	}
	return nil, errors.New("no diff for " + id)
}
func (f *fakeSource) AppSettings(time.Duration) (*view.AppSettings, error) {
	if f.appSettings == nil {
		return nil, errors.New("no settings here")
	}
	return f.appSettings, nil
}
func (f *fakeSource) SetSetting(key, value, scope string, _ time.Duration) (*view.AppSettings, error) {
	f.settingsSaved = append(f.settingsSaved, [3]string{key, value, scope})
	return f.appSettings, nil
}
func (f *fakeSource) Today(time.Duration) (*view.Today, error) {
	if f.today == nil {
		return nil, errors.New("no applies here")
	}
	return f.today, nil
}
func (f *fakeSource) ShipStatus(time.Duration) (*view.ShipStatus, error) {
	if f.shipStatus == nil {
		return nil, errors.New("no upstream")
	}
	return f.shipStatus, nil
}
func (f *fakeSource) Theme(time.Duration) (*view.ThemeReply, error) {
	if f.theme == nil {
		return nil, errors.New("no theme here")
	}
	return f.theme, nil
}
func (f *fakeSource) SetTheme(theme, scope string, _ time.Duration) (*view.ThemeReply, error) {
	f.themeSaved = append(f.themeSaved, [2]string{theme, scope})
	return &view.ThemeReply{Theme: theme, Scope: scope}, nil
}
func (f *fakeSource) ChatSetParent(name string, _ time.Duration) error {
	f.parentSet = append(f.parentSet, name)
	return f.parentSetErr
}
func (f *fakeSource) ChatCancel(session string, _ time.Duration) error {
	f.chatCancelled = append(f.chatCancelled, session)
	return nil
}
func (f *fakeSource) Parent(name string, _ time.Duration) (*view.ShellPlan, error) {
	f.parentCalls++
	f.parentNames = append(f.parentNames, name)
	return f.parentPlan, f.parentErr
}
func (f *fakeSource) Snapshot(int, int, time.Duration) (*view.Snapshot, error) {
	return f.snap, f.snapErr
}
func (f *fakeSource) Act(method string, params map[string]any, _ time.Duration) (string, error) {
	f.calls = append(f.calls, call{method, params})
	return f.text, f.actErr
}

func newReady(t *testing.T) (*Model, *fakeSource) {
	t.Helper()
	v := loadView(t)
	src := &fakeSource{snap: &view.Snapshot{View: *v}, text: "ok"}
	m := NewModel(src, false)
	m.newKey = func() string { return "key-1" }
	m.Update(tea.WindowSizeMsg{Width: 120, Height: 36})
	m.Update(snapshotMsg{snap: src.snap, at: time.Now()})
	return m, src
}

func press(m *Model, keys ...string) tea.Cmd {
	var cmd tea.Cmd
	for _, k := range keys {
		var msg tea.KeyMsg
		switch k {
		case "tab":
			msg = tea.KeyMsg{Type: tea.KeyTab}
		case "shift+tab":
			msg = tea.KeyMsg{Type: tea.KeyShiftTab}
		case "enter":
			msg = tea.KeyMsg{Type: tea.KeyEnter}
		case "esc":
			msg = tea.KeyMsg{Type: tea.KeyEsc}
		case "backspace":
			msg = tea.KeyMsg{Type: tea.KeyBackspace}
		default:
			msg = tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune(k)}
		}
		_, cmd = m.Update(msg)
	}
	return cmd
}

func run(cmd tea.Cmd, m *Model) {
	if cmd == nil {
		return
	}
	if msg := cmd(); msg != nil {
		if batch, ok := msg.(tea.BatchMsg); ok {
			for _, c := range batch {
				run(c, m)
			}
			return
		}
		m.Update(msg)
	}
}

func TestAllowAndDenyOnlyOnAskRows(t *testing.T) {
	m, src := newReady(t)
	run(press(m, "a"), m)
	if len(src.calls) != 1 || src.calls[0].method != "job.allow" || src.calls[0].params["id"] != "job-7f2" {
		t.Fatalf("calls %v", src.calls)
	}
	press(m, "j") // a running job is not an ASK
	run(press(m, "a"), m)
	run(press(m, "d"), m)
	if len(src.calls) != 1 {
		t.Fatalf("approving a non-ASK row must do nothing: %v", src.calls)
	}
	m, src = newReady(t)
	run(press(m, "d"), m)
	if src.calls[0].method != "job.deny" {
		t.Fatalf("calls %v", src.calls)
	}
}

func TestStopNeedsConfirmationAndRefusesFinishedJobs(t *testing.T) {
	m, src := newReady(t)
	press(m, "j", "x")
	if m.st.Mode != ModeConfirm || !strings.Contains(m.st.Prompt, "job-81c") {
		t.Fatalf("mode %v prompt %q", m.st.Mode, m.st.Prompt)
	}
	press(m, "n")
	if m.st.Mode != ModeNormal || len(src.calls) != 0 {
		t.Fatal("n must cancel without calling")
	}
	press(m, "x")
	run(press(m, "y"), m)
	if len(src.calls) != 1 || src.calls[0].method != "job.cancel" || src.calls[0].params["id"] != "job-81c" || src.calls[0].params["reason"] != "shell" {
		t.Fatalf("calls %v", src.calls)
	}
	m, _ = newReady(t)
	press(m, "j", "j", "j", "j", "x") // job-4c1 is Unverified: finished
	if m.st.Mode != ModeNormal || !strings.Contains(m.st.Message, "already finished") {
		t.Fatalf("mode %v message %q", m.st.Mode, m.st.Message)
	}
}

func TestQueueStopUsesQueueCancel(t *testing.T) {
	m, src := newReady(t)
	press(m, "tab", "x")
	run(press(m, "y"), m)
	if src.calls[0].method != "queue.cancel" || src.calls[0].params["id"] != "q-0a1" {
		t.Fatalf("calls %v", src.calls)
	}
	if _, has := src.calls[0].params["reason"]; has {
		t.Fatal("queue.cancel takes no reason")
	}
}

func TestEnqueueDraftIsRetainedOnFailureAndDedupedByKey(t *testing.T) {
	m, src := newReady(t)
	press(m, "e")
	for _, ch := range "Add audit" {
		press(m, string(ch))
	}
	press(m, "backspace")
	src.actErr = errors.New("queue is locked")
	run(press(m, "enter"), m)
	if src.calls[0].method != "queue.add" || src.calls[0].params["text"] != "Add audi" || src.calls[0].params["idempotency_key"] != "key-1" {
		t.Fatalf("calls %v", src.calls)
	}
	if m.st.Mode != ModeInput || m.st.Prompt != "Add audi" || !strings.Contains(m.st.Message, "draft retained") {
		t.Fatalf("draft must survive a failure: mode %v %q %q", m.st.Mode, m.st.Prompt, m.st.Message)
	}
	src.actErr = nil
	run(press(m, "enter"), m)
	if src.calls[1].params["idempotency_key"] != "key-1" {
		t.Fatal("a retry must reuse the same idempotency key")
	}
	if m.st.Mode != ModeNormal || m.st.Prompt != "" {
		t.Fatal("success clears the draft")
	}
}

func TestPasteOutsideEditorIsIgnored(t *testing.T) {
	m, src := newReady(t)
	_, _ = m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("x"), Paste: true})
	if m.st.Mode != ModeNormal || len(src.calls) != 0 {
		t.Fatal("pasted x must not start a stop")
	}
}

func TestSelectionFollowsTheItemAcrossRefreshes(t *testing.T) {
	m, src := newReady(t)
	press(m, "j", "j") // job-6d0
	reordered := *src.snap
	reordered.View.Jobs = append([]view.Row{{ID: "job-new", Title: "new", Label: "Running"}}, src.snap.View.Jobs...)
	m.Update(snapshotMsg{snap: &reordered, at: time.Now()})
	row, _ := m.selected()
	if row.ID != "job-6d0" {
		t.Fatalf("selection moved to %s", row.ID)
	}
	shrunk := *src.snap
	shrunk.View.Jobs = shrunk.View.Jobs[:1]
	m.Update(snapshotMsg{snap: &shrunk, at: time.Now()})
	if row, ok := m.selected(); !ok || row.ID != "job-7f2" {
		t.Fatalf("a vanished item must clamp, got %v %v", row.ID, ok)
	}
}

func TestRefreshFailureKeepsLastSnapshotAndMarksItStale(t *testing.T) {
	m, _ := newReady(t)
	m.now = func() time.Time { return m.lastOK.Add(4200 * time.Millisecond) }
	m.Update(snapshotMsg{err: errors.New("rig API is not running"), at: time.Now()})
	if m.view == nil || len(m.view.Jobs) == 0 {
		t.Fatal("last snapshot must be kept")
	}
	if !strings.Contains(m.st.Stale, "refresh failed") || !strings.Contains(m.st.Stale, "snapshot 4.2s old") {
		t.Fatalf("stale %q", m.st.Stale)
	}
	if !strings.Contains(m.View(), "refresh failed") {
		t.Fatal("stale notice must be on screen")
	}
	m.Update(snapshotMsg{snap: &view.Snapshot{View: *m.view}, at: time.Now()})
	if m.st.Stale != "" {
		t.Fatal("a good refresh clears the notice")
	}
}

func TestOnlyOneSnapshotInFlight(t *testing.T) {
	m := NewModel(&fakeSource{}, false)
	if m.fetch() == nil {
		t.Fatal("first fetch must start")
	}
	if m.fetch() != nil {
		t.Fatal("a second fetch must wait for the first")
	}
}

func TestNavigationTabsDetailAndQuit(t *testing.T) {
	m, _ := newReady(t)
	press(m, "tab")
	if m.st.Tab != "Queue" {
		t.Fatalf("tab %q", m.st.Tab)
	}
	press(m, "tab", "tab", "tab", "tab")
	if m.st.Tab != SessionTab {
		t.Fatalf("tab wraps after Settings to Session, got %q", m.st.Tab)
	}
	press(m, "tab")
	if m.st.Tab != SessionTab || m.st.Focus == "" {
		t.Fatalf("on the Session screen Tab moves between its panes: %q %q", m.st.Tab, m.st.Focus)
	}
	press(m, "shift+tab")
	if m.st.Tab != "Jobs" {
		t.Fatalf("shift+tab leaves the Session screen for Jobs, got %q", m.st.Tab)
	}
	press(m, "shift+tab")
	if m.st.Tab != SessionTab {
		t.Fatalf("shift+tab goes back and wraps, got %q", m.st.Tab)
	}
	press(m, "shift+tab")
	press(m, "enter")
	if !m.st.DetailOpen {
		t.Fatal("enter opens details")
	}
	press(m, "esc")
	if m.st.DetailOpen {
		t.Fatal("esc closes details")
	}
	if cmd := press(m, "q"); cmd == nil {
		t.Fatal("q must quit")
	}
	if msg := press(m, "q")(); msg != tea.Quit() {
		t.Fatalf("q returned %v", msg)
	}
}

func TestKeysBeforeFirstSnapshotOnlyQuit(t *testing.T) {
	m := NewModel(&fakeSource{}, false)
	press(m, "x", "a", "e", "tab")
	if m.st.Mode != ModeNormal {
		t.Fatal("no actions without data")
	}
	if press(m, "q") == nil {
		t.Fatal("q must still quit")
	}
}

func workspaceReady(t *testing.T) (*Model, *fakeSource) {
	t.Helper()
	m, src := newReady(t)
	src.ws = loadWorkspace(t)
	return m, src
}

func TestWorkspaceIsFetchedOnlyWhileItsTabShowsAndNotMoreThanEveryFiveSeconds(t *testing.T) {
	m, src := workspaceReady(t)
	clock := time.Unix(1000, 0)
	m.now = func() time.Time { return clock }
	run(m.fetchWorkspace(false), m)
	if src.wsCalls != 0 {
		t.Fatal("must not fetch while another tab is showing")
	}
	press(m, "tab", "tab")
	run(press(m, "tab"), m) // switching to Workspace fetches at once
	if m.st.Tab != EnvTab {
		t.Fatalf("tab %q", m.st.Tab)
	}
	if src.wsCalls != 1 {
		t.Fatalf("switching to the tab must fetch, calls=%d", src.wsCalls)
	}
	if m.st.Workspace == nil || m.st.WorkspaceE != "" {
		t.Fatal("panel must be stored")
	}
	clock = clock.Add(2 * time.Second)
	run(m.fetchWorkspace(false), m)
	if src.wsCalls != 1 {
		t.Fatal("must wait out the interval")
	}
	clock = clock.Add(4 * time.Second)
	run(m.fetchWorkspace(false), m)
	if src.wsCalls != 2 {
		t.Fatalf("calls=%d", src.wsCalls)
	}
	press(m, "tab") // back to Jobs
	clock = clock.Add(time.Minute)
	run(m.fetchWorkspace(false), m)
	if src.wsCalls != 2 {
		t.Fatal("leaving the tab stops polling")
	}
}

func TestSwitchingToWorkspaceFetchesImmediately(t *testing.T) {
	m, src := workspaceReady(t)
	press(m, "tab", "tab")
	run(press(m, "tab"), m)
	if src.wsCalls != 1 || m.st.Workspace == nil {
		t.Fatalf("calls=%d panel=%v", src.wsCalls, m.st.Workspace)
	}
}

func TestWorkspaceFailureKeepsTheLastPanelAndSaysWhy(t *testing.T) {
	m, src := workspaceReady(t)
	press(m, "tab", "tab")
	run(press(m, "tab"), m)
	good := m.st.Workspace
	if good == nil {
		t.Fatal("panel must load first")
	}
	src.wsErr = errors.New("dc ls failed")
	run(m.fetchWorkspace(true), m)
	if m.st.Workspace != good {
		t.Fatal("last good panel must stay")
	}
	if m.st.WorkspaceE != "dc ls failed" {
		t.Fatalf("error %q", m.st.WorkspaceE)
	}
	src.wsErr = nil
	run(m.fetchWorkspace(true), m)
	if m.st.WorkspaceE != "" {
		t.Fatal("success clears the error")
	}
}

func TestOnlyOneWorkspaceFetchInFlight(t *testing.T) {
	m, _ := workspaceReady(t)
	press(m, "tab", "tab")
	cmd := press(m, "tab") // starts the fetch; it has not completed yet
	if cmd == nil {
		t.Fatal("switching to the tab must start a fetch")
	}
	if m.fetchWorkspace(true) != nil {
		t.Fatal("a second fetch must wait for the first")
	}
	run(cmd, m)
	if m.fetchWorkspace(true) == nil {
		t.Fatal("after it completes a new one may start")
	}
}

func TestWorkspaceTabScrollsAndIgnoresJobActions(t *testing.T) {
	m, src := workspaceReady(t)
	press(m, "tab", "tab", "tab")
	press(m, "j", "j", "j")
	if m.st.DetailOff != 3 {
		t.Fatalf("offset %d", m.st.DetailOff)
	}
	press(m, "k")
	if m.st.DetailOff != 2 {
		t.Fatalf("offset %d", m.st.DetailOff)
	}
	run(press(m, "a"), m)
	run(press(m, "d"), m)
	press(m, "x", "enter")
	if m.st.Mode != ModeNormal || len(src.calls) != 0 || m.st.DetailOpen {
		t.Fatalf("job actions must not apply on the workspace tab: mode=%v calls=%v", m.st.Mode, src.calls)
	}
}

func TestMultiCharacterKeyOutsideTheEditorIsTreatedAsPaste(t *testing.T) {
	m, src := newReady(t)
	_, _ = m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("xyaed")})
	if m.st.Mode != ModeNormal || len(src.calls) != 0 || !strings.Contains(m.st.Message, "pasting") {
		t.Fatalf("mode=%v calls=%v message=%q", m.st.Mode, src.calls, m.st.Message)
	}
}

func TestMultiCharacterKeyInsideTheEditorIsTyped(t *testing.T) {
	m, _ := newReady(t)
	press(m, "e")
	_, _ = m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("pasted text")})
	if m.st.Prompt != "pasted text" {
		t.Fatalf("prompt %q", m.st.Prompt)
	}
}
