package ui

import (
	"fmt"
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"

	"github.com/Brasth/Rig/shell/internal/view"
)

// The design's "Done today" (what was applied, undone or held, with undo and redo) and "Ship" (push what is ahead,
// or open a pull request). Each change asks first; agents never push.

// HistoryState is the done-today screen.
type HistoryState struct{ Sel int }

// ShipState is the ship screen: pull request or push, and the title being edited.
type ShipState struct {
	Push    bool
	Title   string
	Editing bool
}

type todayMsg struct {
	t   *view.Today
	err error
}

type shipMsg struct {
	s   *view.ShipStatus
	err error
}

type appliedMsg struct {
	text string
	tell string // after an undo, the note for the parent: it handles anything built on the undone work
	err  error
}

// noteSentMsg is the parent told about an undo.
type noteSentMsg struct {
	started *view.ChatStarted
	err     error
}

func (m *Model) fetchToday() tea.Cmd {
	src := m.src
	return func() tea.Msg {
		t, err := src.Today(callTimeout)
		return todayMsg{t: t, err: err}
	}
}

func (m *Model) fetchShip() tea.Cmd {
	src := m.src
	return func() tea.Msg {
		s, err := src.ShipStatus(callTimeout)
		return shipMsg{s: s, err: err}
	}
}

// applyAct runs undo, redo, hold or ship and reloads done today.
func (m *Model) applyAct(method string, params map[string]any, done string) tea.Cmd {
	return m.applyThenTell(method, params, done, "")
}

// applyThenTell is applyAct that, once it succeeds, tells the parent (tell), as the design's undo does.
func (m *Model) applyThenTell(method string, params map[string]any, done, tell string) tea.Cmd {
	src := m.src
	return func() tea.Msg {
		_, err := src.Act(method, params, 3*time.Minute)
		return appliedMsg{text: done, tell: tell, err: err}
	}
}

// holdAll holds every helper's work not yet applied: the ones at work and the finished ones waiting on the parent.
func (m *Model) holdAll() tea.Cmd {
	var ids []string
	for _, r := range m.view.Rows("Jobs") {
		if r.Label == "Unverified" || r.Label == "Running" || r.Label == "Needs input" || r.Ask {
			ids = append(ids, r.ID)
		}
	}
	src := m.src
	return func() tea.Msg {
		held := 0
		var last error
		for _, id := range ids {
			if _, err := src.Act("applies.hold", map[string]any{"id": id, "on": true}, 30*time.Second); err != nil {
				last = err
				continue
			}
			held++
		}
		if held == 0 && last != nil {
			return appliedMsg{err: last}
		}
		return appliedMsg{text: fmt.Sprintf("holding %d helpers' work", held)}
	}
}

// undoNote is what the parent is told after an undo, so it can handle anything built on that work.
func (m *Model) undoNote(id string) string {
	title, commit, branch := id, "", ""
	if m.st.Today != nil {
		for _, r := range m.st.Today.Rows {
			if r.JobID == id {
				title, commit, branch = clean(r.Title), r.Commit, r.Branch
			}
		}
	}
	return fmt.Sprintf("I undid %q (job %s, merged as %s on %s): Rig reverted its merge as a new commit. "+
		"Please check whether anything since was built on it and fix that; do not apply it again unless I ask.", title, id, commit, branch)
}

// tellParent sends the note without touching the reply box.
func (m *Model) tellParent(text string) tea.Cmd {
	src, session, parent := m.src, m.chatID, m.parentName
	return func() tea.Msg {
		started, err := src.ChatSend(text, session, parent, 10*time.Second)
		return noteSentMsg{started: started, err: err}
	}
}

// checkMarks is the design's " · ✓ lint ✓ tests ✓ review by codex": each check the job ran, and its review.
func checkMarks(st State, checks []view.Check, review string) string {
	var marks []string
	for _, c := range checks {
		if c.OK {
			marks = append(marks, st.paint(colGreen, "✓ "+c.Name))
		} else {
			marks = append(marks, st.paint(colRed, "✗ "+c.Name))
		}
	}
	if review != "" {
		marks = append(marks, st.paint(colGreen, "✓ review by "+review))
	}
	if len(marks) == 0 {
		return ""
	}
	return " · " + strings.Join(marks, " ")
}

