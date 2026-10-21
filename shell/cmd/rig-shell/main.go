// Command rig-shell is the Rig board pane. It reads everything from the local
// API and never touches .rig files itself.
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/Brasth/Rig/shell/internal/api"
	"github.com/Brasth/Rig/shell/internal/ui"
	"github.com/Brasth/Rig/shell/internal/view"
)

// Version is overridden at build time with -ldflags "-X main.Version=...".
var Version = "dev"

func main() { os.Exit(run(os.Args[1:], os.Stdout, os.Stderr)) }

func parseSize(s string) (int, int, error) {
	parts := strings.SplitN(strings.ToLower(s), "x", 2)
	if len(parts) != 2 {
		return 0, 0, fmt.Errorf("size must look like 120x36")
	}
	w, err1 := strconv.Atoi(parts[0])
	h, err2 := strconv.Atoi(parts[1])
	if err1 != nil || err2 != nil || w < 1 || h < 1 || w > 1000 || h > 1000 {
		return 0, 0, fmt.Errorf("size must be WxH within 1..1000")
	}
	return w, h, nil
}

// wantASCII follows docs/tui-design.md: forced by RIG_TUI_ASCII=1 or RIG_TUI_CJK=1,
// and automatic for a non-UTF locale.
func wantASCII(flagSet bool, env func(string) string) bool {
	if flagSet || env("RIG_TUI_ASCII") == "1" || env("RIG_TUI_CJK") == "1" {
		return true
	}
	for _, k := range []string{"LC_ALL", "LC_CTYPE", "LANG"} {
		if v := env(k); v != "" {
			return !strings.Contains(strings.ToLower(strings.ReplaceAll(v, "-", "")), "utf8")
		}
	}
	return false
}

// ensuringSource restarts the API server when it is gone, at most once per
// interval, and otherwise passes calls straight through.
type ensuringSource struct {
	client  *api.Client
	rigBin  string
	repo    string
	mu      sync.Mutex
	last    time.Time
	enabled bool
}

func (e *ensuringSource) revive() {
	e.mu.Lock()
	defer e.mu.Unlock()
	if !e.enabled || time.Since(e.last) < 15*time.Second {
		return
	}
	e.last = time.Now()
	_ = api.Ensure(e.repo, e.rigBin, 5*time.Second)
}

func (e *ensuringSource) Snapshot(start, rows int, timeout time.Duration) (*view.Snapshot, error) {
	snap, err := e.client.Snapshot(start, rows, timeout)
	if ui.IsNotRunning(err) {
		e.revive()
		return e.client.Snapshot(start, rows, timeout)
	}
	return snap, err
}

func (e *ensuringSource) Workspace(deep bool, timeout time.Duration) (*view.Workspace, error) {
	return e.client.Workspace(deep, timeout)
}

func (e *ensuringSource) Fleet(timeout time.Duration) (*view.Panel, error) {
	return e.client.Fleet(timeout)
}

func (e *ensuringSource) Settings(timeout time.Duration) (*view.Panel, error) {
	return e.client.Settings(timeout)
}

func (e *ensuringSource) JobEvidence(id string, timeout time.Duration) (*view.Evidence, error) {
	return e.client.JobEvidence(id, timeout)
}

func (e *ensuringSource) Recover(confirmID string, timeout time.Duration) (string, error) {
	return e.client.Recover(confirmID, timeout)
}

func (e *ensuringSource) Log(id string, lines int, timeout time.Duration) (*view.LogReply, error) {
	return e.client.Log(id, lines, timeout)
}

func (e *ensuringSource) Parent(name string, timeout time.Duration) (*view.ShellPlan, error) {
	return e.client.Parent(name, timeout)
}

func (e *ensuringSource) Shell(id string, timeout time.Duration) (*view.ShellPlan, error) {
	return e.client.Shell(id, timeout)
}

func (e *ensuringSource) ChatStatus(timeout time.Duration) (*view.ChatStatus, error) {
	return e.client.ChatStatus(timeout)
}

func (e *ensuringSource) ChatCommands(parent string, timeout time.Duration) (*view.ParentCommands, error) {
	return e.client.ChatCommands(parent, timeout)
}

func (e *ensuringSource) ChatModels(parent string, timeout time.Duration) (*view.ParentModels, error) {
	return e.client.ChatModels(parent, timeout)
}

func (e *ensuringSource) ChatTranscript(session string, tail int, timeout time.Duration) (*view.ChatTranscript, error) {
	return e.client.ChatTranscript(session, tail, timeout)
}

