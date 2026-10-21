package ui

import (
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"strings"
)

// Dropping files on the window: a terminal pastes their paths, quoted or with \-escaped spaces (Ghostty, iTerm2,
// Terminal), or as file:// URLs (kitty). When every word of a paste is a file that exists, the box gets @ references,
// as ⌃k's "add a file" makes; anything else is pasted as text.

// droppedFiles is the files a paste names, or nil when it is not only paths of files that exist.
func droppedFiles(text string) []string {
	words, ok := shellWords(strings.TrimSpace(text))
	if !ok || len(words) == 0 || len(words) > 20 {
		return nil
	}
	var out []string
	for _, w := range words {
		if strings.HasPrefix(w, "file://") {
			u, err := url.Parse(w)
			if err != nil || (u.Host != "" && u.Host != "localhost") {
				return nil
			}
			w = u.Path
		}
		if strings.HasPrefix(w, "~/") {
			if home, err := os.UserHomeDir(); err == nil {
				w = filepath.Join(home, w[2:])
			}
		}
		if !filepath.IsAbs(w) {
			return nil // a drop is always a whole path; a word like main.go is pasted text
		}
		if _, err := os.Stat(w); err != nil {
			return nil
		}
		out = append(out, filepath.Clean(w))
	}
	return out
}

// shellWords splits a paste as a shell would: spaces separate, quotes and \ keep them; false for an open quote.
func shellWords(s string) ([]string, bool) {
	var words []string
	var cur strings.Builder
	in, quote, escaped := false, rune(0), false
	for _, r := range s {
		switch {
		case escaped:
			cur.WriteRune(r)
			escaped = false
		case r == '\\' && quote != '\'':
			escaped, in = true, true
		case quote != 0 && r == quote:
			quote = 0
		case quote != 0:
			cur.WriteRune(r)
		case r == '\'' || r == '"':
			quote, in = r, true
		case r == ' ' || r == '\t' || r == '\n' || r == '\r':
			if in {
				words = append(words, cur.String())
				cur.Reset()
				in = false
			}
		default:
			cur.WriteRune(r)
			in = true
		}
	}
	if quote != 0 || escaped {
		return nil, false
	}
	if in {
		words = append(words, cur.String())
	}
	return words, true
}

// fileRef is how the box names a file for the parent: relative inside the repository, quoted when it has a space.
func fileRef(path, repo string) string {
	if repo != "" {
		if rel, err := filepath.Rel(repo, path); err == nil && !strings.HasPrefix(rel, "..") {
			path = rel
		}
	}
	if strings.ContainsAny(path, " \t") {
		return `@"` + path + `"`
	}
	return "@" + path
}

// dropFiles adds @ references for dropped files to the box, and says so.
func (m *Model) dropFiles(paths []string) {
	for _, p := range paths {
		if m.st.Prompt != "" && !strings.HasSuffix(m.st.Prompt, " ") {
			m.st.Prompt += " "
		}
		m.st.Prompt += fileRef(p, m.st.App.Repo) + " "
	}
	m.st.SlashSel = 0
	if len(paths) == 1 {
		m.st.Message = "added " + filepath.Base(paths[0])
	} else {
		m.st.Message = fmt.Sprintf("added %d files", len(paths))
	}
}
