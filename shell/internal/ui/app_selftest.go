package ui

import (
	"fmt"
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/Brasth/Rig/shell/internal/view"
)

// /selftest (or ⌃k › test every parent): does each installed parent really work through Rig, flag by flag. ⏎ on the
// first row starts it: a few tiny real turns per installed parent (an answer, a reasoning level, a model, plan mode,
// auto-edit, stop) in a throwaway project, never in this repository, and the checks fill in as they finish. The
// report is also kept in .rig/selftest.txt to paste into a bug report. It costs what short prompts cost, so it never
// starts by itself.

var selftestEvery = 2 * time.Second // how often the open screen asks again while the test runs

type selftestMsg struct {
	report *view.SelfTest
	err    error
}

type selftestTickMsg struct{}

type selftestStartedMsg struct {
	started *view.SelfTestStarted
	err     error
}

var selftestMark = map[string]string{"pass": "✓", "warn": "!", "fail": "✕", "skip": "–", "waiting": "·", "running": "…"}

// selftestItems are the palette's rows: the start row, then the report parent by parent.
func selftestItems(st State, q string) []paletteItem {
	a := st.App
	r := a.SelfTest
	var out []paletteItem
	if r == nil || !r.Running {
		label := "▶ run the self-test"
		detail := "a few short real turns per installed parent, in a throwaway project · costs a few cents"
		if r != nil && r.State == "done" {
			label = "▶ run it again"
		}
		out = append(out, paletteItem{"self-test", label, detail, "selftest-run"})
	} else {
		out = append(out, paletteItem{"self-test", "running…", "the checks fill in below as they finish", ""})
	}
	if a.SelfTestE != "" {
		return append(out, paletteItem{"self-test", "could not read the report", a.SelfTestE, ""})
	}
	if r != nil {
		fails := 0
		for _, p := range r.Parents {
			for _, c := range p.Checks {
				if q != "" && !strings.Contains(strings.ToLower(p.Name+" "+c.Label+" "+c.Detail), q) {
					continue
				}
				if c.Status == "fail" {
					fails++
				}
				detail := clean(c.Detail)
				if c.Seconds >= 1 {
					detail = strings.TrimSpace(detail + fmt.Sprintf("  (%.0fs)", c.Seconds))
				}
				out = append(out, paletteItem{p.Name, selftestMark[c.Status] + " " + c.Label, detail, ""})
			}
		}
		switch {
		case r.State == "done" && len(r.Parents) == 0:
			out = append(out, paletteItem{"self-test", "no parent CLI is installed", "install codex, claude, grok… and run it again", ""})
		case r.State == "done":
			note := "everything that ran passed"
			if fails > 0 {
				note = fmt.Sprintf("%d check%s failed: the report is in .rig/selftest.txt, paste it in a bug report", fails, plural(fails))
			}
			out = append(out, paletteItem{"self-test", "report", note, "file:.rig/selftest.txt"})
		case r.State == "stopped":
			out = append(out, paletteItem{"self-test", "the test stopped before it finished", "run it again", ""})
		}
	}
	return out
}

func (m *Model) openSelftest() tea.Cmd {
	a := m.st.App
	a.Settings, a.Palette, a.SelfTestE = nil, &Palette{SelfTest: true}, ""
	return m.askSelftest()
}

func (m *Model) askSelftest() tea.Cmd {
	src := m.src
	return func() tea.Msg {
		r, err := src.SelfTestStatus(callTimeout)
		return selftestMsg{report: r, err: err}
	}
}

func (m *Model) startSelftest() tea.Cmd {
	src := m.src
	m.st.Message = "starting the self-test…"
	return func() tea.Msg {
		s, err := src.SelfTestStart(nil, callTimeout)
		return selftestStartedMsg{started: s, err: err}
	}
}

func (m *Model) onSelftestStarted(msg selftestStartedMsg) tea.Cmd {
	switch {
	case msg.err != nil:
		m.st.Message = "self-test: " + clean(msg.err.Error())
	case msg.started != nil && !msg.started.Started:
		m.st.Message = "self-test: " + msg.started.Reason
	default:
		m.st.Message = "self-test running: it takes a few minutes"
	}
	m.st.App.Palette = &Palette{SelfTest: true}
	return m.askSelftest()
}

func (m *Model) onSelftest(msg selftestMsg) tea.Cmd {
	a := m.st.App
	if msg.err != nil {
		a.SelfTestE = clean(msg.err.Error())
		return nil
	}
	a.SelfTest, a.SelfTestE = msg.report, ""
	if p := a.Palette; p != nil && p.SelfTest {
		p.Sel = clampSel(p.Sel, len(paletteItems(m.st)))
		if msg.report != nil && msg.report.Running && !m.selftestTicking {
			m.selftestTicking = true
			return tea.Tick(selftestEvery, func(time.Time) tea.Msg { return selftestTickMsg{} })
		}
	}
	return nil
}

// onSelftestTick asks again while the list is open and the test runs.
func (m *Model) onSelftestTick() tea.Cmd {
	m.selftestTicking = false
	if p := m.st.App.Palette; p == nil || !p.SelfTest {
		return nil
	}
	return m.askSelftest()
}
