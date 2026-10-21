package ui

import (
	"fmt"
	"strings"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"

	"github.com/Brasth/Rig/shell/internal/view"
)

// The design's Settings: sections across the top, rows of a label and its choices, ←→ to change. Only real settings
// are offered; the sections Rig has no settings for yet say what Rig does instead. Billing is left out.

// SettingsSections are the design's sections, without billing.
var SettingsSections = []string{"general", "agents", "routing", "approvals", "sandbox", "editor", "git", "notify", "toml"}

// SettingsScreen is where the Settings screen is.
type SettingsScreen struct {
	Section int
	Row     int
}

// setRow is one row: a label, its choices (the current one marked), or a fixed value with a note.
type setRow struct {
	Label   string
	Key     string   // the setting it changes ("" for a row that only explains)
	Options []string // the values it can take
	Names   []string // how each value is shown; the value itself when empty
	Value   string
	Note    string
	Scoped  bool // g keeps it for all repos or this repo
}

func settingRows(st State, section string) []setRow {
	s := st.AppSettings
	if s == nil {
		return nil
	}
	onoff := func(on bool) string {
		if on {
			return "true"
		}
		return "false"
	}
	switch section {
	case "general":
		parents := append([]string(nil), s.Installed...)
		parent := s.Parent
		if parent == "" {
			parent = st.ChatParent
		}
		return []setRow{
			{Label: "Theme", Key: "ui.theme", Options: s.Themes, Value: st.Theme, Scoped: true},
			{Label: "Default parent", Key: "parent", Options: parents, Value: parent, Note: "the CLI you talk to; it plans and hands work to helpers"},
			{Label: "Rig in this repo", Key: "project.enabled", Options: []string{"true", "false"}, Names: []string{"on", "off"}, Value: onoff(s.Enabled),
				Note: "off keeps history and memory, stops new work"},
		}
	case "agents":
		var rows []setRow
		for _, name := range sortedWorkers(s.Workers) {
			note := "not installed"
			for _, n := range s.Installed {
				if n == name {
					note = "installed"
				}
			}
			rows = append(rows, setRow{Label: name, Key: "workers." + name, Options: []string{"true", "false"}, Names: []string{"on", "off"},
				Value: onoff(s.Workers[name]), Note: note})
		}
		counts := []string{"1", "2", "3", "4"}
		if s.MaxRunning > 4 {
			counts = append(counts, fmt.Sprint(s.MaxRunning))
		}
		return append(rows, setRow{Label: "At the same time", Key: "queue.max_running", Options: counts, Value: fmt.Sprint(s.MaxRunning),
			Note: "helpers working at once"})
	case "routing":
		return []setRow{
			{Label: "Optimise for", Key: "routing.objective", Options: []string{"balanced", "quality", "cost", "speed"}, Value: s.Objective},
			{Label: "Routing", Value: s.RoutingMode, Note: "Rig picks the helper and model for each task; rig pick explains a choice"},
		}
	case "approvals":
		return []setRow{
			{Label: "Helper commands", Value: "the parent decides", Note: "a helper's risky command waits for the parent; it can ask you in the conversation"},
			{Label: "Your parent's own", Value: "its own settings", Note: "Rig adds no permission flags; its requests wait in the conversation (1 allows, 2 denies)"},
		}
	case "sandbox":
		return []setRow{
			{Label: "Where agents work", Key: "sandbox.provider", Options: []string{"devcontainer", "host"}, Names: []string{"dev container", "this machine"}, Value: s.Sandbox,
				Note: "a dev container keeps helpers away from your files"},
			{Label: "A separate copy per helper", Key: "worktrees.mode", Options: []string{"per-job", "off"}, Names: []string{"on", "off"}, Value: s.Worktrees,
				Note: "each writer works in its own worktree"},
			{Label: "A container per helper", Key: "worktrees.container", Options: []string{"per-job", "shared"}, Names: []string{"on", "off"}, Value: s.WorktreeContainer},
		}
	case "editor":
		editor := s.Editor
		if editor == "" {
			editor = "vi ($EDITOR is not set)"
		}
		return []setRow{
			{Label: "e opens in", Value: editor, Note: "in the code tab, e hands the file to your own editor"},
			{Label: "Terminal shell", Value: s.Shell},
		}
	case "git":
		return []setRow{
			{Label: "Shipping", Value: "always yours", Note: "agents never push; Rig commits a helper's work only when the parent accepts it"},
			{Label: "Each helper", Value: "its own branch", Note: "rig/<job> in its own worktree, merged after acceptance"},
		}
	case "notify":
		return []setRow{
			{Label: "Terminal bell", Key: "ui.bell", Options: []string{"true", "false"}, Names: []string{"on", "off"}, Value: onoff(s.Bell), Scoped: true,
				Note: "rings when a request waits for you"},
		}
	}
	return nil
}

