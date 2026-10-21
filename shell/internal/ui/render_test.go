package ui

import (
	"encoding/json"
	"flag"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"

	"github.com/charmbracelet/lipgloss"

	"github.com/Brasth/Rig/shell/internal/view"
)

var update = flag.Bool("update", false, "rewrite golden files")

func loadView(t *testing.T) *view.View {
	t.Helper()
	raw, err := os.ReadFile("../../testdata/snapshot.json")
	if err != nil {
		t.Fatal(err)
	}
	var snap view.Snapshot
	if err := json.Unmarshal(raw, &snap); err != nil {
		t.Fatal(err)
	}
	return &snap.View
}

func loadWorkspace(t *testing.T) *view.Workspace {
	t.Helper()
	raw, err := os.ReadFile("../../testdata/workspace.json")
	if err != nil {
		t.Fatal(err)
	}
	var reply view.WorkspaceReply
	if err := json.Unmarshal(raw, &reply); err != nil {
		t.Fatal(err)
	}
	return &reply.View
}

func loadSettings(t *testing.T) *view.Panel {
	t.Helper()
	raw, err := os.ReadFile("../../testdata/settings.json")
	if err != nil {
		t.Fatal(err)
	}
	var reply view.SettingsReply
	if err := json.Unmarshal(raw, &reply); err != nil {
		t.Fatal(err)
	}
	return &reply.View
}

func loadFleet(t *testing.T) *view.Panel {
	t.Helper()
	raw, err := os.ReadFile("../../testdata/fleet.json")
	if err != nil {
		t.Fatal(err)
	}
	var reply view.FleetReply
	if err := json.Unmarshal(raw, &reply); err != nil {
		t.Fatal(err)
	}
	return &reply.View
}

func loadEvidence(t *testing.T) *view.Evidence {
	t.Helper()
	raw, err := os.ReadFile("../../testdata/evidence.json")
	if err != nil {
		t.Fatal(err)
	}
	var ev view.Evidence
	if err := json.Unmarshal(raw, &ev); err != nil {
		t.Fatal(err)
	}
	return &ev
}

func golden(t *testing.T, name, got string) {
	t.Helper()
	path := filepath.Join("..", "..", "testdata", "golden", name+".txt")
	if *update {
		if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte(got+"\n"), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	want, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("missing golden %s (run go test ./... -update): %v", name, err)
	}
	if strings.TrimRight(string(want), "\n") != got {
		t.Errorf("%s differs from golden\n--- got ---\n%s\n--- want ---\n%s", name, got, want)
	}
}

func assertGrid(t *testing.T, out string, w, h int) {
	t.Helper()
	lines := strings.Split(out, "\n")
	if len(lines) != h {
		t.Fatalf("got %d lines, want %d", len(lines), h)
	}
	for i, l := range lines {
		if lipgloss.Width(l) != w {
			t.Fatalf("line %d is %d cells, want %d: %q", i, lipgloss.Width(l), w, l)
		}
	}
}

