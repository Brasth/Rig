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
	err  error
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
	src := m.src
	return func() tea.Msg {
		_, err := src.Act(method, params, 3*time.Minute)
		return appliedMsg{text: done, err: err}
	}
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
	cw := min(appColumn, w-8)
	lines := []string{spread(rigBarLeft(st, nil)+st.faint(" › done today"), "", "", w), fit("", w)}
	body := []string{"", st.bold("Done today") + "  " + st.faint("applied, held and undone"), ""}
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
			left := st.faint(when) + "  " + st.dot(r.Worker) + " " + fit(clean(r.Title), cw-34)
			right := st.paint(stateColour(r.State), state)
			row := left + strings.Repeat(" ", max(2, cw-2-lipgloss.Width(left)-lipgloss.Width(right))) + right
			if i == sel {
				row = st.bold("›") + " " + row
				if st.Styled {
					row = tint(fit(row, cw), colSelBg)
				}
			} else {
				row = "  " + row
			}
			body = append(body, row)
		}
		r := st.Today.Rows[sel]
		body = append(body, "", "", st.bold(clean(r.Title)))
		switch r.State {
		case "held":
			body = append(body, st.dim("held: "+parent+"'s acceptance will not apply it until you release it (h)"))
			keys = [][2]string{{"h", "release"}, {"esc", "back"}}
		case "undone":
			body = append(body, st.dim(fmt.Sprintf("%d files +%d −%d · undone; r applies it again", r.Files, r.Added, r.Deleted)))
			keys = [][2]string{{"r", "redo"}, {"esc", "back"}}
		default:
			body = append(body, st.dim(fmt.Sprintf("%d files ", r.Files))+st.paint(colGreen, fmt.Sprintf("+%d", r.Added))+" "+st.paint(colRed, fmt.Sprintf("−%d", r.Deleted)),
				st.faint("on "+r.Branch+" as "+r.Commit+" · undo reverts it; the history keeps both"))
			keys = [][2]string{{"u", "undo"}, {"esc", "back"}}
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
	cw := min(appColumn, w-8)
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
		for i, c := range s.Ahead {
			if i == 12 {
				body = append(body, st.faint(fmt.Sprintf("   +%d more", len(s.Ahead)-12)))
				break
			}
			subject := clean(c.Subject)
			who := ""
			if st.Today != nil {
				for _, r := range st.Today.Rows {
					if strings.HasPrefix(c.Commit, r.Commit) && r.Commit != "" {
						who, subject = r.Worker, clean(r.Title)
					}
				}
			}
			mark := "  "
			if who != "" {
				mark = st.dot(who) + " "
			}
			left := mark + fit(subject, cw-14)
			body = append(body, left+strings.Repeat(" ", max(2, cw-2-lipgloss.Width(left)-len(c.Commit)))+st.faint(c.Commit))
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
		body = append(body, "", st.bold("›")+" "+chip("open pull request", !sh.Push)+"   "+chip("push to "+s.Branch, sh.Push))
		if !sh.Push {
			title := sh.Title
			if title == "" {
				title = s.Title
			}
			edit := st.faint("e to edit")
			if sh.Editing {
				title, edit = title+"▏", st.faint("⏎ done")
			}
			body = append(body, "    "+st.faint("branch ")+s.PRBranch, "", st.faint("title  ")+title, "       "+edit)
			if !s.GH {
				body = append(body, "", st.paint(colAmber, "opening a pull request needs the gh CLI; push still works"))
			}
		}
		body = append(body, "", "", st.faint("agents never push · you do"))
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
	case "h":
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
