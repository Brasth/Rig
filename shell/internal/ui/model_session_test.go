package ui

import (
	"strings"
	"testing"
	"time"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/Brasth/Rig/shell/internal/view"
)

func termPlan(name string) *view.ShellPlan {
	return &view.ShellPlan{Available: true, Argv: []string{"/usr/bin/" + name}, Dir: "/repo", Label: name}
}

// sessionModel is a ready model on the Session tab at the composite size, with a recording pane factory.
func sessionModel(t *testing.T, w, h int) (*Model, *fakeSource, *[]spawned, *[]*fakePane) {
	t.Helper()
	m, src := newReady(t)
	m.Update(tea.WindowSizeMsg{Width: w, Height: h})
	src.ws, src.evidence = loadWorkspace(t), loadEvidence(t)
	src.parentPlan = termPlan("codex")
	var calls []spawned
	var panes []*fakePane
	m.spawn = func(argv []string, dir string, env []string, cols, rows int) (Pane, error) {
		calls = append(calls, spawned{argv, dir, env, cols, rows})
		p := newFakePane()
		panes = append(panes, p)
		return p, nil
	}
	run(press(m, "shift+tab"), m)
	if m.st.Tab != SessionTab {
		t.Fatalf("tab %q", m.st.Tab)
	}
	return m, src, &calls, &panes
}

func TestTheSessionScreenIsCompositeOnlyWhenThereIsRoom(t *testing.T) {
	for _, c := range []struct {
		w, h int
		want bool
	}{{120, 36, true}, {120, 30, true}, {140, 40, true}, {119, 36, false}, {120, 29, false}, {80, 24, false}} {
		m, _, _, _ := sessionModel(t, c.w, c.h)
		if m.composite() != c.want {
			t.Errorf("%dx%d composite=%v, want %v", c.w, c.h, m.composite(), c.want)
		}
	}
}

func TestTheParentRunsInTheMiddleColumnAndFollowsResizes(t *testing.T) {
	m, _, calls, panes := sessionModel(t, 120, 36)
	runAll(m, press(m, "p"))
	if len(*calls) != 1 {
		t.Fatalf("spawned %d", len(*calls))
	}
	if c := (*calls)[0]; c.cols != 52 || c.rows != 26 {
		t.Fatalf("pane %dx%d, want the 52x26 middle column", c.cols, c.rows)
	}
	m.Update(tea.WindowSizeMsg{Width: 140, Height: 40})
	if got := (*panes)[0].resizes; len(got) != 1 || got[0] != [2]int{72, 30} {
		t.Fatalf("resizes %v, want the 72x30 middle column", got)
	}
	m.Update(tea.WindowSizeMsg{Width: 100, Height: 36})
	if got := (*panes)[0].resizes; got[len(got)-1] != [2]int{100, 31} {
		t.Fatalf("too narrow for columns: the pane takes the body, resizes %v", got)
	}
}

func TestTheWorkspaceLoadsForTheLeftColumnAndTheHeaderNamesTheContainer(t *testing.T) {
	m, src, _, _ := sessionModel(t, 120, 36)
	if src.wsCalls != 1 {
		t.Fatalf("workspace calls %d: the Session screen needs its left column", src.wsCalls)
	}
	src.ws.Context = "devcontainer a3f9c1"
	m.wsBusy = false
	run(m.fetchWorkspace(true), m)
	if head := strings.Split(m.View(), "\n")[0]; !strings.Contains(head, "my-repo · devcontainer a3f9c1") {
		t.Fatalf("header %q", head)
	}
	narrow, nsrc, _, _ := sessionModel(t, 100, 36)
	if nsrc.wsCalls != 0 || narrow.fetchWorkspace(true) != nil {
		t.Fatal("a narrow Session tab shows no workspace column, so it asks dc-cli for nothing")
	}
}