func (e *ensuringSource) ChatSend(message, session, parent string, timeout time.Duration) (*view.ChatStarted, error) {
	return e.client.ChatSend(message, session, parent, timeout)
}

func (e *ensuringSource) ChatCancel(session string, timeout time.Duration) error {
	return e.client.ChatCancel(session, timeout)
}

func (e *ensuringSource) JobDiff(id string, timeout time.Duration) (*view.JobDiff, error) {
	return e.client.JobDiff(id, timeout)
}

func (e *ensuringSource) AppSettings(timeout time.Duration) (*view.AppSettings, error) {
	return e.client.AppSettings(timeout)
}

func (e *ensuringSource) SetSetting(key, value, scope string, timeout time.Duration) (*view.AppSettings, error) {
	return e.client.SetSetting(key, value, scope, timeout)
}

func (e *ensuringSource) Today(timeout time.Duration) (*view.Today, error) {
	return e.client.Today(timeout)
}

func (e *ensuringSource) ShipStatus(timeout time.Duration) (*view.ShipStatus, error) {
	return e.client.ShipStatus(timeout)
}

func (e *ensuringSource) Theme(timeout time.Duration) (*view.ThemeReply, error) {
	return e.client.Theme(timeout)
}

func (e *ensuringSource) SetTheme(theme, scope string, timeout time.Duration) (*view.ThemeReply, error) {
	return e.client.SetTheme(theme, scope, timeout)
}

func (e *ensuringSource) ChatSetParent(name string, timeout time.Duration) error {
	return e.client.ChatSetParent(name, timeout)
}

func (e *ensuringSource) ChatAnswer(session, askID, decision string, timeout time.Duration) error {
	return e.client.ChatAnswer(session, askID, decision, timeout)
}

func (e *ensuringSource) Act(method string, params map[string]any, timeout time.Duration) (string, error) {
	return e.client.Act(method, params, timeout)
}