func TestGoldenFrames(t *testing.T) {
	v := loadView(t)
	cases := []struct {
		name string
		w, h int
		tab  string
		mut  func(*State)
	}{
		{"jobs-120x36", 120, 36, "Jobs", nil},
		{"jobs-120x36-second-selected", 120, 36, "Jobs", func(s *State) { s.Selected["Jobs"] = 1 }},
		{"queue-120x36", 120, 36, "Queue", nil},
		{"workflows-120x36", 120, 36, "Workflows", nil},
		{"jobs-80x30-stacked", 80, 30, "Jobs", nil},
		{"jobs-60x20-list", 60, 20, "Jobs", nil},
		{"jobs-60x20-detail", 60, 20, "Jobs", func(s *State) { s.DetailOpen = true }},
		{"jobs-120x36-ascii", 120, 36, "Jobs", func(s *State) { s.ASCII = true }},
		{"jobs-120x36-stale", 120, 36, "Jobs", func(s *State) { s.Stale = "refresh failed: connection refused (snapshot 4.2s old)" }},
		{"jobs-120x36-confirm", 120, 36, "Jobs", func(s *State) { s.Mode, s.Prompt = ModeConfirm, "Stop job-81c Rate limit POST /login?" }},
		{"jobs-120x36-input", 120, 36, "Jobs", func(s *State) { s.Mode, s.Prompt = ModeInput, "Add audit log" }},
		{"tiny-30x6", 30, 6, "Jobs", nil},
		{"workspace-120x36", 120, 36, EnvTab, func(s *State) { s.Workspace = loadWorkspace(t) }},
		{"workspace-60x14", 60, 14, EnvTab, func(s *State) { s.Workspace = loadWorkspace(t) }},
		{"workspace-80x24-ascii", 80, 24, EnvTab, func(s *State) { s.Workspace, s.ASCII = loadWorkspace(t), true }},
		{"fleet-120x36", 120, 36, EnvTab, func(s *State) { s.Fleet = loadFleet(t) }},
		{"fleet-60x14", 60, 14, EnvTab, func(s *State) { s.Fleet = loadFleet(t) }},
		{"fleet-loading", 80, 12, EnvTab, nil},
		{"fleet-unavailable", 80, 12, EnvTab, func(s *State) { s.FleetE = "dc ls --all --json failed (1): engine not running" }},
		{"evidence-100x30", 100, 30, "Jobs", func(s *State) {
			s.EvidenceOpen, s.EvidenceID, s.Evidence = true, "job-7", loadEvidence(t)
		}},
		{"evidence-60x14-ascii", 60, 14, "Jobs", func(s *State) {
			s.EvidenceOpen, s.EvidenceID, s.Evidence, s.ASCII = true, "job-7", loadEvidence(t), true
		}},
		{"evidence-loading", 80, 12, "Jobs", func(s *State) { s.EvidenceOpen, s.EvidenceID = true, "job-7" }},
		{"evidence-unavailable", 80, 12, "Jobs", func(s *State) {
			s.EvidenceOpen, s.EvidenceID, s.EvidenceE = true, "job-7", "no such job: job-7"
		}},
		{"logs-100x24", 100, 24, "Logs", func(s *State) {
			s.Log = logReply("job-7f2", "starting\nreading src/middleware/limit.ts\nrunning npm test -- login\n12 passing\nverify next: lint")
		}},
		{"logs-60x12-back", 60, 12, "Logs", func(s *State) {
			s.Log = logReply("job-7f2", strings.Repeat("a long line that has to wrap at the edge of a narrow pane\n", 6)+"newest")
			s.LogUp = 2
		}},
		{"logs-loading", 80, 12, "Logs", nil},
		{"logs-unavailable", 80, 12, "Logs", func(s *State) { s.LogE = "connection refused" }},
		{"logs-no-jobs", 80, 12, "Logs", func(s *State) { s.Log = &view.LogReply{} }},
		{"chat-100x24", 100, 24, "Chat", func(s *State) {
			s.ChatOn = true
			s.Chat = transcript("idle", ce("user", "what is in a.txt?"), ce("assistant", "Looking at it."),
				ce("tool", "Read /repo/a.txt"), ce("assistant", "It says one.\nNothing else is in the file."),
				ce("user", "thanks"), ce("error", "the parent exited with status 3: not logged in"))
		}},
		{"chat-60x12-running", 60, 12, "Chat", func(s *State) {
			s.ChatOn = true
			s.Chat = transcript("running", ce("user", "a question long enough to need wrapping at this narrow width of pane"), ce("tool", "Bash ls"))
		}},
		{"chat-empty", 80, 12, "Chat", func(s *State) { s.ChatOn = true }},
		{"chat-unavailable", 80, 12, "Chat", func(s *State) { s.ChatOn, s.ChatE = true, "no such chat session: c-1a2b3c4d" }},
		{"chat-input", 80, 12, "Chat", func(s *State) {
			s.ChatOn, s.Mode, s.InputLabel, s.Prompt = true, ModeInput, "chat", "summarise the open jobs"
		}},
		{"term-help", 80, 12, SessionTab, nil},
		{"term-100x24-typing", 100, 24, SessionTab, func(s *State) {
			s.PaneTitle, s.PaneFocus, s.PaneRunning = "codex · typing (Ctrl-] returns to the board)", true, true
			s.PaneLines = []string{"$ rig status", "parent codex  workers claude", "$ \x1b[7m \x1b[0m"}
		}},
		{"term-60x12-exited", 60, 12, SessionTab, func(s *State) {
			s.PaneTitle = "container shell · exited: exit status 3"
			s.PaneLines = []string{"bye"}
		}},
		{"session-120x36-loading", 120, 36, SessionTab, nil},
		{"session-120x36", 120, 36, SessionTab, func(s *State) {
			s.Workspace, s.DockEvidence, s.DockID = loadWorkspace(t), loadEvidence(t), loadView(t).Jobs[0].ID
			s.PaneTitle, s.PaneRunning = "codex · running (Enter to type)", true
			s.PaneLines = []string{"> Fix the failing auth tests", "", "  Two writers, then a verify node."}
		}},
		{"session-120x36-ascii", 120, 36, SessionTab, func(s *State) {
			s.ASCII, s.Workspace, s.DockEvidence, s.DockID = true, loadWorkspace(t), loadEvidence(t), loadView(t).Jobs[0].ID
		}},
		{"session-140x40-second-selected", 140, 40, SessionTab, func(s *State) {
			s.Selected["Jobs"], s.Workspace = 1, loadWorkspace(t)
			s.DockE = "connection refused"
		}},
		{"session-120x36-confirm", 120, 36, SessionTab, func(s *State) {
			s.Mode, s.Prompt = ModeConfirm, "Stop job-7f2 Add supertest dev dep?"
		}},
		{"session-100x30-narrow", 100, 30, SessionTab, func(s *State) { s.Workspace = loadWorkspace(t) }},
		{"session-120x36-chat", 120, 36, SessionTab, func(s *State) {
			s.Workspace, s.DockEvidence, s.DockID = loadWorkspace(t), loadEvidence(t), loadView(t).Jobs[0].ID
			s.ChatOn = true
			s.Chat = &view.ChatTranscript{Session: "c-1a2b3c4d", Parent: "claude", State: "idle", Entries: []view.ChatEntry{
				{Role: "user", Text: "Fix the failing auth tests and add rate limiting to POST /login"},
				{Role: "assistant", Text: "Two writers, then a verify node.\nwf-12 · 3 nodes · 0/3 accepted"},
				{Role: "tool", Text: "rig_job_launch auth-tests claude job-7f2"},
				{Role: "user", Text: "allow, and pin the version"},
				{Role: "assistant", Text: "Allowed with a pinned version. job-7f2 resumed; waiting on job-81c."}}}
		}},
		{"session-120x36-chat-typing", 120, 36, SessionTab, func(s *State) {
			s.ChatOn, s.Mode, s.InputLabel, s.Prompt = true, ModeInput, "chat", "summarise the open jobs"
		}},
		{"session-120x36-chat-empty", 120, 36, SessionTab, func(s *State) { s.ChatOn = true }},
		{"settings-120x36", 120, 36, SettingsTab, func(s *State) { s.Settings = loadSettings(t) }},
		{"settings-60x14-ascii", 60, 14, SettingsTab, func(s *State) { s.ASCII, s.Settings = true, loadSettings(t) }},
		{"settings-loading", 80, 12, SettingsTab, nil},
		{"settings-unavailable", 80, 12, SettingsTab, func(s *State) { s.SettingsE = "connection refused" }},
		{"workspace-loading", 80, 12, EnvTab, nil},
		{"workspace-unavailable", 80, 12, EnvTab, func(s *State) { s.WorkspaceE = "dc ls --json failed (2): engine not running" }},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			st := NewState()
			st.Tab = c.tab
			if c.mut != nil {
				c.mut(&st)
			}
			out := Render(v, st, c.w, c.h)
			assertGrid(t, out, c.w, c.h)
			golden(t, c.name, out)
		})
	}
}

