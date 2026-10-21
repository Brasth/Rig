// Command rig-shell is the Rig board pane. It reads everything from the local
// API and never touches .rig files itself.
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"os"
	"strconv"
	"strings"
	"sync"
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

func (e *ensuringSource) ChatTranscript(session string, tail int, timeout time.Duration) (*view.ChatTranscript, error) {
	return e.client.ChatTranscript(session, tail, timeout)
}

func (e *ensuringSource) ChatSend(message, session string, timeout time.Duration) (*view.ChatStarted, error) {
	return e.client.ChatSend(message, session, timeout)
}

func (e *ensuringSource) ChatCancel(session string, timeout time.Duration) error {
	return e.client.ChatCancel(session, timeout)
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
	_, err = tea.NewProgram(model, tea.WithAltScreen()).Run()
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
