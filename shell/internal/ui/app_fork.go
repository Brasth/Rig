package ui

import (
	"strconv"
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/Brasth/Rig/shell/internal/view"
)

// Fork: /fork (or ⌃k › fork the conversation) lists the turns of this conversation, the latest first; ⏎ starts a new
// conversation that begins as a copy of this one up to that turn, with the same parent, and opens it. The two go their
// own ways from the same point, and both stay in /resume.

// forkItems are the palette's rows: each message you sent, which a fork can start after.
func forkItems(st State, q string) []paletteItem {
	var out []paletteItem
	if st.Chat != nil {
		var users []string
		for _, e := range st.Chat.Entries {
			if e.Role == "user" {
				users = append(users, e.Text)
			}
		}
		for i := len(users) - 1; i >= 0; i-- {
			n := st.Chat.Turns - (len(users) - 1 - i) // the turns are counted from the end: older ones may be out of view
			title := clean(users[i])
			if n < 1 || (q != "" && !strings.Contains(strings.ToLower(title), q)) {
				continue
			}
			detail := "branch after its answer"
			if i == len(users)-1 {
				detail = "the whole conversation"
			}
			out = append(out, paletteItem{"branch from", "turn " + strconv.Itoa(n) + " · " + title, detail, "fork:" + strconv.Itoa(n)})
		}
	}
	if len(out) == 0 {
		detail := "no turn matches"
		if q == "" {
			detail = "send a message first: a fork branches from a turn"
		}
		out = append(out, paletteItem{"branch from", "nothing to fork", detail, ""})
	}
	return out
}

func (m *Model) openFork() tea.Cmd {
	a := m.st.App
	a.Settings, a.Palette = nil, &Palette{Fork: true}
	m.chLast = time.Time{}
	return m.fetchChat(true)
}

type forkedMsg struct {
	fork *view.ChatForked
	err  error
}

func (m *Model) doFork(turn int) tea.Cmd {
	src, id := m.src, m.chatID
	if id == "" {
		m.st.Message = "fork: there is no conversation yet"
		return nil
	}
	return func() tea.Msg {
		f, err := src.ChatFork(id, turn, callTimeout)
		return forkedMsg{fork: f, err: err}
	}
}

func (m *Model) onForked(msg forkedMsg) tea.Cmd {
	if msg.err != nil {
		m.st.Message = "fork: " + msg.err.Error()
		return nil
	}
	if msg.fork == nil || msg.fork.Session == "" {
		return nil
	}
	m.parentName = msg.fork.Parent
	cmd := m.openSession(msg.fork.Session, msg.fork.Parent, "forked from turn "+strconv.Itoa(msg.fork.Turn))
	m.st.Message = "forked at turn " + strconv.Itoa(msg.fork.Turn) + ": this is the copy, " + msg.fork.ForkedFrom + " stays in /resume"
	return cmd
}
