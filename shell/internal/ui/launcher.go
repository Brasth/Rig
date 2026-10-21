package ui

import (
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
)

// The launcher is the design's "Open a project": type a path or paste a git URL, or pick a recent project. Opening
// a URL clones it first, showing each step as the design's "Getting api-gateway ready" does. It runs where there is
// no Rig project yet, so it needs no API server; the project it opens is handed back to rig shell.

// Recent is one project rig shell opened before (~/.rig/recent.json, written by rig shell).
type Recent struct {
	Path string  `json:"path"`
	At   float64 `json:"at"`
}

// Launcher is the launcher's model.
type Launcher struct {
	st      State
	w, h    int
	home    string // ~/.rig
	code    string // where clones go: ~/code
	recents []Recent
	sel     int
	input   string
	repos   []string // ⌃g: your GitHub repositories
	steps   []openStep
	cloning string
	message string
	confirm string // a question waiting for y
	pending func() tea.Cmd
	// Open is the project to hand back to rig shell when the launcher quits; empty means quit.
	Open string
	now  func() time.Time
	run  func(name string, args ...string) ([]byte, error)
}

type openStep struct {
	Label, Note  string
	Done, Failed bool
	Took         time.Duration
}

// NewLauncher reads the recent projects from home (~/.rig) and starts with the input given (a path, or empty).
func NewLauncher(home, code, input string) *Launcher {
	st := NewState()
	st.Styled = true
	l := &Launcher{st: st, home: home, code: code, input: input, now: time.Now,
		run: func(name string, args ...string) ([]byte, error) { return exec.Command(name, args...).CombinedOutput() }}
	data, err := os.ReadFile(filepath.Join(home, "recent.json"))
	if err == nil {
		_ = json.Unmarshal(data, &l.recents)
	}
	return l
}

func (l *Launcher) Init() tea.Cmd { return nil }

var gitURL = regexp.MustCompile(`^(https?://|git@|ssh://)?(github\.com|gitlab\.com|bitbucket\.org|[\w.-]+\.[a-z]{2,})[/:][\w.-]+/[\w.-]+?(\.git)?/?$`)

type cloneMsg struct {
	path string
	err  error
	took time.Duration
	out  string
}

type reposMsg struct{ repos []string }

func (l *Launcher) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.WindowSizeMsg:
		l.w, l.h = msg.Width, msg.Height
	case reposMsg:
		l.repos, l.sel = msg.repos, 0
		if len(msg.repos) == 0 {
			l.message = "no GitHub repositories found (is gh installed and signed in?)"
		}
	case cloneMsg:
		if msg.err != nil {
			l.steps[0].Failed = true
			l.message = "clone failed: " + firstLine(msg.out)
			return l, nil
		}
		l.steps[0].Done, l.steps[0].Took = true, msg.took
		found := "none yet · Rig makes one when agents start"
		if _, err := os.Stat(filepath.Join(msg.path, ".devcontainer")); err == nil {
			found = "found"
		}
		l.steps = append(l.steps, openStep{Label: "find the dev container", Note: found, Done: true})
		l.steps = append(l.steps, openStep{Label: "set up Rig here", Note: "rig init"})
		l.cloning = msg.path
	case tea.KeyMsg:
		return l.key(msg)
	}
	return l, nil
}

func (l *Launcher) key(k tea.KeyMsg) (tea.Model, tea.Cmd) {
	l.message = ""
	if l.confirm != "" {
		switch k.String() {
		case "y", "enter":
			l.confirm = ""
			if l.pending != nil {
				return l, l.pending()
			}
		case "n", "esc":
			l.confirm, l.pending = "", nil
		}
		return l, nil
	}
	if l.cloning != "" {
		switch k.String() {
		case "enter":
			return l, l.openProject(l.cloning)
		case "esc":
			l.cloning, l.steps = "", nil
		}
		return l, nil
	}
	if len(l.steps) > 0 {
		if k.String() == "esc" {
			l.steps = nil
		}
		return l, nil
	}
	list := l.choices()
	switch k.Type {
	case tea.KeyCtrlC, tea.KeyEsc:
		if l.repos != nil {
			l.repos = nil
			return l, nil
		}
		return l, tea.Quit
	case tea.KeyUp:
		l.sel = clampSel(l.sel-1, len(list))
	case tea.KeyDown:
		l.sel = clampSel(l.sel+1, len(list))
	case tea.KeyBackspace:
		if r := []rune(l.input); len(r) > 0 {
			l.input = string(r[:len(r)-1])
		}
	case tea.KeyRunes, tea.KeySpace:
		l.input += string(k.Runes)
	case tea.KeyCtrlG:
		return l, func() tea.Msg {
			out, err := l.run("gh", "repo", "list", "--limit", "30", "--json", "nameWithOwner", "--jq", ".[].nameWithOwner")
			if err != nil {
				return reposMsg{}
			}
			return reposMsg{repos: strings.Fields(string(out))}
		}
	case tea.KeyEnter:
		text := strings.TrimSpace(l.input)
		switch {
		case text != "" && gitURL.MatchString(text):
			return l, l.clone(text)
		case text != "":
			return l, l.openProject(expandHome(text))
		case l.repos != nil && len(list) > 0:
			return l, l.clone("github.com/" + list[clampSel(l.sel, len(list))])
		case len(list) > 0:
			return l, l.openProject(list[clampSel(l.sel, len(list))])
		}
	}
	return l, nil
}

