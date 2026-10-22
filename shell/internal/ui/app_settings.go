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
var SettingsSections = []string{"general", "agents", "routing", "approvals", "sandbox", "editor", "keys", "git", "notify", "toml"}

// basicSections are the ones that show first: what most people change. The others (how work is routed, approvals, the
// sandbox, the editor, keys, the raw file) are one key away.
var basicSections = map[string]bool{"general": true, "agents": true, "git": true, "notify": true}

// sectionNames are the sections' names as shown, in plain words.
var sectionNames = map[string]string{"agents": "assistants", "notify": "notifications", "git": "git and saving", "toml": "raw file",
	"routing": "who does what", "approvals": "approvals"}

func sectionShown(sc *SettingsScreen, name string) bool { return sc.Advanced || basicSections[name] }

func sectionLabel(name string) string {
	if n := sectionNames[name]; n != "" {
		return n
	}
	return name
}

// stepSection moves to the next (or previous) section that shows.
func stepSection(sc *SettingsScreen, dir int) {
	for n := 0; n < len(SettingsSections); n++ {
		sc.Section = (sc.Section + dir + len(SettingsSections)) % len(SettingsSections)
		if sectionShown(sc, SettingsSections[sc.Section]) {
			break
		}
	}
	sc.Row = 0
}

// SettingsScreen is where the Settings screen is.
type SettingsScreen struct {
	Advanced bool // the short list (general, assistants, notifications, git) is what shows until `a` asks for the rest
	Section  int
	Row      int
	// Pick is the value ←→ has moved to on the chosen row: shown, but nothing changes until ⏎ applies it
	Pick string
}

