package ui

import (
	"fmt"
	"regexp"
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"

	"github.com/Brasth/Rig/shell/internal/view"
)

// The design's first-run and problem screens: who you talk to (1 of 3), who it can hand work to (2 of 3), where
// agents work (3 of 3), no agent on this computer, the parent signed out, and the sandbox stopped.

// Onboarding is the first-run steps after "Who do you want to talk to?".
type Onboarding struct {
	Step       int // 2 helpers, 3 sandbox
	HelperSel  int
	HelperOn   map[string]bool
	At         int // helpers at once
	SandboxSel int // 0 a sandbox (dev container), 1 this machine
}

// installCommands are how each parent CLI installs, shown and run (after a confirmation) on the no-agents screen.
var installCommands = []struct{ Name, Note, Command string }{
	{"claude", "claude pro/max", "npm install -g @anthropic-ai/claude-code"},
	{"codex", "chatgpt", "npm install -g @openai/codex"},
	{"opencode", "free", "npm install -g opencode-ai"},
}

func stepDots(st State, step int) string {
	var dots []string
	for i := 1; i <= 3; i++ {
		if i == step {
			dots = append(dots, st.bold("●"))
		} else if i < step {
			dots = append(dots, st.faint("●"))
		} else {
			dots = append(dots, st.faint("○"))
		}
	}
	return strings.Join(dots, " ") + "   " + st.faint(fmt.Sprintf("%d of 3", step)) + "   "
}

// rigBar is the first-run screens' top bar: the mark and "rig", and the step dots when there are steps.
func rigBar(st State, w, step int) string {
	logo := st.bold("r") + st.paint("#6d4aff", "•") + st.paint("#0d9488", "•") + st.paint("#db2777", "•")
	right := ""
	if step > 0 {
		right = stepDots(st, step)
	}
	return spread("   "+logo+"   "+st.bold("rig"), "", right, w)
}

// centered lays body in a column of cw in a w by h screen under the bar, with the footer on the last row.
func centered(st State, bar string, body []string, cw, w, h int, keys [][2]string) []string {
	lines := []string{bar, fit("", w)}
	lines = append(lines, indentAll(body, (w-cw)/2, w)...)
	for len(lines) < h-1 {
		lines = append(lines, fit("", w))
	}
	return append(lines[:h-1], keyHints(st, keys, w))
}

// helpersScreen is "Who can claude hand work to?": the installed helpers, on or off, and how many at once.
func helpersScreen(st State, w, h int) []string {
	o := st.App.Onboard
	parent := appParent(st)
	cw := min(60, w-8)
	body := []string{"", "", st.bold("Who can " + parent + " hand work to?"), st.dim("Each helper works in its own copy of the repo."), "", ""}
	names := helperNames(st)
	for i, name := range names {
		box := st.faint("[ ]")
		if o.HelperOn[name] {
			box = st.bold("[x]")
		}
		label := name
		if name == parent {
			label += "  " + st.faint("second session")
		}
		row := "   " + st.tile(name) + "  " + label
		if i == o.HelperSel {
			row = " " + st.bold("›") + " " + st.tile(name) + "  " + st.bold(label)
		}
		target := cw - 9 // inside the box's border, or one cell further right without it, so the [x] line up
		if i != o.HelperSel {
			target = cw - 8
		}
		row += strings.Repeat(" ", max(2, target-lipgloss.Width(row))) + box
		rows := []string{row}
		if i == o.HelperSel {
			rows = roundBox(st, rows, cw, true)
		}
		for _, l := range rows {
			body = append(body, st.zone(fmt.Sprintf("row:helper:%d", i), l))
		}
		body = append(body, "")
	}
	if len(names) == 0 {
		body = append(body, st.dim("No other agent is installed here; "+parent+" works on its own."), "")
	}
	body = append(body, "", st.dim("   up to ")+st.bold(fmt.Sprint(o.At))+st.dim(" at once · "+parent+" approves their commands"))
	return centered(st, rigBar(st, w, 2), body, cw, w, h, [][2]string{{"space", "toggle"}, {"1-4", "at once"}, {"⏎", "continue"}, {"esc", "back"}})
}