func sortedWorkers(m map[string]bool) []string {
	order := []string{"claude", "codex", "opencode", "cursor", "grok", "pi", "omp", "agy", "devin", "mimo"}
	var out []string
	seen := map[string]bool{}
	for _, n := range order {
		if _, ok := m[n]; ok {
			out = append(out, n)
			seen[n] = true
		}
	}
	for n := range m {
		if !seen[n] {
			out = append(out, n)
		}
	}
	return out
}

// settingsScreenLines is the Settings screen.
func settingsScreenLines(v *view.View, st State, w, h int) []string {
	a := st.App
	sc := a.Settings
	section := SettingsSections[sc.Section]
	rows := settingRows(st, section)
	sel := clampSel(sc.Row, len(rows))
	scope, file := "this repo", ".rig/harness.toml"
	if s := st.AppSettings; s != nil && sel < len(rows) && rows[sel].Scoped {
		which := s.ThemeScope
		if rows[sel].Key == "ui.bell" {
			which = s.BellScope
		}
		if which != "repo" {
			scope, file = "all repos", s.Global
		}
	}
	chip := func(text string) string {
		if st.Styled {
			return lipgloss.NewStyle().Background(lipgloss.Color(colText)).Foreground(lipgloss.Color(colGround)).Bold(true).Render(" " + text + " ")
		}
		return "[" + text + "]"
	}
	repo := appProject(v, a)
	other := "all repos"
	if scope == "all repos" {
		other = repo
	}
	head := "   " + st.faint("←") + "  " + st.bold("Settings") + "   " + chip(scope) + "  " + st.faint(other) + "  " + st.faint(file)
	var tabs []string
	for i, name := range SettingsSections {
		if i == sc.Section {
			if st.Styled {
				tabs = append(tabs, lipgloss.NewStyle().Foreground(lipgloss.Color(colText)).Bold(true).Underline(true).Render(name))
			} else {
				tabs = append(tabs, "["+name+"]")
			}
		} else {
			tabs = append(tabs, st.faint(name))
		}
	}
	lines := []string{spread(head, "", "", w), center(strings.Join(tabs, "   "), w)}
	cw := min(76, w-8)
	body := []string{"", st.bold(strings.ToUpper(section[:1]) + section[1:]), ""}
	if section == "routing" {
		body[1] = st.bold("Who does what")
	}
	switch {
	case st.AppSettings == nil:
		body = append(body, st.faint("loading…"))
	case section == "toml":
		body = append(body, tomlLines(st, cw)...)
	default:
		for i, r := range rows {
			body = append(body, settingLine(st, r, i == sel, cw))
			if strings.HasPrefix(r.Key, "workers.") {
				continue // its note is on its line, and the agents sit close together as in the design
			}
			if r.Note != "" {
				body = append(body, "    "+st.dim(fit(r.Note, cw-4)))
			}
			body = append(body, "")
		}
		if section == "sandbox" && st.Workspace != nil && st.Workspace.Context != "" {
			body = append(body, st.paint(colGreen, "●")+" "+st.faint(st.Workspace.Context))
		}
	}
	lines = append(lines, indentAll(body, (w-cw)/2, w)...)
	for len(lines) < h {
		lines = append(lines, fit("", w))
	}
	return lines[:h]
}