func TestMovingTheSelectionLoadsThatJobsEvidenceIntoTheDock(t *testing.T) {
	m, src, _, _ := sessionModel(t, 120, 36)
	first := m.selectedJobID()
	if len(src.evidenceIDs) == 0 || src.evidenceIDs[len(src.evidenceIDs)-1] != first {
		t.Fatalf("dock asked for %v, selected %q", src.evidenceIDs, first)
	}
	m.dkBusy = false
	run(press(m, "j"), m)
	second := m.selectedJobID()
	if second == first || src.evidenceIDs[len(src.evidenceIDs)-1] != second {
		t.Fatalf("selection %q then %q, dock asked %v", first, second, src.evidenceIDs)
	}
	if m.st.DockID != second || m.st.DockEvidence == nil {
		t.Fatalf("dock %q %v", m.st.DockID, m.st.DockEvidence)
	}
	out := m.View()
	for _, want := range []string{"Evidence · " + second, "diff", "checks", "contract", "ports"} {
		if !strings.Contains(out, want) {
			t.Fatalf("dock lacks %q:\n%s", want, out)
		}
	}
}

func TestADockAnswerForAJobTheOwnerMovedOffIsIgnoredAndTheDockPollsSlowly(t *testing.T) {
	m, src, _, _ := sessionModel(t, 120, 36)
	clock := time.Unix(5000, 0)
	m.now = func() time.Time { return clock }
	m.dkBusy = false
	m.dkLast = clock
	if m.fetchDock(false) != nil {
		t.Fatal("a fresh dock for the same job is not refetched")
	}
	clock = clock.Add(workspaceEvery + time.Second)
	if m.fetchDock(false) == nil {
		t.Fatal("the dock refreshes after the interval")
	}
	m.dkBusy = false
	m.Update(dockMsg{ev: src.evidence, id: "someone-else"})
	if m.st.DockEvidence != nil && m.st.DockID == "someone-else" {
		t.Fatal("an answer for another job must not be shown")
	}
}

func TestOnTheSessionScreenJobKeysActOnTheSelectedJob(t *testing.T) {
	m, src, _, _ := sessionModel(t, 120, 36)
	run(press(m, "a"), m)
	if len(src.calls) != 1 || src.calls[0].method != "job.allow" || src.calls[0].params["id"] != "job-7f2" {
		t.Fatalf("a allows the selected ASK job: %v", src.calls)
	}
	press(m, "x")
	if m.st.Mode != ModeConfirm || !strings.Contains(m.st.Prompt, "Stop job-7f2") {
		t.Fatalf("x stops the selected job, not the program: %q", m.st.Prompt)
	}
	press(m, "n")
	press(m, "v")
	if !m.st.EvidenceOpen || m.st.EvidenceID != "job-7f2" {
		t.Fatalf("v opens the full drawer for the selected job: %+v", m.st)
	}
}

func TestShiftXClosesTheProgramOnTheSessionScreen(t *testing.T) {
	m, _, _, panes := sessionModel(t, 120, 36)
	press(m, "X")
	if !strings.Contains(m.st.Message, "nothing is running") {
		t.Fatalf("message %q", m.st.Message)
	}
	runAll(m, press(m, "p"))
	m.Update(tea.KeyMsg{Type: tea.KeyCtrlCloseBracket})
	press(m, "X")
	if m.st.Mode != ModeConfirm || !strings.Contains(m.st.Prompt, "Close codex") {
		t.Fatalf("prompt %q", m.st.Prompt)
	}
	press(m, "y")
	if (*panes)[0].closed != 1 || m.pane != nil {
		t.Fatal("confirmed: the program closes")
	}
}

func TestTheSessionFrameShowsEveryColumnAndTheAttentionLine(t *testing.T) {
	m, _, _, _ := sessionModel(t, 120, 36)
	out := m.View()
	for _, want := range []string{"Workspace", "Ports", "Worktrees", "Memory", "Jobs 1-7/7 · attention first", "Finished",
		"Queue 2 pending", "job-7f2 needs input: a allows, d denies.", "a allow  d deny"} {
		if !strings.Contains(out, want) {
			t.Errorf("missing %q:\n%s", want, out)
		}
	}
	assertGrid(t, Render(m.view, m.st, 120, 36), 120, 36)
}
