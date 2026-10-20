package ui

import (
	"fmt"
	"strings"

	"github.com/charmbracelet/lipgloss"

	"github.com/Brasth/Rig/shell/internal/view"
)

const (
	minWidth, minHeight = 40, 8
	splitMinWidth       = 100
	stackMinHeight      = 24
	twoLineMinBody      = 12
)

// Mode is what the keyboard is doing right now.
type Mode int

const (
	ModeNormal Mode = iota
	ModeConfirm
	ModeInput
)

// State is everything Render needs besides the view. It holds no I/O.
type State struct {
	Tab          string
	Selected     map[string]int
	Offsets      map[string]int
	DetailOpen   bool
	DetailOff    int
	Mode         Mode
	Prompt       string // confirm question or input draft
	Message      string
	Stale        string // set when the last refresh failed; shown until one succeeds
	Loading      bool
	ASCII        bool
	Workspace    *view.Workspace
	WorkspaceE   string // why the workspace panel could not load
	Fleet        *view.Panel
	FleetE       string
	Log          *view.LogReply
	LogE         string
	LogUp        int // lines scrolled back from the newest; 0 follows the tail
	Evidence     *view.Evidence
	EvidenceE    string
	EvidenceID   string
	EvidenceOpen bool // the evidence drawer replaces the board body
	Styled       bool // apply colour and reverse video to list rows; golden tests leave it off
}

// NewState returns a state on the first tab.
func NewState() State {
	return State{Tab: "Jobs", Selected: map[string]int{}, Offsets: map[string]int{}}
}

// Tabs are the board tabs in order.
var Tabs = []string{"Jobs", "Queue", "Workflows", WorkspaceTab, FleetTab, LogsTab}

// LogsTab follows the selected job's log as plain text, so the board works without tmux.
const LogsTab = "Logs"

// FleetTab renders the containers on the engine, observed now.
const FleetTab = "Fleet"

// WorkspaceTab is the tab that renders the workspace panel instead of a list.
const WorkspaceTab = "Workspace"

func glyph(r view.Row) string {
	if r.Glyph == "" {
		return "·"
	}
	return r.Glyph
}

// Render draws one frame at w by h cells as text. Colour appears only when
// st.Styled is set, so frames are comparable as plain text.
func Render(v *view.View, st State, w, h int) string {
	return strings.Join(RenderLines(v, st, w, h), "\n")
}

// RenderLines is Render before joining, for callers that decorate lines.
func RenderLines(v *view.View, st State, w, h int) []string {
	out := frame(v, st, w, h)
	if st.ASCII {
		for i := range out {
			out[i] = asciiOnly(out[i])
		}
	}
	return out
}

