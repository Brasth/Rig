package ui

import (
	"errors"
	"strings"
	"testing"
	"time"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/Brasth/Rig/shell/internal/view"
)

func panel(text string) *view.Panel {
	return &view.Panel{Sections: []view.Section{{Title: "This repository", Lines: []view.Line{{Text: text}}}}}
}

func TestFleetIsFetchedOnlyWhileItsTabShows(t *testing.T) {
	m, src := newReady(t)
	src.fleet = panel("● repo  running")
	if cmd := m.fetchFleet(true); cmd != nil {
		t.Fatal("no fleet fetch while another tab is showing")
	}
	press(m, "tab", "tab", "tab", "tab")
	if m.st.Tab != FleetTab {
		t.Fatalf("tab %q", m.st.Tab)
	}
	m.flBusy = false // the tab press already asked for one; this test drives the fetch itself
	run(m.fetchFleet(true), m)
	if m.st.Fleet == nil || m.st.Fleet.Sections[0].Lines[0].Text != "● repo  running" {
		t.Fatalf("fleet not loaded: %+v", m.st.Fleet)
	}
	if m.fetchFleet(false) != nil {
		t.Fatal("a fresh panel is not refetched before the interval")
	}
}

func TestFleetFailureKeepsTheLastGoodPanel(t *testing.T) {
	m, src := newReady(t)
	press(m, "tab", "tab", "tab", "tab")
	src.fleet = panel("first")
	m.flBusy = false
	run(m.fetchFleet(true), m)
	src.fleet, src.fleetErr = nil, errors.New("engine not running")
	run(m.fetchFleet(true), m)
	if m.st.Fleet == nil || m.st.FleetE != "engine not running" {
		t.Fatalf("stale panel and reason expected: %+v %q", m.st.Fleet, m.st.FleetE)
	}
	out := Render(m.view, m.st, 80, 14)
	if !strings.Contains(out, "first") || !strings.Contains(out, "refresh failed: engine not running") {
		t.Fatalf("stale frame lacks the old panel or the reason:\n%s", out)
	}
}

func TestVOpensTheEvidenceDrawerForTheSelectedJobAndEscCloses(t *testing.T) {
	m, src := newReady(t)
	src.evidence = &view.Evidence{JobID: "x", Sections: []view.Section{{Title: "Ran", Lines: []view.Line{{Text: "on the host"}}}}}
	cmd := press(m, "v")
	if !m.st.EvidenceOpen || m.st.EvidenceID == "" {
		t.Fatalf("drawer not open: %+v", m.st)
	}
	id := m.st.EvidenceID
	run(cmd, m)
	if len(src.evidenceIDs) != 1 || src.evidenceIDs[0] != id {
		t.Fatalf("evidence requested for %v, want %q", src.evidenceIDs, id)
	}
	if m.st.Evidence == nil {
		t.Fatal("evidence not stored")
	}
	if out := Render(m.view, m.st, 100, 30); !strings.Contains(out, "Evidence › "+id) || !strings.Contains(out, "on the host") {
		t.Fatalf("drawer not rendered:\n%s", out)
	}
	press(m, "esc")
	if m.st.EvidenceOpen {
		t.Fatal("esc closes the drawer")
	}
}

func TestEvidenceForAJobTheOwnerMovedOnFromIsDropped(t *testing.T) {
	m, src := newReady(t)
	src.evidence = &view.Evidence{JobID: "old"}
	cmd := press(m, "v")
	press(m, "esc")
	run(cmd, m)
	if m.st.Evidence != nil {
		t.Fatal("a late reply must not repopulate a closed drawer")
	}
}

func TestEvidenceFailureIsShownNotHidden(t *testing.T) {
	m, src := newReady(t)
	src.evidenceErr = errors.New("no such job")
	run(press(m, "v"), m)
	if m.st.EvidenceE != "no such job" {
		t.Fatalf("error %q", m.st.EvidenceE)
	}
	if !strings.Contains(Render(m.view, m.st, 80, 14), "Evidence unavailable: no such job") {
		t.Fatal("frame must say the evidence is unavailable")
	}
}

func TestVDoesNothingOutsideTheJobsTab(t *testing.T) {
	m, src := newReady(t)
	press(m, "tab")
	press(m, "v")
	if m.st.EvidenceOpen || len(src.evidenceIDs) != 0 {
		t.Fatal("v is a Jobs key")
	}
}