func TestEveryJobShowsStateAsGlyphAndWord(t *testing.T) {
	out := Render(loadView(t), NewState(), 120, 36)
	for _, want := range []string{"! Needs input", "● Running", "● Checking", "○ Unverified", "✗ Failed", "✓ Verified", "– Cancelled"} {
		if !strings.Contains(out, want) {
			t.Errorf("missing %q in\n%s", want, out)
		}
	}
	if !strings.Contains(out, "> ! Needs input") {
		t.Error("selected row must carry the > marker")
	}
}

func TestLoadingAndEmptyViews(t *testing.T) {
	st := NewState()
	out := Render(nil, st, 80, 24)
	assertGrid(t, out, 80, 24)
	if !strings.Contains(out, "loading") {
		t.Error("nil view must say it is loading")
	}
	empty := &view.View{Version: 1, Tabs: []view.Tab{{Name: "Jobs"}}}
	assertGrid(t, Render(empty, st, 120, 36), 120, 36)
}

func TestWorkerTextCannotInjectTerminalSequences(t *testing.T) {
	v := loadView(t)
	evil := *v
	evil.Jobs = append([]view.Row(nil), v.Jobs...)
	evil.Jobs[0].Title = "ok\x1b[2J\x1b]0;pwned\x07 title\x07\x00"
	evil.Jobs[0].Detail = []string{"line\x1b[31mred\x1b[0m"}
	out := Render(&evil, NewState(), 120, 36)
	if strings.ContainsRune(out, '\x1b') || strings.ContainsRune(out, '\x07') || strings.ContainsRune(out, 0) {
		t.Fatalf("control sequence leaked: %q", out)
	}
	if !strings.Contains(out, "ok title") {
		t.Error("visible text must survive sanitising")
	}
}