func frame(v *view.View, st State, w, h int) []string {
	if w < minWidth || h < minHeight {
		lines := make([]string, h)
		if h > 0 {
			lines[0] = fit(fmt.Sprintf("Rig · window too small (need %d×%d); q quits", minWidth, minHeight), w)
		}
		for i := 1; i < h; i++ {
			lines[i] = fit("", w)
		}
		return lines
	}
	body := h - 5
	lines := []string{headerLine(v, st, w), tabLine(v, st, w)}
	if st.EvidenceOpen {
		lines = append(lines, rule("Evidence › "+sanitize(st.EvidenceID), w))
		lines = append(lines, evidenceLines(st, w, body)...)
		lines = append(lines, messageLine(st, w), footerLine(nil, 0, st, w))
		for len(lines) < h {
			lines = append(lines, fit("", w))
		}
		return lines[:h]
	}
	if st.Tab == LogsTab {
		lines = append(lines, rule(logTitle(st), w))
		lines = append(lines, logLines(st, w, body)...)
		lines = append(lines, messageLine(st, w), footerLine(nil, 0, st, w))
		for len(lines) < h {
			lines = append(lines, fit("", w))
		}
		return lines[:h]
	}
	if st.Tab == WorkspaceTab || st.Tab == FleetTab {
		lines = append(lines, rule(panelTitle(st), w))
		lines = append(lines, panelBody(st, w, body)...)
		lines = append(lines, messageLine(st, w), footerLine(nil, 0, st, w))
		for len(lines) < h {
			lines = append(lines, fit("", w))
		}
		return lines[:h]
	}
	rows := v.Rows(st.Tab)
	sel := clampSel(st.Selected[st.Tab], len(rows))
	split := w >= splitMinWidth
	stacked := !split && h >= stackMinHeight
	eh := 1
	if (st.Tab == "Jobs" || st.Tab == "Queue") && body >= twoLineMinBody {
		eh = 2
	}

	switch {
	case st.DetailOpen && !split && !stacked:
		lines = append(lines, rule(fmt.Sprintf("%s › %s", st.Tab, selID(rows, sel)), w))
		lines = append(lines, detailLines(rows, sel, st.DetailOff, w, body)...)
	case split:
		lw := (w*52 + 50) / 100
		lw = max(52, min(72, lw))
		dw := w - lw - 1
		lines = append(lines, rule(rangeText(st.Tab, rows, st.Offsets[st.Tab], sel, body, eh), lw)+"┬"+rule("", dw))
		list := listLines(rows, sel, st.Offsets[st.Tab], lw, body, eh, st.Styled)
		det := detailLines(rows, sel, st.DetailOff, dw, body)
		for i := 0; i < body; i++ {
			lines = append(lines, list[i]+"│"+det[i])
		}
	case stacked:
		lb := (body*60 + 50) / 100
		lines = append(lines, rule(rangeText(st.Tab, rows, st.Offsets[st.Tab], sel, lb, eh), w))
		lines = append(lines, listLines(rows, sel, st.Offsets[st.Tab], w, lb, eh, st.Styled)...)
		lines = append(lines, rule("Detail", w))
		lines = append(lines, detailLines(rows, sel, st.DetailOff, w, body-lb-1)...)
	default:
		lines = append(lines, rule(rangeText(st.Tab, rows, st.Offsets[st.Tab], sel, body, eh), w))
		lines = append(lines, listLines(rows, sel, st.Offsets[st.Tab], w, body, eh, st.Styled)...)
	}
	lines = append(lines, messageLine(st, w), footerLine(rows, sel, st, w))
	for len(lines) < h {
		lines = append(lines, fit("", w))
	}
	return lines[:h]
}

func clampSel(sel, n int) int {
	if n == 0 {
		return 0
	}
	return max(0, min(sel, n-1))
}

func selID(rows []view.Row, sel int) string {
	if len(rows) == 0 {
		return ""
	}
	return rows[sel].ID
}

func headerLine(v *view.View, st State, w int) string {
	if v == nil {
		msg := " Rig · loading…"
		if st.Stale != "" {
			msg = " Rig · no snapshot yet"
		}
		return fit(msg, w)
	}
	hd := v.Header
	health := fmt.Sprintf("! %d need attention  ● %d running  slots %d/%d", hd.Attention, hd.Running, hd.Slots, hd.Cap)
	if w < 60 {
		health = fmt.Sprintf("!%d ●%d %d/%d", hd.Attention, hd.Running, hd.Slots, hd.Cap)
	}
	left := strings.TrimRight(fit(" Rig · "+clean(hd.Project), max(1, w-lipgloss.Width(health)-3)), " ")
	gap := max(2, w-lipgloss.Width(left)-lipgloss.Width(health)-1)
	return fit(left+strings.Repeat(" ", gap)+health, w)
}

// tabShort are distinct three-letter forms for a narrow tab bar ("Wor" would be both Workflows and Workspace).
var tabShort = map[string]string{"Jobs": "Job", "Queue": "Que", "Workflows": "Flw", WorkspaceTab: "Wks", FleetTab: "Flt", LogsTab: "Log"}