func TestTabLeavesTheDrawer(t *testing.T) {
	m, _ := newReady(t)
	press(m, "v")
	press(m, "tab")
	if m.st.EvidenceOpen {
		t.Fatal("changing tabs closes the drawer")
	}
}

func TestJKScrollTheDrawerNotTheList(t *testing.T) {
	m, _ := newReady(t)
	press(m, "v")
	before := m.st.Selected["Jobs"]
	press(m, "j", "j")
	if m.st.DetailOff != 2 || m.st.Selected["Jobs"] != before {
		t.Fatalf("scroll %d selected %d", m.st.DetailOff, m.st.Selected["Jobs"])
	}
}

func workspaceWithPlan(plan *view.RecoveryPlan) *view.Workspace {
	return &view.Workspace{Version: 1, Sections: []view.Section{{Title: "Workspace"}}, Recovery: plan}
}

func TestRecoverNeedsASafePlanAndAConfirmation(t *testing.T) {
	m, src := newReady(t)
	press(m, "tab", "tab", "tab")
	m.st.Workspace = workspaceWithPlan(&view.RecoveryPlan{ID: "start_colima", Apply: "colima_start", Summary: "Colima is stopped.", Safe: true})
	press(m, "R")
	if m.st.Mode != ModeConfirm || !strings.Contains(m.st.Prompt, "colima_start") {
		t.Fatalf("confirmation expected: mode %v prompt %q", m.st.Mode, m.st.Prompt)
	}
	press(m, "n")
	if len(src.recovered) != 0 {
		t.Fatal("declining must not apply anything")
	}
	press(m, "R")
	cmd := press(m, "y")
	run(cmd, m)
	if len(src.recovered) != 1 || src.recovered[0] != "start_colima" {
		t.Fatalf("recovered %v", src.recovered)
	}
	if !strings.Contains(m.st.Message, "applied start_colima") {
		t.Fatalf("message %q", m.st.Message)
	}
}

func TestRecoverRefusesAnUnsafePlanAndSaysWhatToRun(t *testing.T) {
	m, src := newReady(t)
	press(m, "tab", "tab", "tab")
	m.st.Workspace = workspaceWithPlan(&view.RecoveryPlan{ID: "install_engine", Apply: "open_guide", Command: "brew install colima", Safe: false})
	press(m, "R")
	if m.st.Mode == ModeConfirm || len(src.recovered) != 0 {
		t.Fatal("an unsafe plan is never confirmable")
	}
	if !strings.Contains(m.st.Message, "brew install colima") {
		t.Fatalf("message %q", m.st.Message)
	}
}

func TestRecoverIsOnlyOfferedOnTheWorkspaceTab(t *testing.T) {
	m, _ := newReady(t)
	m.st.Workspace = workspaceWithPlan(&view.RecoveryPlan{ID: "x", Apply: "colima_start", Safe: true})
	press(m, "R")
	if m.st.Mode == ModeConfirm {
		t.Fatal("R only works on the Workspace tab")
	}
}

func TestRecoverFailureIsShown(t *testing.T) {
	m, src := newReady(t)
	press(m, "tab", "tab", "tab")
	src.recoverErr = errors.New("the plan changed")
	m.st.Workspace = workspaceWithPlan(&view.RecoveryPlan{ID: "x", Apply: "take_ports", Summary: "Port held.", Safe: true})
	press(m, "R")
	run(press(m, "y"), m)
	if !strings.Contains(m.st.Message, "Recovery failed: the plan changed") {
		t.Fatalf("message %q", m.st.Message)
	}
}

func TestFooterOffersRecoverOnlyForASafePlan(t *testing.T) {
	st := NewState()
	st.Tab = WorkspaceTab
	st.Workspace = workspaceWithPlan(&view.RecoveryPlan{ID: "x", Apply: "colima_start", Safe: true})
	if !strings.Contains(footerLine(nil, 0, st, 100), "R recover") {
		t.Fatal("safe plan: R recover shown")
	}
	st.Workspace.Recovery.Safe = false
	if strings.Contains(footerLine(nil, 0, st, 100), "R recover") {
		t.Fatal("unsafe plan: R recover hidden")
	}
}

var _ = tea.Quit
var _ = time.Second
