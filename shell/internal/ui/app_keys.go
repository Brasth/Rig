package ui

import (
	"bufio"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/Brasth/Rig/shell/internal/view"
)

type filesMsg struct{ files []string }

type fileMsg struct {
	path  string
	lines []string
	err   error
}

// SetApp opens the board as the app (the design's ADE screens) for the repository at repo.
func (m *Model) SetApp(repo string) {
	m.st.App = &AppState{Repo: repo, Branch: gitBranch(repo), Tab: 1}
	m.st.Tab = SessionTab
	if !m.themeFixed && ApplyTheme("light") { // the design's default; a saved theme replaces it when it loads
		m.st.Theme = "light"
	}
	m.noticeNewJobs() // the helpers already at work are not new
}

func (m *Model) appOn() bool { return m.st.App != nil }

// gitBranch reads the checked-out branch from .git/HEAD; empty for a detached head or no repository.
func gitBranch(repo string) string {
	data, err := os.ReadFile(filepath.Join(repo, ".git", "HEAD"))
	if err != nil {
		return ""
	}
	head := strings.TrimSpace(string(data))
	if name, ok := strings.CutPrefix(head, "ref: refs/heads/"); ok {
		return name
	}
	return ""
}

// loadFiles lists the repository's tracked files once, for Ctrl-K.
func (m *Model) loadFiles() tea.Cmd {
	if len(m.st.App.Files) > 0 {
		return nil
	}
	repo := m.st.App.Repo
	return func() tea.Msg {
		out, err := exec.Command("git", "-C", repo, "ls-files").Output()
		if err != nil {
			return filesMsg{}
		}
		var files []string
		sc := bufio.NewScanner(strings.NewReader(string(out)))
		for sc.Scan() && len(files) < 20000 {
			files = append(files, sc.Text())
		}
		return filesMsg{files: files}
	}
}

// openFile reads one repository file for the code tab (only files inside the repository, at most 5000 lines).
func (m *Model) openFile(rel string) tea.Cmd {
	repo := m.st.App.Repo
	return func() tea.Msg {
		path := filepath.Join(repo, filepath.Clean("/"+rel))
		data, err := os.ReadFile(path)
		if err != nil {
			return fileMsg{path: rel, err: err}
		}
		lines := strings.Split(strings.ReplaceAll(string(data), "\r\n", "\n"), "\n")
		if len(lines) > 5000 {
			lines = lines[:5000]
		}
		return fileMsg{path: rel, lines: lines}
	}
}

// appSend sends the box's text to the parent, or runs it as a / command.
func (m *Model) appSend(text string) tea.Cmd {
	text = strings.TrimSpace(text)
	if text == "" {
		return nil
	}
	if strings.HasPrefix(text, "/") {
		if cmd, ok := m.runSlash(text); ok {
			m.st.Mode, m.st.InputLabel, m.inputFor = ModeNormal, "", ""
			if m.st.HelpOpen {
				m.st.HelpOpen, m.st.App.Help = false, true
			}
			return cmd
		}
	}
	m.st.Prompt, m.st.SlashSel, m.st.Message = "", 0, "sending…"
	m.st.App.Tab, m.st.App.Thread = 1, ""
	src, session, parent := m.src, m.chatID, m.parentName
	return func() tea.Msg {
		started, err := src.ChatSend(text, session, parent, 10*time.Second)
		return chatSentMsg{started: started, err: err, sent: text}
	}
}

// answer allows (1) or denies (2) what the conversation shows waiting: the parent's own request, else a helper's.
func (m *Model) answer(allow bool) (tea.Model, tea.Cmd) {
	key := "d"
	if allow {
		key = "a"
	}
	if m.st.Chat != nil && m.st.Chat.Ask != nil {
		return m.answerAsk(key)
	}
	if job := waitingJob(m.view); job != nil {
		method, word := "job.deny", "deny"
		if allow {
			method, word = "job.allow", "allow"
		}
		m.st.Message = word + " requested " + job.ID
		return m, m.act(method, map[string]any{"id": job.ID}, false)
	}
	return m, nil
}

func (m *Model) askStopJob(r view.Row) {
	m.pendingX, m.pendingI = "job.cancel", r.ID
	m.st.Mode, m.st.Prompt = ModeConfirm, "Stop "+r.ID+" "+clean(r.Title)+"?"
}

