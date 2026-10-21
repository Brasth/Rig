package ui

import (
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"strings"
)

// attachment is a picture dropped on the box: what it is, and the thumbnail the terminal was sent for it, if any.
type attachment struct {
	ref, name  string
	w, h       int
	size       int64
	cols, rows int // the thumbnail's cells; 0 where the terminal cannot show one
}

// isPicture is whether a path is a picture the shell can read.
func isPicture(path string) bool {
	switch strings.ToLower(filepath.Ext(path)) {
	case ".png", ".jpg", ".jpeg", ".gif":
		return true
	}
	return false
}

// thumbCells is the cells a picture's thumbnail takes: five rows, as wide as its shape asks (a cell is about twice
// as tall as it is wide), never wider than 40.
func thumbCells(w, h int) (cols, rows int) {
	rows = 5
	cols = min(40, max(4, (rows*2*w+h/2)/max(1, h)))
	return cols, rows
}

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

// dropFiles adds @ references for dropped files to the box, and says so. A picture is shown above the box too: its
// name, size and shape, and a thumbnail where the terminal draws images.
func (m *Model) dropFiles(paths []string) {
	a := m.st.App
	for _, p := range paths {
		if m.st.Prompt != "" && !strings.HasSuffix(m.st.Prompt, " ") {
			m.st.Prompt += " "
		}
		ref := fileRef(p, a.Repo)
		m.st.Prompt += ref + " "
		if isPicture(p) {
			m.attach(p, ref)
		}
	}
	m.st.SlashSel = 0
	if len(paths) == 1 {
		m.st.Message = "added " + filepath.Base(paths[0])
	} else {
		m.st.Message = fmt.Sprintf("added %d files", len(paths))
	}
}

// attach shows a dropped picture above the box. The newest one gets the thumbnail.
func (m *Model) attach(path, ref string) {
	a := m.st.App
	at := attachment{ref: ref, name: filepath.Base(path)}
	if info, err := os.Stat(path); err == nil {
		at.size = info.Size()
	}
	png, w, h := pictureAsPNG(path)
	if png == nil {
		return // not a picture after all: the @ reference stands alone
	}
	at.w, at.h = w, h
	for i := range a.Attached {
		a.Attached[i].cols, a.Attached[i].rows = 0, 0 // one thumbnail at a time
	}
	if m.st.Logos {
		if cols, rows := thumbCells(w, h); SendImageAs(dropImageID, png, cols, rows) {
			at.cols, at.rows = cols, rows
		}
	}
	kept := a.Attached[:0:0]
	for _, o := range a.Attached {
		if o.ref != ref {
			kept = append(kept, o)
		}
	}
	a.Attached = append(kept, at)
	if len(a.Attached) > 6 {
		a.Attached = a.Attached[len(a.Attached)-6:]
	}
}

// attachedRows are the rows above the box for the pictures the draft still names: the newest one's thumbnail, then a
// line for each with its name, shape and size.
func attachedRows(st State, w int) []string {
	a := st.App
	if a == nil || st.Prompt == "" {
		return nil
	}
	var here []attachment
	for _, at := range a.Attached {
		if strings.Contains(st.Prompt, at.ref) {
			here = append(here, at)
		}
	}
	if len(here) == 0 {
		return nil
	}
	var out []string
	for _, at := range here {
		if at.cols > 0 {
			for _, r := range imageRows(dropImageID, at.cols, at.rows) {
				out = append(out, " "+r)
			}
		}
	}
	for _, at := range here {
		line := " " + st.faint("▣") + " " + st.bold(at.name)
		if at.w > 0 {
			line += st.faint(fmt.Sprintf("  %d×%d · %s", at.w, at.h, sizeWord(at.size)))
		}
		out = append(out, fit(line, w))
	}
	return out
}

// sizeWord is a file size as people say it: 214 KB, 1.3 MB.
func sizeWord(n int64) string {
	switch {
	case n >= 1<<20:
		return fmt.Sprintf("%.1f MB", float64(n)/(1<<20))
	case n >= 1<<10:
		return fmt.Sprintf("%d KB", (n+512)>>10)
	}
	return fmt.Sprintf("%d B", n)
}
