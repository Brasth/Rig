package ui

import (
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
)

func TestErrorsPeopleMeetAreSaidInPlainWordsWithTheOriginalKept(t *testing.T) {
	cases := []struct{ raw, want string }{
		{"the parent exited with status 1: Invalid API key · Please run /login", "claude is not signed in"},
		{"API Error: 429 rate_limit_error", "busy, or you reached a usage limit"},
		{"Prompt is too long", "/compact keeps a short summary"},
		{"model_not_found: The model `gpt-9` does not exist", "/model lists the ones it offers"},
		{"claude is not on PATH", "claude is not installed on this computer"},
		{"getaddrinfo ENOTFOUND api.anthropic.com", "could not reach the internet"},
		{"list index out of range (IndexError at jobs.py:113 in current_thread)", "Rig hit an internal error"},
		{"launch setup failed: boom", "A helper could not start"},
		{"the parent exited with status 2", "stopped unexpectedly"},
	}
	for _, c := range cases {
		if got := plainError(c.raw, "claude"); !strings.Contains(got, c.want) {
			t.Errorf("%q: want %q, got %q", c.raw, c.want, got)
		}
	}
	if got := plainError("something nobody has seen", "claude"); got != "" {
		t.Fatalf("an unknown error is shown as it is: %q", got)
	}
	m, src := appModel(t)
	withConversation(t, m, src)
	src.chatView = transcript("failed", ce("user", "go"), ce("error", "API Error: 429 rate_limit_error: slow down"))
	run(m.fetchChat(true), m)
	out := appScreen(m)
	for _, want := range []string{"! The model is busy, or you reached a usage limit", "rate_limit_error: slow down", "/doctor shows what is wrong"} {
		if !strings.Contains(out, want) {
			t.Fatalf("missing %q:\n%s", want, out)
		}
	}
	src.chatView = transcript("failed", ce("user", "go"), ce("error", "something nobody has seen"))
	m.chBusy = false
	run(m.fetchChat(true), m)
	if out := appScreen(m); !strings.Contains(out, "! something nobody has seen") {
		t.Fatalf("unknown errors keep their own words:\n%s", out)
	}
}

func TestLoginRunsTheParentsOwnSignInAndLooksAgainWhenItIsDone(t *testing.T) {
	m, src := appModel(t)
	withConversation(t, m, src)
	var ran [][]string
	m.exec = func(argv []string, done func(error) tea.Msg) tea.Cmd {
		ran = append(ran, argv)
		return func() tea.Msg { return done(nil) }
	}
	for parent, want := range map[string]string{"claude": "claude auth login", "codex": "codex login", "opencode": "opencode auth login", "grok": "grok"} {
		m.parentName, m.st.ChatParent = parent, parent
		ran = nil
		follow(m, m.appSend("/login"), 0)
		if len(ran) != 1 || strings.Join(ran[0], " ") != want {
			t.Fatalf("%s signs in with %q, ran %v", parent, want, ran)
		}
	}
	if !strings.Contains(m.st.Message, "'s sign-in: send a message to go on") {
		t.Fatalf("it says it is back: %q", m.st.Message)
	}
	// the signed-out screen's first choice does the same
	m.parentName, m.st.ChatParent = "claude", "claude"
	ran = nil
	src.chatView = transcript("failed", ce("user", "go"), ce("error", "the parent exited with status 1: Invalid API key · Please run /login"))
	run(m.fetchChat(true), m)
	if problemScreen(m.st) != "signedout" {
		t.Fatal("the signed-out screen shows")
	}
	follow(m, press(m, "1"), 0)
	if len(ran) != 1 || strings.Join(ran[0], " ") != "claude auth login" {
		t.Fatalf("1 signs in with the real command: %v", ran)
	}
}
