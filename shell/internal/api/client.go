// Package api is a client for the Rig local API (docs/local-api.md).
package api

import (
	"bufio"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"github.com/Brasth/Rig/shell/internal/runtimedir"
	"github.com/Brasth/Rig/shell/internal/view"
)

const maxResponse = 8 << 20

// ErrNotRunning means no API server answered. Callers may start one.
var ErrNotRunning = errors.New("rig API is not running")

// RPCError is an error the server reported for a request it received.
type RPCError struct {
	Code    int
	Message string
}

func (e *RPCError) Error() string { return e.Message }

type Client struct {
	Repo      string
	Sock      string
	TokenPath string
}

// New locates the API endpoint for repo without contacting it.
func New(repo string) (*Client, error) {
	dir, err := runtimedir.Dir(repo)
	if err != nil {
		return nil, err
	}
	return &Client{Repo: repo, Sock: filepath.Join(dir, "api.sock"), TokenPath: filepath.Join(dir, "api.token")}, nil
}

type response struct {
	RigAPI int             `json:"rig_api"`
	Result json.RawMessage `json:"result"`
	Error  *struct {
		Code    int    `json:"code"`
		Message string `json:"message"`
	} `json:"error"`
}

// Call sends one request and decodes the result into out (which may be nil).
// A server that cannot be reached returns ErrNotRunning, never a partial send.
func (c *Client) Call(method string, params any, timeout time.Duration, out any) error {
	raw, err := os.ReadFile(c.TokenPath)
	if err != nil {
		return ErrNotRunning
	}
	if params == nil {
		params = map[string]any{}
	}
	payload, err := json.Marshal(map[string]any{
		"jsonrpc": "2.0", "id": 1, "method": method, "params": params, "token": strings.TrimSpace(string(raw)),
	})
	if err != nil {
		return err
	}
	conn, err := net.DialTimeout("unix", c.Sock, timeout)
	if err != nil {
		return ErrNotRunning
	}
	defer conn.Close()
	_ = conn.SetDeadline(time.Now().Add(timeout))
	if _, err := conn.Write(append(payload, '\n')); err != nil {
		return err
	}
	line, err := bufio.NewReaderSize(conn, 64<<10).ReadBytes('\n')
	if err != nil {
		return fmt.Errorf("rig API: reading response: %w", err)
	}
	if len(line) > maxResponse {
		return errors.New("rig API: response exceeds size limit")
	}
	var resp response
	if err := json.Unmarshal(line, &resp); err != nil {
		return fmt.Errorf("rig API: invalid response: %w", err)
	}
	if resp.Error != nil {
		return &RPCError{Code: resp.Error.Code, Message: resp.Error.Message}
	}
	if out != nil && len(resp.Result) > 0 {
		return json.Unmarshal(resp.Result, out)
	}
	return nil
}

// Ping reports whether a server answers.
func (c *Client) Ping(timeout time.Duration) error {
	return c.Call("api.ping", nil, timeout, nil)
}

// Snapshot fetches the board view.
func (c *Client) Snapshot(start, rows int, timeout time.Duration) (*view.Snapshot, error) {
	var snap view.Snapshot
	if err := c.Call("board.snapshot", map[string]any{"start": start, "rows": rows}, timeout, &snap); err != nil {
		return nil, err
	}
	if snap.View.Version != view.Version {
		return nil, fmt.Errorf("rig API view version %d is not supported (shell expects %d)", snap.View.Version, view.Version)
	}
	return &snap, nil
}

// Workspace fetches the workspace panel. deep also asks for ports and dc-cli's
// own diagnosis, which costs the server a few dc invocations (it caches them).
func (c *Client) Workspace(deep bool, timeout time.Duration) (*view.Workspace, error) {
	var reply view.WorkspaceReply
	if err := c.Call("workspace.status", map[string]any{"deep": deep}, timeout, &reply); err != nil {
		return nil, err
	}
	if reply.View.Version != view.WorkspaceVersion {
		return nil, fmt.Errorf("rig API workspace view version %d is not supported (shell expects %d)", reply.View.Version, view.WorkspaceVersion)
	}
	reply.View.Recovery = reply.Status.Recovery
	return &reply.View, nil
}

// Fleet fetches the fleet panel.
func (c *Client) Fleet(timeout time.Duration) (*view.Panel, error) {
	var reply view.FleetReply
	if err := c.Call("fleet.status", nil, timeout, &reply); err != nil {
		return nil, err
	}
	return &reply.View, nil
}