// setRow is one row of the design's Settings: a bold label on the left and its value on the right, a note under it.
type setRow struct {
	Label   string
	Key     string   // the setting it changes ("" for a row that only shows a value)
	Options []string // the values it can take
	Names   []string // how each value is shown; the value itself when empty
	Value   string
	Note    string
	Scoped  bool   // g keeps it for all repos or this repo
	Toggle  bool   // on/off, shown as the design's green "on" or grey "off"
	Right   string // the value as drawn, for a row that only shows one
	Group   bool   // a blank line before it: the design groups its rows
	Agent   string // an agent's row: its tile and name on the left
	Install string // the command that installs a missing agent (i)
	Stacked bool   // the label on its own line and the choices under it, as the design draws the theme
	Pick    string // the value ←→ has picked and ⏎ would apply
	Action  string // ⏎ (or a click) runs this palette action: the row opens a picker of its own
	At      int    // its place in the section, for the mouse
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
	toggle := func(label, key string, on bool, note string) setRow {
		return setRow{Label: label, Key: key, Options: []string{"true", "false"}, Names: []string{"on", "off"}, Value: onoff(on), Note: note, Toggle: true}
	}
	parent := s.Parent
	if parent == "" {
		parent = st.ChatParent
	}
	switch section {
	case "general":
		var themes []string
		for _, t := range s.Themes {
			if t != "rig" { // the design's four; the board's own colours stay with /theme rig
				themes = append(themes, t)
			}
		}
		model := st.ChatModel
		if model == "" {
			model = "auto"
		}
		return []setRow{
			{Label: "Theme", Key: "ui.theme", Options: themes, Value: st.Theme, Scoped: true, Stacked: true},
			{Label: "Default parent", Key: "parent", Options: append([]string(nil), s.Installed...), Value: parent, Group: true,
				Right: st.tile(parent) + " " + parent + st.faint(" · "+model)},
			{Label: parent + "'s model", Action: "model", Right: modelRight(st, parent)},
			{Label: parent + "'s reasoning", Action: "model", Right: effortRight(st, parent)},
			{Label: "When rig starts", Key: "ui.start", Options: []string{"last", "picker"}, Names: []string{"last project", "project picker"}, Value: s.Start,
				Group: true},
			func() setRow {
				r := toggle("Rig in this repo", "project.enabled", s.Enabled, "off keeps history and memory, stops new work")
				r.Group = true
				return r
			}(),
			{Label: "Updates", Right: versions(st, s), Group: true},
		}
	case "agents":
		var rows []setRow
		for _, name := range sortedWorkers(s.Workers) {
			installed := false
			for _, n := range s.Installed {
				installed = installed || n == name
			}
			role := "helper"
			if name == parent {
				role = "parent"
			}
			r := toggle(name, "workers."+name, s.Workers[name], role)
			r.Agent = name
			if model := s.Models[name]; model != "" && installed {
				r.Right = st.dim(model) + "  "
			}
			if !installed {
				r.Note, r.Options, r.Key = "not installed", nil, ""
				for _, c := range installCommands {
					if c.Name == name {
						r.Install = c.Command
					}
				}
				if r.Install != "" {
					r.Right = st.zone(fmt.Sprintf("row:install:%d", len(rows)), st.dim("i")+" "+st.faint("install"))
				}
			}
			rows = append(rows, r)
		}
		counts := []string{"1", "2", "3", "4"}
		if s.MaxRunning > 4 {
			counts = append(counts, fmt.Sprint(s.MaxRunning))
		}
		return append(rows, setRow{Label: "At the same time", Key: "queue.max_running", Options: counts, Value: fmt.Sprint(s.MaxRunning), Group: true,
			Note: "helpers working at once"})
	case "routing":
		return []setRow{{Label: "Optimise for", Key: "routing.objective", Options: []string{"balanced", "quality", "cost", "speed"}, Value: s.Objective}}
	case "approvals":
		return []setRow{
			{Label: "Helper commands", Key: "approvals", Options: []string{parent + " decides"}, Value: parent + " decides", Stacked: true,
				Note: "  " + parent + " allows routine commands and asks you about risky ones."},
			{Label: parent + " keeps work that passed its checks", Right: st.paint(colGreen, "on"), Group: true, Note: "you can undo any of it with u, or hold a helper with h"},
		}
	case "sandbox":
		image := ""
		if st.Workspace != nil {
			for _, sec := range st.Workspace.Brief {
				for _, l := range sec.Lines {
					if v, ok := strings.CutPrefix(strings.TrimSpace(l.Text), "image"); ok {
						image = strings.TrimSpace(v)
					}
				}
			}
		}
		rows := []setRow{
			{Label: "Where agents work", Key: "sandbox.provider", Options: []string{"devcontainer", "host"}, Names: []string{"dev container", "this machine"}, Value: s.Sandbox},
		}
		if image != "" {
			rows = append(rows, setRow{Label: "Image", Right: image + st.faint(" · .devcontainer wins"), Group: true})
		}
		copyRow := setRow{Label: "A separate copy per helper", Key: "worktrees.mode", Options: []string{"per-job", "off"}, Names: []string{"on", "off"},
			Value: s.Worktrees, Toggle: true, Group: image == ""}
		boxRow := setRow{Label: "A container per helper", Key: "worktrees.container", Options: []string{"per-job", "shared"}, Names: []string{"on", "off"},
			Value: s.WorktreeContainer, Toggle: true}
		return append(rows, copyRow, boxRow)
	case "editor":
		editor := s.Editor
		if editor == "" {
			editor = "vi"
		}
		size := fmt.Sprint(s.TabSize)
		if s.TabSize == 0 {
			size = "4"
		}
		return []setRow{
			{Label: "Tab size", Key: "ui.tab_size", Options: []string{"2", "4", "8"}, Value: size},
			toggle("Format on save", "ui.format_on_save", s.FormatOnSave, "⌃s runs gofmt, prettier, ruff, black, rustfmt or shfmt first, when it is here"),
			{Label: "Terminal shell", Right: lastWord(strings.ReplaceAll(s.Shell, "/", " "))},
			{Label: "e opens in", Right: "$EDITOR" + st.faint(" · "+editor), Group: true, Note: "in the terminal, e hands the file to your own editor"},
		}
	case "keys":
		// the editor's keys that clash in some terminals: each one of a few chords, kept for all repos
		key := func(label, name, def, note string, options []string) setRow {
			v := def
			if s.Keys != nil && s.Keys[name] != "" {
				v = s.Keys[name]
			}
			names := make([]string, len(options))
			for i, o := range options {
				names[i] = glyphOf(o)
			}
			return setRow{Label: label, Key: "keys." + name, Options: options, Names: names, Value: v, Note: note}
		}
		return []setRow{
			key("Add next occurrence", "next_occurrence", "ctrl+d", "in the editor, selects the next place the selected text is", []string{"ctrl+d", "ctrl+n", "alt+d"}),
			key("Rename in file", "rename", "f2", "every whole-word occurrence of the name at the cursor", []string{"f2", "ctrl+r", "alt+r"}),
			key("Go to definition", "definition", "f12", "⌃t goes back", []string{"f12", "ctrl+]", "alt+g"}),
			key("Find references", "references", "f7", "every use of the name at the cursor, in the search tab", []string{"f7", "alt+u", "ctrl+u"}),
			key("Find in conversation", "find_in_chat", "ctrl+/", "a find row in place of the reply box", []string{"ctrl+/", "ctrl+f", "alt+f"}),
			key("Permission mode", "permission_mode", "shift+tab", "goes round ask, auto-edit and plan for the conversation", []string{"shift+tab", "alt+m", "ctrl+y"}),
			key("Terminal", "terminal", "ctrl+`", "⌃` is taken by some terminals; the shell split at the foot of the screen", []string{"ctrl+`", "ctrl+j", "alt+t"}),
		}
	case "git":
		account := st.faint("gh is not installed")
		switch {
		case s.GHUser != "":
			account = st.paint(colGreen, "●") + " " + s.GHUser + st.faint(" · via gh")
		case s.GH:
			account = st.faint("not signed in · gh auth login")
		}
		sign := st.faint("off")
		if s.SignCommits {
			sign = st.paint(colGreen, "on")
		}
		return []setRow{
			{Label: "GitHub account", Right: account},
			{Label: "Clone new repos into", Right: s.CloneDir},
			{Label: "Each apply is one commit", Right: st.faint("rig: merge job <id>"), Group: true},
			{Label: "When you ship", Key: "ui.ship", Options: []string{"pr", "push"}, Names: []string{"pull request", "push to branch"}, Value: s.Ship, Group: true},
			{Label: "Pull request branch", Right: "rig/{topic}"},
			{Label: "Sign commits", Right: sign},
		}
	case "notify":
		needs := toggle("Something needs me", "ui.notify_needs", s.NotifyNeeds, "a request waiting for you, or a sandbox problem")
		done := toggle("A long turn ended", "ui.notify_done", s.NotifyDone, "the parent finished or failed after 20 seconds or more")
		applied := toggle("Work was applied", "ui.notify_applied", s.NotifyApplied, "")
		bell := toggle("Terminal bell", "ui.bell", s.Bell, "")
		bell.Scoped, bell.Group = true, true
		desktop := toggle("Desktop notice", "ui.desktop", s.Desktop, "Ghostty, kitty, iTerm2 and others show it")
		return []setRow{needs, done, applied, bell, desktop}
	}
	return nil
}

