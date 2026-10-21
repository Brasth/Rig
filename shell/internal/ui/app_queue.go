package ui

import (
	"fmt"
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"
)

// Typing while the parent works: a message sent in the middle of a turn waits behind it (it is shown under the
// spinner) and goes when the turn ends well, one at a time. ↑ in an empty box takes the last one back. If the turn
// fails or is stopped, nothing is sent on its own: the queued messages come back to the box.

// working is whether the parent is in the middle of a turn that is not waiting on you.
func (m *Model) working() bool {
	c := m.st.Chat
	return m.appOn() && m.chatID != "" && c != nil && c.Session == m.chatID && c.State == "running" && c.Ask == nil
}

// drainOutbox sends the first queued message once the turn that was running ended well.
func (m *Model) drainOutbox() tea.Cmd {
	a := m.st.App
	c := m.st.Chat
	if len(a.Outbox) == 0 || a.Sending || c == nil || c.State == "running" {
		return nil
	}
	if c.State != "idle" || c.Note == "cancelled" {
		m.takeBackOutbox("the turn did not finish")
		return nil
	}
	text := a.Outbox[0]
	a.Outbox = a.Outbox[1:]
	a.Sending = true
	m.st.Message = ""
	return m.dispatch(text)
}

// takeBackOutbox puts the queued messages in the box, for a turn that failed or was stopped: nothing is sent behind it.
func (m *Model) takeBackOutbox(why string) {
	a := m.st.App
	if len(a.Outbox) == 0 {
		return
	}
	text := strings.Join(a.Outbox, "\n\n")
	if m.st.Prompt != "" {
		text = m.st.Prompt + "\n\n" + text
	}
	n := len(a.Outbox)
	a.Outbox = nil
	m.setPrompt(text, len([]rune(text)))
	word := "message is"
	if n > 1 {
		word = fmt.Sprintf("%d messages are", n)
		if n == 2 {
			word = "2 messages are"
		}
	}
	m.st.Message = why + ": your queued " + word + " back in the box"
}

// queuedRows are the messages waiting behind the running turn, under the spinner.
func queuedRows(st State, w int) []string {
	a := st.App
	if a == nil || len(a.Outbox) == 0 {
		return nil
	}
	var out []string
	for i, text := range a.Outbox {
		lead := "  ↳ next  "
		if i > 0 {
			lead = "  ↳ then  "
		}
		line := st.faint(lead) + st.dim(fit(clean(text), max(8, w-lipglossWidth(lead)-2)))
		out = append(out, st.zone(fmt.Sprintf("row:queued:%d", i), line))
	}
	return append(out, st.faint("    ↑ takes the last one back to edit"))
}

// cycleMode is the permission-mode key: ask, then auto-edit (the parent edits files without asking and still asks about
// commands), then plan, then ask again. It changes between turns; the status line names the mode.
func (m *Model) cycleMode() tea.Cmd {
	if m.working() {
		m.st.Message = "the mode changes between turns: wait for the answer"
		return nil
	}
	m.st.Message = "changing the mode…"
	src, session, parent := m.src, m.chatID, m.parentName
	return func() tea.Msg { // not through the box: a draft stays as it is
		started, err := src.ChatSend("/mode next", session, parent, 10*time.Second)
		return chatSentMsg{started: started, err: err, sent: "/mode next", quiet: true}
	}
}
