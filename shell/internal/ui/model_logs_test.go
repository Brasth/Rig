package ui

import (
	"errors"
	"fmt"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/Brasth/Rig/shell/internal/view"
)

func logReply(id, text string) *view.LogReply {
	return &view.LogReply{Job: &struct {
		ID     string `json:"id"`
		Label  string `json:"label"`
		Worker string `json:"worker"`
	}{ID: id, Label: "Running", Worker: "claude"}, Text: text}
}

func toLogs(m *Model) { press(m, "shift+tab") }

func TestLogsFollowTheSelectedJobAndPollOnlyWhileShowing(t *testing.T) {
	m, src := newReady(t)
	if m.fetchLog(true) != nil {
		t.Fatal("no log fetch while another tab shows")
	}
	press(m, "j")
	want := m.selectedJobID()
	toLogs(m)
	if m.st.Tab != LogsTab {
		t.Fatalf("tab %q", m.st.Tab)
	}
	src.logReply = logReply(want, "one\ntwo")
	m.lgBusy = false
	run(m.fetchLog(true), m)
	if len(src.logIDs) == 0 || src.logIDs[len(src.logIDs)-1] != want {
		t.Fatalf("followed %v, selected %q", src.logIDs, want)
	}
	if m.st.Log == nil || !strings.Contains(m.st.Log.Text, "two") {
		t.Fatal("log not stored")
	}
	if m.fetchLog(false) != nil {
		t.Fatal("a fresh log is not refetched before the interval")
	}
}

func TestLogFailureKeepsTheLastTextAndSaysWhy(t *testing.T) {
	m, src := newReady(t)
	toLogs(m)
	src.logReply = logReply("job-1", "still here")
	m.lgBusy = false
	run(m.fetchLog(true), m)
	src.logReply, src.logErr = nil, errors.New("api gone")
	m.lgBusy = false
	run(m.fetchLog(true), m)
	out := Render(m.view, m.st, 80, 14)
	if !strings.Contains(out, "still here") || !strings.Contains(out, "refresh failed: api gone") {
		t.Fatalf("stale log frame:\n%s", out)
	}
}

func TestScrollingBackStopsFollowingAndGReturnsToTheTail(t *testing.T) {
	m, src := newReady(t)
	toLogs(m)
	var rows []string
	for i := 1; i <= 100; i++ {
		rows = append(rows, fmt.Sprintf("line %03d", i))
	}
	src.logReply = logReply("job-1", strings.Join(rows, "\n"))
	m.lgBusy = false
	run(m.fetchLog(true), m)
	tail := Render(m.view, m.st, 80, 20)
	if !strings.Contains(tail, "line 100") || strings.Contains(tail, "line 001") {
		t.Fatalf("follows the tail:\n%s", tail)
	}
	press(m, "k", "k", "k")
	if m.st.LogUp != 3 {
		t.Fatalf("LogUp %d", m.st.LogUp)
	}
	back := Render(m.view, m.st, 80, 20)
	if strings.Contains(back, "line 100") || !strings.Contains(back, "line 097") || !strings.Contains(back, "3 lines back") {
		t.Fatalf("scrolled back:\n%s", back)
	}
	press(m, "G")
	if m.st.LogUp != 0 {
		t.Fatal("G follows the tail again")
	}
	press(m, "j")
	if m.st.LogUp != 0 {
		t.Fatal("cannot scroll past the newest line")
	}
}

func TestLogTextCannotInjectTerminalSequences(t *testing.T) {
	st := NewState()
	st.Tab = LogsTab
	st.Log = logReply("job-1", "ok\x1b[2J\x1b]0;owned\x07 done")
	out := Render(nil, st, 80, 12)
	if strings.Contains(out, "\x1b") || strings.Contains(out, "owned") && strings.Contains(out, "]0;") {
		t.Fatalf("escape sequence reached the frame: %q", out)
	}
}

func TestShellOpensTheServersCommandAndOnlyThat(t *testing.T) {
	m, src := newReady(t)
	var ran [][]string
	m.exec = func(argv []string, done func(error) tea.Msg) tea.Cmd {
		ran = append(ran, argv)
		return func() tea.Msg { return done(nil) }
	}
	src.shellPlan = &view.ShellPlan{Available: true, Argv: []string{"/usr/bin/dc", "exec", "--no-start", "/repo"}, Workspace: "/repo"}
	run(press(m, "s"), m)
	if len(ran) != 1 || strings.Join(ran[0], " ") != "/usr/bin/dc exec --no-start /repo" {
		t.Fatalf("ran %v", ran)
	}
	if len(src.shellIDs) != 1 || src.shellIDs[0] != m.selectedJobID() {
		t.Fatalf("asked for %v", src.shellIDs)
	}
}

func TestShellRefusalShowsTheReasonAndRunsNothing(t *testing.T) {
	m, src := newReady(t)
	m.exec = func([]string, func(error) tea.Msg) tea.Cmd { t.Fatal("must not run"); return nil }
	src.shellPlan = &view.ShellPlan{Available: false, Reason: "no running container for /repo; start it with: rig env up"}
	run(press(m, "s"), m)
	if !strings.Contains(m.st.Message, "No shell: no running container") {
		t.Fatalf("message %q", m.st.Message)
	}
	src.shellPlan, src.shellErr = nil, errors.New("api gone")
	run(press(m, "s"), m)
	if !strings.Contains(m.st.Message, "Shell failed: api gone") {
		t.Fatalf("message %q", m.st.Message)
	}
}

func TestShellFromAnEmptyArgvIsNeverRun(t *testing.T) {
	m, src := newReady(t)
	m.exec = func([]string, func(error) tea.Msg) tea.Cmd { t.Fatal("must not run"); return nil }
	src.shellPlan = &view.ShellPlan{Available: true}
	run(press(m, "s"), m)
}

func TestShellOnTheWorkspaceTabTargetsTheRepository(t *testing.T) {
	m, src := newReady(t)
	press(m, "tab", "tab", "tab")
	src.shellPlan = &view.ShellPlan{Available: false, Reason: "x"}
	run(press(m, "s"), m)
	if len(src.shellIDs) != 1 || src.shellIDs[0] != "" {
		t.Fatalf("asked for %q, want the repository (empty id)", src.shellIDs)
	}
}

func TestShellDoneRefreshesAndReports(t *testing.T) {
	m, _ := newReady(t)
	m.Update(shellDoneMsg{err: errors.New("exit status 3")})
	if !strings.Contains(m.st.Message, "shell closed: exit status 3") {
		t.Fatalf("message %q", m.st.Message)
	}
}