// choices are the rows ↑↓ moves through: your GitHub repositories after ⌃g, else the recent projects.
func (l *Launcher) choices() []string {
	if l.repos != nil {
		return l.repos
	}
	var out []string
	for _, r := range l.recents {
		out = append(out, r.Path)
	}
	return out
}

func expandHome(p string) string {
	if strings.HasPrefix(p, "~/") {
		if home, err := os.UserHomeDir(); err == nil {
			return filepath.Join(home, p[2:])
		}
	}
	abs, err := filepath.Abs(p)
	if err != nil {
		return p
	}
	return abs
}

func tildePath(p string) string {
	if home, err := os.UserHomeDir(); err == nil && strings.HasPrefix(p, home+"/") {
		return "~/" + strings.TrimPrefix(p, home+"/")
	}
	return p
}

// clone starts the design's "Getting <name> ready": the clone into ~/code/<name>, then the dev container check.
func (l *Launcher) clone(url string) tea.Cmd {
	url = strings.TrimSuffix(strings.TrimSuffix(url, "/"), ".git")
	name := filepath.Base(url)
	dest := filepath.Join(l.code, name)
	if _, err := os.Stat(dest); err == nil {
		return l.openProject(dest)
	}
	full := url
	if !strings.Contains(url, "://") && !strings.HasPrefix(url, "git@") {
		full = "https://" + url
	}
	l.steps = []openStep{{Label: "clone", Note: tildePath(dest)}}
	l.message = ""
	start := l.now()
	return func() tea.Msg {
		_ = os.MkdirAll(l.code, 0o755)
		out, err := l.run("git", "clone", "--quiet", full, dest)
		return cloneMsg{path: dest, err: err, took: time.Since(start), out: string(out)}
	}
}

// openProject hands a project back to rig shell, asking first to set Rig up (rig init) or to create the folder.
func (l *Launcher) openProject(path string) tea.Cmd {
	info, err := os.Stat(path)
	switch {
	case err != nil:
		l.confirm = "Create " + tildePath(path) + " as a new project (git init, rig init)?"
		l.pending = func() tea.Cmd {
			if err := os.MkdirAll(path, 0o755); err != nil {
				l.message = "could not create it: " + err.Error()
				return nil
			}
			if out, err := l.run("git", "-C", path, "init", "-q"); err != nil {
				l.message = "git init failed: " + firstLine(string(out))
				return nil
			}
			return l.setUp(path)
		}
		return nil
	case !info.IsDir():
		l.message = tildePath(path) + " is a file, not a project folder"
		return nil
	}
	if _, err := os.Stat(filepath.Join(path, ".rig", "harness.toml")); err != nil {
		l.confirm = "Set up Rig in " + tildePath(path) + " (rig init)?"
		l.pending = func() tea.Cmd { return l.setUp(path) }
		return nil
	}
	l.Open = path
	return tea.Quit
}

func (l *Launcher) setUp(path string) tea.Cmd {
	if out, err := l.run("sh", "-c", `cd "$0" && rig init`, path); err != nil { // rig init sets up the folder it runs in
		l.message = "rig init failed: " + firstLine(string(out))
		return nil
	}
	l.Open = path
	return tea.Quit
}