func run(args []string, stdout, stderr io.Writer) int {
	fs := flag.NewFlagSet("rig-shell", flag.ContinueOnError)
	fs.SetOutput(stderr)
	repo := fs.String("repo", "", "repository root (default $RIG_REPO or the current directory)")
	once := fs.Bool("once", false, "render one frame to stdout and exit")
	size := fs.String("size", "120x36", "frame size for --once")
	fixture := fs.String("fixture", "", "render --once from a board.snapshot JSON file instead of the API")
	wsFixture := fs.String("workspace-fixture", "", "render the Workspace tab for --once from a workspace.status JSON file")
	fleetFixture := fs.String("fleet-fixture", "", "render the Fleet tab for --once from a fleet.status JSON file")
	settingsFixture := fs.String("settings-fixture", "", "render the Settings tab for --once from a settings.view JSON file")
	evFixture := fs.String("evidence-fixture", "", "render the evidence drawer for --once from a job.evidence JSON file")
	tab := fs.String("tab", "Jobs", "tab to show for --once: Session, Jobs, Queue, Workflows, Env, Settings or Logs (Workspace and Fleet mean Env, Term means Session)")
	start := fs.String("start", "", "tab to open on in the interactive board (default Jobs)")
	ascii := fs.Bool("ascii", false, "use ASCII glyphs")
	noEnsure := fs.Bool("no-ensure", false, "do not start the API server when it is not running")
	rigBin := fs.String("rig", os.Getenv("RIG_BIN"), "path to the rig CLI used to start the API server")
	showVersion := fs.Bool("version", false, "print the version and exit")
	launcher := fs.Bool("launcher", false, "open a project: recent ones, a path, or a git URL to clone (rig shell runs this outside a Rig project)")
	layout := fs.String("layout", os.Getenv("RIG_SHELL_LAYOUT"), "app: the design's screens (what rig shell opens); board: the jobs board (the default here)")
	theme := fs.String("theme", os.Getenv("RIG_SHELL_THEME"), "draw this board in a theme without saving it: "+strings.Join(ui.ThemeNames(), ", "))
	if err := fs.Parse(args); err != nil {
		return 2
	}
	if *showVersion {
		fmt.Fprintln(stdout, "rig-shell", Version)
		return 0
	}
	if *repo == "" {
		*repo = os.Getenv("RIG_REPO")
	}
	if *repo == "" {
		wd, err := os.Getwd()
		if err != nil {
			fmt.Fprintln(stderr, "rig-shell:", err)
			return 1
		}
		*repo = wd
	}
	useASCII := wantASCII(*ascii, os.Getenv)
	if *theme != "" && !ui.ApplyTheme(*theme) {
		fmt.Fprintln(stderr, "rig-shell: --theme must be one of", strings.Join(ui.ThemeNames(), ", "))
		return 2
	}

	if *launcher {
		return runLauncher(*repo, *rigBin, stderr)
	}
	if *once {
		return renderOnce(stdout, stderr, *repo, *rigBin, *fixture, *wsFixture, *fleetFixture, *evFixture, *settingsFixture, *size, *tab, useASCII, !*noEnsure)
	}
	client, err := api.New(*repo)
	if err != nil {
		fmt.Fprintln(stderr, "rig-shell:", err)
		return 1
	}
	src := &ensuringSource{client: client, rigBin: *rigBin, repo: *repo, enabled: !*noEnsure}
	model := ui.NewModel(src, useASCII)
	if *start != "" {
		if !ui.ValidTab(ui.TabAlias(*start)) {
			fmt.Fprintln(stderr, "rig-shell: --start must be Session, Jobs, Queue, Workflows, Env, Settings or Logs")
			return 2
		}
		model.SetTab(ui.TabAlias(*start))
	} else if from := ui.TabAlias(os.Getenv("RIG_SHELL_START")); from != "" && ui.ValidTab(from) {
		model.SetTab(from) // set by `rig shell`; an unknown value is ignored
	}
	model.SetParent(os.Getenv("RIG_SHELL_PARENT")) // from `rig shell --parent`; empty means the project's own
	switch *layout {
	case "app":
		model.SetApp(*repo)
		model.SetSplash(os.Getenv("RIG_SPLASH") != "0") // the design's rig mark while the shell opens
	case "", "board":
	default:
		fmt.Fprintln(stderr, "rig-shell: --layout must be app or board")
		return 2
	}
	if *theme != "" {
		model.SetTheme(*theme)
	}
	opts := []tea.ProgramOption{tea.WithAltScreen()}
	if *layout == "app" && ui.LogosSupported(os.Getenv) {
		// the design's agent logos, drawn by a terminal with kitty's image placeholders (Ghostty, kitty)
		model.SetLogos(true)
		opts = append(opts, tea.WithOutput(ui.NewLogoWriter(os.Stdout, os.Getenv("TMUX") != "")))
	} else if *layout == "app" {
		opts = append(opts, tea.WithOutput(ui.NewTermWriter(os.Stdout, os.Getenv("TMUX") != ""))) // for Copy
	}
	if *layout == "app" && os.Getenv("RIG_MOUSE") != "0" {
		// clicks, the wheel and hover, as in codex and grok; Shift-drag still selects text in most terminals
		model.SetMouse(true)
		opts = append(opts, tea.WithMouseAllMotion())
	}
	_, err = tea.NewProgram(model, opts...).Run()
	model.Close() // a program running in the Term pane ends with the board
	if err != nil {
		fmt.Fprintln(stderr, "rig-shell:", err)
		return 1
	}
	return 0
}