func helperNames(st State) []string {
	if st.AppSettings == nil {
		return nil
	}
	var out []string
	for _, name := range sortedWorkers(st.AppSettings.Workers) {
		for _, n := range st.AppSettings.Installed {
			if n == name {
				out = append(out, name)
			}
		}
	}
	return out
}

// sandboxScreen is "Where should agents work?": a sandbox (the dev container) or this machine.
func sandboxScreen(st State, w, h int) []string {
	o := st.App.Onboard
	cw := min(60, w-8)
	repo := appProject(nil, st.App)
	kind := "no dev container yet: Rig makes one"
	if st.Workspace != nil {
		for _, sec := range st.Workspace.Sections {
			for _, l := range sec.Lines {
				if strings.HasPrefix(strings.TrimSpace(l.Text), "kind") && strings.Contains(l.Text, "devcontainer") {
					kind = "it has a dev container"
				}
			}
		}
	}
	body := []string{"", "", st.bold("Where should agents work?"), st.dim(repo + ": " + kind + "."), "", ""}
	option := func(i int, title, right string, notes []string) {
		top := "   " + title
		if i == o.SandboxSel {
			top = " " + st.bold("› "+title)
		}
		if right != "" {
			top += strings.Repeat(" ", max(2, cw-4-lipgloss.Width(top)-lipgloss.Width(right))) + right
		}
		rows := []string{top}
		for _, n := range notes {
			rows = append(rows, "   "+n)
		}
		if i == o.SandboxSel {
			rows = roundBox(st, rows, cw, true)
		}
		for _, l := range rows {
			body = append(body, st.zone(fmt.Sprintf("row:sandbox:%d", i), l))
		}
		body = append(body, "")
	}
	option(0, "New sandbox", st.paint(colGreen, "recommended"), []string{st.dim("a dev container · your repo inside, nothing else")})
	option(1, "This machine", "", []string{st.paint(colAmber, "! no boundary around your files")})
	body = append(body, "", st.faint("   rig env up starts it · only .rig/ is written here"))
	enter := "start sandbox"
	if o.SandboxSel == 1 {
		enter = "use this machine"
	}
	return centered(st, rigBar(st, w, 3), body, cw, w, h, [][2]string{{"↑↓", "choose"}, {"⏎", enter}, {"esc", "back"}})
}

// noAgentsScreen is "Let's get you an agent": nothing installed, and how to install one.
func noAgentsScreen(st State, w, h int) []string {
	cw := min(70, w-8)
	body := []string{"", "", st.bold("Let's get you an agent") + "  " + st.faint("1 of 3"),
		st.dim("no agent found on this computer · looked for claude codex opencode grok pi omp agy"), "", ""}
	sel := clampSel(st.App.InstallSel, len(installCommands))
	head := st.bold("› 1  install one for me") + "  " + st.paint(colGreen, "recommended")
	if st.Styled {
		head = tint(fit(head, cw), colSelBg)
	}
	var agents []string
	for i, c := range installCommands {
		name := c.Name
		if i == sel {
			name = st.bold(name)
		}
		agents = append(agents, st.zone(fmt.Sprintf("row:installagent:%d", i), st.tile(c.Name)+" "+name+"  "+st.faint(c.Note)))
	}
	body = append(body, st.zone("key:⏎", head), "     "+strings.Join(agents, "   "), "     "+st.faint("on this computer · $ "+installCommands[sel].Command),
		"", "", st.faint("one agent is enough: it talks to you and hands work to helpers"))
	return centered(st, rigBar(st, w, 0), body, cw, w, h, [][2]string{{"←→", "agent"}, {"⏎", "install " + installCommands[sel].Name}, {"r", "scan again"}})
}

var signedOut = regexp.MustCompile(`(?i)not logged in|log ?in again|please (run /)?login|sign(ed)? (in|out)|unauthori[sz]ed|authentication|invalid api key|401`)

// parentSignedOut: the parent's last turn failed because it is not signed in.
func parentSignedOut(st State) bool {
	if st.Chat == nil || len(st.Chat.Entries) == 0 {
		return false
	}
	last := st.Chat.Entries[len(st.Chat.Entries)-1]
	return last.Role == "error" && signedOut.MatchString(last.Text)
}