func tabLine(v *view.View, st State, w int) string {
	if line := tabLineWith(v, st, false); lipgloss.Width(line) <= w {
		return fit(line, w)
	}
	return fit(tabLineWith(v, st, true), w)
}

// tabLineWith builds the tab bar; short abbreviates the inactive tabs so the active one is never cut off.
func tabLineWith(v *view.View, st State, short bool) string {
	var parts []string
	for _, name := range Tabs {
		label := name
		if short && name != st.Tab {
			label = tabShort[name]
		}
		if v != nil {
			for _, t := range v.Tabs {
				if t.Name != name {
					continue
				}
				switch {
				case t.Attention > 0:
					label += fmt.Sprintf(" !%d", t.Attention)
				case t.Count > 0:
					label += fmt.Sprintf(" %d", t.Count)
				}
			}
		}
		if name == WorkspaceTab && st.Workspace != nil && st.Workspace.Attention {
			label += " !"
		}
		if name == FleetTab && st.Fleet != nil && st.Fleet.Attention {
			label += " !"
		}
		if name == st.Tab {
			label = "[" + label + "]"
		} else {
			label = " " + label + " "
		}
		parts = append(parts, label)
	}
	return " " + strings.Join(parts, " ")
}

func rule(title string, w int) string {
	if w <= 0 {
		return ""
	}
	if title == "" {
		return strings.Repeat("─", w)
	}
	head := "─ " + title + " "
	if lipgloss.Width(head) >= w {
		return fit(head, w)
	}
	return head + strings.Repeat("─", w-lipgloss.Width(head))
}

func rangeText(tab string, rows []view.Row, off, sel, capRows, eh int) string {
	n := len(rows)
	if n == 0 {
		return tab
	}
	per := max(1, capRows/eh)
	start := windowStart(off, sel, per, n)
	end := min(n, start+per)
	return fmt.Sprintf("%s %d-%d/%d", tab, start+1, end, n)
}

func windowStart(off, sel, per, n int) int {
	off = max(0, min(off, max(0, n-per)))
	if sel < off {
		off = sel
	}
	if sel >= off+per {
		off = sel - per + 1
	}
	return max(0, off)
}

// WindowStart exposes the viewport rule so the model keeps offsets in step.
func WindowStart(off, sel, bodyRows, eh, n int) int {
	return windowStart(off, sel, max(1, bodyRows/eh), n)
}

func listLines(rows []view.Row, sel, off, w, capRows, eh int, styled bool) []string {
	out := make([]string, 0, capRows)
	if len(rows) == 0 {
		out = append(out, fit("  Nothing here yet.", w))
	} else {
		per := max(1, capRows/eh)
		start := windowStart(off, sel, per, len(rows))
		for i := start; i < min(len(rows), start+per); i++ {
			out = append(out, entry(rows[i], i == sel, w, eh, styled)...)
		}
	}
	for len(out) < capRows {
		out = append(out, fit("", w))
	}
	return out[:capRows]
}

func entry(r view.Row, selected bool, w, eh int, styled bool) []string {
	marker := " "
	if selected {
		marker = ">"
	}
	label := fit(glyph(r)+" "+clean(r.Label), 14)
	prefixPlain := fmt.Sprintf("%s %s  ", marker, label)
	room := w - lipgloss.Width(prefixPlain)
	title := clean(r.Title)
	if r.Summary != "" {
		title = fit(title, max(1, room-lipgloss.Width(r.Summary)-2)) + "  " + r.Summary
	}
	rest := strings.TrimRight(title, " ")
	if w >= 70 && r.ID != "" {
		tag := abbrevTail(clean(r.ID), 12)
		used := lipgloss.Width(prefixPlain) + lipgloss.Width(rest)
		if used+2+lipgloss.Width(tag) <= w {
			rest += strings.Repeat(" ", w-used-lipgloss.Width(tag)) + tag
		}
	}
	rest = fit(rest, room)
	second := ""
	if eh == 2 {
		second = fit("    "+clean(r.Second), w)
	}
	if styled {
		label = tone(r.Tone).Render(label)
		if selected {
			first := marker + " " + label + "  " + rest
			return paintSelected(first, second, eh)
		}
	}
	lines := []string{marker + " " + label + "  " + rest}
	if eh == 2 {
		lines = append(lines, second)
	}
	return lines
}

