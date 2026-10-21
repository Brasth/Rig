package ui

import (
	"path/filepath"
	"strconv"
	"strings"

	tea "github.com/charmbracelet/bubbletea"
)

// A commit's diff and a file's history, as an IDE's git log: ⏎ on one of the git view's recent commits shows what it
// changed, file by file, in place of the file; l on the open file lists the commits that touched it (git log
// --follow), and ⏎ on one shows that commit's change to the file. esc closes the diff.

type gitLogMsg struct {
	file    string
	commits []gitCommit
}

// fetchCommitDiff reads one commit's diff: all of it, or its change to one file.
func (m *Model) fetchCommitDiff(hash, file string) tea.Cmd {
	repo := m.st.App.Repo
	return func() tea.Msg {
		args := []string{"show", "--no-color", "--no-ext-diff", "--format=%h%x00%s%x00%an%x00%at", hash}
		if file != "" {
			args = append(args, "--follow", "--", file)
		}
		out, err := gitOut(repo, args...)
		if err != nil {
			return gitDoneMsg{note: "could not show " + hash, err: err}
		}
		view := &gitDiffView{path: file, commit: hash}
		head, rest, _ := strings.Cut(out, "\n")
		if f := strings.Split(head, "\x00"); len(f) == 4 {
			at, _ := strconv.ParseFloat(f[3], 64)
			view.meta = gitCommit{f[0], f[1], f[2], at}
		}
		view.raw = rest
		view.files = ParseDiff(rest)
		if len(view.files) > 0 {
			view.file = view.files[0]
		}
		return gitDiffMsg{view}
	}
}

// fetchLog reads the commits that touched the open file, for l.
func (m *Model) fetchFileLog() tea.Cmd {
	a := m.st.App
	if a.File == "" {
		return nil
	}
	repo, file := a.Repo, a.File
	return func() tea.Msg {
		out, err := gitOut(repo, "log", "--follow", "-40", "--format=%h%x00%s%x00%an%x00%at", "--", file)
		msg := gitLogMsg{file: file}
		if err == nil {
			for _, l := range strings.Split(strings.TrimSpace(out), "\n") {
				if f := strings.Split(l, "\x00"); len(f) == 4 {
					at, _ := strconv.ParseFloat(f[3], 64)
					msg.commits = append(msg.commits, gitCommit{f[0], f[1], f[2], at})
				}
			}
		}
		return msg
	}
}

// openLog lists the open file's history in the palette once it is read.
func (m *Model) openLog() tea.Cmd {
	a := m.st.App
	if a.File == "" {
		return nil
	}
	a.FileLog, a.FileLogFor = nil, a.File
	a.Palette = &Palette{Log: true}
	return m.fetchFileLog()
}

// logItems are the palette's rows in the file's history: each commit, newest first.
func logItems(st State, q string) []paletteItem {
	a := st.App
	name := filepath.Base(a.File)
	if a.FileLogFor != a.File || a.FileLog == nil {
		return []paletteItem{{"history of " + name, "reading git log…", "", ""}}
	}
	var out []paletteItem
	for _, c := range a.FileLog {
		if q != "" && !strings.Contains(strings.ToLower(c.subject+" "+c.author+" "+c.hash), q) {
			continue
		}
		out = append(out, paletteItem{"history of " + name, clean(c.subject), c.hash + " · " + c.author + ", " + ago(st.Now, c.at), "commit:" + c.hash + ":" + a.File})
		if len(out) == 12 {
			break
		}
	}
	if len(out) == 0 {
		out = append(out, paletteItem{"history of " + name, "no commits", "the file is not committed yet, or nothing matches", ""})
	}
	return out
}

// openCommit shows a commit's diff: the "commit:HASH[:file]" palette action.
func (m *Model) openCommit(ref string) tea.Cmd {
	hash, file, _ := strings.Cut(ref, ":")
	m.st.App.TreeFocus = false
	return m.fetchCommitDiff(hash, file)
}
