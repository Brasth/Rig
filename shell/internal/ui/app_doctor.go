package ui

import (
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/Brasth/Rig/shell/internal/view"
)

// /doctor (or ⌃k › what is wrong): what is wrong with this project in plain words, the worst first. ⏎ on a finding with
// a fix runs the fix (Rig only offers fixes it can check are safe: an empty file, a job that never started and holds
// no slot, the error log); ⏎ on the others says what to do. The list is asked again after every fix.

type doctorMsg struct {
	found *view.ShellDoctor
	err   error
}

type doctorFixedMsg struct {
	done string
	err  error
}

// doctorItems are the palette's rows.
func doctorItems(st State, q string) []paletteItem {
	a := st.App
	if a.FindingsE != "" {
		return []paletteItem{{"doctor", "could not check", a.FindingsE, ""}}
	}
	if a.Findings == nil {
		return []paletteItem{{"doctor", "checking the project…", "", ""}}
	}
	var out []paletteItem
	for _, f := range a.Findings.Findings {
		text := strings.ToLower(f.Title + " " + f.Detail)
		if q != "" && !strings.Contains(text, q) {
			continue
		}
		mark := "✕ "
		if f.Level != "problem" {
			mark = "! "
		}
		detail, action := clean(f.Detail), "doctor-info:"+f.ID
		if f.Fix != "" {
			detail, action = "⏎ "+f.FixLabel+" · "+clean(f.Detail), "doctor-fix:"+f.Fix
		}
		out = append(out, paletteItem{map[string]string{"problem": "problems", "warning": "warnings"}[f.Level], mark + f.Title, detail, action})
	}
	if len(out) == 0 {
		detail := "no finding matches"
		if q == "" {
			detail = "nothing wrong found"
		}
		out = append(out, paletteItem{"doctor", "✓ all clear", detail, ""})
	}
	return out
}

func (m *Model) openDoctor() tea.Cmd {
	a := m.st.App
	a.Settings, a.Palette, a.Findings, a.FindingsE = nil, &Palette{Doctor: true}, nil, ""
	return m.askDoctor()
}

func (m *Model) askDoctor() tea.Cmd {
	src := m.src
	return func() tea.Msg {
		found, err := src.ShellDoctor(30 * time.Second)
		return doctorMsg{found: found, err: err}
	}
}

func (m *Model) onDoctor(msg doctorMsg) tea.Cmd {
	a := m.st.App
	if msg.err != nil {
		a.FindingsE = clean(msg.err.Error())
		return nil
	}
	a.Findings, a.FindingsE = msg.found, ""
	if p := a.Palette; p != nil && p.Doctor {
		p.Sel = clampSel(p.Sel, len(paletteItems(m.st)))
	}
	return nil
}

// doctorFix runs one fix; the palette opens again with the new list.
func (m *Model) doctorFix(fix string) tea.Cmd {
	src := m.src
	m.st.Message = "fixing…"
	return func() tea.Msg {
		done, err := src.ShellFix(fix, 30*time.Second)
		return doctorFixedMsg{done: done, err: err}
	}
}

func (m *Model) onDoctorFixed(msg doctorFixedMsg) tea.Cmd {
	if msg.err != nil {
		m.st.Message = "fix: " + clean(msg.err.Error())
	} else {
		m.st.Message = msg.done
	}
	return m.openDoctor() // what is left, asked again
}

// doctorInfo says in full what a finding without a fix wants done.
func (m *Model) doctorInfo(id string) tea.Cmd {
	if f := m.st.App.Findings; f != nil {
		for _, one := range f.Findings {
			if one.ID == id {
				m.st.Message = one.Title + ": " + clean(one.Detail)
			}
		}
	}
	m.st.App.Palette = &Palette{Doctor: true}
	return nil
}