func stateColour(state string) string {
	switch state {
	case "applied":
		return colGreen
	case "held":
		return colAmber
	}
	return colFaint
}

// historyScreen is "Done today": one row per applied, undone or held job, and the chosen one's details.
func historyScreen(st State, w, h int) []string {
	parent := appParent(st)
	cw := min(70, w-8) // the design's done today and ship are 70 wide
	lines := []string{spread(rigBarLeft(st, nil)+st.faint(" › done today"), "", "", w), fit("", w)}
	body := []string{"", st.bold("Done today") + "  " + st.faint("applied, held and allowed by "+parent), ""}
	var keys [][2]string
	switch {
	case st.Today == nil:
		body = append(body, st.faint("loading…"))
		keys = [][2]string{{"esc", "back"}}
	case len(st.Today.Rows) == 0:
		body = append(body, st.dim("Nothing applied today yet. When "+parent+" accepts a helper's work, it shows here."))
		keys = [][2]string{{"esc", "back"}}
	default:
		sel := clampSel(st.App.History.Sel, len(st.Today.Rows))
		for i, r := range st.Today.Rows {
			when := time.Unix(int64(r.At), 0).Format("15:04")
			state := r.State
			if state == "undone" {
				state = "undone by you"
			}
			title := fit(clean(r.Title), cw-34)
			if r.State == "undone" && st.Styled {
				text := strings.TrimRight(title, " ")
				title = lipgloss.NewStyle().Strikethrough(true).Render(text) + strings.Repeat(" ", lipgloss.Width(title)-lipgloss.Width(text))
			}
			right := st.paint(stateColour(r.State), state)
			if i == sel {
				when, right = st.bold(when), st.bold(right)
				if r.State != "undone" {
					title = st.bold(title)
				}
			}
			left := st.faint(when) + "  " + st.tile(r.Worker) + " " + title
			row := left + strings.Repeat(" ", max(2, cw-2-lipgloss.Width(left)-lipgloss.Width(right))) + right + "  "
			if i == sel {
				row = " " + row // the design shifts the chosen row by one and lights it
				if st.Styled {
					row = tint(fit(row, cw), colSelBg)
				} else {
					row = ">" + row[1:]
				}
			}
			body = append(body, row)
		}
		r := st.Today.Rows[sel]
		body = append(body, "", "", st.bold(clean(r.Title)))
		keys = [][2]string{{"u", "undo"}, {"r", "redo"}, {"d", "diff"}, {"a", "let " + parent + " apply"}, {"esc", "back"}}
		switch r.State {
		case "command":
			by := parent
			if r.By == "you" {
				by = "you"
			}
			body = append(body, st.faint("allowed by "+by+" at "+time.Unix(int64(r.At), 0).Format("15:04")+" · "+r.Worker+" ran it"))
		case "held":
			body = append(body, st.dim("held: "+parent+"'s acceptance will not apply it until you let it (a)"))
		case "undone":
			body = append(body, fmt.Sprintf("%d files +%d −%d", r.Files, r.Added, r.Deleted)+checkMarks(st, r.Checks, r.Review),
				st.faint("undone by you · r applies it again"))
		default:
			body = append(body, fmt.Sprintf("%d files +%d −%d", r.Files, r.Added, r.Deleted)+checkMarks(st, r.Checks, r.Review),
				st.faint("on "+r.Branch+" as "+r.Commit+" · undo reverts it; "+parent+" handles anything built on"))
		}
	}
	lines = append(lines, indentAll(body, (w-cw)/2, w)...)
	for len(lines) < h-1 {
		lines = append(lines, fit("", w))
	}
	return append(lines[:h-1], keyHints(st, keys, w))
}