func TestAsciiModeHasNoNonAsciiUIGlyphs(t *testing.T) {
	st := NewState()
	st.ASCII = true
	for _, r := range Render(loadView(t), st, 120, 36) {
		if r > 127 {
			t.Fatalf("non-ASCII rune %q in ASCII mode", r)
		}
	}
}

func TestWindowStartKeepsSelectionVisibleAndNeverSplitsEntries(t *testing.T) {
	for n := 1; n <= 30; n++ {
		for per := 1; per <= 8; per++ {
			for sel := 0; sel < n; sel++ {
				for _, off := range []int{0, sel, n - 1, 100} {
					s := windowStart(off, sel, per, n)
					if sel < s || sel >= s+per || s < 0 || s > max(0, n-1) {
						t.Fatalf("n=%d per=%d sel=%d off=%d gave start %d", n, per, sel, off, s)
					}
				}
			}
		}
	}
}

func TestLongTitlesTruncateWithoutBreakingWidth(t *testing.T) {
	v := loadView(t)
	long := *v
	long.Jobs = append([]view.Row(nil), v.Jobs...)
	long.Jobs[1].Title = strings.Repeat("très long 日本語 title ", 20)
	for _, w := range []int{40, 52, 70, 100, 140} {
		assertGrid(t, Render(&long, NewState(), w, 30), w, 30)
	}
}

func TestWorkspaceTabFlagsAttentionAndNeverShowsListChrome(t *testing.T) {
	st := NewState()
	st.Tab = "Jobs"
	st.Workspace = loadWorkspace(t)
	out := Render(loadView(t), st, 120, 36)
	if !strings.Contains(strings.Split(out, "\n")[1], "Env !") {
		t.Errorf("tab bar must flag workspace attention:\n%s", strings.Split(out, "\n")[1])
	}
	st.Tab = EnvTab
	out = Render(loadView(t), st, 120, 36)
	for _, banned := range []string{"│", "┬", "Enter details", "x stop"} {
		if strings.Contains(out, banned) {
			t.Errorf("workspace tab must not show %q", banned)
		}
	}
	for _, want := range []string{"kind     devcontainer", "! 9229  held by node", "fix: dc recover engine-slow"} {
		if !strings.Contains(out, want) {
			t.Errorf("missing %q", want)
		}
	}
}

func TestWorkspaceTextIsSanitisedAndScrolls(t *testing.T) {
	ws := &view.Workspace{Version: 1}
	var lines []view.Line
	for i := 0; i < 40; i++ {
		lines = append(lines, view.Line{Text: "row \x1b[31m" + strings.Repeat("x", i) + "\x1b[0m"})
	}
	ws.Sections = []view.Section{{Title: "Big\x1b[2J", Lines: lines}}
	st := NewState()
	st.Tab, st.Workspace = EnvTab, ws
	out := Render(nil, st, 80, 20)
	if strings.ContainsRune(out, '\x1b') {
		t.Fatal("escape sequence leaked")
	}
	st.DetailOff = 10
	scrolled := Render(nil, st, 80, 20)
	first := regexp.MustCompile(`(?m)^ row +$`)
	if !first.MatchString(out) || first.MatchString(scrolled) {
		t.Fatal("scrolling must move the first line out of view")
	}
	st.DetailOff = 10000
	assertGrid(t, Render(nil, st, 80, 20), 80, 20)
}
