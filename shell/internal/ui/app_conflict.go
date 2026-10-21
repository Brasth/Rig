package ui

import (
	"fmt"
	"path/filepath"
	"strings"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
)

// Conflicts, as an IDE resolves them: a file git left conflict markers in opens as a list of its conflicts, each with
// the two sides; o keeps ours, t theirs, b both (ours first), ] [ move between them, and once none are left the file
// is marked resolved (git add). The file on disk is rewritten at each choice, so the editor and git see it too.

// conflict is one block between <<<<<<< and >>>>>>>: where it is in the file and what each side has.
type conflict struct {
	start, end   int // lines of the file, from the <<<<<<< line to the >>>>>>> line, inclusive
	ours, theirs []string
	oursName     string // after <<<<<<<: HEAD, or a branch
	theirsName   string
}

// conflicts finds every conflict block in a file's lines.
func conflicts(lines []string) []conflict {
	var out []conflict
	for i := 0; i < len(lines); i++ {
		if !strings.HasPrefix(lines[i], "<<<<<<< ") && lines[i] != "<<<<<<<" {
			continue
		}
		c := conflict{start: i, oursName: strings.TrimSpace(strings.TrimPrefix(lines[i], "<<<<<<<"))}
		j, side := i+1, 0
		for ; j < len(lines); j++ {
			l := lines[j]
			switch {
			case strings.HasPrefix(l, "||||||| ") || l == "|||||||":
				side = 2 // the diff3 base: not shown, not kept
			case l == "=======" && side < 3:
				side = 3
			case strings.HasPrefix(l, ">>>>>>> ") || l == ">>>>>>>":
				c.end, c.theirsName = j, strings.TrimSpace(strings.TrimPrefix(l, ">>>>>>>"))
				out = append(out, c)
				i = j
				j = len(lines)
			case side == 0:
				c.ours = append(c.ours, l)
			case side == 3:
				c.theirs = append(c.theirs, l)
			}
		}
	}
	return out
}

// resolveConflict puts a choice in place of conflict c of the lines: ours, theirs or both.
func resolveConflict(lines []string, c conflict, choice string) []string {
	var keep []string
	switch choice {
	case "ours":
		keep = c.ours
	case "theirs":
		keep = c.theirs
	default:
		keep = append(append([]string(nil), c.ours...), c.theirs...)
	}
	out := make([]string, 0, len(lines)-(c.end-c.start+1)+len(keep))
	out = append(out, lines[:c.start]...)
	out = append(out, keep...)
	out = append(out, lines[c.end+1:]...)
	return out
}

// conflictView is the conflict screen's state: which conflict is chosen.
type conflictView struct {
	path string
	sel  int
}

// openConflicts opens a conflicted file's conflict view, reading the file.
func (m *Model) openConflicts(path string) tea.Cmd {
	a := m.st.App
	a.Conflict = &conflictView{path: path}
	a.GitDiff = nil
	if a.File != path || a.FileLines == nil {
		a.File, a.FileLines, a.FileErr, a.FileOff, a.Editing, a.Dirty = path, nil, "", 0, false, false
		a.Tabs = addTab(a.Tabs, path)
		return m.openFile(path)
	}
	return nil
}

// chooseSide resolves the chosen conflict, writes the file, and marks it resolved once no conflict is left.
func (m *Model) chooseSide(choice string) tea.Cmd {
	a := m.st.App
	v := a.Conflict
	if v == nil || a.FileLines == nil || a.File != v.path {
		return nil
	}
	cs := conflicts(a.FileLines)
	if len(cs) == 0 {
		return nil
	}
	v.sel = clampSel(v.sel, len(cs))
	m.rememberEdit("conflict")
	a.FileLines = resolveConflict(a.FileLines, cs[v.sel], choice)
	a.Dirty = true
	m.saveFile()
	left := conflicts(a.FileLines)
	if len(left) == 0 {
		path := v.path
		m.st.Message = "resolved " + filepath.Base(path)
		return m.gitAct("resolved "+path+": no conflicts left, marked resolved", func(repo string) error {
			_, err := gitOut(repo, "add", "--", path)
			return err
		})
	}
	v.sel = min(v.sel, len(left)-1)
	m.st.Message = fmt.Sprintf("kept %s · %d left", choice, len(left))
	return tea.Batch(m.fetchGit(), m.fetchGutter())
}