// versions is the design's "rig 0.9.2 · dc-cli 0.23.0": what is installed.
func versions(st State, s *view.AppSettings) string {
	var parts []string
	if s.RigVersion != "" {
		parts = append(parts, "rig "+s.RigVersion)
	}
	if s.DCVersion != "" {
		parts = append(parts, "dc-cli "+s.DCVersion)
	}
	if len(parts) == 0 {
		return st.faint("rig doctor shows them")
	}
	return strings.Join(parts, st.faint(" · "))
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

// settingsTitles are each section's heading and the line under it, as the design writes them.
var settingsTitles = map[string][2]string{
	"agents": {"Agents", ""}, "routing": {"Who does what", "smart defaults · change only what you care about"}, "approvals": {"", ""},
	"sandbox": {"Sandbox", "powered by dc-cli"}, "editor": {"Editor", ""}, "keys": {"Keys", "the editor's keys that clash in some terminals"}, "git": {"Git and shipping", ""}, "notify": {"Notifications", ""},
}

// settingsScreenLines is the Settings screen.
func settingsScreenLines(v *view.View, st State, w, h int) []string {
	a := st.App
	sc := a.Settings
	section := SettingsSections[sc.Section]
	rows := settingRows(st, section)
	sel := clampSel(sc.Row, len(rows))
	repo := appProject(v, a)
	scope, file := repo, ".rig/harness.toml"
	if s := st.AppSettings; s != nil && sel < len(rows) && strings.HasPrefix(rows[sel].Key, "ui.") {
		which := "all"
		switch rows[sel].Key {
		case "ui.theme":
			which = s.ThemeScope
		case "ui.bell":
			which = s.BellScope
		}
		if which != "repo" {
			scope, file = "all repos", s.Global
		}
	}
	other := "all repos"
	if scope == "all repos" {
		other = repo
	}
	head := "   " + st.zone("key:esc", st.faint("←")+"  "+st.bold("Settings")) + "   " + chipOn(st, scope) + "  " + st.faint(other) + "  " + st.faint(file)
	var tabs []string
	for i, name := range SettingsSections {
		if !sectionShown(sc, name) {
			continue
		}
		label := sectionLabel(name)
		if i == sc.Section {
			if st.Styled {
				tabs = append(tabs, lipgloss.NewStyle().Foreground(lipgloss.Color(colText)).Bold(true).Underline(true).Render(label))
			} else {
				tabs = append(tabs, "["+label+"]")
			}
		} else {
			tabs = append(tabs, st.faint(label))
		}
		tabs[len(tabs)-1] = st.zone(fmt.Sprintf("row:section:%d", i), tabs[len(tabs)-1])
	}
	more := "more settings"
	if sc.Advanced {
		more = "fewer settings"
	}
	more = st.zone("row:section:adv", st.dim("a  "+more)) + "   " // at the head's right, so the tabs keep their room
	lines := []string{spread(head, "", more, w), center(strings.Join(tabs, "   "), w)}
	cw := min(76, w-8)
	body := []string{""}
	if t := settingsTitles[section]; t[0] != "" {
		body = append(body, st.bold(t[0]))
		sub := t[1]
		if section == "agents" && st.AppSettings != nil {
			sub = fmt.Sprintf("today %d helper runs", st.AppSettings.RunsToday)
		}
		if sub != "" {
			body = append(body, st.dim(sub))
		}
		body = append(body, "")
	}
	switch {
	case st.AppSettings == nil:
		body = append(body, st.faint("loading…"))
	case section == "toml":
		body = append(body, tomlLines(st, cw)...)
	default:
		var rowsOut []string
		for i, r := range rows {
			if r.Group && i > 0 {
				rowsOut = append(rowsOut, "")
			}
			if i == sel && sc.Pick != "" {
				r.Pick = sc.Pick
			}
			r.At = i
			for _, l := range settingLines(st, r, i == sel, cw, scopeName(st, r)) {
				if strings.HasPrefix(l, "\x00") {
					l = "\x00" + st.zone(fmt.Sprintf("row:setting:%d", i), l[1:])
				}
				rowsOut = append(rowsOut, l)
			}
			if r.Note != "" && r.Agent == "" {
				rowsOut = append(rowsOut, "    "+st.dim(fit(r.Note, cw-4)))
			}
		}
		body = append(body, rowsOut...)
		body = append(body, settingsExtra(st, section, cw)...)
	}
	// the rows carry the cursor in the two cells left of the column, as the design's › sits in its margin
	for i, l := range body {
		if !strings.HasPrefix(l, "\x00") {
			body[i] = "  " + l
		} else {
			body[i] = l[1:]
		}
	}
	lines = append(lines, indentAll(body, (w-cw)/2-2, w)...)
	for len(lines) < h {
		lines = append(lines, fit("", w))
	}
	return lines[:h]
}

// settingsExtra is what a section shows under its rows: routing's table, today's allowed commands, the sandbox's state.
func settingsExtra(st State, section string, cw int) []string {
	s := st.AppSettings
	var out []string
	switch section {
	case "routing":
		if len(s.RoutingTable) == 0 {
			break
		}
		out = append(out, "", "", st.faint(fmt.Sprintf("%-20s%-28s%s", "task", "who", "model")))
		for _, r := range s.RoutingTable {
			who := "auto" + st.faint(" · usually "+r.Worker)
			if r.Parent {
				who = r.Worker + st.faint(" · parent")
			}
			out = append(out, fmt.Sprintf("%-20s", r.Task)+who+strings.Repeat(" ", max(1, 28-lipgloss.Width(who)))+r.Model)
		}
		out = append(out, "", st.faint("rig pick explains a choice · helpers come from the agents section"))
	case "approvals":
		var allowed []string
		if st.Today != nil {
			for _, r := range st.Today.Rows {
				if r.State == "command" {
					allowed = append(allowed, strings.TrimPrefix(clean(r.Title), "allowed "))
				}
			}
		}
		out = append(out, "", "", st.bold("Allowed today"), "")
		if len(allowed) == 0 {
			out = append(out, "    "+st.faint("nothing yet"))
		}
		for i := 0; i < len(allowed) && i < 9; i += 3 {
			row := "    "
			for _, c := range allowed[i:min(len(allowed), i+3)] {
				row += fit(c, 22) + "  "
			}
			out = append(out, row)
		}
	case "sandbox":
		if st.Workspace == nil {
			break
		}
		state := ""
		for _, sec := range st.Workspace.Brief {
			for _, l := range sec.Lines {
				if t := strings.TrimSpace(l.Text); strings.HasPrefix(t, "●") || strings.HasPrefix(t, "○") {
					state = t
				}
			}
		}
		if st.Workspace.Context != "" {
			up := strings.TrimSpace(strings.TrimPrefix(state, "● up"))
			out = append(out, "", "", st.paint(colGreen, "●")+" running "+up+st.faint(" · "+versions(st, s)))
			out = append(out, "  "+st.dim("s")+" "+st.faint("stop")+"   "+st.dim("d")+" "+st.faint("doctor"))
		} else {
			out = append(out, "", "", st.faint("○")+" not running", "  "+st.dim("u")+" "+st.faint("start")+"   "+st.dim("d")+" "+st.faint("doctor"))
		}
	case "git":
		out = append(out, "", "", st.faint("agents never push · shipping is always yours"))
	}
	return out
}

// chipOn is the design's black chip for the chosen value.
func chipOn(st State, text string) string {
	if st.Styled {
		return lipgloss.NewStyle().Background(lipgloss.Color(colText)).Foreground(lipgloss.Color(colGround)).Bold(true).Render(" " + text + " ")
	}
	return "[" + text + "]"
}

// scopeName is where a row's value is kept, for the theme's "all repos" after its choices.
func scopeName(st State, r setRow) string {
	if r.Key != "ui.theme" || st.AppSettings == nil {
		return ""
	}
	if st.AppSettings.ThemeScope == "repo" {
		return "this repo"
	}
	return "all repos"
}

// settingLines is a row as lines that carry their own cursor margin (marked with a leading NUL): one line, or for a
// stacked row its label and then its choices under it.
func settingLines(st State, r setRow, selected bool, cw int, scope string) []string {
	if !r.Stacked {
		return []string{"\x00" + settingLine(st, r, selected, cw)}
	}
	mark := "  "
	if selected {
		mark = st.bold("› ")
	}
	var parts []string
	for _, o := range r.Options {
		parts = append(parts, choiceZone(st, r, o, choice(st, r, o, o)))
	}
	if scope != "" {
		parts = append(parts, st.faint(" "+scope))
	}
	return []string{"\x00  " + st.bold(r.Label), "", "\x00    " + mark + strings.Join(parts, "  ")}
}

// settingLine is one row: the label on the left in bold, the value on the right: chips, a green on or grey off, or text.
func settingLine(st State, r setRow, selected bool, cw int) string {
	label := st.bold(r.Label)
	if r.Agent != "" {
		label = st.tile(r.Agent) + " " + fmt.Sprintf("%-9s", r.Agent) + " " + st.faint(r.Note)
		if r.Note == "not installed" {
			label = st.tile(r.Agent) + " " + fmt.Sprintf("%-9s", r.Agent) + " " + st.paint(colAmber, r.Note)
		}
	}
	mark := "  "
	if selected {
		mark = st.bold("› ")
	}
	right := r.Right
	switch {
	case len(r.Options) > 0 && r.Toggle:
		switch {
		case r.Pick != "" && r.Pick == r.Options[0]:
			right += picked(st, "on") + st.faint(" ⏎")
		case r.Pick != "":
			right += picked(st, "off") + st.faint(" ⏎")
		case r.Value == r.Options[0]:
			right += st.paint(colGreen, "on")
		default:
			right += st.faint("off")
		}
	case len(r.Options) > 0:
		var parts []string
		for i, o := range r.Options {
			name := o
			if i < len(r.Names) {
				name = r.Names[i]
			}
			parts = append(parts, choiceZone(st, r, o, choice(st, r, o, name)))
		}
		if r.Right == "" {
			right = strings.Join(parts, "  ")
		}
	}
	if len(r.Options) > 0 && r.Toggle {
		right = choiceZone(st, r, "", right)
	}
	left := mark + label
	return left + strings.Repeat(" ", max(2, cw-lipgloss.Width(left)-lipgloss.Width(right))) + right
}

// modelRight is the model row's value: the model in use and where it was chosen, and ⏎ to change it.
func modelRight(st State, parent string) string {
	model, where := st.ChatModel, "its own setting"
	if s := st.AppSettings; s != nil {
		if pm, ok := s.ParentModels[parent]; ok {
			switch {
			case pm.Model != "" && pm.Scope == "repo":
				model, where = pm.Model, "this repo"
			case pm.Model != "":
				model, where = pm.Model, "all repos"
			case pm.Own != "":
				model = pm.Own
			}
		}
	}
	if model == "" {
		model = "auto"
	}
	return model + st.faint(" · "+where+"  ⏎ change")
}

// effortRight is the reasoning row's value: the level in use and where it was chosen, and ⏎ to change it.
func effortRight(st State, parent string) string {
	level, where := st.ChatEffort, "its own setting"
	if s := st.AppSettings; s != nil {
		if pe, ok := s.ParentEfforts[parent]; ok {
			switch {
			case pe.Effort != "" && pe.Scope == "repo":
				level, where = pe.Effort, "this repo"
			case pe.Effort != "":
				level, where = pe.Effort, "all repos"
			}
			if len(pe.Levels) == 0 {
				return st.faint("no reasoning flag for " + parent)
			}
		}
	}
	if level == "" {
		level = "default"
	}
	return level + st.faint(" · "+where+"  ⏎ change")
}

// tomlLines is the toml section: the harness itself as the design shows it (its tables in the accent colour), whether
// it is valid, and how to edit it.
func tomlLines(st State, cw int) []string {
	s := st.AppSettings
	head := st.bold(s.Harness)
	out := []string{head + strings.Repeat(" ", max(2, cw-lipgloss.Width(head)-9)) + st.faint("this repo"), ""}
	for i, l := range strings.Split(strings.TrimRight(s.HarnessText, "\n"), "\n") {
		if i == 24 {
			out = append(out, st.faint("…"))
			break
		}
		text := sanitize(l)
		if strings.HasPrefix(strings.TrimSpace(text), "[") {
			text = st.paint(colAccent, text)
		} else if strings.HasPrefix(strings.TrimSpace(text), "#") {
			text = st.faint(text)
		}
		out = append(out, text)
	}
	out = append(out, "")
	if len(s.Problems) == 0 {
		out = append(out, st.paint(colGreen, "✓ valid")+" "+st.faint("· E edits it in $EDITOR · mistakes show here"))
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
			sc.Advanced = !basicSections[s] // asked for by name: the rest shows too
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
	if sc.Pick != "" && (k.String() == "esc" || k.String() == "q") {
		sc.Pick = "" // esc drops the pick first; a second esc leaves
		return m, nil
	}
	switch k.String() {
	case "up", "down", "k", "j", "tab", "shift+tab":
		sc.Pick = ""
	}
	switch k.String() {
	case "esc", "q":
		a.Settings = nil
	case "tab":
		stepSection(sc, 1)
	case "shift+tab":
		stepSection(sc, -1)
	case "a":
		toggleAdvanced(sc)
	case "up", "k":
		sc.Row = clampSel(sc.Row-1, len(rows))
	case "down", "j":
		sc.Row = clampSel(sc.Row+1, len(rows))
	case "left", "right", "h", "l":
		// ←→ only picks: the theme and every other choice change when ⏎ applies them
		if len(rows) == 0 {
			return m, nil
		}
		r := rows[clampSel(sc.Row, len(rows))]
		if r.Key == "" || r.Key == "approvals" || len(r.Options) < 2 {
			return m, nil
		}
		from := r.Value
		if sc.Pick != "" {
			from = sc.Pick
		}
		at := 0
		for i, o := range r.Options {
			if o == from {
				at = i
			}
		}
		step := 1
		if k.String() == "left" || k.String() == "h" {
			step = -1
		}
		sc.Pick = r.Options[(at+step+len(r.Options))%len(r.Options)]
		if sc.Pick == r.Value {
			sc.Pick = ""
		}
	case " ", "enter":
		if len(rows) == 0 {
			return m, nil
		}
		r := rows[clampSel(sc.Row, len(rows))]
		if r.Action != "" {
			return m, m.runPalette(paletteItem{Action: r.Action}, "")
		}
		if r.Key == "" || r.Key == "approvals" || len(r.Options) < 2 {
			return m, nil
		}
		pick := sc.Pick
		sc.Pick = ""
		switch {
		case pick != "" && pick != r.Value:
			return m, m.changeSetting(r, pick, "")
		case pick == "" && r.Toggle:
			// an on/off row: ⏎ or space turns it over
			other := r.Options[0]
			if r.Value == other {
				other = r.Options[1]
			}
			return m, m.changeSetting(r, other, "")
		}
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
	case "i":
		if len(rows) > 0 {
			if r := rows[clampSel(sc.Row, len(rows))]; r.Install != "" {
				m.pendingX, m.pendingI = "install", r.Install
				m.st.Mode, m.st.Prompt = ModeConfirm, "Install "+r.Agent+"? It runs: "+r.Install
			}
		}
	case "s", "u", "d":
		if SettingsSections[sc.Section] != "sandbox" {
			return m, nil
		}
		switch k.String() {
		case "s":
			m.pendingX, m.pendingI = "env.down", "down"
			m.st.Mode, m.st.Prompt = ModeConfirm, "Stop this project's container (rig env down)? Agents in it pause."
		case "u":
			m.pendingX, m.pendingI = "env.up", "up"
			m.st.Mode, m.st.Prompt = ModeConfirm, "Start this project's container (rig env up)?"
		case "d":
			return m, m.exec([]string{"sh", "-c", "rig doctor; printf '\\n⏎ goes back to rig shell '; read _"}, func(err error) tea.Msg { return shellDoneMsg{err: err} })
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
		switch {
		case r.Key == "ui.bell" && s.BellScope != "all":
		case strings.HasPrefix(r.Key, "ui."), strings.HasPrefix(r.Key, "keys."):
			scope = "all" // the board's own preferences follow you to every repo; g keeps the bell for this one
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
	fresh := key != "" && key != m.lastAsk
	m.lastAsk = key
	if !fresh || m.st.AppSettings == nil || !m.st.AppSettings.NotifyNeeds {
		return nil
	}
	return m.notify(appParent(m.st) + " needs you: a request is waiting")
}

// notify reaches you the ways Settings › Notifications keeps on: the terminal bell, a desktop notice, or both.
func (m *Model) notify(text string) tea.Cmd {
	s := m.st.AppSettings
	if s == nil || (!s.Bell && !s.Desktop) {
		return nil
	}
	ring, desktop := s.Bell, s.Desktop
	return func() tea.Msg {
		if ring {
			m.ringer()
		}
		if desktop && m.notifier != nil {
			m.notifier("rig", text)
		}
		return nil
	}
}

// notifyApplied tells you when the parent applied a helper's work, if Settings › Notifications asks for it.
func (m *Model) notifyApplied(t *view.Today) tea.Cmd {
	applied := map[string]bool{}
	var fresh string
	if t != nil {
		for _, r := range t.Rows {
			if r.State == "applied" {
				applied[r.JobID] = true
				if m.seenApplied != nil && !m.seenApplied[r.JobID] && fresh == "" {
					fresh = r.Title
				}
			}
		}
	}
	first := m.seenApplied == nil
	m.seenApplied = applied
	if first || fresh == "" || m.st.AppSettings == nil || !m.st.AppSettings.NotifyApplied {
		return nil
	}
	return m.notify(appParent(m.st) + " applied " + clean(fresh))
}

// settingsKeys are each section's keys, as the design's footers list them.
func settingsKeys(section string) [][2]string {
	switch section {
	case "agents":
		return [][2]string{{"↑↓", "agent"}, {"space", "on/off"}, {"i", "install"}, {"⇥", "section"}, {"esc", "back"}}
	case "routing":
		return [][2]string{{"↑↓", "row"}, {"←→", "choose"}, {"⏎", "apply"}, {"⇥", "section"}, {"esc", "back"}}
	case "approvals":
		return [][2]string{{"⇥", "section"}, {"E", "edit toml"}, {"esc", "back"}}
	case "sandbox":
		return [][2]string{{"←→", "choose"}, {"⏎", "apply"}, {"s", "stop"}, {"d", "doctor"}, {"⇥", "section"}, {"esc", "back"}}
	case "editor", "keys", "git":
		return [][2]string{{"←→", "choose"}, {"⏎", "apply"}, {"⇥", "section"}, {"esc", "back"}}
	case "notify":
		return [][2]string{{"space", "on/off"}, {"g", "all repos / this repo"}, {"⇥", "section"}, {"a", "more"}, {"esc", "back"}}
	case "toml":
		return [][2]string{{"E", "edit in $EDITOR"}, {"⇥", "section"}, {"esc", "back"}}
	}
	return [][2]string{{"←→", "choose"}, {"⏎", "apply"}, {"⇥", "section"}, {"g", "all repos / this repo"}, {"esc", "back"}}
}

// choice is one value of a row: the current one on the black chip, the one ←→ has picked outlined until ⏎ applies it.
func choice(st State, r setRow, value, name string) string {
	switch {
	case value == r.Pick:
		return picked(st, name)
	case value == r.Value:
		return chipOn(st, name)
	}
	return st.faint(" " + name + " ")
}

// choiceZone makes a row's value clickable: a click picks it and applies it.
func choiceZone(st State, r setRow, value, s string) string {
	return st.zone(fmt.Sprintf("row:choice:%d=%s", r.At, value), s)
}

// picked is a value ←→ has moved to: bold and underlined, not yet applied.
func picked(st State, name string) string {
	if st.Styled {
		return " " + lipgloss.NewStyle().Foreground(lipgloss.Color(colText)).Bold(true).Underline(true).Render(name) + " "
	}
	return "<" + name + ">"
}

// toggleAdvanced shows or hides the sections beyond the short list; a hidden one that was open goes back to general.
func toggleAdvanced(sc *SettingsScreen) {
	sc.Advanced = !sc.Advanced
	if !sectionShown(sc, SettingsSections[sc.Section]) {
		sc.Section, sc.Row = 0, 0
	}
}
