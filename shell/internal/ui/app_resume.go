package ui

import (
	"strconv"
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/Brasth/Rig/shell/internal/view"
)

// Earlier conversations: /resume (or ⌃k › resume a conversation) lists this repository's conversations, the one used
// last first, each with its first message, its parent, how many turns it had and how long ago. Type to search them;
// ⏎ opens one, with its own parent, and the next message goes on from where it stopped.

// resumeItems are the palette's rows in /resume: the conversations that fit the query.
func resumeItems(st State, q string) []paletteItem {
	var out []paletteItem
	for _, s := range st.ChatSessions {
		title := clean(s.Title)
		if title == "" {
			title = "(no message yet)"
		}
		if q != "" && !strings.Contains(strings.ToLower(title+" "+s.Parent), q) {
			continue
		}
		detail := s.Parent + " · " + strconv.Itoa(s.Turns) + " turns"
		if s.Turns == 1 {
			detail = s.Parent + " · 1 turn"
		}
		if when := ago(st.Now, s.Updated); when != "" {
			detail += " · " + when + " ago"
		}
		if s.State == "running" {
			detail += " · working"
		}
		if m := st.Chat; m != nil && m.Session == s.ID {
			detail += " · open"
		}
		out = append(out, paletteItem{"conversations", title, detail, "resume:" + s.ID})
	}
	if len(out) == 0 {
		detail := "no conversation matches"
		if q == "" {
			detail = "the first message you send starts one"
		}
		out = append(out, paletteItem{"conversations", "nothing to resume", detail, ""})
	}
	return out
}

// openResume lists the conversations, asking the server for them again so a turn that just ended shows.
func (m *Model) openResume() tea.Cmd {
	a := m.st.App
	a.Settings, a.Palette = nil, &Palette{Resume: true}
	m.csLast = time.Time{}
	return m.fetchChatStatus()
}

// resumeSession opens a conversation again: it becomes the one the box talks to, with its own parent.
func (m *Model) resumeSession(id string) tea.Cmd {
	var found bool
	var parent string
	for _, s := range m.st.ChatSessions {
		if s.ID == id {
			found, parent = true, s.Parent
		}
	}
	if !found {
		return nil
	}
	a := m.st.App
	if id == m.chatID && m.st.Chat != nil {
		a.Tab, a.Thread = 1, ""
		return nil // already open
	}
	m.chatID, m.chatAdopted = id, true
	m.st.Chat, m.st.ChatUp, m.st.ChatE = nil, 0, ""
	if parent != "" {
		m.parentName, m.st.ChatParent, m.st.ChatModel, m.st.ChatEffort = parent, parent, "", "" // the conversation's own parent
	}
	a.Tab, a.Thread, a.Outbox, a.Sending = 1, "", nil, false
	a.chatKey = ""
	a.SeenChat, a.NewChat = 0, false
	m.chLast = time.Time{}
	m.st.Message = "opened: " + shortTitle(m.st.ChatSessions, id)
	return tea.Batch(m.fetchChat(true), m.fetchChatStatus())
}

// shortTitle is a conversation's first message, cut short for a note.
func shortTitle(rows []view.ChatSession, id string) string {
	for _, s := range rows {
		if s.ID == id {
			return fit(clean(s.Title), 50)
		}
	}
	return id
}
