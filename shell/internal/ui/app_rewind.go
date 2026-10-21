package ui

import (
	"fmt"
	"strconv"
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/Brasth/Rig/shell/internal/view"
)

// Rewind: /rewind (or ⌃k › rewind files) lists the turns that changed files, the latest first. ⏎ on one shows what
// putting it back changes: every file that turn and the later ones touched, or one file at a time. What was there is
// kept, so "undo the last rewind" puts it back; the parent is told once, in its next message, that the files moved.

// rewindItems are the palette's rows: the turns, or, once one is chosen, what putting it back would do.
func rewindItems(st State, q string) []paletteItem {
	p := st.App.Palette
	if p.RewindTurn > 0 {
		return rewindPlanItems(st, q)
	}
	var out []paletteItem
	if st.Chat != nil {
		if st.Chat.CanUndo && (q == "" || strings.Contains("undo the last rewind", q)) {
			out = append(out, paletteItem{"turns", "undo the last rewind", "put back what it took away", "rewind-undo"})
		}
		for i := len(st.Chat.Marks) - 1; i >= 0; i-- {
			mk := st.Chat.Marks[i]
			title := clean(mk.Title)
			if title == "" {
				title = "(no message)"
			}
			if q != "" && !strings.Contains(strings.ToLower(title+" "+markFiles(mk)), q) {
				continue
			}
			label := "turn " + strconv.Itoa(mk.Turn) + " · " + title
			detail := fmt.Sprintf("%d file%s: %s", len(mk.Files), plural(len(mk.Files)), strings.TrimRight(fit(markFiles(mk), 60), " "))
			out = append(out, paletteItem{"turns that changed files", label, detail, "rewind-turn:" + strconv.Itoa(mk.Turn)})
		}
	}
	if len(out) == 0 {
		detail := "no turn matches"
		if q == "" {
			detail = "no turn has changed files in this conversation yet"
		}
		out = append(out, paletteItem{"turns", "nothing to rewind", detail, ""})
	}
	return out
}

func plural(n int) string {
	if n == 1 {
		return ""
	}
	return "s"
}

// markFiles names a turn's files on one line.
func markFiles(mk view.ChatMark) string {
	names := make([]string, 0, len(mk.Files))
	for _, f := range mk.Files {
		names = append(names, f.Path)
	}
	return strings.Join(names, ", ")
}

// rewindPlanItems are the second step: all of it, then one file at a time.
func rewindPlanItems(st State, q string) []paletteItem {
	p := st.App.Palette
	if p.RewindPlan == nil {
		return []paletteItem{{"turn " + strconv.Itoa(p.RewindTurn), "looking at the files…", "", ""}}
	}
	plan := p.RewindPlan
	section := "before turn " + strconv.Itoa(plan.Turn)
	edited := map[string]bool{}
	for _, e := range plan.Edited {
		edited[e] = true
	}
	all := fmt.Sprintf("put back all %d file%s", len(plan.Paths), plural(len(plan.Paths)))
	detail := "what this turn and the later ones changed"
	if n := len(plan.Edited); n > 0 {
		detail = fmt.Sprintf("%d edited since will be overwritten (undo brings them back)", n)
	}
	out := []paletteItem{{section, all, detail, "rewind-do:" + strconv.Itoa(plan.Turn)}}
	for _, path := range plan.Paths {
		if q != "" && !strings.Contains(strings.ToLower(path), q) {
			continue
		}
		d := "put just this file back"
		if edited[path] {
			d = "edited since: its changes are kept for undo"
		}
		out = append(out, paletteItem{section, path, d, "rewind-file:" + strconv.Itoa(plan.Turn) + ":" + path})
	}
	return out
}

// openRewind lists the turns, with the conversation read again so a turn that just ended is in it.
func (m *Model) openRewind() tea.Cmd {
	a := m.st.App
	a.Settings, a.Palette = nil, &Palette{Rewind: true}
	m.chLast = time.Time{}
	return m.fetchChat(true)
}

type rewindPlanMsg struct {
	turn int
	plan *view.ChatRewound
	err  error
}

type rewoundMsg struct {
	plan *view.ChatRewound
	path string
	undo bool
	err  error
}

// planRewind asks what putting a turn back would change, without changing it.
func (m *Model) planRewind(turn int) tea.Cmd {
	m.st.App.Palette = &Palette{Rewind: true, RewindTurn: turn} // ⏎ closed the list: this is the second step's own
	src, id := m.src, m.chatID
	return func() tea.Msg {
		plan, err := src.ChatRewind(id, turn, "", true, false, callTimeout)
		return rewindPlanMsg{turn: turn, plan: plan, err: err}
	}
}

func (m *Model) onRewindPlan(msg rewindPlanMsg) tea.Cmd {
	p := m.st.App.Palette
	if p == nil || !p.Rewind || p.RewindTurn != msg.turn {
		return nil
	}
	if msg.err != nil {
		m.st.App.Palette = nil
		m.st.Message = "rewind: " + msg.err.Error()
		return nil
	}
	p.RewindPlan = msg.plan
	return nil
}

// doRewind puts a turn (or one file of it) back, or takes the last rewind back.
func (m *Model) doRewind(turn int, path string, undo bool) tea.Cmd {
	m.st.App.Palette = nil
	src, id := m.src, m.chatID
	if id == "" {
		m.st.Message = "rewind: there is no conversation yet"
		return nil
	}
	return func() tea.Msg {
		plan, err := src.ChatRewind(id, turn, path, false, undo, callTimeout)
		return rewoundMsg{plan: plan, path: path, undo: undo, err: err}
	}
}

func (m *Model) onRewound(msg rewoundMsg) tea.Cmd {
	if msg.err != nil {
		m.st.Message = "rewind: " + msg.err.Error()
		return nil
	}
	n := 0
	if msg.plan != nil {
		n = len(msg.plan.Paths)
	}
	switch {
	case msg.undo:
		m.st.Message = fmt.Sprintf("undid the rewind: %d file%s back", n, plural(n))
	case msg.path != "":
		m.st.Message = "put back " + msg.path
	default:
		m.st.Message = fmt.Sprintf("put back %d file%s as before the turn", n, plural(n))
	}
	a := m.st.App
	cmds := []tea.Cmd{m.fetchChat(true), m.loadFiles(), m.fetchChanged()}
	if a.File != "" && !a.Dirty && !a.Editing {
		cmds = append(cmds, m.openFile(a.File)) // the open file shows what is on disk now
	}
	m.chLast = time.Time{}
	return tea.Batch(cmds...)
}