// parentLostScreen is the design's "claude is signed out": sign in again, or hand over to another parent.
func parentLostScreen(v *view.View, st State, w, h int) []string {
	parent := appParent(st)
	cw := min(70, w-8)
	body := []string{"", "", st.paint(colRed, "● "+parent+" is signed out"),
		st.bold("Nothing gets checked or applied until a parent is back"), st.dim("helpers keep working in their copies"), "", ""}
	body = append(body, st.zone("key:1", st.dim("1")+"  "+st.bold("sign in to "+parent+" again")))
	n := 2
	for _, other := range otherParents(st) {
		body = append(body, st.zone(fmt.Sprintf("key:%d", n), st.dim(fmt.Sprint(n))+"  hand over to "+st.tile(other)+" "+other+"  "+
			st.faint("installed · starts a new conversation")))
		n++
		if n > 4 {
			break
		}
	}
	body = append(body, "", st.faint("    the queue, today's applies and the helpers' work stay with the project"))
	if jobs := activeJobs(v); len(jobs) > 0 {
		var parts []string
		for _, r := range jobs {
			word, colour := "working", colGreen
			if r.Ask {
				word, colour = "waiting for a check", colFaint
			}
			parts = append(parts, st.tile(r.Worker)+" "+st.paint(colour, word))
		}
		body = append(body, "", "", strings.Join(parts, "   "))
	}
	bar := fit(rigBarLeft(st, v)+"   "+st.paint(colRed, "● "+parent+" signed out"), w)
	return centered(st, bar, body, cw, w, h, [][2]string{{"1-4", "choose"}, {"esc", "back"}})
}

func rigBarLeft(st State, v *view.View) string {
	logo := st.bold("r") + st.paint("#6d4aff", "•") + st.paint("#0d9488", "•") + st.paint("#db2777", "•")
	left := "   " + logo + "   " + st.bold(appProject(v, st.App))
	if st.App.Branch != "" {
		left += st.faint(" / " + st.App.Branch)
	}
	return left
}

func otherParents(st State) []string {
	var out []string
	if st.Picker != nil {
		for _, it := range st.Picker.Items {
			if it.Installed && it.Name != appParent(st) {
				out = append(out, it.Name)
			}
		}
	}
	return out
}

// sandboxDown: the project works in a dev container, and the workspace says it is not running.
func sandboxDown(st State) bool {
	if st.AppSettings == nil || st.AppSettings.Sandbox != "devcontainer" || st.Workspace == nil {
		return false
	}
	for _, sec := range st.Workspace.Sections {
		for _, l := range sec.Lines {
			t := strings.TrimSpace(l.Text)
			if strings.HasPrefix(t, "● running") || strings.HasPrefix(t, "● up") {
				return false
			}
		}
	}
	return true
}

