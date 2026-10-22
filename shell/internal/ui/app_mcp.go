package ui

import (
	"regexp"
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"
)

// Signing in to an MCP server is something you do, in your own terminal, where the browser opens and the sign-in's
// local callback can finish. When the parent runs `codex mcp login` itself inside a turn, the browser cannot open and
// the callback listener ends with the turn ("the earlier login process is no longer active"). So /mcp login NAME runs
// codex's own `codex mcp login NAME` here, and a "not authorized" answer from a tool points to it.

var mcpServerName = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$`)

type mcpSignedInMsg struct {
	name string
	err  error
}

// mcpLogin runs the parent's sign-in for one MCP server in the terminal. Only codex's command is known (documented as
// `codex mcp login <server-name>`); for the others the parent's own screen is where it is done.
func (m *Model) mcpLogin(name string) tea.Cmd {
	parent := appParent(m.st)
	switch {
	case name == "":
		m.st.Message = "usage: /mcp login NAME · /mcp lists the names"
		return nil
	case !mcpServerName.MatchString(name):
		m.st.Message = "that is not an MCP server name: /mcp lists them"
		return nil
	case parent != "codex":
		m.st.Message = "sign in to " + name + " from " + parent + "'s own screen (its /mcp): Rig can run only codex's sign-in"
		return nil
	}
	m.st.Message = "signing in to " + name + ": it opens your browser…"
	return m.exec([]string{"codex", "mcp", "login", name}, func(err error) tea.Msg { return mcpSignedInMsg{name: name, err: err} })
}

func (m *Model) onMcpSignedIn(msg mcpSignedInMsg) tea.Cmd {
	if msg.err != nil {
		m.st.Message = "the sign-in to " + msg.name + " did not finish: " + clean(msg.err.Error()) + " · /mcp login " + msg.name + " tries again"
	} else {
		m.st.Message = "back from the sign-in to " + msg.name + ": send your message again"
	}
	m.csLast = time.Time{}
	return tea.Batch(m.fetchChatStatus(), m.fetchChat(true))
}

var (
	notAuthorized = regexp.MustCompile(`(?i)not authori[sz]ed|unauthori[sz]ed|authori[sz]ation required|waiting for authori[sz]ation|login is waiting`)
	toolServer    = regexp.MustCompile(`^([A-Za-z0-9_-]+)\.([A-Za-z0-9_-]+)(?:\.[A-Za-z0-9_-]+)?$`)
)

// mcpServerOf is the MCP server a tool line names: "codex_apps.asana.get_task" is asana's, "github.create_issue" github's.
func mcpServerOf(tool string) string {
	first, _, _ := strings.Cut(strings.TrimSpace(tool), " ")
	m := toolServer.FindStringSubmatch(first)
	if m == nil {
		return ""
	}
	if m[1] == "codex_apps" && strings.Count(first, ".") >= 2 {
		return m[2]
	}
	return m[1]
}

// signInHint is the line under a tool result that says "not authorized": who to sign in to, yourself.
func signInHint(st State, tool, result string) string {
	if appParent(st) != "codex" || !notAuthorized.MatchString(result) {
		return ""
	}
	name := mcpServerOf(tool)
	if name == "" {
		return ""
	}
	if first, _, _ := strings.Cut(strings.TrimSpace(tool), " "); strings.HasPrefix(first, "codex_apps.") {
		// a connected app: its sign-in lives in ChatGPT, not in `codex mcp login`
		return "reconnect " + name + " in ChatGPT: Settings → Apps, with the account that can open this item. Still the same? /logs writes a report to send."
	}
	return "sign in yourself: /mcp login " + name + " (opens your browser here). If it was a connected app, reconnect it in Codex's app settings."
}
