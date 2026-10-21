package ui

import (
	"encoding/json"
	"fmt"
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
	for _, want := range []string{"r•••   my-repo",
		"What should we work on in my-repo?", "ask claude anything about my-repo", "1  explain this codebase", "4  review the open changes",
		"⏎ send    1-4 suggestion    @ add file    ⌃k commands    ? more"} {
		if !strings.Contains(out, want) {
			t.Errorf("missing %q:\n%s", want, out)
		}
	}
	src.chatStarted = &view.ChatStarted{Session: "c-1a2b3c4d", Parent: "claude"}
	run(press(m, "1"), m)
	if len(src.chatSent) != 1 || src.chatSent[0][0] != "explain this codebase" {
		t.Fatalf("a digit sends its suggestion: %v", src.chatSent)
	}
	if strings.Contains(strings.Split(out, "\n")[0], "conversation") {
		t.Fatal("the first conversation's top bar is the mark and the repo only, as the design draws it")
	}
}

func TestTheConversationScreenAndTheReplyBox(t *testing.T) {
	m, src := appModel(t)
	withConversation(t, m, src)
	out := appScreen(m)
	for _, want := range []string{"[c]  Add rate limiting to POST /login", "claude · ready", "I put a sliding-window limiter",
		"Settings loader", "claude asks, via claude", "npm i -D supertest", "[allow]", " deny ", "or type", "› ▏Reply to claude"} {
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
	src.today = sampleToday()
	run(m.fetchToday(), m)
	press(m, "tab")
	out := appScreen(m)
	for _, want := range []string{"Done today", "1 · all ›", "Rate limit POST /login", "claude · applied by claude", "Agents", "started by claude",
		"parent · in the convers", "claude allowed 1 command today",
		"codex", "Rate limit POST /login", "Queue", "claude picks next", "1  Add /logout audit", "⏎ thread", "p pause", "⇥ close"} {
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
	run(press(m, "enter"), m)
	if m.st.App.Tab != 3 || m.st.App.File != "src/limit.ts" || len(m.st.App.FileLines) == 0 {
		t.Fatalf("a file opens in the code tab: %+v", m.st.App)
	}
	if out := appScreen(m); !strings.Contains(out, "src › limit.ts") || !strings.Contains(out, "1  export const rateLimit = 1") {
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
	for _, want := range []string{"Who do you want to talk to?", "It plans, hands work to helpers and checks it.", "› [c]  claude", "codex",
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

const sampleDiff = `diff --git a/src/routes/login.ts b/src/routes/login.ts
index 1111111..2222222 100644
--- a/src/routes/login.ts
+++ b/src/routes/login.ts
@@ -13,4 +13,6 @@ import { audit } from '../audit'
 export const router = Router()
 
-router.post('/login', async (req, res) => {
+router.post('/login',
+  rateLimit({ windowMs: 60_000, max: 5 }),
+  async (req, res) => {
   const user = await verify(req.body.email, req.body.password)
diff --git a/tests/login.test.ts b/tests/login.test.ts
new file mode 100644
--- /dev/null
+++ b/tests/login.test.ts
@@ -0,0 +1,2 @@
+test('limits', () => {})
+// five tries
`

func TestParseDiffNumbersTheLinesAsTheDesignDoes(t *testing.T) {
	files := ParseDiff(sampleDiff)
	if len(files) != 2 || files[0].Path != "src/routes/login.ts" || files[1].Path != "tests/login.test.ts" {
		t.Fatalf("files %+v", files)
	}
	if files[0].Added != 3 || files[0].Deleted != 1 || files[1].Added != 2 {
		t.Fatalf("counts %+v", files)
	}
	got := []string{}
	for _, l := range files[0].Lines[1:] {
		got = append(got, fmt.Sprintf("%d%c", l.Num, l.Kind))
	}
	if strings.Join(got, " ") != "13  14  15- 15+ 16+ 17+ 18 " {
		t.Fatalf("numbers %q", strings.Join(got, " "))
	}
}

func TestTheChangesTabShowsTheHelpersDiff(t *testing.T) {
	m, src := appModel(t)
	withConversation(t, m, src)
	for i := range m.view.Jobs {
		m.view.Jobs[i].Ask = false // with a request waiting, 1 and 2 answer it instead
	}
	src.evidence = loadEvidence(t)
	src.diffs = map[string]*view.JobDiff{"job-7f2": {JobID: "job-7f2", Source: "git", Diff: sampleDiff}}
	run(press(m, "2"), m)
	if m.st.App.Tab != 2 || len(src.diffIDs) == 0 || src.diffIDs[0] != "job-7f2" {
		t.Fatalf("2 opens the changes tab and loads the diff: %d %v", m.st.App.Tab, src.diffIDs)
	}
	out := appScreen(m)
	for _, want := range []string{"2 [changes]", "Add supertest dev dep", "claude · 2 files", "login.ts", "login.test.ts",
		"src/routes/login.ts   +3 −1", "  13   export const router = Router()", "  15 − router.post('/login', async (req, res) => {",
		"  16 +   rateLimit({ windowMs: 60_000, max: 5 }),", "] next file", "c comment"} {
		if !strings.Contains(out, want) {
			t.Errorf("missing %q:\n%s", want, out)
		}
	}
	press(m, "]")
	if out := appScreen(m); !strings.Contains(out, "tests/login.test.ts   +2 −0") {
		t.Fatalf("] the next file:\n%s", out)
	}
	press(m, "c")
	typeText(m, "send 429 with a Retry-After header")
	run(press(m, "enter"), m)
	if last := src.calls[len(src.calls)-1]; last.method != "job.message" || last.params["id"] != "job-7f2" || last.params["text"] != "send 429 with a Retry-After header" {
		t.Fatalf("c leaves a comment in the helper's inbox: %v", src.calls)
	}
	if out := appScreen(m); !strings.Contains(out, "you     send 429 with a Retry-After header") {
		t.Fatalf("the comment shows under the diff:\n%s", out)
	}
	press(m, "esc")
	if m.st.App.Tab != 1 {
		t.Fatal("esc goes back to the conversation")
	}
}

func TestTheCodeTabSelectsLinesToAskAboutAndFindsText(t *testing.T) {
	m, src := appModel(t)
	withConversation(t, m, src)
	path := filepath.Join(m.st.App.Repo, "login.ts")
	if err := os.WriteFile(path, []byte("import { Router } from 'express'\n\nexport const router = Router()\nrouter.post('/login')\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	run(m.runPalette(paletteItem{Action: "file:login.ts"}, ""), m)
	out := appScreen(m)
	for _, want := range []string{"login.ts", "  1  import { Router } from 'express'", "⌃b tree", "/ find", "v select", "e edit", "E $EDITOR"} {
		if !strings.Contains(out, want) {
			t.Errorf("missing %q:\n%s", want, out)
		}
	}
	press(m, "v")
	press(m, "down", "down")
	if out := appScreen(m); !strings.Contains(out, "Ln 3, Col 1 (3 selected)") || !strings.Contains(out, "[a  ask claude]   @ add to chat   x explain") {
		t.Fatalf("v then ↓ selects lines:\n%s", out)
	}
	press(m, "a")
	if m.st.App.Tab != 1 || m.st.Prompt != "About login.ts:1-3: " {
		t.Fatalf("a asks the parent about them: %d %q", m.st.App.Tab, m.st.Prompt)
	}
	m.st.App.Tab = 3
	press(m, "/")
	typeText(m, "post")
	run(press(m, "enter"), m)
	if m.st.App.FileOff != 0 || !strings.Contains(appScreen(m), "[post]") {
		t.Fatalf("/ finds and marks the word:\n%s", appScreen(m))
	}
}

func TestSearchFindsTextAcrossTheRepository(t *testing.T) {
	m, src := appModel(t)
	withConversation(t, m, src)
	m.Update(tea.KeyMsg{Type: tea.KeyCtrlF})
	m.st.App.Search.Query = "rateLimit"
	m.Update(searchMsg{query: m.st.App.Search.key(), hits: []SearchHit{{"src/middleware/limit.ts", 4, "export const rateLimit = (opts = {}) => {"},
		{"src/routes/login.ts", 2, "import { rateLimit } from '../middleware/limit'"}}})
	out := appScreen(m)
	for _, want := range []string{"/ rateLimit▏", "2 results in 2 files", "src/middleware/limit.ts", "4    export const [rateLimit]", "src/routes/login.ts",
		"⇥ ask claude"} {
		if !strings.Contains(out, want) {
			t.Errorf("missing %q:\n%s", want, out)
		}
	}
	m.Update(tea.KeyMsg{Type: tea.KeyDown})
	m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	if m.st.App.Search != nil || m.st.App.Tab != 3 || m.st.App.File != "src/routes/login.ts" {
		t.Fatalf("Enter opens the result: %+v", m.st.App)
	}
}

func TestMarkdownAndTablesArePreviewed(t *testing.T) {
	m, src := appModel(t)
	withConversation(t, m, src)
	_ = os.WriteFile(filepath.Join(m.st.App.Repo, "README.md"), []byte("# my-repo\n\nThe API.\n\n## Rate limits\n- five a minute\n"), 0o644)
	_ = os.WriteFile(filepath.Join(m.st.App.Repo, "orders.csv"), []byte("id,city,total\n10421,Hanoi,1240000\n"), 0o644)
	run(m.runPalette(paletteItem{Action: "file:README.md"}, ""), m)
	out := appScreen(m)
	for _, want := range []string{"README.md  rendered · s source", "my-repo", "Rate limits", "• five a minute"} {
		if !strings.Contains(out, want) {
			t.Errorf("missing %q:\n%s", want, out)
		}
	}
	press(m, "s")
	if !strings.Contains(appScreen(m), "## Rate limits") {
		t.Fatal("s shows the source")
	}
	run(m.runPalette(paletteItem{Action: "file:orders.csv"}, ""), m)
	if out := appScreen(m); !strings.Contains(out, "orders.csv  1 rows · table") || !strings.Contains(out, "10421  Hanoi  1240000") {
		t.Fatalf("a csv is a table:\n%s", out)
	}
}

func sampleSettings() *view.AppSettings {
	return &view.AppSettings{Theme: "light", ThemeScope: "default", Themes: ThemeNames(), BellScope: "default", Parent: "claude", Start: "last", Ship: "pr", TabSize: 4,
		Enabled: true, Workers: map[string]bool{"claude": true, "codex": true, "grok": false}, Installed: []string{"claude", "codex"},
		MaxRunning: 3, Objective: "balanced", RoutingMode: "smart", Sandbox: "host", Worktrees: "off", WorktreeContainer: "shared",
		Harness: ".rig/harness.toml", HarnessText: "parent = \"claude\"\n\n[project]\nenabled = true\n", Global: "~/.rig/config.toml"}
}

func TestSettingsChangeRealSettingsWhereTheyAreKept(t *testing.T) {
	m, src := appModel(t)
	src.appSettings = sampleSettings()
	run(m.runPalette(paletteItem{Action: "settings"}, ""), m)
	out := appScreen(m)
	for _, want := range []string{"←  Settings   [all repos]", "[general]", "agents", "routing", "toml", "Theme", "› [light]", "dracula", "all repos",
		"Default parent", "[c] claude · auto", "When rig starts", "[last project]", "Rig in this repo", "on", "off keeps history and memory", "Updates",
		"←→ choose    ⏎ apply    ⇥ section    g all repos / this repo"} {
		if !strings.Contains(out, want) {
			t.Errorf("missing %q:\n%s", want, out)
		}
	}
	if strings.Contains(out, "billing") {
		t.Fatal("no billing")
	}
	run(press(m, "right"), m)
	if colGround != "#fafafa" || len(src.themeSaved) != 0 || m.st.App.Settings.Pick != "dark" {
		t.Fatalf("→ only picks: nothing changes yet: %v", src.themeSaved)
	}
	if out := appScreen(m); !strings.Contains(out, "<dark>") || !strings.Contains(out, "⏎ apply dark") {
		t.Fatalf("the pick is shown with ⏎ to apply it:\n%s", out)
	}
	press(m, "esc")
	if m.st.App.Settings == nil || m.st.App.Settings.Pick != "" {
		t.Fatal("esc drops the pick and stays in Settings")
	}
	press(m, "right")
	run(press(m, "enter"), m)
	if colGround != "#15161a" || src.themeSaved[0] != [2]string{"dark", "all"} {
		t.Fatalf("⏎ applies the theme and keeps it: %v", src.themeSaved)
	}
	press(m, "tab")
	press(m, "down")
	run(press(m, " "), m)
	if src.settingsSaved[0] != [3]string{"workers.codex", "false", "repo"} {
		t.Fatalf("space turns a helper off in this repo: %v", src.settingsSaved)
	}
	for i := 0; i < 4; i++ {
		press(m, "down")
	}
	press(m, "left")
	run(press(m, "enter"), m)
	if last := src.settingsSaved[len(src.settingsSaved)-1]; last != [3]string{"queue.max_running", "2", "repo"} {
		t.Fatalf("← fewer at the same time: %v", src.settingsSaved)
	}
	press(m, "shift+tab")                    // back to general
	press(m, "down", "down", "down", "down") // past the theme, the parent, its model and when rig starts
	press(m, "right")
	run(press(m, "enter"), m)
	if m.st.Mode != ModeConfirm || !strings.Contains(m.st.Prompt, "Turn Rig off") {
		t.Fatalf("turning Rig off asks first: %q", m.st.Prompt)
	}
	run(press(m, "y"), m)
	if last := src.settingsSaved[len(src.settingsSaved)-1]; last != [3]string{"project.enabled", "false", "repo"} {
		t.Fatalf("saved %v", src.settingsSaved)
	}
	m.st.App.Settings.Section = len(SettingsSections) - 1
	if out := appScreen(m); !strings.Contains(out, "[toml]") || !strings.Contains(out, "parent = \"claude\"") || !strings.Contains(out, "✓ valid") {
		t.Fatalf("the toml section:\n%s", out)
	}
	press(m, "esc")
	if m.st.App.Settings != nil {
		t.Fatal("esc leaves the settings")
	}
}

func TestTheBellRingsOnceForANewRequest(t *testing.T) {
	m, src := appModel(t)
	rings := 0
	m.ringer = func() { rings++ }
	src.appSettings = sampleSettings()
	src.appSettings.Bell, src.appSettings.NotifyNeeds = true, true
	run(m.fetchAppSettings(), m)
	run(m.bell(), m)
	run(m.bell(), m)
	if rings != 1 {
		t.Fatalf("one ring for job-7f2's request, rang %d", rings)
	}
	m.st.AppSettings.Bell = false
	m.lastAsk = ""
	run(m.bell(), m)
	if rings != 1 {
		t.Fatal("no ring with the bell off")
	}
}

func TestFirstRunGoesFromTheParentToHelpersToTheSandbox(t *testing.T) {
	t.Cleanup(func() { ApplyTheme("rig") })
	m, src := newReady(t)
	m.SetApp(t.TempDir())
	src.appSettings = sampleSettings()
	run(m.fetchAppSettings(), m)
	src.chatStatus = chatOn()
	src.chatStatus.Configured = ""
	src.chatStatus.Available = []string{"claude", "codex"}
	m.csLast = time.Time{}
	run(m.fetchChatStatus(), m)
	if !strings.Contains(appScreen(m), "1 of 3") {
		t.Fatalf("step 1 of 3:\n%s", appScreen(m))
	}
	run(press(m, "enter"), m)
	out := appScreen(m)
	for _, want := range []string{"2 of 3", "Who can claude hand work to?", "Each helper works in its own copy of the repo.", "codex", "[x]", "up to 3 at once"} {
		if !strings.Contains(out, want) {
			t.Errorf("missing %q:\n%s", want, out)
		}
	}
	press(m, "down", " ", "2")
	run(press(m, "enter"), m)
	saved := map[string]string{}
	for _, s := range src.settingsSaved {
		saved[s[0]] = s[1]
	}
	if saved["workers.codex"] != "false" || saved["queue.max_running"] != "2" {
		t.Fatalf("helpers and how many at once saved: %v", src.settingsSaved)
	}
	out = appScreen(m)
	for _, want := range []string{"3 of 3", "Where should agents work?", "New sandbox", "recommended", "This machine", "! no boundary around your files", "⏎ start sandbox"} {
		if !strings.Contains(out, want) {
			t.Errorf("missing %q:\n%s", want, out)
		}
	}
	press(m, "down")
	run(press(m, "enter"), m)
	if last := src.settingsSaved[len(src.settingsSaved)-1]; last != [3]string{"sandbox.provider", "host", "repo"} || m.st.App.Onboard != nil {
		t.Fatalf("this machine saved and the steps done: %v", src.settingsSaved)
	}
}

func TestNoAgentShowsHowToInstallOne(t *testing.T) {
	m, src := appModel(t)
	src.appSettings = sampleSettings()
	src.appSettings.Installed = nil
	run(m.fetchAppSettings(), m)
	m.st.ChatParent = ""
	out := appScreen(m)
	for _, want := range []string{"Let's get you an agent  1 of 3", "› 1  install one for me  recommended", "[c] claude  claude pro/max   [c] codex  chatgpt",
		"on this computer · $ npm install -g @anthropic-ai/claude-code", "←→ agent", "⏎ install claude", "r scan again"} {
		if !strings.Contains(out, want) {
			t.Errorf("missing %q:\n%s", want, out)
		}
	}
	var ran []string
	m.exec = func(argv []string, done func(error) tea.Msg) tea.Cmd { ran = argv; return nil }
	m.Update(tea.KeyMsg{Type: tea.KeyRight})
	press(m, "enter")
	if m.st.Mode != ModeConfirm || !strings.Contains(m.st.Prompt, "Install codex") {
		t.Fatalf("installing asks first: %q", m.st.Prompt)
	}
	press(m, "y")
	if strings.Join(ran, " ") != "sh -c npm install -g @openai/codex" {
		t.Fatalf("ran %v", ran)
	}
}

func TestASignedOutParentCanBeReplaced(t *testing.T) {
	m, src := appModel(t)
	src.chatView = transcript("failed", ce("user", "hi"), ce("error", "the parent exited with status 1: Invalid API key · Please run /login"))
	m.chatID, m.chBusy = "c-1a2b3c4d", false
	run(m.fetchChat(true), m)
	m.openPicker()
	m.st.Picker.Items = []PickItem{{Name: "claude", Installed: true}, {Name: "codex", Installed: true}}
	out := appScreen(m)
	for _, want := range []string{"claude is signed out", "Nothing gets checked or applied until a parent is back", "1  sign in to claude again", "2  hand over to [c] codex"} {
		if !strings.Contains(out, want) {
			t.Errorf("missing %q:\n%s", want, out)
		}
	}
	m.st.Picker = nil
	m.openPicker()
	m.st.Picker.Items = []PickItem{{Name: "claude", Installed: true}, {Name: "codex", Installed: true}}
	m.st.Picker = &Picker{Items: m.st.Picker.Items}
	run(press(m, "2"), m)
	if len(src.parentSet) != 1 || src.parentSet[0] != "codex" {
		t.Fatalf("handed over: %v", src.parentSet)
	}
}

func TestAStoppedSandboxSaysSoAndOffersTheWaysOut(t *testing.T) {
	m, src := appModel(t)
	src.appSettings = sampleSettings()
	src.appSettings.Sandbox = "devcontainer"
	run(m.fetchAppSettings(), m)
	m.st.Workspace = &view.Workspace{Sections: []view.Section{{Title: "Workspace", Lines: []view.Line{{Text: "kind    devcontainer"},
		{Text: "! docker is not running", Tone: "attention"}}}}}
	out := appScreen(m)
	for _, want := range []string{"sandbox stopped", "agents are paused", "✗ docker is not running", "✓ kind", "devcontainer", "1  start it", "l doctor log", "3  run on this machine instead"} {
		if !strings.Contains(out, want) {
			t.Errorf("missing %q:\n%s", want, out)
		}
	}
	run(press(m, "3"), m)
	if last := src.settingsSaved[len(src.settingsSaved)-1]; last != [3]string{"sandbox.provider", "host", "repo"} {
		t.Fatalf("this machine instead: %v", src.settingsSaved)
	}
}

func sampleToday() *view.Today {
	var t view.Today
	_ = json.Unmarshal([]byte(`{"rows": [
		{"job_id": "job-3b9", "title": "Rate limit POST /login", "worker": "claude", "branch": "main", "state": "applied", "at": 1792500000, "commit": "7f3a2c1", "files": 3, "added": 151, "deleted": 1,
			"checks": [{"name": "lint", "ok": true}, {"name": "tests", "ok": true}], "review": "codex"},
		{"job_id": "job-4c1", "title": "Add index to session store", "worker": "opencode", "branch": "main", "state": "undone", "at": 1792499000, "commit": "4e91b0c", "files": 1, "added": 4, "deleted": 0},
		{"job_id": "job-81c", "title": "Tests for password reset", "worker": "codex", "branch": "main", "state": "held", "at": 1792498000},
		{"job_id": "", "title": "allowed prisma migrate dev", "worker": "claude", "by": "you", "state": "command", "at": 1792497000}]}`), &t)
	return &t
}

func TestDoneTodayUndoesAndRedoesAfterAQuestion(t *testing.T) {
	m, src := appModel(t)
	src.today = sampleToday()
	run(m.runPalette(paletteItem{Action: "today"}, ""), m)
	out := appScreen(m)
	for _, want := range []string{"done today", "Done today  applied, held and allowed by claude", "Rate limit POST /login", "applied",
		"undone by you", "held", "allowed prisma migrate dev", "command", "3 files +151 −1 · ✓ lint ✓ tests ✓ review by codex",
		"on main as 7f3a2c1 · undo reverts it; claude handles anything built on", "u undo", "r redo", "d diff", "a let claude apply"} {
		if !strings.Contains(out, want) {
			t.Errorf("missing %q:\n%s", want, out)
		}
	}
	press(m, "u")
	if m.st.Mode != ModeConfirm || !strings.Contains(m.st.Prompt, "It reverts 7f3a2c1 on main") {
		t.Fatalf("undo asks first: %q", m.st.Prompt)
	}
	src.chatStarted = &view.ChatStarted{Session: "c-9", Parent: "claude"}
	_, next := m.Update(press(m, "y")())
	run(next, m)
	if last := src.calls[len(src.calls)-1]; last.method != "applies.undo" || last.params["id"] != "job-3b9" {
		t.Fatalf("undone: %v", src.calls)
	}
	if len(src.chatSent) != 1 || !strings.Contains(src.chatSent[0][0], `I undid "Rate limit POST /login" (job job-3b9, merged as 7f3a2c1 on main)`) {
		t.Fatalf("the parent is told, to handle anything built on it: %v", src.chatSent)
	}
	if m.chatID != "c-9" || m.st.App.History == nil {
		t.Fatalf("done today stays open while the parent works: %q", m.chatID)
	}
	press(m, "down")
	press(m, "r")
	run(press(m, "y"), m)
	if last := src.calls[len(src.calls)-1]; last.method != "applies.redo" || last.params["id"] != "job-4c1" {
		t.Fatalf("redone: %v", src.calls)
	}
	press(m, "down")
	run(press(m, "a"), m)
	if last := src.calls[len(src.calls)-1]; last.method != "applies.hold" || last.params["on"] != false {
		t.Fatalf("released: %v", src.calls)
	}
	press(m, "down")
	if out := appScreen(m); !strings.Contains(out, "allowed by you at") {
		t.Errorf("a command row says who allowed it:\n%s", out)
	}
}

func TestDoneTodayOpensAnAppliedJobsDiff(t *testing.T) {
	m, src := appModel(t)
	withConversation(t, m, src)
	today := sampleToday()
	today.Rows[0].JobID = m.view.Rows("Jobs")[1].ID
	src.today = today
	run(m.runPalette(paletteItem{Action: "today"}, ""), m)
	run(press(m, "d"), m)
	if m.st.App.History != nil || m.st.App.Tab != 2 || m.st.Selected["Jobs"] != 1 {
		t.Fatalf("d opens the changes tab on that job: tab %d sel %d", m.st.App.Tab, m.st.Selected["Jobs"])
	}
}

func TestTheChangesTabHoldsAHelpersWork(t *testing.T) {
	m, src := appModel(t)
	withConversation(t, m, src)
	for i := range m.view.Jobs {
		m.view.Jobs[i].Ask = false
	}
	src.today = sampleToday()
	run(m.fetchToday(), m)
	run(press(m, "2"), m)
	run(press(m, "h"), m)
	if last := src.calls[len(src.calls)-1]; last.method != "applies.hold" || last.params["id"] != "job-7f2" || last.params["on"] != true {
		t.Fatalf("h holds the shown helper's work: %v", src.calls)
	}
	if !strings.Contains(appScreen(m), "u undo") || !strings.Contains(appScreen(m), "h hold") {
		t.Fatal("the footer names them")
	}
}

func TestShipPushesOrOpensAPullRequestAfterAQuestion(t *testing.T) {
	m, src := appModel(t)
	src.today = sampleToday()
	var ship view.ShipStatus
	_ = json.Unmarshal([]byte(`{"branch": "main", "upstream": "origin/main", "title": "Rate limit login, reset-token tests", "pr_branch": "rig/rate-limit-login", "gh": true,
		"title_from": "tasks", "checks": [{"name": "lint", "ok": true}, {"name": "build", "ok": true}],
		"ahead": [{"commit": "7f3a2c1", "subject": "rig: merge job job-3b9"}, {"commit": "b81e09d", "subject": "fix typo in reset email"}]}`), &ship)
	src.shipStatus = &ship
	run(m.runPalette(paletteItem{Action: "ship"}, ""), m)
	out := appScreen(m)
	for _, want := range []string{"ship", "Ship 2 changes", "main is 2 ahead of origin/main", "Rate limit POST /login", "7f3a2c1", "fix typo in reset email",
		"[open pull request]", "push to main", "branch rig/rate-limit-login", "title  Rate limit login, reset-token tests", "written by claude · e to edit",
		"✓ lint  ✓ build  agents never push; you do"} {
		if !strings.Contains(out, want) {
			t.Errorf("missing %q:\n%s", want, out)
		}
	}
	press(m, "e")
	m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("!")})
	press(m, "enter")
	run(press(m, "enter"), m)
	if m.st.Mode != ModeConfirm || !strings.Contains(m.st.Prompt, "open a pull request") {
		t.Fatalf("asks first: %q", m.st.Prompt)
	}
	run(press(m, "y"), m)
	if last := src.calls[len(src.calls)-1]; last.method != "ship.run" || last.params["mode"] != "pr" || last.params["title"] != "Rate limit login, reset-token tests!" {
		t.Fatalf("pull request: %v", src.calls)
	}
	press(m, "right")
	run(press(m, "enter"), m)
	run(press(m, "y"), m)
	if last := src.calls[len(src.calls)-1]; last.params["mode"] != "push" {
		t.Fatalf("push: %v", src.calls)
	}
}

func TestTheConversationFooterAndAnswerChipsAreTheDesigns(t *testing.T) {
	m, src := appModel(t)
	withConversation(t, m, src)
	lines := strings.Split(appScreen(m), "\n")
	if strings.TrimSpace(lines[0]) != "" || strings.TrimSpace(lines[len(lines)-1]) != "" {
		t.Fatal("a row of room above the top bar and below the footer")
	}
	lines = lines[1 : len(lines)-1]
	foot := lines[len(lines)-1]
	for _, want := range []string{"d see changes", "← → pick answer", "⏎ send", "u undo   h hold   ⌃p files   ? help"} {
		if !strings.Contains(foot, want) {
			t.Errorf("footer missing %q: %q", want, foot)
		}
	}
	if !strings.HasPrefix(lines[len(lines)-2], "────") {
		t.Errorf("a rule above the footer: %q", lines[len(lines)-2])
	}
	m.Update(tea.KeyMsg{Type: tea.KeyRight})
	if !strings.Contains(appScreen(m), " allow    [deny]") {
		t.Fatalf("→ picks deny:\n%s", appScreen(m))
	}
	run(press(m, "enter"), m)
	if last := src.calls[len(src.calls)-1]; last.method != "job.deny" || last.params["id"] != "job-7f2" {
		t.Fatalf("⏎ sends the picked answer: %v", src.calls)
	}
}

func TestHoldAndUndoFromTheReplyBoxAskFirstAndNeverEatTyping(t *testing.T) {
	m, src := appModel(t)
	withConversation(t, m, src)
	for i := range m.view.Jobs {
		m.view.Jobs[i].Ask = false
	}
	src.today = sampleToday()
	run(m.fetchToday(), m)
	press(m, "h")
	if m.st.Mode != ModeConfirm || !strings.Contains(m.st.Prompt, "Hold ") || !strings.Contains(appScreen(m), "⏎ yes    esc no") {
		t.Fatalf("h asks first: %q", m.st.Prompt)
	}
	press(m, "o")
	if m.st.Mode == ModeConfirm || m.st.Prompt != "ho" {
		t.Fatalf("typing on cancels and keeps the letters: %q", m.st.Prompt)
	}
	calls := len(src.calls)
	m.st.Prompt = ""
	press(m, "h")
	run(press(m, "enter"), m)
	if len(src.calls) != calls+1 || src.calls[calls].method != "applies.hold" || src.calls[calls].params["on"] != true {
		t.Fatalf("⏎ holds: %v", src.calls[calls:])
	}
	press(m, "d")
	if m.st.App.Tab != 2 {
		t.Fatalf("d sees the changes: tab %d", m.st.App.Tab)
	}
}

func TestTheParentsMarkdownMarkersAreNotShownRaw(t *testing.T) {
	if got := plainMarkdown("on `feature/x` with **care**"); got != "on feature/x with care" {
		t.Fatalf("%q", got)
	}
}

func TestChecksAreWordedAsTheDesignDoes(t *testing.T) {
	st := State{}
	for in, want := range map[string]string{"● unit  exit 0  devcontainer": "● unit pass", "! lint  exit 1  host": "● lint failed",
		"● tests  exit running  host": "● tests running", "no checks run": ""} {
		if got := checkWord(st, in); got != want {
			t.Errorf("%q: %q", in, got)
		}
	}
}

func TestSearchOptionsAndReplaceAskFirst(t *testing.T) {
	m, src := appModel(t)
	withConversation(t, m, src)
	path := filepath.Join(m.st.App.Repo, "limit.ts")
	_ = os.WriteFile(path, []byte("const rateLimit = 1\nuse(rateLimit)\nrateLimiter()\n"), 0o644)
	m.Update(tea.KeyMsg{Type: tea.KeyCtrlF})
	s := m.st.App.Search
	s.Query = "rateLimit"
	m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("w"), Alt: true})
	if !s.Word {
		t.Fatal("alt+w matches whole words")
	}
	m.Update(searchMsg{query: s.key(), hits: []SearchHit{{"limit.ts", 1, "const rateLimit = 1"}, {"limit.ts", 2, "use(rateLimit)"}}})
	out := appScreen(m)
	for _, want := range []string{"› search", "/ rateLimit▏", "aa   ab   .*   **", "2 results in 1 files · tab: ask claude instead", "⌃r replace"} {
		if !strings.Contains(out, want) {
			t.Errorf("missing %q:\n%s", want, out)
		}
	}
	m.Update(tea.KeyMsg{Type: tea.KeyCtrlR})
	for _, r := range "limitRate" {
		m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{r}})
	}
	m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	if m.st.Mode != ModeConfirm || !strings.Contains(m.st.Prompt, `Replace 2 matches of "rateLimit" in 1 files with "limitRate"?`) {
		t.Fatalf("asks first: %q", m.st.Prompt)
	}
	if msg := m.replaceAll("limitRate")(); msg.(appliedMsg).err != nil {
		t.Fatal(msg.(appliedMsg).err)
	}
	if data, _ := os.ReadFile(path); string(data) != "const limitRate = 1\nuse(limitRate)\nrateLimiter()\n" {
		t.Fatalf("only the matches, whole words: %q", data)
	}
}

func TestNotificationsGoWhereSettingsSay(t *testing.T) {
	m, src := appModel(t)
	rings := 0
	var notices []string
	m.ringer = func() { rings++ }
	m.notifier = func(title, text string) { notices = append(notices, text) }
	src.appSettings = sampleSettings()
	src.appSettings.NotifyNeeds, src.appSettings.Desktop = true, true
	run(m.fetchAppSettings(), m)
	run(m.bell(), m)
	if rings != 0 || len(notices) != 1 || !strings.Contains(notices[0], "claude needs you") {
		t.Fatalf("a desktop notice, no bell: %d %v", rings, notices)
	}
	src.today = sampleToday()
	run(m.fetchToday(), m) // what is already applied does not notify
	m.st.AppSettings.NotifyApplied = true
	today := sampleToday()
	today.Rows[1].JobID, today.Rows[1].State = "job-new", "applied"
	src.today = today
	_, cmd := m.Update(todayMsg{t: today})
	run(cmd, m)
	if len(notices) != 2 || notices[1] != "claude applied Add index to session store" {
		t.Fatalf("a new apply notifies: %v", notices)
	}
}

func TestThePreviewPageKeepsItsKeysAtTheFoot(t *testing.T) {
	m, src := appModel(t)
	withConversation(t, m, src)
	_ = os.WriteFile(filepath.Join(m.st.App.Repo, "README.md"), []byte("# my-repo\n"), 0o644)
	run(m.runPalette(paletteItem{Action: "file:README.md"}, ""), m)
	lines := strings.Split(appScreen(m), "\n")
	if len(lines) != 36 || !strings.Contains(lines[34], "⇥ next file") || !strings.Contains(lines[1]+lines[2], "› preview") {
		t.Fatalf("the keys on the last line:\n%s", strings.Join(lines, "\n"))
	}
}

func TestTheSplashShowsTheMarkWhileTheShellOpens(t *testing.T) {
	m, _ := appModel(t)
	clock := time.Now()
	m.now = func() time.Time { return clock }
	m.lastOK = time.Time{}
	m.SetSplash(true)
	out := appScreen(m)
	for _, want := range []string{"██ ▄▄▄▄▄", "rig", "opening my-repo"} {
		if !strings.Contains(out, want) {
			t.Errorf("missing %q:\n%s", want, out)
		}
	}
	if strings.Contains(out, "What should we work on") {
		t.Fatal("the splash covers the screen")
	}
	clock = clock.Add(2 * time.Second)
	m.Update(splashTickMsg{})
	if m.st.App.Splash != 2 {
		t.Fatalf("the server has not answered: the splash stays and pulses on: %d", m.st.App.Splash)
	}
	m.lastOK = clock
	m.Update(splashTickMsg{})
	if m.st.App.Splash != 0 {
		t.Fatal("it gives way once the server answered")
	}
	m.SetSplash(true)
	press(m, "x")
	if m.st.App.Splash != 0 || m.st.Prompt != "" {
		t.Fatal("a key skips it, and is not typed")
	}
}

func TestAJobThatEditedInPlaceShowsItsChange(t *testing.T) {
	m, src := appModel(t)
	withConversation(t, m, src)
	for i := range m.view.Jobs {
		m.view.Jobs[i].Ask = false
	}
	src.evidence = loadEvidence(t)
	src.diffs = map[string]*view.JobDiff{"job-7f2": {JobID: "job-7f2", Source: "working tree", Diff: sampleDiff}}
	run(press(m, "2"), m)
	out := appScreen(m)
	for _, want := range []string{"changed in place: the file now, against the last commit", "  16 +   rateLimit({ windowMs: 60_000, max: 5 }),"} {
		if !strings.Contains(out, want) {
			t.Fatalf("missing %q:\n%s", want, out)
		}
	}
	src.diffs["job-7f2"] = &view.JobDiff{JobID: "job-7f2", Source: "recorded"}
	src.diffs["job-7f2"].Files = append(src.diffs["job-7f2"].Files, struct {
		Path    string `json:"path"`
		Added   string `json:"added"`
		Deleted string `json:"deleted"`
	}{Path: "src/a.ts"})
	m.st.App.Diff = src.diffs["job-7f2"]
	if out := appScreen(m); !strings.Contains(out, "Rig kept only the names of the files this job changed.") {
		t.Fatalf("no diff to show says why:\n%s", out)
	}
}

func TestAFinishedHelpersThreadStaysInThePanel(t *testing.T) {
	m, src := appModel(t)
	withConversation(t, m, src)
	m.view.Jobs = append(m.view.Jobs, view.Row{ID: "job-done", Title: "Sessions store", Label: "Unverified", Worker: "opencode"})
	press(m, "tab")
	out := appScreen(m)
	for _, want := range []string{"Finished", "⏎ its thread", "opencode", "finished · not accepted yet"} {
		if !strings.Contains(out, want) {
			t.Fatalf("missing %q:\n%s", want, out)
		}
	}
	src.thread = &view.JobThread{JobID: "job-done", Worker: "opencode", Parent: "claude", Title: "Sessions store", Ending: "finished"}
	_ = json.Unmarshal([]byte(`{"entries": [{"who": "claude", "kind": "brief", "text": "Move sessions to the store."},
		{"who": "opencode", "kind": "activity", "text": "edit src/store.ts"}, {"who": "opencode", "kind": "activity", "text": "run tests"},
		{"who": "opencode", "kind": "result", "text": "Sessions live in the store; tests pass."}]}`), src.thread)
	jobs := panelJobs(m.view)
	m.st.App.PanelSel = len(jobs) - 1
	run(press(m, "enter"), m)
	if m.st.App.Thread != "job-done" {
		t.Fatalf("⏎ opens a finished helper's thread: %q", m.st.App.Thread)
	}
	out = appScreen(m)
	for _, want := range []string{"claude  → opencode · the task", "Move sessions to the store.", "opencode  working", "· edit src/store.ts",
		"· run tests", "opencode  → claude · reported", "Sessions live in the store; tests pass.", "── finished"} {
		if !strings.Contains(out, want) {
			t.Fatalf("the thread reads as a conversation, missing %q:\n%s", want, out)
		}
	}
	press(m, "tab")
	press(m, "s")
	if m.st.Mode == ModeConfirm {
		t.Fatal("a finished helper has nothing to stop")
	}
}

func TestAConversationHandedToAnotherParentSaysWhoSaidWhat(t *testing.T) {
	m, src := appModel(t)
	withConversation(t, m, src)
	src.chatView = transcript("idle", ce("user", "what is in a.txt?"), view.ChatEntry{Role: "assistant", Text: "It says one.", Parent: "claude"},
		view.ChatEntry{Role: "switch", Text: "claude → codex", Parent: "codex"}, ce("user", "rename it"),
		view.ChatEntry{Role: "assistant", Text: "Renamed to b.txt.", Parent: "codex"})
	src.chatView.Parent = "codex"
	m.chBusy = false
	run(m.fetchChat(true), m)
	m.st.ChatParent = "codex"
	out := appScreen(m)
	for _, want := range []string{"claude", "It says one.", "── codex takes over from claude ──", "codex", "Renamed to b.txt."} {
		if !strings.Contains(out, want) {
			t.Fatalf("missing %q:\n%s", want, out)
		}
	}
	m.st.ChatParent = "grok"
	if out := appScreen(m); !strings.Contains(out, "── grok takes over from codex with your next message ──") {
		t.Fatalf("a switch not sent yet says what happens next:\n%s", out)
	}
}