func abbrevTail(s string, n int) string {
	r := []rune(s)
	if len(r) <= n {
		return s
	}
	return "…" + string(r[len(r)-n+1:])
}

func detailLines(rows []view.Row, sel, off, w, h int) []string {
	out := make([]string, 0, h)
	if len(rows) == 0 || h <= 0 {
		for len(out) < h {
			out = append(out, fit("", w))
		}
		return out
	}
	r := rows[sel]
	var all []string
	all = append(all, wrap(glyph(r)+" "+r.Label, w-2, "  ")...)
	if r.Explanation != "" {
		all = append(all, wrap(r.Explanation, w-2, "  ")...)
	}
	all = append(all, "")
	for _, line := range r.Detail {
		all = append(all, wrap(line, w-2, "  ")...)
	}
	off = max(0, min(off, max(0, len(all)-h)))
	for _, l := range all[off:] {
		if len(out) == h {
			break
		}
		out = append(out, fit(" "+l, w))
	}
	for len(out) < h {
		out = append(out, fit("", w))
	}
	return out
}

func messageLine(st State, w int) string {
	switch st.Mode {
	case ModeConfirm:
		return fit(" "+st.Prompt+"  y yes · n no", w)
	case ModeInput:
		return fit(" enqueue › "+st.Prompt+"▏", w)
	}
	switch {
	case st.Stale != "":
		return fit(" "+clean(st.Stale), w)
	case st.Message != "":
		return fit(" "+clean(st.Message), w)
	}
	return fit("", w)
}

func footerLine(rows []view.Row, sel int, st State, w int) string {
	keys := []string{"Tab pane", "Enter details", "v evidence", "s shell", "e enqueue", "x stop", "r refresh", "q quit"}
	if st.Tab == WorkspaceTab || st.Tab == FleetTab {
		keys = []string{"Tab pane", "j/k scroll", "r refresh", "q quit"}
		if st.Tab == WorkspaceTab && st.Workspace != nil && st.Workspace.Recovery != nil && st.Workspace.Recovery.Safe && !st.Workspace.Recovery.Ready {
			keys = []string{"Tab pane", "j/k scroll", "R recover", "r refresh", "q quit"}
		}
	}
	if st.Tab == LogsTab {
		keys = []string{"Tab pane", "k/j scroll", "G follow", "s shell", "r refresh", "q quit"}
	}
	if st.EvidenceOpen {
		keys = []string{"Esc close", "j/k scroll", "r refresh", "q quit"}
	}
	if st.Mode == ModeInput {
		keys = []string{"Enter queue it", "Esc discard"}
	} else if st.Mode == ModeConfirm {
		keys = []string{"y confirm", "n cancel"}
	} else if len(rows) > 0 && st.Tab == "Jobs" && rows[sel].Ask {
		keys = append([]string{"a allow", "d deny"}, keys...)
	}
	return fit(" "+strings.Join(keys, "  "), w)
}

func logTitle(st State) string {
	if st.Log != nil && st.Log.Job != nil {
		j := st.Log.Job
		title := "Logs › " + sanitize(j.ID)
		if j.Label != "" || j.Worker != "" {
			title += " · " + sanitize(strings.TrimSpace(j.Label+" "+j.Worker))
		}
		if st.LogUp > 0 {
			title += fmt.Sprintf(" · %d lines back", st.LogUp)
		}
		return title
	}
	return "Logs"
}

