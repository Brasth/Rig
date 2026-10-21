package ui

import (
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"
)

// The parent's model, as the parents' own /model does: /model (or ⌃k › change model, or Settings › General) lists the
// models to choose from: the CLI's own setting, every model the CLI lists (chat.models), the one in use; any other
// name can be typed.
// The choice is kept for all repos and goes to the parent's --model from the next message.

// modelItems is the palette in /model: what fits the query, and the query itself as a model to use.
func modelItems(st State, q string) []paletteItem {
	parent := appParent(st)
	current, own, scope := st.ChatModel, "", "default"
	var suggest []string
	if s := st.AppSettings; s != nil {
		if pm, ok := s.ParentModels[parent]; ok {
			own, scope, suggest = pm.Own, pm.Scope, pm.Suggest
			if pm.Model != "" {
				current = pm.Model
			}
		}
	}
	section := "models for " + parent
	var out []paletteItem
	add := func(label, detail, action string) {
		if q == "" || strings.Contains(strings.ToLower(label), q) {
			out = append(out, paletteItem{section, label, detail, action})
		}
	}
	ownDetail := "its own setting"
	if own != "" {
		ownDetail = "its own setting: " + own
	}
	if scope == "default" {
		ownDetail += " · in use"
	}
	add("default", ownDetail, "model:default")
	seen := map[string]bool{"default": true}
	about := map[string]string{}
	names := append([]string{current}, suggest...)
	if st.ModelsFor == parent {
		for _, c := range st.ModelList {
			names = append(names, c.Name)
			about[c.Name] = c.Detail
		}
	}
	for _, name := range names {
		if name == "" || seen[name] {
			continue
		}
		seen[name] = true
		detail := about[name]
		if name == current && scope != "default" {
			detail = strings.TrimPrefix(detail+" · in use", " · ")
			if scope == "repo" {
				detail += " · this repo"
			}
		}
		add(name, detail, "model:"+name)
	}
	if st.ModelsFor == "loading:"+parent && q == "" {
		out = append(out, paletteItem{section, "…", "asking " + parent + " for its models", "model"})
	}
	if typed := strings.TrimSpace(st.App.Palette.Query); typed != "" && !seen[typed] {
		out = append(out, paletteItem{section, "use " + typed, "as " + parent + " writes it", "model:" + typed})
	}
	return out
}

// ModelChoice is one model the parent offers, and what it is.
type ModelChoice struct{ Name, Detail string }

type modelsMsg struct {
	parent string
	list   []ModelChoice
	err    error
}

// fetchModels asks for every model the parent offers, when /model opens: once for each parent, then again after ten
// minutes. Listing can take the CLI a few seconds, so the menu shows what it knows meanwhile.
func (m *Model) fetchModels() tea.Cmd {
	parent := appParent(m.st)
	if parent == "" || m.st.ModelsFor == "loading:"+parent || (m.st.ModelsFor == parent && m.now().Sub(m.modelsAt) < 10*time.Minute) {
		return nil
	}
	if m.st.ModelsFor != parent {
		m.st.ModelsFor, m.st.ModelList = "loading:"+parent, nil
	}
	src := m.src
	return func() tea.Msg {
		got, err := src.ChatModels(parent, 20*time.Second)
		if err != nil || got == nil {
			return modelsMsg{parent: parent, err: err} // an older server has no chat.models: what Settings knows
		}
		list := make([]ModelChoice, 0, len(got.Models))
		for _, r := range got.Models {
			list = append(list, ModelChoice{r.Name, r.Detail})
		}
		return modelsMsg{parent: parent, list: list}
	}
}

func (m *Model) afterModels(msg modelsMsg) {
	if msg.parent != appParent(m.st) {
		return
	}
	m.modelsAt = m.now()
	if msg.err != nil && m.st.ModelsFor == msg.parent {
		return // keep the list it had
	}
	m.st.ModelsFor, m.st.ModelList = msg.parent, msg.list
	if a := m.st.App; a.Palette != nil && a.Palette.Model {
		a.Palette.Sel = clampSel(a.Palette.Sel, len(paletteItems(m.st)))
	}
}

// setModel keeps the parent's model for all repos (default: back to its own setting) and says so.
func (m *Model) setModel(name string) tea.Cmd {
	parent := m.st.ChatParent
	if parent == "" {
		m.st.Message = "no parent yet: /parent chooses one"
		return nil
	}
	src := m.src
	m.st.Message = parent + " uses " + name + " from the next message"
	if name == "default" {
		m.st.Message = parent + " uses its own model setting from the next message"
	}
	return func() tea.Msg {
		if _, err := src.SetSetting("parent_model."+parent, name, "all", callTimeout); err != nil {
			return appSettingsMsg{err: err}
		}
		return modelSetMsg{}
	}
}

type modelSetMsg struct{}

// afterModelSet shows the new model in the top bar and Settings: the status is asked again at once.
func (m *Model) afterModelSet() tea.Cmd {
	m.csLast = time.Time{}
	return tea.Batch(m.fetchAppSettings(), m.fetchChatStatus())
}