// settingLine is one row: the label on the left, its choices (or value) on the right, the chosen one on a chip.
func settingLine(st State, r setRow, selected bool, cw int) string {
	label := r.Label
	if strings.HasPrefix(r.Key, "workers.") {
		label = st.dot(r.Label) + " " + fmt.Sprintf("%-9s", r.Label) + " " + st.faint(r.Note) // the design's "codex  chatgpt plus"
	}
	mark := "  "
	if selected {
		mark = st.bold("› ")
		label = st.bold(label)
	}
	var right string
	if len(r.Options) == 0 {
		right = st.dim(r.Value)
	} else {
		var parts []string
		for i, o := range r.Options {
			name := o
			if i < len(r.Names) {
				name = r.Names[i]
			}
			if o == r.Value {
				if st.Styled {
					parts = append(parts, lipgloss.NewStyle().Background(lipgloss.Color(colText)).Foreground(lipgloss.Color(colGround)).Bold(true).Render(" "+name+" "))
				} else {
					parts = append(parts, "["+name+"]")
				}
			} else {
				parts = append(parts, st.faint(" "+name+" "))
			}
		}
		right = strings.Join(parts, " ")
	}
	left := mark + label
	return left + strings.Repeat(" ", max(2, cw-lipgloss.Width(left)-lipgloss.Width(right))) + right
}

// tomlLines is the toml section: the harness itself, whether it is valid, and how to edit it.
func tomlLines(st State, cw int) []string {
	s := st.AppSettings
	out := []string{st.bold(s.Harness) + "  " + st.faint("this repo"), ""}
	for i, l := range strings.Split(strings.TrimRight(s.HarnessText, "\n"), "\n") {
		if i == 24 {
			out = append(out, st.faint("…"))
			break
		}
		text := sanitize(l)
		if strings.HasPrefix(strings.TrimSpace(text), "[") {
			text = st.bold(text)
		} else if strings.HasPrefix(strings.TrimSpace(text), "#") {
			text = st.faint(text)
		}
		out = append(out, st.faint(fmt.Sprintf("%3d  ", i+1))+text)
	}
	out = append(out, "")
	if len(s.Problems) == 0 {
		out = append(out, st.paint(colGreen, "✓ valid")+" "+st.faint("· E edits it in $EDITOR"))
	} else {
		for _, p := range s.Problems {
			out = append(out, st.paint(colAmber, "! "+clean(p)))
		}
	}
	return out
}

type appSettingsMsg struct {
	s   *view.AppSettings
	err error
}

func (m *Model) fetchAppSettings() tea.Cmd {
	src := m.src
	return func() tea.Msg {
		s, err := src.AppSettings(callTimeout)
		return appSettingsMsg{s: s, err: err}
	}
}

// openSettings shows the Settings screen on a section.
func (m *Model) openSettings(section string) tea.Cmd {
	sc := &SettingsScreen{}
	for i, s := range SettingsSections {
		if s == section {
			sc.Section = i
		}
	}
	m.st.App.Settings = sc
	return m.fetchAppSettings()
}

