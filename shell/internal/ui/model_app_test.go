package ui

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/Brasth/Rig/shell/internal/view"
)

// appModel is the app on a ready board, the chat on and claude the parent; the conversation is empty.
func appModel(t *testing.T) (*Model, *fakeSource) {
	t.Helper()
	t.Cleanup(func() { ApplyTheme("rig") })
	m, src := newReady(t)
	repo := t.TempDir()
	m.SetApp(repo)
	m.Update(tea.WindowSizeMsg{Width: 120, Height: 36})
	src.chatStatus = chatOn()
	src.chatStatus.Parent = "claude"
	m.csLast = time.Time{}
	run(m.fetchChatStatus(), m)
	return m, src
}

func appScreen(m *Model) string {
	st := m.st
	st.Styled = false
	return strings.Join(AppFrame(m.view, st, 120, 36), "\n")
}

func withConversation(t *testing.T, m *Model, src *fakeSource) {
	t.Helper()
	src.chatView = transcript("idle", ce("user", "Add rate limiting to POST /login"), ce("assistant", "I put a sliding-window limiter in front of it."))
	m.chatID = "c-1a2b3c4d"
	m.chBusy = false
	run(m.fetchChat(true), m)
}

func TestTheAppOpensOnTheFirstConversationInTheDesignsLight(t *testing.T) {
	m, src := appModel(t)
	if m.st.Theme != "light" || colGround != "#fafafa" {
		t.Fatalf("the design's default theme: %q", m.st.Theme)
	}
	out := appScreen(m)
	for _, want := range []string{"r•••   my-repo", "1 [conversation]", "2 changes", "3 code", "working", "⌃k", "⇥ panel",
		"What should we work on in my-repo?", "ask claude anything about my-repo", "1  explain this codebase", "4  review the open changes",
		"⏎ send    1-4 suggestion    ⌃k commands"} {
		if !strings.Contains(out, want) {
			t.Errorf("missing %q:\n%s", want, out)
		}
	}
	src.chatStarted = &view.ChatStarted{Session: "c-1a2b3c4d", Parent: "claude"}
	run(press(m, "1"), m)
	if len(src.chatSent) != 1 || src.chatSent[0][0] != "explain this codebase" {
		t.Fatalf("a digit sends its suggestion: %v", src.chatSent)
	}
}

func TestTheConversationScreenAndTheReplyBox(t *testing.T) {
	m, src := appModel(t)
	withConversation(t, m, src)
	out := appScreen(m)
	for _, want := range []string{"●  Add rate limiting to POST /login", "claude · ready", "I put a sliding-window limiter",
		"Settings loader", "claude asks, via claude", "npm i -D supertest", "[1  allow]", "2 deny", "or type", "› ▏Reply to claude"} {
		if !strings.Contains(out, want) {
			t.Errorf("missing %q:\n%s", want, out)
		}
	}
	run(press(m, "1"), m)
	if last := src.calls[len(src.calls)-1]; last.method != "job.allow" || last.params["id"] != "job-7f2" {
		t.Fatalf("1 allows the helper's request: %v", src.calls)
	}
	typeText(m, "pin the version")
	if m.st.Prompt != "pin the version" || !strings.Contains(appScreen(m), "› pin the version▏") {
		t.Fatalf("the box has the keyboard: %q", m.st.Prompt)
	}
	src.chatStarted = &view.ChatStarted{Session: "c-1a2b3c4d", Parent: "claude"}
	run(press(m, "enter"), m)
	if len(src.chatSent) != 1 || src.chatSent[0] != [2]string{"pin the version", "c-1a2b3c4d"} || m.st.Prompt != "" {
		t.Fatalf("sent %v", src.chatSent)
	}
	typeText(m, "/queue Docs for rate limit")
	run(press(m, "enter"), m)
	if last := src.calls[len(src.calls)-1]; last.method != "queue.add" || last.params["text"] != "Docs for rate limit" {
		t.Fatalf("/queue parks the task: %v", src.calls)
	}
}

func TestTabOpensTheAgentsPanel(t *testing.T) {
	m, src := appModel(t)
	withConversation(t, m, src)
	press(m, "tab")
	out := appScreen(m)
	for _, want := range []string{"Done today", "Settings loader", "Agents", "started by claude", "parent · in the conversation",
		"codex", "Rate limit POST /login", "Queue", "claude picks next", "1  Add /logout audit", "⏎ watch thread", "⇥ close"} {
		if !strings.Contains(out, want) {
			t.Errorf("missing %q:\n%s", want, out)
		}
	}
	press(m, "down")
	src.logReply = &view.LogReply{Text: "step 1\nstep 2"}
	run(press(m, "enter"), m)
	if m.st.App.Thread != "job-81c" || m.st.App.Panel || src.logIDs[len(src.logIDs)-1] != "job-81c" {
		t.Fatalf("enter watches the chosen helper's thread: %q %v", m.st.App.Thread, src.logIDs)
	}
	if out := appScreen(m); !strings.Contains(out, "Thread · job-81c") || !strings.Contains(out, "step 2") {
		t.Fatalf("the thread:\n%s", out)
	}
	press(m, "s")
	if m.st.Mode != ModeConfirm || !strings.Contains(m.st.Prompt, "Stop job-81c") {
		t.Fatalf("s asks before stopping: %q", m.st.Prompt)
	}
	run(press(m, "y"), m)
	if last := src.calls[len(src.calls)-1]; last.method != "job.cancel" || last.params["id"] != "job-81c" {
		t.Fatalf("stopped %v", src.calls)
	}
	press(m, "esc")
	if m.st.App.Thread != "" {
		t.Fatal("esc closes the thread")
	}
}