// shipScreen is the design's "Ship N changes": the commits ahead, pull request or push, the branch and the title.
func shipScreen(st State, w, h int) []string {
	s := st.Ship
	sh := st.App.ShipScreen
	cw := min(70, w-8) // the design's done today and ship are 70 wide
	lines := []string{spread(rigBarLeft(st, nil)+st.faint(" › ship"), "", "", w), fit("", w)}
	var body []string
	keys := [][2]string{{"esc", "not now"}}
	switch {
	case s == nil && st.ShipE != "":
		body = append(body, "", st.dim("Nothing to ship: "+clean(st.ShipE)))
	case s == nil:
		body = append(body, "", st.faint("loading…"))
	case s.Upstream == "":
		body = append(body, "", st.dim(s.Branch+" has no upstream branch to ship to."))
	case len(s.Ahead) == 0:
		body = append(body, "", st.bold("Nothing to ship"), st.dim(s.Branch+" is level with "+s.Upstream+"."))
	default:
		body = append(body, "", st.bold(fmt.Sprintf("Ship %d changes", len(s.Ahead)))+"  "+st.faint(fmt.Sprintf("%s is %d ahead of %s", s.Branch, len(s.Ahead), s.Upstream)), "")
		// Each commit as the helper's task when done today knows it, the hashes in one column after the longest.
		shown := s.Ahead[:min(len(s.Ahead), 12)]
		subjects, whos := make([]string, len(shown)), make([]string, len(shown))
		col := 0
		for i, c := range shown {
			subjects[i] = clean(c.Subject)
			if st.Today != nil {
				for _, r := range st.Today.Rows {
					if strings.HasPrefix(c.Commit, r.Commit) && r.Commit != "" {
						whos[i], subjects[i] = r.Worker, clean(r.Title)
					}
				}
			}
			col = max(col, lipgloss.Width(subjects[i]))
		}
		col = min(col, cw-14)
		for i, c := range shown {
			mark := "  "
			if whos[i] != "" {
				mark = st.tile(whos[i]) + " "
			}
			body = append(body, mark+fit(subjects[i], col)+"   "+st.faint(c.Commit))
		}
		if len(s.Ahead) > len(shown) {
			body = append(body, st.faint(fmt.Sprintf("   +%d more", len(s.Ahead)-len(shown))))
		}
		chip := func(text string, on bool) string {
			if on && st.Styled {
				return lipgloss.NewStyle().Background(lipgloss.Color(colText)).Foreground(lipgloss.Color(colGround)).Bold(true).Render(" " + text + " ")
			}
			if on {
				return "[" + text + "]"
			}
			return st.faint(" " + text + " ")
		}
		body = append(body, "", "", st.bold("›")+" "+chip("open pull request", !sh.Push)+"   "+chip("push to "+s.Branch, sh.Push))
		if !sh.Push {
			title := sh.Title
			if title == "" {
				title = s.Title
			}
			from := "from the commits"
			if s.TitleFrom == "tasks" && sh.Title == "" {
				from = "written by " + appParent(st)
			}
			edit := st.faint(from + " · e to edit")
			if sh.Editing {
				title, edit = title+"▏", st.faint("⏎ done")
			}
			body = append(body, "    "+st.faint("branch ")+s.PRBranch, "", st.faint("title  ")+st.bold(title), "       "+edit)
			if !s.GH {
				body = append(body, "", st.paint(colAmber, "opening a pull request needs the gh CLI; push still works"))
			}
		}
		var marks []string
		for _, c := range s.Checks {
			if c.OK {
				marks = append(marks, st.paint(colGreen, "✓ "+c.Name))
			} else {
				marks = append(marks, st.paint(colRed, "✗ "+c.Name))
			}
		}
		marks = append(marks, st.faint("agents never push; you do"))
		body = append(body, "", strings.Join(marks, "  "))
		enter := "open pull request"
		if sh.Push {
			enter = "push"
		}
		keys = [][2]string{{"←→", "pr / push"}, {"e", "edit title"}, {"⏎", enter}, {"esc", "not now"}}
		if sh.Editing {
			keys = [][2]string{{"⏎", "done"}, {"esc", "cancel"}}
		}
	}
	lines = append(lines, indentAll(body, (w-cw)/2, w)...)
	for len(lines) < h-1 {
		lines = append(lines, fit("", w))
	}
	return append(lines[:h-1], keyHints(st, keys, w))
}