// Settings fetches the read-only settings panel.
func (c *Client) Settings(timeout time.Duration) (*view.Panel, error) {
	var reply view.SettingsReply
	if err := c.Call("settings.view", nil, timeout, &reply); err != nil {
		return nil, err
	}
	return &reply.View, nil
}

// JobEvidence fetches the evidence drawer for one job.
func (c *Client) JobEvidence(id string, timeout time.Duration) (*view.Evidence, error) {
	var ev view.Evidence
	if err := c.Call("job.evidence", map[string]any{"id": id}, timeout, &ev); err != nil {
		return nil, err
	}
	return &ev, nil
}

// Recover applies the environment recovery plan the owner confirmed, by id, and says what happened.
func (c *Client) Recover(confirmID string, timeout time.Duration) (string, error) {
	var out view.RecoverReply
	if err := c.Call("env.recover", map[string]any{"confirm_id": confirmID}, timeout, &out); err != nil {
		return "", err
	}
	state := "still needs attention"
	if out.After.Ready {
		state = "environment ready"
	}
	return fmt.Sprintf("applied %s (exit %d): %s", out.Applied, out.Exit, state), nil
}

// Log fetches the tail of a job's log. An empty id lets the server pick the job worth watching.
// JobProblems fetches what a job's failed checks found; an empty id means the newest job with any.
func (c *Client) JobProblems(id string, timeout time.Duration) (*view.JobProblems, error) {
	var out view.JobProblems
	if err := c.Call("job.problems", map[string]any{"id": id}, timeout, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// JobThread fetches a helper's thread with the parent.
func (c *Client) JobThread(id string, timeout time.Duration) (*view.JobThread, error) {
	var out view.JobThread
	if err := c.Call("job.thread", map[string]any{"id": id}, timeout, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

func (c *Client) Log(id string, lines int, timeout time.Duration) (*view.LogReply, error) {
	var out view.LogReply
	if err := c.Call("log.follow", map[string]any{"id": id, "lines": lines}, timeout, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// Parent asks the server for the command that runs the parent CLI in a pane. name picks one of the parent
// CLIs the server allows (from `rig shell --parent`); empty means the project's configured parent.
func (c *Client) Parent(name string, timeout time.Duration) (*view.ShellPlan, error) {
	var out view.ShellPlan
	var params map[string]any
	if name != "" {
		params = map[string]any{"parent": name}
	}
	if err := c.Call("shell.parent", params, timeout, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// Shell asks the server for the command that opens a shell in a job's container.
func (c *Client) Shell(id string, timeout time.Duration) (*view.ShellPlan, error) {
	var out view.ShellPlan
	if err := c.Call("env.shell", map[string]any{"id": id}, timeout, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// ChatStatus asks whether the chat experiment is on. An older server without it answers with an error.
func (c *Client) ChatStatus(timeout time.Duration) (*view.ChatStatus, error) {
	var out view.ChatStatus
	if err := c.Call("chat.status", nil, timeout, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// ChatCommands lists the parent's own skills and slash commands; an empty parent means the project's.
func (c *Client) ChatCommands(parent string, timeout time.Duration) (*view.ParentCommands, error) {
	var out view.ParentCommands
	if err := c.Call("chat.commands", map[string]any{"parent": parent}, timeout, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// ChatModels lists every model the parent CLI offers; an empty parent means the project's.
func (c *Client) ChatModels(parent string, timeout time.Duration) (*view.ParentModels, error) {
	var out view.ParentModels
	if err := c.Call("chat.models", map[string]any{"parent": parent}, timeout, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// ChatTranscript fetches the tail of one chat session.
func (c *Client) ChatTranscript(session string, tail int, timeout time.Duration) (*view.ChatTranscript, error) {
	var out view.ChatTranscript
	if err := c.Call("chat.transcript", map[string]any{"session": session, "tail": tail}, timeout, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// ChatSend starts one turn; an empty session starts a new one, with parent (a name the server checks) or the
// project's own parent. The answer arrives in the transcript.
func (c *Client) ChatSend(message, session, parent string, timeout time.Duration) (*view.ChatStarted, error) {
	var out view.ChatStarted
	params := map[string]any{"message": message}
	if session != "" {
		params["session"] = session
	}
	if parent != "" {
		params["parent"] = parent
	}
	if err := c.Call("chat.send", params, timeout, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// ChatCancel stops a session's running turn.
func (c *Client) ChatCancel(session string, timeout time.Duration) error {
	return c.Call("chat.cancel", map[string]any{"session": session}, timeout, nil)
}

// ChatRewind puts the working tree back as it was before a turn: every file that turn and the later ones changed, or
// just path. dry only reports what would change; undo takes the last rewind back.
func (c *Client) ChatRewind(session string, turn int, path string, dry, undo bool, timeout time.Duration) (*view.ChatRewound, error) {
	var out view.ChatRewound
	params := map[string]any{"session": session, "turn": turn, "path": path, "dry": dry, "undo": undo}
	if err := c.Call("chat.rewind", params, timeout, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// ChatFork starts a conversation that begins as a copy of this one up to turn (all of it when 0).
func (c *Client) ChatFork(session string, turn int, timeout time.Duration) (*view.ChatForked, error) {
	var out view.ChatForked
	if err := c.Call("chat.fork", map[string]any{"session": session, "turn": turn}, timeout, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// ChatSearch finds the conversations with a message that contains the words of query.
func (c *Client) ChatSearch(query string, timeout time.Duration) ([]view.ChatHit, error) {
	var out struct {
		Rows []view.ChatHit `json:"rows"`
	}
	if err := c.Call("chat.search", map[string]any{"query": query}, timeout, &out); err != nil {
		return nil, err
	}
	return out.Rows, nil
}

// JobDiff is what one job changed (job.diff).
func (c *Client) JobDiff(id string, timeout time.Duration) (*view.JobDiff, error) {
	var out view.JobDiff
	if err := c.Call("job.diff", map[string]any{"id": id}, timeout, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// AppSettings is the Settings screen's values (settings.app).
func (c *Client) AppSettings(timeout time.Duration) (*view.AppSettings, error) {
	var out view.AppSettings
	if err := c.Call("settings.app", map[string]any{}, timeout, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// SetSetting changes one setting (settings.set) and returns the screen's values after it.
func (c *Client) SetSetting(key, value, scope string, timeout time.Duration) (*view.AppSettings, error) {
	var out view.AppSettings
	if err := c.Call("settings.set", map[string]any{"key": key, "value": value, "scope": scope}, timeout, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// Today is what helpers applied today (applies.today).
func (c *Client) Today(timeout time.Duration) (*view.Today, error) {
	var out view.Today
	if err := c.Call("applies.today", map[string]any{}, timeout, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// ShipStatus is what is ahead of the upstream branch (ship.status).
func (c *Client) ShipStatus(timeout time.Duration) (*view.ShipStatus, error) {
	var out view.ShipStatus
	if err := c.Call("ship.status", map[string]any{}, timeout, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// Theme is the board's colour theme (ui.theme).
func (c *Client) Theme(timeout time.Duration) (*view.ThemeReply, error) {
	var out view.ThemeReply
	if err := c.Call("ui.theme", map[string]any{}, timeout, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// SetTheme keeps the board's colour theme for all repos ("all") or this repo ("repo").
func (c *Client) SetTheme(theme, scope string, timeout time.Duration) (*view.ThemeReply, error) {
	var out view.ThemeReply
	if err := c.Call("ui.set_theme", map[string]any{"theme": theme, "scope": scope}, timeout, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// ChatSetParent names the project's parent CLI in .rig/harness.toml.
func (c *Client) ChatSetParent(name string, timeout time.Duration) error {
	return c.Call("chat.parent", map[string]any{"name": name}, timeout, nil)
}

// ChatAnswer allows or denies the parent's pending permission request (decision "allow" or "deny").
func (c *Client) ChatAnswer(session, askID, decision string, timeout time.Duration) error {
	return c.Call("chat.answer", map[string]any{"session": session, "ask_id": askID, "decision": decision}, timeout, nil)
}

type textResult struct {
	Text string `json:"text"`
}

// Act runs a mutating method and returns the tool's text.
func (c *Client) Act(method string, params map[string]any, timeout time.Duration) (string, error) {
	var out textResult
	if err := c.Call(method, params, timeout, &out); err != nil {
		return "", err
	}
	return out.Text, nil
}

// Ensure starts a detached, idle-limited server through the rig CLI and waits
// until it answers. rigBin defaults to "rig" on PATH.
func Ensure(repo, rigBin string, wait time.Duration) error {
	if rigBin == "" {
		rigBin = "rig"
	}
	cmd := exec.Command(rigBin, "api", "ensure", "--timeout", fmt.Sprintf("%.0f", wait.Seconds()))
	cmd.Dir = repo
	if out, err := cmd.CombinedOutput(); err != nil {
		return fmt.Errorf("rig api ensure: %v: %s", err, strings.TrimSpace(string(out)))
	}
	return nil
}