// sandboxProblemScreen is the design's "sandbox stopped": what the doctor found, and the ways out.
func sandboxProblemScreen(v *view.View, st State, w, h int) []string {
	cw := min(70, w-8)
	worktrees := 0
	if st.Workspace != nil {
		for _, sec := range st.Workspace.Brief {
			if sec.Title == "Worktrees" {
				worktrees = max(0, len(sec.Lines)-1) // the base checkout and one per helper
			}
		}
	}
	safe := "your work is safe"
	if worktrees > 0 {
		safe += fmt.Sprintf(" · %d worktrees saved", worktrees)
	}
	body := []string{"", "", st.paint(colRed, "● sandbox stopped"),
		st.bold("The dev container isn't running, so agents are paused"), st.dim(safe), "", ""}
	if st.Workspace != nil {
		for _, sec := range st.Workspace.Sections {
			if sec.Title != "Workspace" && sec.Title != "Doctor" && sec.Title != "Recovery" {
				continue
			}
			for _, l := range sec.Lines {
				t := strings.TrimSpace(sanitize(l.Text))
				if t == "" {
					continue
				}
				bad := l.Tone == "attention" || strings.HasPrefix(t, "!")
				name, value, _ := strings.Cut(strings.TrimPrefix(t, "! "), "  ")
				left, right := st.paint(colGreen, "✓ ")+name, st.faint(strings.TrimSpace(value))
				if bad {
					left, right = st.paint(colRed, "✗ ")+name, st.paint(colRed, strings.TrimSpace(value))
				}
				body = append(body, left+strings.Repeat(" ", max(2, cw-2-lipgloss.Width(left)-lipgloss.Width(right)))+right)
			}
		}
	}
	body = append(body, "", "", st.dim("1")+"  "+st.bold("start it")+"  "+st.faint("rig env up"))
	if st.Workspace != nil && st.Workspace.Recovery != nil && !st.Workspace.Recovery.Ready && st.Workspace.Recovery.Safe {
		body = append(body, st.dim("2")+"  let dc-cli fix it  "+st.faint(clean(st.Workspace.Recovery.Command)))
	} else {
		body = append(body, st.faint("2  let dc-cli fix it  (no safe fix proposed)"))
	}
	body = append(body, st.dim("3")+"  "+st.paint(colAmber, "run on this machine instead"))
	if jobs := activeJobs(v); len(jobs) > 0 {
		paused := st.faint("paused") + "  "
		for _, r := range jobs {
			paused += st.tile(r.Worker) + " "
		}
		body = append(body, "", "", paused)
	}
	bar := fit(rigBarLeft(st, v)+"   "+st.paint(colRed, "● sandbox stopped"), w)
	return centered(st, bar, body, cw, w, h, [][2]string{{"1-3", "choose"}, {"l", "doctor log"}, {"esc", "back"}})
}

// onboardKey is the helpers and sandbox steps' keys.
func (m *Model) onboardKey(key string) (tea.Model, tea.Cmd) {
	a := m.st.App
	o := a.Onboard
	if o.Step == 2 {
		names := helperNames(m.st)
		switch key {
		case "up", "k":
			o.HelperSel = clampSel(o.HelperSel-1, len(names))
		case "down", "j":
			o.HelperSel = clampSel(o.HelperSel+1, len(names))
		case " ", "x":
			if len(names) > 0 {
				n := names[clampSel(o.HelperSel, len(names))]
				o.HelperOn[n] = !o.HelperOn[n]
			}
		case "1", "2", "3", "4":
			o.At = int(key[0] - '0')
		case "enter":
			type change struct{ key, value string }
			var changes []change // saved one after another: they all write the same harness
			for _, n := range names {
				if m.st.AppSettings.Workers[n] != o.HelperOn[n] {
					changes = append(changes, change{"workers." + n, fmt.Sprint(o.HelperOn[n])})
				}
			}
			if o.At != m.st.AppSettings.MaxRunning {
				changes = append(changes, change{"queue.max_running", fmt.Sprint(o.At)})
			}
			o.Step = 3
			if len(changes) == 0 {
				return m, nil
			}
			src := m.src
			return m, func() tea.Msg {
				var last *view.AppSettings
				for _, c := range changes {
					s, err := src.SetSetting(c.key, c.value, "repo", callTimeout)
					if err != nil {
						return appSettingsMsg{err: err}
					}
					last = s
				}
				return appSettingsMsg{s: last}
			}
		case "esc":
			o.Step = 0
			m.openWelcome()
			m.st.App.Onboard = o
		}
		return m, nil
	}
	switch key {
	case "up", "k", "down", "j":
		o.SandboxSel = 1 - o.SandboxSel
	case "esc":
		o.Step = 2
	case "enter":
		a.Onboard = nil
		provider := "devcontainer"
		if o.SandboxSel == 1 {
			provider = "host"
		}
		src := m.src
		save := func() tea.Msg {
			s, err := src.SetSetting("sandbox.provider", provider, "repo", callTimeout)
			return appSettingsMsg{s: s, err: err}
		}
		if provider == "devcontainer" {
			m.st.Message = "starting the sandbox (rig env up)"
			return m, tea.Batch(save, m.exec([]string{"rig", "env", "up"}, func(err error) tea.Msg { return shellDoneMsg{err: err} }))
		}
		return m, save
	}
	return m, nil
}

