package ui

import (
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/Brasth/Rig/shell/internal/view"
)

// What is running: the parent's tool call in progress (its line in the conversation carries ⟳ and how long it has
// taken), the helpers at work, and the terminal split. /running (or ⌃k › what is running) lists them: ⏎ goes to one,
// x stops the parent's turn or a helper.

// runningTool is the parent's tool call that has no result yet, while its turn runs: the entry and when it began.
func runningTool(st State) (view.ChatEntry, time.Time, bool) {
	c := st.Chat
	if c == nil || c.State != "running" {
		return view.ChatEntry{}, time.Time{}, false
	}
	for i := len(c.Entries) - 1; i >= 0; i-- {
		e := c.Entries[i]
		switch e.Role {
		case "tool":
			at, err := time.Parse(time.RFC3339, e.At)
			if err != nil {
				at = time.Time{}
			}
			return e, at, true
		case "result", "result-error", "assistant", "user", "error":
			return view.ChatEntry{}, time.Time{}, false
		}
	}
	return view.ChatEntry{}, time.Time{}, false
}

// toolProgress is the suffix of a tool line still running: a spinner and how long it has taken.
func toolProgress(st State, e view.ChatEntry) string {
	running, at, ok := runningTool(st)
	if !ok || running.At != e.At || running.Text != e.Text {
		return ""
	}
	frame := spinFrames[0]
	if !st.Now.IsZero() {
		frame = spinFrames[(st.Now.UnixMilli()/120)%int64(len(spinFrames))]
	}
	out := " " + string(frame)
	if !at.IsZero() && !st.Now.IsZero() && st.Now.After(at) {
		out += " " + elapsed(st.Now.Sub(at))
	}
	return out
}

// runningItems are the palette's rows: what is running now.
func runningItems(st State, q string) []paletteItem {
	var out []paletteItem
	if e, at, ok := runningTool(st); ok {
		name, detail, _ := strings.Cut(clean(e.Text), " ")
		d := "the parent's tool call"
		if !at.IsZero() && !st.Now.IsZero() && st.Now.After(at) {
			d += " · " + elapsed(st.Now.Sub(at))
		}
		out = append(out, paletteItem{"parent", name + " " + fit(detail, 50), d + " · x stops the turn", "running:turn"})
	} else if st.Chat != nil && st.Chat.State == "running" {
		out = append(out, paletteItem{"parent", appParent(st) + " is thinking", "x stops the turn", "running:turn"})
	}
	if st.App.Palette != nil {
		for _, r := range st.App.Palette.Jobs {
			if r.Label == "Running" || r.Label == "Needs input" || r.Ask {
				d := strings.ToLower(r.Label)
				if r.Second != "" {
					d += " · " + clean(r.Second)
				}
				out = append(out, paletteItem{"helpers", clean(r.Title), fit(d, 60) + " · x stops it", "running:job:" + r.ID})
			}
		}
	}
	if st.App.Term {
		d := "open at the foot of the screen · ⏎ gives it the keys"
		out = append(out, paletteItem{"terminal", "shell split", d, "running:term"})
	}
	if q != "" {
		kept := out[:0]
		for _, it := range out {
			if strings.Contains(strings.ToLower(it.Label+" "+it.Detail), q) {
				kept = append(kept, it)
			}
		}
		out = kept
	}
	if len(out) == 0 {
		out = append(out, paletteItem{"running", "nothing is running", "the parent is idle, no helper works and the terminal is closed", ""})
	}
	return out
}

func (m *Model) openRunning() tea.Cmd {
	a := m.st.App
	a.Settings, a.Palette = nil, &Palette{Running: true, Jobs: activeJobs(m.view)}
	m.chLast = time.Time{}
	return tea.Batch(m.fetchChat(true), m.fetchDock(true))
}

// goRunning is ⏎ on a row: the conversation for the parent, the job for a helper, the keys for the terminal.
func (m *Model) goRunning(action string) tea.Cmd {
	a := m.st.App
	switch {
	case action == "running:turn":
		a.Tab, a.Thread = 1, ""
	case strings.HasPrefix(action, "running:job:"):
		m.selectJob(strings.TrimPrefix(action, "running:job:"))
		a.Tab = 2
		return tea.Batch(m.fetchDock(true), m.fetchDiff(true))
	case action == "running:term":
		if !a.Term {
			return m.toggleTerm()
		}
		a.TermFocus = true
	}
	return nil
}

// stopRunning is x on a row: the parent's turn, or a helper.
func (m *Model) stopRunning(action string) tea.Cmd {
	switch {
	case action == "running:turn":
		if m.chatID == "" {
			return nil
		}
		m.st.Message = "stopping " + m.chatID
		src, id := m.src, m.chatID
		return func() tea.Msg { return chatCancelMsg{err: src.ChatCancel(id, callTimeout)} }
	case strings.HasPrefix(action, "running:job:"):
		id := strings.TrimPrefix(action, "running:job:")
		m.st.Message = "stopping " + id
		return m.act("job.cancel", map[string]any{"id": id}, false)
	}
	return nil
}
