package ui

import (
	"strings"

	tea "github.com/charmbracelet/bubbletea"
)

// /help (and ⌃k › what can I do) is organised by what you want to do, not by key: each row says it in plain words
// and shows how; ⏎ does it or puts the command in the box. "every key" opens the full list that ? shows.

type helpRow struct {
	Section, Label, How, Action string
}

var helpRows = []helpRow{
	{"Keep going", "Ask about a file", "type @ and pick it (auth.py:10-40 for just those lines)", "attach-file"},
	{"Keep going", "Say something while it works", "⏎ queues it · ⏎ again sends it now", ""},
	{"Keep going", "Start something new", "/new", "new"},
	{"Keep going", "Continue an earlier conversation", "/resume", "resume"},
	{"Keep going", "Try a different approach from a point", "/fork", "fork"},
	{"Check and keep", "See what it just changed", "g after a turn · the changes tab", "review-turn"},
	{"Check and keep", "Save it in git", "c after a turn", "commit-turn"},
	{"Check and keep", "Put the files back as they were", "u after a turn · /rewind", "rewind"},
	{"Check and keep", "Plan first, change nothing", "/plan", "slash:/plan"},
	{"Check and keep", "Let it edit without asking each time", "⇧Tab goes round ask, auto-edit and plan", "mode-next"},
	{"Make it yours", "Switch assistant", "/parent", "parent"},
	{"Make it yours", "Pick the model or how hard it thinks", "/model · /effort", "slash:/model"},
	{"Make it yours", "Let it hand work to other assistants", "/helpers", "slash:/helpers"},
	{"Make it yours", "Keep a note it reads in every conversation", "/remember", "slash:/remember"},
	{"Something wrong?", "What is wrong with this project", "/doctor · it fixes what it safely can", "doctor"},
	{"Something wrong?", "What is running right now", "/running", "running"},
	{"Something wrong?", "Stop it", "esc, or /stop", "stop-turn"},
	{"Something wrong?", "Check that each assistant works", "/selftest", "selftest"},
	{"All of it", "Every key", "?", "help-keys"},
}

// helpItems are the palette's rows.
func helpItems(st State, q string) []paletteItem {
	var out []paletteItem
	for _, r := range helpRows {
		if q != "" && !strings.Contains(strings.ToLower(r.Label+" "+r.How), q) {
			continue
		}
		out = append(out, paletteItem{r.Section, r.Label, r.How, r.Action})
	}
	if len(out) == 0 {
		out = append(out, paletteItem{"help", "nothing matches", "try another word, or ? for every key", ""})
	}
	return out
}

func (m *Model) openHelp() tea.Cmd {
	a := m.st.App
	a.Settings, a.Palette = nil, &Palette{Help: true}
	return nil
}

// helpAction does one help row; the ones that name a command put it in the box.
func (m *Model) helpAction(action string) tea.Cmd {
	a := m.st.App
	switch action {
	case "attach-file":
		a.Palette = &Palette{Attach: true}
		return m.loadFiles()
	case "review-turn":
		if lastMark(m.st) == nil {
			m.st.Message = "nothing has changed yet: after a turn that changes files, g shows it"
			return nil
		}
		return m.openTurnChanges()
	case "commit-turn":
		if lastMark(m.st) == nil {
			m.st.Message = "nothing has changed yet: after a turn that changes files, c saves it in git"
			return nil
		}
		return m.askTurnCommit()
	case "mode-next":
		return m.cycleMode()
	case "stop-turn":
		if m.chatID == "" || m.st.Chat == nil || m.st.Chat.State != "running" {
			m.st.Message = "it is not working on anything"
			return nil
		}
		return m.stopRunning("running:turn")
	case "help-keys":
		a.Help = true
	}
	return nil
}