func TestCtrlKFindsFilesCommandsAndAsksTheParent(t *testing.T) {
	m, src := appModel(t)
	withConversation(t, m, src)
	if err := os.MkdirAll(filepath.Join(m.st.App.Repo, "src"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(m.st.App.Repo, "src", "limit.ts"), []byte("export const rateLimit = 1\nline two\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	m.Update(tea.KeyMsg{Type: tea.KeyCtrlK})
	m.Update(filesMsg{files: []string{"src/limit.ts", "src/login.ts"}})
	typeText(m, "limit")
	out := appScreen(m)
	for _, want := range []string{"› limit▏", "ask", "ask claude: limit", "files", "limit.ts  src"} {
		if !strings.Contains(out, want) {
			t.Errorf("missing %q:\n%s", want, out)
		}
	}
	m.Update(tea.KeyMsg{Type: tea.KeyDown})
	runAll(m, press(m, "enter"))
	if m.st.App.Tab != 3 || m.st.App.File != "src/limit.ts" || len(m.st.App.FileLines) == 0 {
		t.Fatalf("a file opens in the code tab: %+v", m.st.App)
	}
	if out := appScreen(m); !strings.Contains(out, "src/limit.ts") || !strings.Contains(out, "1  export const rateLimit = 1") {
		t.Fatalf("the code tab:\n%s", out)
	}
	press(m, "esc")
	m.Update(tea.KeyMsg{Type: tea.KeyCtrlK})
	typeText(m, "board")
	m.Update(tea.KeyMsg{Type: tea.KeyDown}) // past "ask claude: board" to the command
	run(press(m, "enter"), m)
	if m.st.App != nil {
		t.Fatal("the board command leaves the app for the jobs board")
	}
}

func TestAProjectWithNoParentIsAskedWhoToTalkTo(t *testing.T) {
	t.Cleanup(func() { ApplyTheme("rig") })
	m, src := newReady(t)
	m.SetApp(t.TempDir())
	src.chatStatus = chatOn()
	src.chatStatus.Configured = ""
	src.chatStatus.Available = []string{"claude", "codex"}
	m.csLast = time.Time{}
	run(m.fetchChatStatus(), m)
	if !m.st.App.Welcome {
		t.Fatal("the welcome screen opens by itself")
	}
	out := appScreen(m)
	for _, want := range []string{"Who do you want to talk to?", "It plans, hands work to helpers and checks it.", "› ●  claude", "codex",
		"not installed", "↑↓ choose    ⏎ continue    esc skip"} {
		if !strings.Contains(out, want) {
			t.Errorf("missing %q:\n%s", want, out)
		}
	}
	press(m, "down")
	run(press(m, "enter"), m)
	if len(src.parentSet) != 1 || src.parentSet[0] != "codex" || m.st.App.Welcome {
		t.Fatalf("saved %v", src.parentSet)
	}
}

func TestANewHelperIsAnnouncedOnce(t *testing.T) {
	m, src := appModel(t)
	withConversation(t, m, src)
	snap := *src.snap
	snap.View.Jobs = append([]view.Row{{ID: "job-9aa", Title: "Write tests for the reset flow", Label: "Running", Worker: "codex",
		Second: "reading tests/auth/"}}, snap.View.Jobs...)
	m.Update(snapshotMsg{snap: &snap, at: time.Now()})
	out := appScreen(m)
	for _, want := range []string{"claude started codex", "Write tests for the reset flow", "worktree job-9aa", "reading tests/auth/", "⏎ watch thread"} {
		if !strings.Contains(out, want) {
			t.Errorf("missing %q:\n%s", want, out)
		}
	}
	press(m, "esc")
	m.Update(snapshotMsg{snap: &snap, at: time.Now()})
	if m.st.App.NewJob != nil {
		t.Fatal("once only")
	}
}

func TestTheChangesTabFollowsTheJobs(t *testing.T) {
	m, src := appModel(t)
	withConversation(t, m, src)
	for i := range m.view.Jobs {
		m.view.Jobs[i].Ask = false // with a request waiting, 1 and 2 answer it instead
	}
	src.evidence = loadEvidence(t)
	runAll(m, press(m, "2"))
	if m.st.App.Tab != 2 || len(src.evidenceIDs) == 0 {
		t.Fatalf("2 opens the changes tab and loads the evidence: %d %v", m.st.App.Tab, src.evidenceIDs)
	}
	m.dkBusy = false
	run(m.fetchDock(true), m)
	if out := appScreen(m); !strings.Contains(out, "2 [changes]") || !strings.Contains(out, "diff") || !strings.Contains(out, "checks") {
		t.Fatalf("the changes tab:\n%s", out)
	}
	press(m, "1")
	if m.st.App.Tab != 1 {
		t.Fatal("1 goes back to the conversation")
	}
}
