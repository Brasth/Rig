package ui

import (
	"fmt"
	"strings"

	tea "github.com/charmbracelet/bubbletea"
)

// Reviewing a helper's work hunk by hunk, as a code review does: on the changes tab n and N choose a hunk, v marks it
// reviewed, and x asks the helper to redo it with your reason. Nothing here changes the helper's files: a request
// goes to the helper's inbox (job.message) naming the file, its lines and the hunk itself, and the parent still
// decides what to apply.

// hunkSpan is one hunk of a file's diff: where it starts in the parsed lines and which lines of the new file it covers.
type hunkSpan struct {
	from, to   int // indexes into DiffFile.Lines: its '@' line and its last line
	start, end int // the new file's lines it covers
}

// hunkSpans splits a file's parsed diff into its hunks.
func hunkSpans(f DiffFile) []hunkSpan {
	var out []hunkSpan
	for i, l := range f.Lines {
		if l.Kind == '@' {
			if n := len(out); n > 0 {
				out[n-1].to = i - 1
			}
			out = append(out, hunkSpan{from: i, to: len(f.Lines) - 1, start: l.Num, end: l.Num})
			continue
		}
		if n := len(out); n > 0 && l.Kind != '-' {
			out[n-1].end = max(out[n-1].end, l.Num)
		}
	}
	return out
}

// hunkKey names a hunk for the review marks.
func hunkKey(job, path string, i int) string { return fmt.Sprintf("%s|%s|%d", job, path, i) }

// hunkDiffText is a hunk as unified-diff text, capped, for a request to the helper.
func hunkDiffText(f DiffFile, h hunkSpan) string {
	var b strings.Builder
	b.WriteString(fmt.Sprintf("@@ new lines %d-%d @@\n", h.start, h.end))
	n := 0
	for _, l := range f.Lines[h.from+1 : h.to+1] {
		if n == 40 {
			b.WriteString("…\n")
			break
		}
		b.WriteString(string(l.Kind) + l.Text + "\n")
		n++
	}
	return strings.TrimRight(b.String(), "\n")
}

// redoTarget is the hunk a request to redo is being written for.
type redoTarget struct {
	job, path string
	hunk      int
	span      hunkSpan
	file      DiffFile
}

// reviewCounts is how many hunks of a job's diff are marked reviewed, and how many there are.
func reviewCounts(a *AppState, job string, files []DiffFile) (done, total int) {
	for _, f := range files {
		for i := range hunkSpans(f) {
			total++
			if a.Reviewed[hunkKey(job, f.Path, i)] == "ok" {
				done++
			}
		}
	}
	return
}

// currentHunk is the chosen file and hunk of the changes tab's helper, when there is one.
func (m *Model) currentHunk() (job string, f DiffFile, spans []hunkSpan, i int, ok bool) {
	a := m.st.App
	job = m.selectedJobID()
	if job == "" || a.Diff == nil || a.DiffID != job {
		return
	}
	files := ParseDiff(a.Diff.Diff)
	if len(files) == 0 {
		return
	}
	f = files[clampSel(a.DiffFile, len(files))]
	spans = hunkSpans(f)
	if len(spans) == 0 {
		return
	}
	return job, f, spans, clampSel(a.DiffHunk, len(spans)), true
}

// reviewKey is the changes tab's hunk keys; false when the key is not one of them.
func (m *Model) reviewKey(key string) (tea.Cmd, bool) {
	a := m.st.App
	job, f, spans, i, ok := m.currentHunk()
	switch key {
	case "n", "N", "v", "x":
	default:
		return nil, false
	}
	if !ok {
		m.st.Message = "no hunk to review here"
		return nil, true
	}
	switch key {
	case "n":
		a.DiffHunk, a.DiffFollow = min(i+1, len(spans)-1), true
	case "N":
		a.DiffHunk, a.DiffFollow = max(i-1, 0), true
	case "v":
		if a.Reviewed == nil {
			a.Reviewed = map[string]string{}
		}
		k := hunkKey(job, f.Path, i)
		if a.Reviewed[k] == "ok" {
			delete(a.Reviewed, k)
		} else {
			a.Reviewed[k] = "ok"
			a.DiffHunk, a.DiffFollow = min(i+1, len(spans)-1), true // on to the next one, as a review goes
		}
	case "x":
		a.Commenting, m.st.Prompt = true, ""
		a.RedoHunk = &redoTarget{job: job, path: f.Path, hunk: i, span: spans[i], file: f}
	}
	return nil, true
}

// redoMessage is what the helper is sent for a hunk it should redo.
func redoMessage(t *redoTarget, reason string) string {
	var b strings.Builder
	fmt.Fprintf(&b, "Please redo this change in %s (lines %d-%d):\n```diff\n%s\n```\n", t.path, t.span.start, t.span.end, hunkDiffText(t.file, t.span))
	if reason = strings.TrimSpace(reason); reason != "" {
		b.WriteString("Why: " + reason)
	} else {
		b.WriteString("Why: not given; it did not meet the brief.")
	}
	return b.String()
}

// sendRedo sends the helper the request for the chosen hunk, marks the hunk, and shows it in what you said.
func (m *Model) sendRedo(reason string) tea.Cmd {
	a := m.st.App
	t := a.RedoHunk
	a.RedoHunk, a.Commenting, m.st.Prompt = nil, false, ""
	if t == nil {
		return nil
	}
	if a.Reviewed == nil {
		a.Reviewed = map[string]string{}
	}
	a.Reviewed[hunkKey(t.job, t.path, t.hunk)] = "redo"
	if a.Comments == nil {
		a.Comments = map[string][]string{}
	}
	note := fmt.Sprintf("redo %s:%d-%d", t.path, t.span.start, t.span.end)
	if r := strings.TrimSpace(reason); r != "" {
		note += " — " + r
	}
	a.Comments[t.job] = append(a.Comments[t.job], note)
	return m.act("job.message", map[string]any{"id": t.job, "text": redoMessage(t, reason)}, false)
}