// logLines renders the log tail: plain text, wrapped, newest at the bottom, scrolled back by st.LogUp.
func logLines(st State, w, h int) []string {
	var all []string
	switch {
	case st.Log == nil && st.LogE != "":
		for _, l := range wrap("Logs unavailable: "+st.LogE, w-2, "  ") {
			all = append(all, " "+l)
		}
	case st.Log == nil:
		all = []string{" Loading log…"}
	case st.Log.Job == nil:
		all = []string{" No jobs yet. Output of the active job appears here."}
	default:
		for _, raw := range strings.Split(strings.TrimRight(st.Log.Text, "\n"), "\n") {
			raw = sanitize(raw) // per line: sanitize drops control characters, newlines included
			if raw == "" {
				all = append(all, "")
				continue
			}
			for _, l := range wrap(raw, w-2, "  ") {
				all = append(all, " "+l)
			}
		}
		if st.LogE != "" {
			all = append(all, rule("stale", w))
			for _, l := range wrap("refresh failed: "+st.LogE, w-2, "  ") {
				all = append(all, " "+l)
			}
		}
	}
	end := len(all) - min(st.LogUp, max(0, len(all)-h))
	start := max(0, end-h)
	out := make([]string, 0, h)
	for _, line := range all[start:end] {
		out = append(out, fit(line, w))
	}
	for len(out) < h {
		out = append(out, fit("", w))
	}
	return out
}

// panelTitle is the first section's title: it labels the rule under the tab bar, so that section
// does not repeat it.
func panelTitle(st State) string {
	p, fallback := st.Workspace, WorkspaceTab
	if st.Tab == FleetTab {
		p, fallback = st.Fleet, FleetTab
	}
	if p != nil && len(p.Sections) > 0 {
		return sanitize(p.Sections[0].Title)
	}
	return fallback
}

// panelBody renders the Workspace or Fleet tab.
func panelBody(st State, w, h int) []string {
	p, errText, name := st.Workspace, st.WorkspaceE, "Workspace"
	if st.Tab == FleetTab {
		p, errText, name = st.Fleet, st.FleetE, "Fleet"
	}
	return sectionLines(p.SectionsOrNil(), errText, name, true, st, w, h)
}

// evidenceLines renders the drawer for one job; every section, including the first, gets its own rule.
func evidenceLines(st State, w, h int) []string {
	var sections *[]view.Section
	if st.Evidence != nil {
		sections = &st.Evidence.Sections
	}
	return sectionLines(sections, st.EvidenceE, "Evidence", false, st, w, h)
}

func sectionLines(sections *[]view.Section, errText, name string, firstInRule bool, st State, w, h int) []string {
	var all []string
	switch {
	case sections == nil && errText != "":
		for _, l := range wrap(name+" unavailable: "+errText, w-2, "  ") {
			all = append(all, " "+l)
		}
	case sections == nil:
		all = []string{" Loading " + strings.ToLower(name) + "…"}
	default:
		for i, sec := range *sections {
			if i > 0 || !firstInRule {
				all = append(all, rule(sanitize(sec.Title), w))
			}
			for _, l := range sec.Lines {
				all = append(all, " "+tonedLine(l.Tone, sanitize(l.Text), st.Styled))
			}
		}
		if errText != "" {
			all = append(all, rule("stale", w))
			for _, l := range wrap("refresh failed: "+errText, w-2, "  ") {
				all = append(all, " "+l)
			}
		}
	}
	off := max(0, min(st.DetailOff, max(0, len(all)-h)))
	out := make([]string, 0, h)
	for _, line := range all[off:] {
		if len(out) == h {
			break
		}
		out = append(out, fit(line, w))
	}
	for len(out) < h {
		out = append(out, fit("", w))
	}
	return out
}

func tonedLine(toneName, text string, styled bool) string {
	if styled && toneName != "" {
		return tone(map[string]string{"ok": "verified", "attention": "attention"}[toneName]).Render(text)
	}
	return text
}