// conflictKey is the conflict view's keys.
func (m *Model) conflictKey(k tea.KeyMsg) (tea.Model, tea.Cmd) {
	a := m.st.App
	v := a.Conflict
	cs := conflicts(a.FileLines)
	switch k.String() {
	case "]", "down", "j":
		v.sel = clampSel(v.sel+1, len(cs))
	case "[", "up", "k":
		v.sel = clampSel(v.sel-1, len(cs))
	case "o", "1":
		return m, m.chooseSide("ours")
	case "t", "2":
		return m, m.chooseSide("theirs")
	case "b", "3":
		return m, m.chooseSide("both")
	case "e":
		// edit by hand: the cursor on the chosen conflict
		a.Conflict = nil
		m.startEdit()
		if a.Editing && len(cs) > 0 {
			m.moveTo(pos{cs[clampSel(v.sel, len(cs))].start, 0}, false)
			m.keepCursorShown()
		}
	case "esc", "g":
		a.Conflict = nil
		if k.String() == "g" {
			return m, m.openGit()
		}
	}
	return m, nil
}

// conflictRows draws the conflict view in place of the file: each conflict with its two sides.
func conflictRows(st State, w, room int) []string {
	a := st.App
	v := a.Conflict
	cs := conflicts(a.FileLines)
	var out []string
	switch {
	case a.FileLines == nil:
		return []string{"  " + st.faint("loading…")}
	case len(cs) == 0:
		return []string{"  " + st.bold(v.path), "", "  " + st.paint(colGreen, "no conflicts left") + "  " + st.faint("esc shows the file")}
	}
	out = append(out, "  "+st.bold(v.path)+"  "+st.faint(fmt.Sprintf("%d conflicts · ] [ choose · o ours · t theirs · b both · e edit", len(cs))), "")
	sel := clampSel(v.sel, len(cs))
	half := (w - 10) / 2
	oursBg, theirsBg := "#dcfce7", "#dbeafe"
	if !lightGround() {
		oursBg, theirsBg = "#14532d", "#1e3a8a"
	}
	for i, c := range cs {
		edge := "  "
		if i == sel {
			edge = st.paint(colAccent, "▎") + " "
		}
		head := edge + st.faint(fmt.Sprintf("%d · lines %d–%d", i+1, c.start+1, c.end+1))
		out = append(out, head)
		ours := "ours"
		if c.oursName != "" {
			ours += " (" + c.oursName + ")"
		}
		theirs := "theirs"
		if c.theirsName != "" {
			theirs += " (" + c.theirsName + ")"
		}
		out = append(out, edge+fit(st.bold(ours), half)+"  "+fit(st.bold(theirs), half))
		n := max(len(c.ours), len(c.theirs))
		if n == 0 {
			out = append(out, edge+st.faint("(empty)"))
		}
		for j := 0; j < n && j < 40; j++ {
			l, r := "", ""
			if j < len(c.ours) {
				l = sanitize(c.ours[j])
			}
			if j < len(c.theirs) {
				r = sanitize(c.theirs[j])
			}
			l, r = fit(l, half), fit(r, half)
			if st.Styled {
				l, r = tint(l, oursBg), tint(r, theirsBg)
			}
			out = append(out, edge+l+"  "+r)
		}
		if n > 40 {
			out = append(out, edge+st.faint(fmt.Sprintf("… %d more lines", n-40)))
		}
		out = append(out, "")
	}
	// scroll so the chosen conflict is in view
	at := 0
	for i := range cs {
		if i == sel {
			break
		}
		at += 3 + min(40, max(len(cs[i].ours), len(cs[i].theirs), 1)) + 1
	}
	if at+2 >= room-2 && len(out) > room {
		out = append(out[:2], out[2+max(0, at-1):]...)
	}
	_ = lipgloss.Width
	return out
}