func renderOnce(stdout, stderr io.Writer, repo, rigBin, fixture, wsFixture, fleetFixture, evFixture, settingsFixture, size, tab string, ascii, ensure bool) int {
	w, h, err := parseSize(size)
	if err != nil {
		fmt.Fprintln(stderr, "rig-shell:", err)
		return 2
	}
	tab = ui.TabAlias(tab)
	if !ui.ValidTab(tab) {
		fmt.Fprintln(stderr, "rig-shell: --tab must be Session, Jobs, Queue, Workflows, Env, Settings or Logs")
		return 2
	}
	var snap *view.Snapshot
	var ws *view.Workspace
	var wsErr, fleetErr, settingsErr string
	if wsFixture != "" {
		raw, err := os.ReadFile(wsFixture)
		var reply view.WorkspaceReply
		if err == nil {
			err = json.Unmarshal(raw, &reply)
		}
		if err != nil {
			fmt.Fprintln(stderr, "rig-shell:", err)
			return 1
		}
		ws = &reply.View
	}
	var fleetPanel *view.Panel
	if fleetFixture != "" {
		raw, err := os.ReadFile(fleetFixture)
		var reply view.FleetReply
		if err == nil {
			err = json.Unmarshal(raw, &reply)
		}
		if err != nil {
			fmt.Fprintln(stderr, "rig-shell:", err)
			return 1
		}
		fleetPanel = &reply.View
	}
	var settingsPanel *view.Panel
	if settingsFixture != "" {
		raw, err := os.ReadFile(settingsFixture)
		var reply view.SettingsReply
		if err == nil {
			err = json.Unmarshal(raw, &reply)
		}
		if err != nil {
			fmt.Fprintln(stderr, "rig-shell:", err)
			return 1
		}
		settingsPanel = &reply.View
	}
	var evidence *view.Evidence
	if evFixture != "" {
		raw, err := os.ReadFile(evFixture)
		evidence = &view.Evidence{}
		if err == nil {
			err = json.Unmarshal(raw, evidence)
		}
		if err != nil {
			fmt.Fprintln(stderr, "rig-shell:", err)
			return 1
		}
	}
	if fixture != "" {
		raw, err := os.ReadFile(fixture)
		if err == nil {
			snap = &view.Snapshot{}
			err = json.Unmarshal(raw, snap)
		}
		if err != nil {
			fmt.Fprintln(stderr, "rig-shell:", err)
			return 1
		}
	} else {
		client, err := api.New(repo)
		if err != nil {
			fmt.Fprintln(stderr, "rig-shell:", err)
			return 1
		}
		src := &ensuringSource{client: client, rigBin: rigBin, repo: repo, enabled: ensure}
		if snap, err = src.Snapshot(0, 200, 5*time.Second); err != nil {
			fmt.Fprintln(stderr, "rig-shell:", err)
			return 1
		}
		if tab == ui.EnvTab && fleetPanel == nil {
			if fleetPanel, err = src.Fleet(20 * time.Second); err != nil {
				fleetErr = err.Error() // the workspace half can still be shown
			}
		}
		if tab == ui.SettingsTab && settingsPanel == nil {
			if settingsPanel, err = src.Settings(5 * time.Second); err != nil {
				settingsErr = err.Error()
			}
		}
		if tab == ui.EnvTab && ws == nil {
			if ws, err = src.Workspace(true, 15*time.Second); err != nil {
				wsErr = err.Error()
			}
		}
	}
	if snap == nil {
		snap = &view.Snapshot{}
		snap.View = view.View{Version: view.Version}
	}
	st := ui.NewState()
	st.Tab, st.ASCII, st.Workspace, st.Fleet = tab, ascii, ws, fleetPanel
	st.WorkspaceE, st.FleetE = wsErr, fleetErr
	st.Settings, st.SettingsE = settingsPanel, settingsErr
	switch {
	case evidence != nil && tab == ui.SessionTab:
		// the Session screen docks the selected job's evidence instead of opening the drawer
		if rows := snap.View.Rows("Jobs"); len(rows) > 0 {
			st.DockID, st.DockEvidence = rows[0].ID, evidence
		}
	case evidence != nil:
		st.EvidenceOpen, st.EvidenceID, st.Evidence = true, evidence.JobID, evidence
	}
	fmt.Fprintln(stdout, ui.Render(&snap.View, st, w, h))
	return 0
}

// runLauncher shows "Open a project" and hands the chosen project to rig shell, replacing this process.
func runLauncher(input, rigBin string, stderr io.Writer) int {
	home := os.Getenv("RIG_HOME")
	if home == "" {
		if h, err := os.UserHomeDir(); err == nil {
			home = filepath.Join(h, ".rig")
		}
	}
	code := os.Getenv("RIG_CODE_DIR")
	if code == "" {
		if h, err := os.UserHomeDir(); err == nil {
			code = filepath.Join(h, "code")
		}
	}
	if wd, err := os.Getwd(); err == nil && input == wd {
		input = "" // rig shell passes the folder it ran in; the launcher starts with an empty box then
	}
	l := ui.NewLauncher(home, code, input)
	opts := []tea.ProgramOption{tea.WithAltScreen()}
	if os.Getenv("RIG_MOUSE") != "0" {
		l.SetMouse(true)
		opts = append(opts, tea.WithMouseAllMotion())
	}
	if _, err := tea.NewProgram(l, opts...).Run(); err != nil {
		fmt.Fprintln(stderr, "rig-shell:", err)
		return 1
	}
	if l.Open == "" {
		return 0
	}
	if rigBin == "" {
		rigBin = "rig"
	}
	path, err := exec.LookPath(rigBin)
	if err != nil {
		fmt.Fprintln(stderr, "rig-shell: cannot find rig to open", l.Open)
		return 1
	}
	if err := syscall.Exec(path, []string{rigBin, "shell", "--repo", l.Open}, os.Environ()); err != nil {
		fmt.Fprintln(stderr, "rig-shell:", err)
		return 1
	}
	return 0
}
