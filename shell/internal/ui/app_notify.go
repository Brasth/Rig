package ui

import (
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/Brasth/Rig/shell/internal/view"
)

// longTurn is how long a turn runs before its end is worth a notice: shorter ones end while you are looking.
const longTurn = 20 * time.Second

// afterTurn is what happens when a turn that was running ends: a notice for a long one (Settings › Notifications),
// and, after /compact, the move to the conversation that carries its summary.
func (m *Model) afterTurn(prev, cur *view.ChatTranscript) tea.Cmd {
	if prev == nil || cur == nil || prev.Session != cur.Session || prev.State != "running" || cur.State == "running" {
		return nil
	}
	var cmds []tea.Cmd
	if cur.State == "idle" && cur.Successor != "" {
		cmds = append(cmds, m.followSuccessor(cur.Successor))
	}
	if cur.Note != "cancelled" {
		cmds = append(cmds, m.turnNotice(cur))
	}
	return tea.Batch(cmds...)
}

// turnNotice tells you a turn that took a while ended, or failed, the ways Settings › Notifications keeps on.
func (m *Model) turnNotice(cur *view.ChatTranscript) tea.Cmd {
	s := m.st.AppSettings
	if s == nil || !s.NotifyDone {
		return nil
	}
	var started time.Time
	for i := len(cur.Entries) - 1; i >= 0; i-- {
		if e := cur.Entries[i]; e.Role == "user" {
			if at, err := time.Parse(time.RFC3339, e.At); err == nil {
				started = at
			}
			break
		}
	}
	if started.IsZero() || m.now().Sub(started) < longTurn {
		return nil
	}
	parent := cur.Parent
	if parent == "" {
		parent = appParent(m.st)
	}
	if cur.State == "failed" {
		return m.notify(parent + "'s turn failed after " + elapsed(m.now().Sub(started)))
	}
	said := ""
	for i := len(cur.Entries) - 1; i >= 0 && said == ""; i-- {
		if e := cur.Entries[i]; e.Role == "assistant" {
			said = firstLine(e.Text)
		} else if e.Role == "user" {
			break
		}
	}
	text := parent + " finished after " + elapsed(m.now().Sub(started))
	if said = strings.TrimSpace(said); said != "" {
		text += ": " + strings.TrimRight(fit(clean(said), 80), " ")
	}
	return m.notify(text)
}

// followSuccessor moves to the conversation /compact started from the summary, which you were just watching it write.
func (m *Model) followSuccessor(id string) tea.Cmd {
	a := m.st.App
	m.chatID, m.chatAdopted = id, true
	m.st.Chat, m.st.ChatUp = nil, 0
	a.chatKey, a.SeenChat, a.NewChat = "", 0, false
	m.chLast = time.Time{}
	m.st.Message = "compacted: continuing in a fresh conversation with the summary (the old one is in /resume)"
	return tea.Batch(m.fetchChat(true), m.fetchChatStatus())
}