// startOnboarding moves from the parent's choice to the helpers step, with the settings as they are.
func (m *Model) startOnboarding() {
	s := m.st.AppSettings
	if s == nil {
		return
	}
	on := map[string]bool{}
	for n, v := range s.Workers {
		on[n] = v
	}
	sel := 0
	if s.Sandbox == "host" {
		sel = 0
	}
	m.st.App.Onboard = &Onboarding{Step: 2, HelperOn: on, At: max(1, min(4, s.MaxRunning)), SandboxSel: sel}
}

// problemKey answers the no-agents, signed-out and sandbox-stopped screens.
func (m *Model) problemKey(screen, key string) (tea.Model, tea.Cmd) {
	a := m.st.App
	switch screen {
	case "noagents":
		switch key {
		case "1", "2", "3":
			a.InstallSel = int(key[0] - '1')
		case "up", "k", "left", "h":
			a.InstallSel = clampSel(a.InstallSel-1, len(installCommands))
		case "down", "j", "right", "l":
			a.InstallSel = clampSel(a.InstallSel+1, len(installCommands))
		case "r":
			m.csLast, m.csBusy = time.Time{}, false
			return m, m.fetchChatStatus()
		case "enter":
			c := installCommands[clampSel(a.InstallSel, len(installCommands))]
			m.pendingX, m.pendingI = "install", c.Command
			m.st.Mode, m.st.Prompt = ModeConfirm, "Install "+c.Name+" on this computer ("+c.Command+")?"
		case "q", "esc":
			return m, tea.Quit
		}
	case "signedout":
		switch key {
		case "1":
			parent := appParent(m.st)
			return m, m.exec([]string{parent}, func(err error) tea.Msg { return shellDoneMsg{err: err} })
		case "2", "3", "4":
			others := otherParents(m.st)
			if i := int(key[0] - '2'); i < len(others) {
				name := others[i]
				src := m.src
				m.chatID, m.st.Chat, m.chatAdopted = "", nil, true
				return m, func() tea.Msg { return parentSetMsg{name: name, err: src.ChatSetParent(name, callTimeout)} }
			}
		case "esc":
			a.ProblemSeen = "signedout"
		}
	case "sandbox":
		switch key {
		case "1":
			a.ProblemSeen = "sandbox"
			m.pendingX, m.pendingI = "env.up", "up"
			m.st.Mode, m.st.Prompt = ModeConfirm, "Start this project's container (rig env up)?"
		case "2":
			if ws := m.st.Workspace; ws != nil && ws.Recovery != nil && ws.Recovery.Safe && !ws.Recovery.Ready {
				a.ProblemSeen = "sandbox"
				m.pendingX, m.pendingI = "env.recover", ws.Recovery.ID
				m.st.Mode, m.st.Prompt = ModeConfirm, "Apply "+clean(ws.Recovery.Apply)+"?"
			}
		case "3":
			a.ProblemSeen = "sandbox"
			src := m.src
			return m, func() tea.Msg {
				s, err := src.SetSetting("sandbox.provider", "host", "repo", callTimeout)
				return appSettingsMsg{s: s, err: err}
			}
		case "l":
			return m, m.exec([]string{"sh", "-c", "rig doctor; printf '\\n⏎ goes back to rig shell '; read _"}, func(err error) tea.Msg { return shellDoneMsg{err: err} })
		case "esc":
			a.ProblemSeen = "sandbox"
		}
	}
	return m, nil
}

// problemScreen is which first-run or problem screen shows now, if any.
func problemScreen(st State) string {
	a := st.App
	switch {
	case a.Welcome || a.Onboard != nil:
		return ""
	case st.ChatOn && st.Picker == nil && noAgentInstalled(st):
		return "noagents"
	case parentSignedOut(st) && a.ProblemSeen != "signedout":
		return "signedout"
	case sandboxDown(st) && a.ProblemSeen != "sandbox":
		return "sandbox"
	}
	return ""
}

func noAgentInstalled(st State) bool {
	return st.ChatParent == "" && st.AppSettings != nil && len(st.AppSettings.Installed) == 0
}