func (l *Launcher) View() string {
	st := l.st
	w, h := l.w, l.h
	if w == 0 {
		w, h = 120, 36
	}
	lines := []string{rigBar(st, w, 0), fit("", w)}
	cw := min(70, w-8)
	var body []string
	var keys [][2]string
	if len(l.steps) > 0 {
		name := filepath.Base(l.steps[0].Note)
		body = append(body, "", "", st.bold("Getting "+name+" ready"), st.dim("first time only · "+l.steps[0].Note), "", "")
		for _, s := range l.steps {
			mark, note := st.faint("○"), st.faint(s.Note)
			switch {
			case s.Failed:
				mark = st.paint(colRed, "✗")
			case s.Done:
				mark = st.paint(colGreen, "✓")
			case l.cloning == "" && s.Label == "clone":
				mark = st.paint(colAccent, "⠹")
			}
			row := mark + " " + s.Label + "  " + note
			if s.Took > 0 {
				took := st.faint(fmt.Sprintf("%.1fs", s.Took.Seconds()))
				row += strings.Repeat(" ", max(2, cw-lipgloss.Width(row)-lipgloss.Width(took)-2)) + took
			}
			body = append(body, row)
		}
		body = append(body, "", "", st.faint("next: pick the agent you'll talk to in this repo"))
		keys = [][2]string{{"⏎", "open when ready"}, {"esc", "cancel"}}
	} else {
		title := "Open a project"
		section := "Recent"
		if l.repos != nil {
			title, section = "Open one of your GitHub repositories", "GitHub"
		}
		body = append(body, "", "", st.bold(title), st.dim("Type a path, paste a git URL, or pick a recent one."), "")
		text := " " + st.faint("›") + " " + l.input + "▏"
		body = append(body, roundBox(st, []string{text}, cw, true)...)
		body = append(body, "", "", st.faint(section), "")
		list := l.choices()
		if len(list) == 0 {
			body = append(body, st.faint("   nothing yet"))
		}
		for i, p := range list {
			if i == 10 {
				break
			}
			name, where, right := filepath.Base(p), tildePath(p), ""
			if l.repos != nil {
				name, where = p, ""
			} else if _, err := os.Stat(filepath.Join(p, ".rig", "harness.toml")); err != nil {
				right = st.paint(colAmber, "not set up")
			} else {
				right = st.faint(ago(l.now(), l.recents[i].At))
			}
			if room := cw - 16 - lipgloss.Width(right) - 4; lipgloss.Width(where) > room {
				where = "…" + string([]rune(where)[len([]rune(where))-max(1, room-1):]) // keep the end, the folder's name
			}
			row := fmt.Sprintf("   %-12s ", name) + st.faint(where)
			if i == clampSel(l.sel, len(list)) {
				row = " › " + st.bold(fmt.Sprintf("%-12s ", name)) + st.faint(where)
			}
			row += strings.Repeat(" ", max(2, cw-lipgloss.Width(row)-lipgloss.Width(right)-2)) + right
			if i == clampSel(l.sel, len(list)) && st.Styled {
				row = tint(fit(row, cw), colSelBg)
			}
			body = append(body, row)
		}
		body = append(body, "", "", st.faint("Paste a URL to clone, e.g. github.com/brasth/api-gateway"), st.faint("It goes into "+tildePath(l.code)+"/<name> and opens with Rig."))
		keys = [][2]string{{"↑↓", "choose"}, {"⏎", "open"}, {"⌃g", "my GitHub repos"}, {"esc", "quit"}}
	}
	lines = append(lines, indentAll(body, (w-cw)/2, w)...)
	for len(lines) < h-1 {
		lines = append(lines, fit("", w))
	}
	foot := keyHints(st, keys, w)
	switch {
	case l.confirm != "":
		foot = center(st.paint(colAmber, l.confirm)+"   "+st.dim("y")+" "+st.faint("yes")+"    "+st.dim("n")+" "+st.faint("no"), w)
	case l.message != "":
		foot = center(st.paint(colAmber, l.message), w)
	}
	lines = append(lines[:h-1], foot)
	return strings.Join(Paint(lines), "\n")
}

func ago(now time.Time, at float64) string {
	if at <= 0 {
		return ""
	}
	d := now.Sub(time.Unix(int64(at), 0))
	switch {
	case d < time.Hour:
		return fmt.Sprintf("%dm", max(1, int(d.Minutes())))
	case d < 24*time.Hour:
		return fmt.Sprintf("%dh", int(d.Hours()))
	case d < 48*time.Hour:
		return "yesterday"
	}
	return fmt.Sprintf("%dd", int(d.Hours()/24))
}
