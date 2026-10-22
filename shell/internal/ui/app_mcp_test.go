package ui

import (
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
)

func TestMcpLoginRunsCodexsOwnSignInInTheTerminalAndTellsYouWhenItIsDone(t *testing.T) {
	m, src := appModel(t)
	withConversation(t, m, src)
	var ran [][]string
	m.exec = func(argv []string, done func(error) tea.Msg) tea.Cmd {
		ran = append(ran, argv)
		return func() tea.Msg { return done(nil) }
	}
	m.parentName, m.st.ChatParent = "codex", "codex"
	follow(m, m.appSend("/mcp login asana"), 0)
	if len(ran) != 1 || strings.Join(ran[0], " ") != "codex mcp login asana" {
		t.Fatalf("it runs codex's documented sign-in in the terminal: %v", ran)
	}
	if !strings.Contains(m.st.Message, "back from the sign-in to asana: send your message again") {
		t.Fatalf("it says it is back: %q", m.st.Message)
	}
	if len(src.chatSent) != 0 {
		t.Fatalf("the parent is never asked to log in itself: %v", src.chatSent)
	}
	for _, bad := range []string{"/mcp login", "/mcp login ../x", "/mcp login a b; rm -rf /", "/mcp login $(x)"} {
		ran = nil
		follow(m, m.appSend(bad), 0)
		if len(ran) != 0 {
			t.Fatalf("%q must not run anything: %v", bad, ran)
		}
	}
	if !strings.Contains(m.st.Message, "not an MCP server name") && !strings.Contains(m.st.Message, "usage") {
		t.Fatalf("a bad name says so: %q", m.st.Message)
	}
	// only codex's sign-in is known: another parent is told where to do it
	m.parentName, m.st.ChatParent = "claude", "claude"
	ran = nil
	follow(m, m.appSend("/mcp login asana"), 0)
	if len(ran) != 0 || !strings.Contains(m.st.Message, "claude's own screen") {
		t.Fatalf("claude is pointed to its own screen: %v %q", ran, m.st.Message)
	}
	// /mcp alone is still answered by the server (the list)
	src.chatSent = nil
	src.chatStarted = nil
	m.appSend("/mcp")
	if m.st.Message == "" && len(ran) != 0 {
		t.Fatal("/mcp alone is not a login")
	}
}

func TestANotAuthorizedAnswerFromATeamsToolPointsToSigningInYourself(t *testing.T) {
	// the lines of the screenshot: the connected app's tool answered "Not Authorized"
	for tool, want := range map[string]string{"codex_apps.asana.get_task": "asana", "github.create_issue": "github", "asana.get_task": "asana", "ran /bin/zsh": "", "Bash": ""} {
		if got := mcpServerOf(tool); got != want {
			t.Errorf("%q names server %q, not %q", tool, want, got)
		}
	}
	m, src := appModel(t)
	withConversation(t, m, src)
	m.parentName, m.st.ChatParent = "codex", "codex"
	src.chatView = transcript("idle", ce("user", "please try to get asana tickets"),
		ce("tool", "codex_apps.asana.get_task"), ce("result", "Asana MCP still returns “Not Authorized.”"),
		ce("assistant", "Please reconnect the Asana app."))
	run(m.fetchChat(true), m)
	out := appScreen(m)
	for _, want := range []string{"→ sign in yourself: /mcp login asana", "reconnect it in Codex's app settings"} {
		if !strings.Contains(out, want) {
			t.Fatalf("missing %q:\n%s", want, out)
		}
	}
	// no hint for an ordinary result, a tool without a server, or another parent
	for name, entries := range map[string][]string{"fine": {"codex_apps.asana.get_task", "task 12: ok"}, "no server": {"Bash", "401 Unauthorized"}} {
		src.chatView = transcript("idle", ce("user", "x"), ce("tool", entries[0]), ce("result", entries[1]))
		m.chBusy = false
		run(m.fetchChat(true), m)
		if strings.Contains(appScreen(m), "sign in yourself") {
			t.Errorf("%s: no hint", name)
		}
	}
	m.parentName, m.st.ChatParent = "claude", "claude"
	src.chatView = transcript("idle", ce("user", "x"), ce("tool", "asana.get_task"), ce("result", "Not Authorized"))
	m.chBusy = false
	run(m.fetchChat(true), m)
	if strings.Contains(appScreen(m), "sign in yourself") {
		t.Error("only codex's sign-in is known: no hint for another parent")
	}
}
