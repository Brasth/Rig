package ui

import (
	"bytes"
	"context"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"
)

// Format document, as an IDE's: ⌥⇧f (or ⌃k › format document) runs the formatter the file's language has on this
// computer, on the text as it is in the editor, and puts its answer back as one edit (⌃z takes it back); with
// Settings › Editor › Format on save, ⌃s formats first. The formatters are the usual ones: gofmt, prettier (the
// project's own first), ruff or black, rustfmt, shfmt. Nothing is installed: a language with none says so.

type formatMsg struct {
	file string
	id   string // the version formatted (linesID), so an edit meanwhile is not thrown away
	text string
	err  string
	save bool
}

// formatter is the command that formats a file of this kind from stdin to stdout; nil when there is none here.
func formatter(repo, file string) []string {
	ext := strings.ToLower(filepath.Ext(file))
	have := func(name string) string {
		if p, err := exec.LookPath(name); err == nil {
			return p
		}
		return ""
	}
	switch ext {
	case ".go":
		if p := have("gofmt"); p != "" {
			return []string{p}
		}
	case ".py":
		if p := have("ruff"); p != "" {
			return []string{p, "format", "--stdin-filename", file, "-"}
		}
		if p := have("black"); p != "" {
			return []string{p, "-q", "--stdin-filename", file, "-"}
		}
	case ".rs":
		if p := have("rustfmt"); p != "" {
			return []string{p, "--emit", "stdout"}
		}
	case ".sh", ".bash":
		if p := have("shfmt"); p != "" {
			return []string{p, "-filename", file}
		}
	case ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".json", ".css", ".scss", ".less", ".html", ".vue", ".svelte", ".md", ".yaml", ".yml", ".graphql":
		local := filepath.Join(repo, "node_modules", ".bin", "prettier")
		if _, err := exec.LookPath(local); err == nil {
			return []string{local, "--stdin-filepath", file}
		}
		if p := have("prettier"); p != "" {
			return []string{p, "--stdin-filepath", file}
		}
	}
	return nil
}

// formatDocument runs the formatter on the editor's text; save then saves the result.
func (m *Model) formatDocument(save bool) tea.Cmd {
	a := m.st.App
	if a.File == "" || a.FileLines == nil || a.FileTruncated || previewKind(a.File) != "" {
		return nil
	}
	argv := formatter(a.Repo, a.File)
	if argv == nil {
		m.st.Message = "no formatter for " + filepath.Base(a.File) + " on this computer (gofmt, prettier, ruff, black, rustfmt, shfmt)"
		return nil
	}
	eol := "\n"
	if a.FileCRLF {
		eol = "\r\n"
	}
	text, repo, file, id := strings.Join(a.FileLines, eol), a.Repo, a.File, linesID(a.FileLines)
	m.st.Message = "formatting " + filepath.Base(a.File) + "…"
	return func() tea.Msg {
		ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
		defer cancel()
		cmd := exec.CommandContext(ctx, argv[0], argv[1:]...)
		cmd.Dir = repo
		cmd.Stdin = strings.NewReader(text)
		var out, errb bytes.Buffer
		cmd.Stdout, cmd.Stderr = &out, &errb
		if err := cmd.Run(); err != nil {
			note := strings.TrimSpace(errb.String())
			if note == "" {
				note = err.Error()
			}
			return formatMsg{file: file, id: id, err: clean(firstLineOf(note)), save: save}
		}
		return formatMsg{file: file, id: id, text: out.String(), save: save}
	}
}

func firstLineOf(s string) string {
	if i := strings.IndexByte(s, '\n'); i >= 0 {
		return s[:i]
	}
	return s
}

// afterFormat puts the formatter's answer in the editor as one edit, and saves when asked.
func (m *Model) afterFormat(msg formatMsg) tea.Cmd {
	a := m.st.App
	if !m.appOn() || msg.file != a.File || a.FileLines == nil {
		return nil
	}
	if msg.err != "" {
		m.st.Message = "formatter: " + msg.err
		return nil
	}
	if linesID(a.FileLines) != msg.id {
		m.st.Message = "the file changed while it was being formatted; try again"
		return nil
	}
	lines := strings.Split(strings.ReplaceAll(msg.text, "\r\n", "\n"), "\n")
	if strings.Join(lines, "\n") != strings.Join(a.FileLines, "\n") {
		cur := a.cursor()
		m.rememberEdit("format")
		last := pos{len(a.FileLines) - 1, len([]rune(a.FileLines[len(a.FileLines)-1]))}
		a.replaceRange(pos{0, 0}, last, strings.Join(lines, "\n"))
		a.EdSel, a.Extra = false, nil
		a.setCursor(pos{min(cur.l, len(a.FileLines)-1), cur.c})
		m.keepCursorShown()
		m.st.Message = "formatted " + filepath.Base(a.File)
	} else {
		m.st.Message = filepath.Base(a.File) + " is already formatted"
	}
	if msg.save {
		m.saveFile()
		return m.afterSave()
	}
	return nil
}

// saveMaybeFormatted saves the file, formatting it first when Settings › Editor says so and a formatter is here.
func (m *Model) saveMaybeFormatted() tea.Cmd {
	a := m.st.App
	if s := m.st.AppSettings; s != nil && s.FormatOnSave && a.Dirty && formatter(a.Repo, a.File) != nil {
		if cmd := m.formatDocument(true); cmd != nil {
			return cmd
		}
	}
	m.saveFile()
	return m.afterSave()
}
