package api

import (
	"bufio"
	"encoding/json"
	"errors"
	"net"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/Brasth/Rig/shell/internal/view"
)

type fakeServer struct {
	t        *testing.T
	client   *Client
	requests chan map[string]any
}

// serve answers one request per connection with reply(method) until the test ends.
func serve(t *testing.T, reply func(method string) string) *fakeServer {
	t.Helper()
	// Unix socket paths are short-limited; keep the directory name small.
	dir, err := os.MkdirTemp("/tmp", "rsh")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.RemoveAll(dir) })
	sock := filepath.Join(dir, "api.sock")
	token := filepath.Join(dir, "api.token")
	if err := os.WriteFile(token, []byte("secret-token\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	ln, err := net.Listen("unix", sock)
	if err != nil {
		t.Skipf("unix sockets unavailable: %v", err)
	}
	t.Cleanup(func() { ln.Close() })
	fs := &fakeServer{t: t, client: &Client{Repo: "/r", Sock: sock, TokenPath: token}, requests: make(chan map[string]any, 16)}
	go func() {
		for {
			conn, err := ln.Accept()
			if err != nil {
				return
			}
			go func(c net.Conn) {
				defer c.Close()
				line, err := bufio.NewReader(c).ReadBytes('\n')
				if err != nil {
					return
				}
				var req map[string]any
				_ = json.Unmarshal(line, &req)
				fs.requests <- req
				method, _ := req["method"].(string)
				_, _ = c.Write([]byte(reply(method) + "\n"))
			}(conn)
		}
	}()
	return fs
}

func TestSnapshotSendsTokenAndParamsAndDecodesView(t *testing.T) {
	fs := serve(t, func(string) string {
		return `{"jsonrpc":"2.0","id":1,"rig_api":1,"result":{"captured_wall":5,"view":{"version":1,"header":{"project":"p","attention":2,"running":1,"slots":1,"cap":3},"tabs":[],"jobs":[{"id":"j1","title":"t","label":"Running","tone":"active","glyph":"●","unknown_future_field":1}],"queue":[],"workflows":[]}}}`
	})
	snap, err := fs.client.Snapshot(3, 50, time.Second)
	if err != nil {
		t.Fatal(err)
	}
	if snap.View.Header.Attention != 2 || snap.View.Jobs[0].ID != "j1" || snap.View.Jobs[0].Glyph != "●" {
		t.Fatalf("bad decode: %+v", snap)
	}
	req := <-fs.requests
	if req["token"] != "secret-token" || req["method"] != "board.snapshot" || req["jsonrpc"] != "2.0" {
		t.Fatalf("bad request %v", req)
	}
	params := req["params"].(map[string]any)
	if params["start"] != float64(3) || params["rows"] != float64(50) {
		t.Fatalf("bad params %v", params)
	}
}

func TestUnsupportedViewVersionIsRefused(t *testing.T) {
	fs := serve(t, func(string) string {
		return `{"jsonrpc":"2.0","id":1,"rig_api":1,"result":{"view":{"version":99}}}`
	})
	if _, err := fs.client.Snapshot(0, 10, time.Second); err == nil {
		t.Fatal("a view version the shell does not know must be an error")
	}
}

func TestServerErrorsAreRPCErrorsNotNotRunning(t *testing.T) {
	fs := serve(t, func(string) string {
		return `{"jsonrpc":"2.0","id":1,"rig_api":1,"error":{"code":-32000,"message":"Rig is disabled for this project; run rig on"}}`
	})
	_, err := fs.client.Act("job.cancel", map[string]any{"id": "x"}, time.Second)
	var rpc *RPCError
	if !errors.As(err, &rpc) || rpc.Code != -32000 || errors.Is(err, ErrNotRunning) {
		t.Fatalf("want RPCError, got %v", err)
	}
}

func TestActReturnsText(t *testing.T) {
	fs := serve(t, func(string) string {
		return `{"jsonrpc":"2.0","id":1,"rig_api":1,"result":{"text":"allow job-7f2  npm i"}}`
	})
	text, err := fs.client.Act("job.allow", map[string]any{"id": "job-7f2"}, time.Second)
	if err != nil || text != "allow job-7f2  npm i" {
		t.Fatalf("got %q %v", text, err)
	}
}

func TestMissingTokenOrSocketIsNotRunning(t *testing.T) {
	dir := t.TempDir()
	c := &Client{Sock: filepath.Join(dir, "api.sock"), TokenPath: filepath.Join(dir, "api.token")}
	if err := c.Ping(100 * time.Millisecond); !errors.Is(err, ErrNotRunning) {
		t.Fatalf("no token: %v", err)
	}
	if err := os.WriteFile(c.TokenPath, []byte("t"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := c.Ping(100 * time.Millisecond); !errors.Is(err, ErrNotRunning) {
		t.Fatalf("no socket: %v", err)
	}
}

func TestViewRowsByTab(t *testing.T) {
	v := &view.View{Jobs: []view.Row{{ID: "a"}}, Queue: []view.Row{{ID: "b"}, {ID: "c"}}}
	if len(v.Rows("Jobs")) != 1 || len(v.Rows("Queue")) != 2 || v.Rows("Settings") != nil {
		t.Fatal("Rows picks the wrong slice")
	}
	var nilView *view.View
	if nilView.Rows("Jobs") != nil {
		t.Fatal("nil view must have no rows")
	}
}

func TestWorkspaceSendsDeepFlagAndChecksVersion(t *testing.T) {
	fs := serve(t, func(string) string {
		return `{"jsonrpc":"2.0","id":1,"rig_api":1,"result":{"status":{},"view":{"version":1,"attention":true,"sections":[{"title":"Workspace","lines":[{"text":"kind     none","tone":""}]}]}}}`
	})
	ws, err := fs.client.Workspace(true, time.Second)
	if err != nil || !ws.Attention || ws.Sections[0].Lines[0].Text != "kind     none" {
		t.Fatalf("got %+v %v", ws, err)
	}
	req := <-fs.requests
	if req["method"] != "workspace.status" || req["params"].(map[string]any)["deep"] != true {
		t.Fatalf("bad request %v", req)
	}
	old := serve(t, func(string) string {
		return `{"jsonrpc":"2.0","id":1,"rig_api":1,"result":{"view":{"version":7}}}`
	})
	if _, err := old.client.Workspace(false, time.Second); err == nil {
		t.Fatal("unknown workspace view version must be refused")
	}
}