func (m *Model) historyKey(key string) (tea.Model, tea.Cmd) {
	a := m.st.App
	if key == "esc" || key == "q" {
		a.History = nil
		return m, nil
	}
	if m.st.Today == nil || len(m.st.Today.Rows) == 0 {
		return m, nil
	}
	rows := m.st.Today.Rows
	sel := clampSel(a.History.Sel, len(rows))
	r := rows[sel]
	switch key {
	case "up", "k":
		a.History.Sel = clampSel(sel-1, len(rows))
	case "down", "j":
		a.History.Sel = clampSel(sel+1, len(rows))
	case "u":
		if r.State == "applied" {
			m.askApply("applies.undo", r.JobID, "Undo "+clean(r.Title)+"? It reverts "+r.Commit+" on "+r.Branch+".")
		}
	case "r":
		if r.State == "undone" {
			m.askApply("applies.redo", r.JobID, "Apply "+clean(r.Title)+" again?")
		}
	case "d":
		if r.JobID == "" {
			return m, nil
		}
		for i, row := range m.view.Rows("Jobs") {
			if row.ID == r.JobID {
				m.st.Selected["Jobs"] = i
				a.History, a.Tab, a.DiffFile, a.DiffOff = nil, 2, 0, 0
				return m, m.fetchDiff(true)
			}
		}
		m.st.Message = r.JobID + " is no longer in the jobs list"
	case "a", "h":
		if r.State == "held" {
			return m, m.applyAct("applies.hold", map[string]any{"id": r.JobID, "on": false}, "released "+r.JobID)
		}
	}
	return m, nil
}

// askApply asks before undo or redo, which change the base branch.
func (m *Model) askApply(method, id, question string) {
	m.pendingX, m.pendingI = method, id
	m.st.Mode, m.st.Prompt = ModeConfirm, question
}

func (m *Model) shipKey(k tea.KeyMsg) (tea.Model, tea.Cmd) {
	a := m.st.App
	sh := a.ShipScreen
	s := m.st.Ship
	if sh.Editing {
		switch k.Type {
		case tea.KeyEnter:
			sh.Editing = false
		case tea.KeyEsc:
			sh.Editing, sh.Title = false, ""
		case tea.KeyBackspace:
			if r := []rune(sh.Title); len(r) > 0 {
				sh.Title = string(r[:len(r)-1])
			}
		case tea.KeyRunes, tea.KeySpace:
			sh.Title += string(k.Runes)
		}
		return m, nil
	}
	switch k.String() {
	case "esc", "q":
		a.ShipScreen = nil
	case "left", "right", "h", "l":
		sh.Push = !sh.Push
	case "e":
		if !sh.Push && s != nil {
			if sh.Title == "" {
				sh.Title = s.Title
			}
			sh.Editing = true
		}
	case "enter":
		if s == nil || len(s.Ahead) == 0 || s.Upstream == "" {
			return m, nil
		}
		title := sh.Title
		if title == "" {
			title = s.Title
		}
		if sh.Push {
			m.pendingX, m.pendingI = "ship.push", ""
			m.st.Mode, m.st.Prompt = ModeConfirm, fmt.Sprintf("Push %d commits to %s?", len(s.Ahead), s.Upstream)
		} else {
			m.pendingX, m.pendingI = "ship.pr", title
			m.st.Mode, m.st.Prompt = ModeConfirm, "Push to "+s.PRBranch+" and open a pull request?"
		}
	}
	return m, nil
}

// fetchTodayEvery keeps done today fresh while the app shows, every ten seconds.
func (m *Model) fetchTodayEvery() tea.Cmd {
	if !m.appOn() || m.now().Sub(m.tdLast) < 10*time.Second {
		return nil
	}
	m.tdLast = m.now()
	return m.fetchToday()
}
