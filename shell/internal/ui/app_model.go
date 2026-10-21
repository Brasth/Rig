package ui

import (
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"
)

// The parent's model, as the parents' own /model does: /model (or ⌃k › change model, or Settings › General) lists the
// models to choose from: the CLI's own setting, the ones Rig knows for it, the one in use; any other name can be typed.
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
	for _, name := range append([]string{current}, suggest...) {
		if name == "" || seen[name] {
			continue
		}
		seen[name] = true
		detail := ""
		if name == current && scope != "default" {
			detail = "in use"
			if scope == "repo" {
				detail += " · this repo"
			}
		}
		add(name, detail, "model:"+name)
	}
	if typed := strings.TrimSpace(st.App.Palette.Query); typed != "" && !seen[typed] {
		out = append(out, paletteItem{section, "use " + typed, "as " + parent + " writes it", "model:" + typed})
	}
	return out
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
