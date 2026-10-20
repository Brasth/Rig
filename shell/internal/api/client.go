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
func (c *Client) Log(id string, lines int, timeout time.Duration) (*view.LogReply, error) {
	var out view.LogReply
	if err := c.Call("log.follow", map[string]any{"id": id, "lines": lines}, timeout, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// Parent asks the server for the command that runs the parent CLI in a pane.
func (c *Client) Parent(timeout time.Duration) (*view.ShellPlan, error) {
	var out view.ShellPlan
	if err := c.Call("shell.parent", nil, timeout, &out); err != nil {
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

// ChatTranscript fetches the tail of one chat session.
func (c *Client) ChatTranscript(session string, tail int, timeout time.Duration) (*view.ChatTranscript, error) {
	var out view.ChatTranscript
	if err := c.Call("chat.transcript", map[string]any{"session": session, "tail": tail}, timeout, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// ChatSend starts one turn; an empty session starts a new one. The answer arrives in the transcript.
func (c *Client) ChatSend(message, session string, timeout time.Duration) (*view.ChatStarted, error) {
	var out view.ChatStarted
	params := map[string]any{"message": message}
	if session != "" {
		params["session"] = session
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