// appKey is the app's keyboard. The box has the keyboard on the conversation, as in codex; overlays take it while open.
func (m *Model) appKey(k tea.KeyMsg) (tea.Model, tea.Cmd) {
	a := m.st.App
	key := k.String()
	if m.st.Mode == ModeConfirm {
		return m.confirmKey(k)
	}
	if k.Type == tea.KeyCtrlC {
		if m.st.Prompt != "" {
			m.st.Prompt, m.st.SlashSel = "", 0
			return m, nil
		}
		return m, tea.Quit
	}
	m.st.Message = ""
	switch {
	case a.Help:
		if key == "esc" || key == "?" || key == "q" || key == "enter" {
			a.Help = false
		}
		return m, nil
	case a.Welcome:
		return m.welcomeKey(key)
	case a.Palette != nil:
		return m.paletteKey(k)
	case a.NewJob != nil:
		job := *a.NewJob
		switch key {
		case "enter":
			a.NewJob, a.Thread, a.Panel = nil, job.ID, false
			m.lgLast = time.Time{}
			return m, m.fetchLog(true)
		case "s":
			a.NewJob = nil
			m.askStopJob(job)
		case "t", "esc":
			a.NewJob = nil
		}
		return m, nil
	case a.Panel:
		jobs := activeJobs(m.view)
		switch key {
		case "up", "k":
			a.PanelSel = clampSel(a.PanelSel-1, len(jobs))
		case "down", "j":
			a.PanelSel = clampSel(a.PanelSel+1, len(jobs))
		case "enter":
			if len(jobs) > 0 {
				a.Thread, a.Panel = jobs[clampSel(a.PanelSel, len(jobs))].ID, false
				m.st.Log, m.st.LogE, m.lgLast = nil, "", time.Time{}
				return m, m.fetchLog(true)
			}
		case "s":
			if len(jobs) > 0 {
				m.askStopJob(jobs[clampSel(a.PanelSel, len(jobs))])
			}
		case "tab", "esc":
			a.Panel = false
		}
		return m, nil
	}
	switch key {
	case "ctrl+k":
		a.Palette = &Palette{}
		return m, m.loadFiles()
	case "tab":
		if matches := slashMatches(m.st.Prompt); len(matches) > 0 {
			sel := clampSel(m.st.SlashSel, len(matches))
			m.st.Prompt, m.st.SlashSel = matches[sel].Name, 0
			if matches[sel].Args != "" {
				m.st.Prompt += " "
			}
			return m, nil
		}
		a.Panel = true
		return m, nil
	case "esc":
		switch {
		case m.st.Prompt != "":
			m.st.Prompt, m.st.SlashSel = "", 0
		case a.Thread != "":
			a.Thread = ""
		case a.Tab != 1:
			a.Tab = 1
		}
		return m, nil
	case "pgup":
		m.st.ChatUp += 8
		return m, nil
	case "pgdown":
		m.st.ChatUp = max(0, m.st.ChatUp-8)
		return m, nil
	}
	if a.Thread != "" && key == "s" {
		for _, r := range m.view.Rows("Jobs") {
			if r.ID == a.Thread {
				m.askStopJob(r)
			}
		}
		return m, nil
	}
	if a.Tab != 1 {
		return m.appTabKey(key)
	}
	matches := slashMatches(m.st.Prompt)
	switch k.Type {
	case tea.KeyUp, tea.KeyDown:
		if len(matches) > 0 {
			step := 1
			if k.Type == tea.KeyUp {
				step = -1
			}
			m.st.SlashSel = clampSel(clampSel(m.st.SlashSel, len(matches))+step, len(matches))
		}
		return m, nil
	case tea.KeyEnter:
		if len(matches) > 0 {
			sel := matches[clampSel(m.st.SlashSel, len(matches))]
			if sel.Args != "" {
				m.st.Prompt, m.st.SlashSel = sel.Name+" ", 0
				return m, nil
			}
			return m, m.appSend(sel.Name)
		}
		return m, m.appSend(m.st.Prompt)
	case tea.KeyBackspace:
		if r := []rune(m.st.Prompt); len(r) > 0 {
			m.st.Prompt = string(r[:len(r)-1])
		}
		m.st.SlashSel = 0
		return m, nil
	case tea.KeyCtrlU:
		m.st.Prompt = ""
		return m, nil
	case tea.KeyRunes, tea.KeySpace:
		if m.st.Prompt == "" && !k.Paste && k.Type == tea.KeyRunes && len(k.Runes) == 1 {
			// with the box empty, the design's single keys: the answer chips, the suggestions, the tabs, help
			asking := (m.st.Chat != nil && m.st.Chat.Ask != nil) || waitingJob(m.view) != nil
			switch r := k.Runes[0]; {
			case m.st.Chat == nil && r >= '1' && r <= '4':
				return m, m.appSend(Suggestions[r-'1'])
			case asking && (r == '1' || r == '2'):
				return m.answer(r == '1')
			case r == '2' || r == '3':
				a.Tab = int(r - '0')
				return m, m.fetchDock(true)
			case r == '?':
				a.Help = true
				return m, nil
			}
		}
		m.st.Prompt += string(k.Runes)
		m.st.SlashSel = 0
	}
	return m, nil
}

// appTabKey is the changes and code tabs' keys.
func (m *Model) appTabKey(key string) (tea.Model, tea.Cmd) {
	a := m.st.App
	switch key {
	case "1", "2", "3":
		a.Tab = int(key[0] - '0')
		return m, m.fetchDock(true)
	case "up", "k":
		if a.Tab == 3 {
			a.FileOff = max(0, a.FileOff-1)
			return m, nil
		}
		m.move(-1)
		return m, m.fetchDock(true)
	case "down", "j":
		if a.Tab == 3 {
			a.FileOff++
			return m, nil
		}
		m.move(1)
		return m, m.fetchDock(true)
	case "pgdown", " ":
		a.FileOff += 20
	case "q":
		return m, tea.Quit
	case "?":
		a.Help = true
	}
	return m, nil
}

