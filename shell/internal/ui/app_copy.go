package ui

import (
	"fmt"
	"regexp"
	"strings"

	"github.com/Brasth/Rig/shell/internal/view"
)

var (
	gutterRow = regexp.MustCompile(`^\s*[│▎] ?`)
	fenceOpen = regexp.MustCompile(`^\s*╭ ?(.*)$`)
	fenceEnd  = regexp.MustCompile(`^\s*╰\s*$`)
)

// undecorate puts back what the screen drew around the text: a code block's gutter and its corners become the fence of
// markdown, a quote's bar goes. What was copied is then what the parent wrote, not the way Rig drew it.
func undecorate(rows []string) []string {
	out := make([]string, 0, len(rows))
	for _, r := range rows {
		switch {
		case fenceOpen.MatchString(r):
			out = append(out, "```"+strings.TrimSpace(fenceOpen.FindStringSubmatch(r)[1]))
		case fenceEnd.MatchString(r):
			out = append(out, "```")
		case gutterRow.MatchString(r) && strings.Count(r, "│") < 2: // a table row has a bar on each side of every cell: it keeps them
			out = append(out, gutterRow.ReplaceAllString(r, ""))
		default:
			out = append(out, r)
		}
	}
	return out
}

// lastAnswer is the text of the parent's last answer: the entries after the last thing you said, joined.
func lastAnswer(entries []view.ChatEntry) string {
	i := len(entries)
	for i > 0 && entries[i-1].Role != "user" {
		i--
	}
	var parts []string
	for _, e := range entries[i:] {
		if e.Role == "assistant" && strings.TrimSpace(e.Text) != "" {
			parts = append(parts, strings.TrimSpace(e.Text))
		}
	}
	return strings.Join(parts, "\n\n")
}

// lastCodeBlock is the last fenced block of markdown in text, without its fences.
func lastCodeBlock(text string) (string, bool) {
	var block []string
	var last []string
	in, found := false, false
	for _, line := range strings.Split(text, "\n") {
		t := strings.TrimSpace(line)
		switch {
		case !in && (strings.HasPrefix(t, "```") || strings.HasPrefix(t, "~~~")):
			in, block = true, nil
		case in && (strings.HasPrefix(t, "```") || strings.HasPrefix(t, "~~~")):
			in, found, last = false, true, block
		case in:
			block = append(block, line)
		}
	}
	if in && len(block) > 0 { // still streaming in: what there is so far
		return strings.Join(block, "\n"), true
	}
	return strings.Join(last, "\n"), found
}

// copyLast does /copy: the last answer, or with "code" its last code block, to the clipboard. It returns the message.
func copyLast(st State, arg string) string {
	if st.Chat == nil {
		return "nothing to copy yet"
	}
	text := lastAnswer(st.Chat.Entries)
	what := "the last answer"
	if strings.TrimSpace(arg) == "code" {
		block, ok := lastCodeBlock(text)
		if !ok {
			return "the last answer has no code block"
		}
		text, what = block, "the last code block"
	}
	if strings.TrimSpace(text) == "" {
		return "nothing to copy yet"
	}
	if !Copy(text) {
		return "could not reach the clipboard here: select the text with the mouse instead"
	}
	return fmt.Sprintf("copied %s (%d characters)", what, len([]rune(text)))
}
