package ui

import (
	"fmt"

	tea "github.com/charmbracelet/bubbletea"
)

// What next: when the parent's last turn changed files, a bar above the reply box says so and offers the three things
// people do next: look at what changed (g), keep it in git (c), or put it back (u). It is clickable, and the keys work
// with the box empty. It goes away as soon as you type, or a turn that changed nothing follows.

// nextBarOn is whether the bar shows: the latest turn changed files, the parent is done, and you are not typing.
func nextBarOn(st State) bool {
	c := st.Chat
	a := st.App
	if c == nil || c.State != "idle" || c.Ask != nil || st.Prompt != "" || a == nil {
		return false
	}
	if a.Palette != nil || a.ChatFind != nil || a.Settings != nil || a.Help || len(a.Outbox) > 0 {
		return false
	}
	mk := lastMark(st)
	return mk != nil && mk.Turn == c.Turns
}

// nextBar is the bar's row, with a blank under it.
func nextBar(st State, cw int) []string {
	mk := lastMark(st)
	n := len(mk.Files)
	what := fmt.Sprintf("%d file%s changed", n, plural(n))
	if n == 1 {
		what = clean(mk.Files[0].Path) + " changed"
	}
	button := func(id, key, label string) string {
		text := " " + key + " " + label + " "
		if st.Styled {
			text = tint(text, colRaised)
		} else {
			text = "[" + key + " " + label + "]"
		}
		return st.zone("row:next:"+id, text)
	}
	row := "  " + st.paint(colGreen, "✎") + " " + fit(what, max(10, cw-52)) + "  " +
		button("review", "g", "Review") + " " + button("commit", "c", "Commit") + " " + button("undo", "u", "Undo")
	return []string{fit(row, cw), ""}
}

// nextAction does what a button or its key does.
func (m *Model) nextAction(id string) tea.Cmd {
	switch id {
	case "review":
		return m.openTurnChanges()
	case "commit":
		return m.askTurnCommit()
	case "undo":
		if mk := lastMark(m.st); mk != nil {
			return m.planRewind(mk.Turn) // the files, all or one at a time, and ⏎ puts them back
		}
	}
	return nil
}