func (m *Model) welcomeKey(key string) (tea.Model, tea.Cmd) {
	a := m.st.App
	pk := m.st.Picker
	if pk == nil {
		a.Welcome = false
		return m, nil
	}
	installed := []int{}
	for i, it := range pk.Items {
		if it.Installed {
			installed = append(installed, i)
		}
	}
	at := 0
	for i, idx := range installed {
		if idx == a.WelcomeSel {
			at = i
		}
	}
	switch key {
	case "up", "k":
		if len(installed) > 0 {
			a.WelcomeSel = installed[max(0, at-1)]
		}
	case "down", "j":
		if len(installed) > 0 {
			a.WelcomeSel = installed[min(len(installed)-1, at+1)]
		}
	case "enter":
		a.Welcome = false
		if len(installed) == 0 {
			m.st.Picker = nil
			return m, nil
		}
		pk.Sel = a.WelcomeSel
		return m.pickerKey(tea.KeyMsg{Type: tea.KeyEnter})
	case "esc":
		a.Welcome, m.st.Picker = false, nil
	}
	return m, nil
}

// openWelcome shows "Who do you want to talk to?" on the parent in use.
func (m *Model) openWelcome() {
	m.openPicker()
	m.st.App.Welcome = true
	m.st.App.WelcomeSel = m.st.Picker.Sel
	if len(m.st.Picker.Items) > 0 && !m.st.Picker.Items[m.st.Picker.Sel].Installed {
		for i, it := range m.st.Picker.Items {
			if it.Installed {
				m.st.App.WelcomeSel = i
				break
			}
		}
	}
	m.st.Mode, m.st.InputLabel, m.inputFor = ModeNormal, "", ""
}

func (m *Model) paletteKey(k tea.KeyMsg) (tea.Model, tea.Cmd) {
	a := m.st.App
	p := a.Palette
	items := paletteItems(m.st)
	switch k.Type {
	case tea.KeyEsc, tea.KeyCtrlK:
		a.Palette = nil
	case tea.KeyUp:
		p.Sel = clampSel(clampSel(p.Sel, len(items))-1, len(items))
	case tea.KeyDown:
		p.Sel = clampSel(clampSel(p.Sel, len(items))+1, len(items))
	case tea.KeyBackspace:
		if r := []rune(p.Query); len(r) > 0 {
			p.Query = string(r[:len(r)-1])
		}
		p.Sel = 0
	case tea.KeyRunes, tea.KeySpace:
		p.Query += string(k.Runes)
		p.Sel = 0
	case tea.KeyEnter:
		if len(items) == 0 {
			return m, nil
		}
		a.Palette = nil
		return m, m.runPalette(items[clampSel(p.Sel, len(items))], p.Query)
	}
	return m, nil
}

func (m *Model) runPalette(it paletteItem, query string) tea.Cmd {
	a := m.st.App
	switch {
	case it.Action == "ask":
		return m.appSend(query)
	case strings.HasPrefix(it.Action, "file:"):
		rel := strings.TrimPrefix(it.Action, "file:")
		a.Tab, a.File, a.FileLines, a.FileOff = 3, rel, nil, 0
		return m.openFile(rel)
	}
	switch it.Action {
	case "parent":
		m.openWelcome()
	case "new":
		m.chatID, m.st.Chat, m.st.ChatUp = "", nil, 0
		a.Tab = 1
	case "queue":
		m.st.Prompt, a.Tab = "/queue ", 1
	case "theme":
		names := ThemeNames()
		next := names[0]
		for i, n := range names {
			if n == m.st.Theme || (m.st.Theme == "" && n == "rig") {
				next = names[(i+1)%len(names)]
			}
		}
		return m.switchTheme(next, m.st.ThemeScope)
	case "env-up":
		m.pendingX, m.pendingI = "env.up", "up"
		m.st.Mode, m.st.Prompt = ModeConfirm, "Start this project's container (rig env up)?"
	case "panel":
		a.Panel = true
	case "board":
		m.st.App = nil
		m.st.Tab, m.st.Focus = SessionTab, ""
		return m.goTab(SessionTab)
	case "help":
		a.Help = true
	case "quit":
		return tea.Quit
	}
	return nil
}

// noticeNewJobs shows the design's "claude started codex" card for a helper that appeared since the last snapshot.
func (m *Model) noticeNewJobs() {
	if !m.appOn() || m.view == nil {
		return
	}
	first := m.seenJobs == nil
	if first {
		m.seenJobs = map[string]bool{}
	}
	for _, r := range activeJobs(m.view) {
		if m.seenJobs[r.ID] {
			continue
		}
		m.seenJobs[r.ID] = true
		if !first && m.st.App.NewJob == nil {
			r := r
			m.st.App.NewJob = &r
		}
	}
}
