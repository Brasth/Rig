package ui

import (
	"strconv"
	"strings"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/Brasth/Rig/shell/internal/view"
)

// After a turn that changed files, with the box empty: g opens the git view with just that turn's files (t shows every
// change again), and c commits those files, and only those, with a message taken from the parent's answer that you can
// change first. Nothing is staged for other files, and what the turn did not touch stays out of the commit.

// lastMark is the latest turn of the open conversation that changed files.
func lastMark(st State) *view.ChatMark {
	if st.Chat == nil || len(st.Chat.Marks) == 0 {
		return nil
	}
	return &st.Chat.Marks[len(st.Chat.Marks)-1]
}

func markPaths(mk *view.ChatMark) []string {
	if mk == nil {
		return nil
	}
	out := make([]string, 0, len(mk.Files))
	for _, f := range mk.Files {
		out = append(out, f.Path)
	}
	return out
}

// openTurnChanges is g: the git view, scoped to the last turn's files.
func (m *Model) openTurnChanges() tea.Cmd {
	a := m.st.App
	mk := lastMark(m.st)
	if mk == nil {
		m.st.Message = "no turn has changed files yet"
		return nil
	}
	a.GitScope, a.Tab = markPaths(mk), 3
	m.st.Message = "this turn's files · t shows every change · c commits just these"
	return tea.Batch(m.openGit(), m.loadFiles())
}

// inScope is whether a path is one of the files the view is scoped to (every path when there is no scope).
func inScope(a *AppState, path string) bool {
	if len(a.GitScope) == 0 {
		return true
	}
	path = strings.TrimSuffix(path, "/")
	for _, p := range a.GitScope {
		if p == path || strings.HasPrefix(p, path+"/") {
			return true
		}
	}
	return false
}

// commitMessage is a first line for the commit, from what the parent said last: its first sentence, plain, cut to 72.
func commitMessage(st State) string {
	text := ""
	if st.Chat != nil {
		for i := len(st.Chat.Entries) - 1; i >= 0; i-- {
			if e := st.Chat.Entries[i]; e.Role == "assistant" && strings.TrimSpace(e.Text) != "" {
				text = e.Text
				break
			}
			if st.Chat.Entries[i].Role == "user" {
				break
			}
		}
	}
	if text == "" {
		if mk := lastMark(st); mk != nil {
			text = mk.Title
		}
	}
	for _, raw := range strings.Split(text, "\n") {
		line := strings.TrimSpace(plainMarkdown(sanitize(raw)))
		line = strings.TrimLeft(line, "#-*• ")
		if line == "" {
			continue
		}
		if i := strings.IndexAny(line, ".:;"); i > 20 {
			line = line[:i]
		}
		if r := []rune(line); len(r) > 72 {
			line = strings.TrimRight(string(r[:71]), " ,") + "…"
		}
		return line
	}
	return ""
}

// askTurnCommit is c: the message, prefilled, for a commit of just the last turn's files.
func (m *Model) askTurnCommit() tea.Cmd {
	a := m.st.App
	mk := lastMark(m.st)
	if mk == nil {
		m.st.Message = "no turn has changed files yet"
		return nil
	}
	a.GitScope, a.Tab = markPaths(mk), 3
	a.CodeAsk = &codeAsk{kind: "commit-turn", label: "commit " + itoa(len(a.GitScope)) + " file" + plural(len(a.GitScope)), text: commitMessage(m.st)}
	a.TreeFocus = true
	return tea.Batch(m.openGit(), m.loadFiles())
}

// commitTurn commits the scoped files and only those, whatever is staged, with the message.
func (m *Model) commitTurn(message string) tea.Cmd {
	a := m.st.App
	message = strings.TrimSpace(message)
	paths := append([]string(nil), a.GitScope...)
	if message == "" || len(paths) == 0 {
		m.st.Message = "a commit needs a message"
		return nil
	}
	return m.gitAct("committed "+itoa(len(paths))+" file"+plural(len(paths))+": "+message, func(repo string) error {
		if _, err := gitOut(repo, append([]string{"add", "-A", "--"}, paths...)...); err != nil {
			return err // new files must be known to git before a pathspec commit takes them
		}
		_, err := gitOut(repo, append([]string{"commit", "-q", "-m", message, "--"}, paths...)...)
		return err
	})
}

func itoa(n int) string { return strconv.Itoa(n) }
