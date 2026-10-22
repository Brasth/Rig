package ui

import (
	"errors"
	"strings"
	"testing"

	"github.com/Brasth/Rig/shell/internal/view"
)

func TestDoctorListsWhatIsWrongWorstFirstAndFixesWhatRigCan(t *testing.T) {
	m, src := appModel(t)
	withConversation(t, m, src)
	src.doctor = &view.ShellDoctor{Problems: 2, Warnings: 1, Findings: []view.DoctorFinding{
		{ID: "blank-thread", Level: "problem", Title: "The thread file is empty", Detail: ".rig/thread has no session id.", Fix: "remove-thread", FixLabel: "remove it"},
		{ID: "check-mcp", Level: "problem", Title: "mcp: missing", Detail: "Rig MCP is not registered for codex; run rig setup."},
		{ID: "stale-job", Level: "warning", Title: "Job hero never started", Detail: "Its folder has a brief and no meta.json.", Fix: "remove-job:hero", FixLabel: "remove the folder"},
	}}
	follow(m, m.appSend("/doctor"), 0)
	if p := m.st.App.Palette; p == nil || !p.Doctor || src.doctors != 1 {
		t.Fatalf("/doctor opens the list and asks the server: %v %d", m.st.App.Palette, src.doctors)
	}
	var rows []string
	for _, it := range paletteItems(m.st) {
		rows = append(rows, it.Section+"|"+it.Label+"|"+it.Detail+"|"+it.Action)
	}
	list := strings.Join(rows, "\n")
	for _, want := range []string{
		"problems|✕ The thread file is empty|⏎ remove it · .rig/thread has no session id.|doctor-fix:remove-thread",
		"problems|✕ mcp: missing|Rig MCP is not registered for codex; run rig setup.|doctor-info:check-mcp",
		"warnings|! Job hero never started|⏎ remove the folder · Its folder has a brief and no meta.json.|doctor-fix:remove-job:hero"} {
		if !strings.Contains(list, want) {
			t.Fatalf("missing %q:\n%s", want, list)
		}
	}
	// ⏎ on a finding without a fix says what to do and keeps the list
	m.st.App.Palette.Sel = 1
	follow(m, press(m, "enter"), 0)
	if len(src.fixes) != 0 || !strings.Contains(m.st.Message, "run rig setup") || m.st.App.Palette == nil || !m.st.App.Palette.Doctor {
		t.Fatalf("no fix: the message says what to do, and the list stays: %v %q", src.fixes, m.st.Message)
	}
	// ⏎ on one with a fix runs it and asks again
	m.st.App.Palette.Sel = 0
	src.doctor = &view.ShellDoctor{}
	before := src.doctors
	follow(m, press(m, "enter"), 0)
	if len(src.fixes) != 1 || src.fixes[0] != "remove-thread" || src.doctors != before+1 {
		t.Fatalf("the fix ran, then the project was checked again: %v %d", src.fixes, src.doctors)
	}
	if items := paletteItems(m.st); len(items) != 1 || !strings.HasPrefix(items[0].Label, "✓ all clear") {
		t.Fatalf("nothing left: %+v", items)
	}
	if !strings.Contains(m.st.Message, "done: remove-thread") {
		t.Fatalf("what was done is said: %q", m.st.Message)
	}
	// a fix that is refused says why
	src.fixErr = errors.New("job hero is not a stale job")
	src.doctor = &view.ShellDoctor{Findings: []view.DoctorFinding{{ID: "stale-job", Level: "warning", Title: "Job hero never started", Fix: "remove-job:hero", FixLabel: "remove the folder"}}}
	follow(m, m.openDoctor(), 0)
	follow(m, press(m, "enter"), 0)
	if !strings.Contains(m.st.Message, "fix: job hero is not a stale job") {
		t.Fatalf("a refusal is shown: %q", m.st.Message)
	}
	// the server failing is shown in the list
	src.doctorErr = errors.New("server unavailable")
	follow(m, m.openDoctor(), 0)
	if items := paletteItems(m.st); len(items) != 1 || items[0].Label != "could not check" {
		t.Fatalf("an unreachable server: %+v", items)
	}
}

func TestTheLatestErrorPointsToDoctor(t *testing.T) {
	m, src := appModel(t)
	withConversation(t, m, src)
	src.chatView = transcript("failed", ce("user", "go"), ce("error", "the parent exited with status 1"), ce("user", "again"), ce("error", "list index out of range (IndexError at jobs.py:113 in current_thread)"))
	run(m.fetchChat(true), m)
	out := appScreen(m)
	if strings.Count(out, "/doctor shows what is wrong") != 1 || !strings.Contains(out, "IndexError at jobs.py:113") {
		t.Fatalf("one hint, under the latest error only:\n%s", out)
	}
}
