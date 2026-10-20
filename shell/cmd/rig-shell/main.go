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
var Version = "0.1.0"

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
	tab := fs.String("tab", "Jobs", "tab to show for --once: Jobs, Queue, Workflows or Workspace")
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
		return renderOnce(stdout, stderr, *repo, *rigBin, *fixture, *wsFixture, *size, *tab, useASCII, !*noEnsure)
	}
	client, err := api.New(*repo)
	if err != nil {
		fmt.Fprintln(stderr, "rig-shell:", err)
		return 1
	}
	src := &ensuringSource{client: client, rigBin: *rigBin, repo: *repo, enabled: !*noEnsure}
	if _, err := tea.NewProgram(ui.NewModel(src, useASCII), tea.WithAltScreen()).Run(); err != nil {
		fmt.Fprintln(stderr, "rig-shell:", err)
		return 1
	}
	return 0
}

func renderOnce(stdout, stderr io.Writer, repo, rigBin, fixture, wsFixture, size, tab string, ascii, ensure bool) int {
	w, h, err := parseSize(size)
	if err != nil {
		fmt.Fprintln(stderr, "rig-shell:", err)
		return 2
	}
	valid := false
	for _, t := range ui.Tabs {
		valid = valid || t == tab
	}
	if !valid {
		fmt.Fprintln(stderr, "rig-shell: --tab must be Jobs, Queue, Workflows or Workspace")
		return 2
	}
	var snap *view.Snapshot
	var ws *view.Workspace
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
		if tab == ui.WorkspaceTab && ws == nil {
			if ws, err = src.Workspace(true, 15*time.Second); err != nil {
				fmt.Fprintln(stderr, "rig-shell:", err)
				return 1
			}
		}
	}
	if snap == nil {
		snap = &view.Snapshot{}
		snap.View = view.View{Version: view.Version}
	}
	st := ui.NewState()
	st.Tab, st.ASCII, st.Workspace = tab, ascii, ws
	fmt.Fprintln(stdout, ui.Render(&snap.View, st, w, h))
	return 0
}
