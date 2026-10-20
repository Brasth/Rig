// Package term runs a program on a pseudo-terminal and keeps its screen in an emulator, so the board can show
// an interactive program (a shell in a container, the parent CLI) inside one of its tabs without tmux.
//
// Limits, by design: wide characters take one cell in the emulator, so CJK text and emoji can misalign; there is
// no scrollback (only the live screen); the mouse is not forwarded; bracketed paste is forwarded as plain input.
package term

import (
	"fmt"
	"os"
	"os/exec"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/creack/pty"
	"github.com/hinshun/vt10x"
)

// Pane is one running (or finished) program and its emulated screen.
type Pane struct {
	cmd  *exec.Cmd
	ptmx *os.File
	vt   vt10x.Terminal

	mu      sync.Mutex
	cols    int
	rows    int
	exited  bool
	exitErr error
	closed  bool

	updates chan struct{}
	done    chan struct{}
}

// Start runs argv in dir with the extra environment entries (KEY=VALUE) on a new pseudo-terminal of the given size.
func Start(argv []string, dir string, env []string, cols, rows int) (*Pane, error) {
	if len(argv) == 0 || argv[0] == "" {
		return nil, fmt.Errorf("no command to run")
	}
	cols, rows = clampSize(cols, rows)
	cmd := exec.Command(argv[0], argv[1:]...)
	cmd.Dir = dir
	cmd.Env = append(os.Environ(), "TERM=xterm-256color")
	cmd.Env = append(cmd.Env, env...)
	ptmx, err := pty.StartWithSize(cmd, &pty.Winsize{Cols: uint16(cols), Rows: uint16(rows)})
	if err != nil {
		return nil, err
	}
	p := &Pane{cmd: cmd, ptmx: ptmx, cols: cols, rows: rows, updates: make(chan struct{}, 1), done: make(chan struct{})}
	// Answers the program asks the terminal for (cursor position and the like) go back down the pty.
	p.vt = vt10x.New(vt10x.WithSize(cols, rows), vt10x.WithWriter(ptmx))
	go p.pump()
	return p, nil
}

func clampSize(cols, rows int) (int, int) {
	return max(2, min(cols, 500)), max(2, min(rows, 200))
}

func (p *Pane) notify() {
	select {
	case p.updates <- struct{}{}:
	default: // one pending notice is enough; the reader renders the whole screen
	}
}

func (p *Pane) pump() {
	buf := make([]byte, 32<<10)
	for {
		n, err := p.ptmx.Read(buf)
		if n > 0 {
			_, _ = p.vt.Write(buf[:n])
			p.notify()
		}
		if err != nil {
			break
		}
	}
	werr := p.cmd.Wait()
	p.mu.Lock()
	p.exited, p.exitErr = true, werr
	p.mu.Unlock()
	close(p.done)
	p.notify()
}

// Updates receives a value whenever the screen changed or the program exited. Values coalesce.
func (p *Pane) Updates() <-chan struct{} { return p.updates }

// Exited reports whether the program has ended and how.
func (p *Pane) Exited() (bool, error) {
	p.mu.Lock()
	defer p.mu.Unlock()
	return p.exited, p.exitErr
}

// Size is the pane's current size in cells.
func (p *Pane) Size() (int, int) {
	p.mu.Lock()
	defer p.mu.Unlock()
	return p.cols, p.rows
}

// Resize changes the pseudo-terminal and the emulated screen together.
func (p *Pane) Resize(cols, rows int) {
	cols, rows = clampSize(cols, rows)
	p.mu.Lock()
	same := cols == p.cols && rows == p.rows
	p.cols, p.rows = cols, rows
	closed := p.closed || p.exited
	p.mu.Unlock()
	if same || closed {
		return
	}
	_ = pty.Setsize(p.ptmx, &pty.Winsize{Cols: uint16(cols), Rows: uint16(rows)})
	p.vt.Resize(cols, rows)
	p.notify()
}

// Write sends bytes to the program as if typed. It does nothing once the program has ended.
func (p *Pane) Write(b []byte) {
	if ok, _ := p.Exited(); ok || len(b) == 0 {
		return
	}
	_, _ = p.ptmx.Write(b)
}

// AppCursor is true while the program has asked for application cursor keys (arrows send ESC O A, not ESC [ A).
func (p *Pane) AppCursor() bool { return p.vt.Mode()&vt10x.ModeAppCursor != 0 }

// Title is the window title the program set, if any.
func (p *Pane) Title() string { return p.vt.Title() }

// Close hangs up the program's session, then kills what is left. It is safe to call twice.
func (p *Pane) Close() {
	p.mu.Lock()
	if p.closed {
		p.mu.Unlock()
		return
	}
	p.closed = true
	exited := p.exited
	p.mu.Unlock()
	if !exited && p.cmd.Process != nil {
		pid := p.cmd.Process.Pid
		_ = syscall.Kill(-pid, syscall.SIGHUP) // the pty made the child a session leader, so -pid is its group
		select {
		case <-p.done:
		case <-time.After(time.Second):
			_ = syscall.Kill(-pid, syscall.SIGKILL)
			select {
			case <-p.done:
			case <-time.After(2 * time.Second):
			}
		}
	}
	_ = p.ptmx.Close()
}

// Wait blocks until the program has ended. Intended for tests and for Close.
func (p *Pane) Wait(timeout time.Duration) bool {
	select {
	case <-p.done:
		return true
	case <-time.After(timeout):
		return false
	}
}

// Lines renders the screen as one string per row, with SGR styling when styled is set. The cursor is drawn in
// reverse video when showCursor is set and the program wants it visible.
func (p *Pane) Lines(styled, showCursor bool) []string {
	p.vt.Lock()
	defer p.vt.Unlock()
	cols, rows := p.vt.Size()
	cur := p.vt.Cursor()
	curOn := showCursor && p.vt.CursorVisible()
	out := make([]string, rows)
	for y := 0; y < rows; y++ {
		cx := -1
		if curOn && cur.Y == y {
			cx = cur.X
		}
		out[y] = renderRow(p.vt, y, cols, styled, cx)
	}
	return out
}

// Text is the screen as plain text, trailing spaces trimmed. For tests and for copying what is on screen.
func (p *Pane) Text() string {
	lines := p.Lines(false, false)
	for i, l := range lines {
		lines[i] = strings.TrimRight(l, " ")
	}
	return strings.TrimRight(strings.Join(lines, "\n"), "\n")
}