// settingsKey is the Settings screen's keys: ⇥ section, ↑↓ row, ←→ change, space toggles, g where it is kept.
func (m *Model) settingsKey(k tea.KeyMsg) (tea.Model, tea.Cmd) {
	a := m.st.App
	sc := a.Settings
	rows := settingRows(m.st, SettingsSections[sc.Section])
	switch k.String() {
	case "esc", "q":
		a.Settings = nil
	case "tab":
		sc.Section, sc.Row = (sc.Section+1)%len(SettingsSections), 0
	case "shift+tab":
		sc.Section, sc.Row = (sc.Section+len(SettingsSections)-1)%len(SettingsSections), 0
	case "up", "k":
		sc.Row = clampSel(sc.Row-1, len(rows))
	case "down", "j":
		sc.Row = clampSel(sc.Row+1, len(rows))
	case "left", "right", "h", "l", " ", "enter":
		if len(rows) == 0 {
			return m, nil
		}
		r := rows[clampSel(sc.Row, len(rows))]
		if r.Key == "" || len(r.Options) == 0 {
			return m, nil
		}
		at := 0
		for i, o := range r.Options {
			if o == r.Value {
				at = i
			}
		}
		step := 1
		if k.String() == "left" || k.String() == "h" {
			step = -1
		}
		return m, m.changeSetting(r, r.Options[(at+step+len(r.Options))%len(r.Options)], "")
	case "g":
		if len(rows) > 0 {
			r := rows[clampSel(sc.Row, len(rows))]
			if r.Scoped && m.st.AppSettings != nil {
				which := m.st.AppSettings.ThemeScope
				if r.Key == "ui.bell" {
					which = m.st.AppSettings.BellScope
				}
				scope := "repo"
				if which == "repo" {
					scope = "all"
				}
				return m, m.changeSetting(r, r.Value, scope)
			}
		}
	case "E", "e":
		if SettingsSections[sc.Section] == "toml" {
			editor := m.st.AppSettings.Editor
			if editor == "" {
				editor = "vi"
			}
			argv := append(strings.Fields(editor), a.Repo+"/.rig/harness.toml")
			return m, m.exec(argv, func(error) tea.Msg { return settingsEditedMsg{} })
		}
	}
	return m, nil
}

type settingsEditedMsg struct{}

// changeSetting saves one row's new value where it is kept (scope "" keeps the current place).
func (m *Model) changeSetting(r setRow, value, scope string) tea.Cmd {
	s := m.st.AppSettings
	switch r.Key {
	case "ui.theme":
		if scope == "" {
			scope = s.ThemeScope
		}
		return tea.Batch(m.switchTheme(value, scope), m.fetchAppSettings())
	case "parent":
		src := m.src
		return func() tea.Msg { return parentSetMsg{name: value, err: src.ChatSetParent(value, callTimeout)} }
	case "project.enabled":
		if value == "false" {
			m.pendingX, m.pendingI = "settings.set", "project.enabled=false"
			m.st.Mode, m.st.Prompt = ModeConfirm, "Turn Rig off in this repo? Work stops; history and memory stay."
			return nil
		}
	}
	if scope == "" {
		scope = "repo"
		if r.Scoped && s.BellScope == "all" {
			scope = "all"
		}
	}
	src := m.src
	key := r.Key
	return func() tea.Msg {
		reply, err := src.SetSetting(key, value, scope, callTimeout)
		return appSettingsMsg{s: reply, err: err}
	}
}

// saveConfirmed is the confirmed "turn Rig off".
func (m *Model) saveConfirmed(spec string) tea.Cmd {
	key, value, _ := strings.Cut(spec, "=")
	src := m.src
	return func() tea.Msg {
		reply, err := src.SetSetting(key, value, "repo", callTimeout)
		return appSettingsMsg{s: reply, err: err}
	}
}

// bell rings the terminal once when a new request starts waiting, if the bell is on.
func (m *Model) bell() tea.Cmd {
	key := ""
	if m.st.Chat != nil && m.st.Chat.Ask != nil {
		key = "chat:" + m.st.Chat.Ask.ID
	} else if job := waitingJob(m.view); job != nil {
		key = "job:" + job.ID
	}
	ring := key != "" && key != m.lastAsk && m.st.AppSettings != nil && m.st.AppSettings.Bell
	m.lastAsk = key
	if !ring {
		return nil
	}
	return func() tea.Msg {
		m.ringer()
		return nil
	}
}
