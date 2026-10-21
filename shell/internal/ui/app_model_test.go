package ui

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/Brasth/Rig/shell/internal/view"
)

func withModels(t *testing.T, src *fakeSource) {
	t.Helper()
	src.appSettings = sampleSettings()
	_ = json.Unmarshal([]byte(`{"claude": {"model": "", "scope": "default", "own": "claude-opus-4", "suggest": ["opus", "sonnet", "haiku"]}}`),
		&src.appSettings.ParentModels)
}

func TestSlashModelListsTheParentsModelsAndKeepsTheChoice(t *testing.T) {
	m, src := appModel(t)
	withConversation(t, m, src)
	withModels(t, src)
	typeText(m, "/model")
	run(press(m, "enter"), m)
	if m.st.App.Palette == nil || !m.st.App.Palette.Model {
		t.Fatal("/model opens the models")
	}
	items := paletteItems(m.st)
	var labels []string
	for _, it := range items {
		labels = append(labels, it.Label+" · "+it.Detail)
	}
	got := strings.Join(labels, " | ")
	for _, want := range []string{"default · its own setting: claude-opus-4 · in use", "opus", "sonnet", "haiku"} {
		if !strings.Contains(got, want) {
			t.Fatalf("missing %q in %s", want, got)
		}
	}
	m.st.App.Palette.Query = "sonnet"
	m.st.App.Palette.Sel = 0
	_, cmd := m.Update(press(m, "enter")())
	run(cmd, m)
	if n := len(src.settingsSaved); n == 0 || src.settingsSaved[n-1] != [3]string{"parent_model.claude", "sonnet", "all"} {
		t.Fatalf("kept for all repos: %v", src.settingsSaved)
	}
	m.st.App.Palette = &Palette{Model: true, Query: "my-own-model"}
	items = paletteItems(m.st)
	if last := items[len(items)-1]; last.Label != "use my-own-model" || last.Action != "model:my-own-model" {
		t.Fatalf("any name can be typed: %+v", last)
	}
	m.st.App.Palette = nil
	run(m.appSend("/model haiku"), m)
	if n := len(src.settingsSaved); src.settingsSaved[n-1][1] != "haiku" {
		t.Fatal("/model NAME sets it at once")
	}
}

func TestSettingsShowsTheParentsModel(t *testing.T) {
	m, src := appModel(t)
	withModels(t, src)
	src.appSettings.ParentModels["claude"] = struct {
		Model   string   `json:"model"`
		Scope   string   `json:"scope"`
		Own     string   `json:"own"`
		Suggest []string `json:"suggest"`
	}{Model: "sonnet", Scope: "all"}
	run(m.runPalette(paletteItem{Action: "settings"}, ""), m)
	if screen := appScreen(m); !strings.Contains(screen, "claude's model") || !strings.Contains(screen, "sonnet · all repos  ⏎ change") {
		t.Fatalf("the row:\n%s", screen)
	}
}

func TestTheTopBarOpensSettingsAndSwitchesParent(t *testing.T) {
	m, src := mouseModel(t)
	withConversation(t, m, src)
	src.appSettings = sampleSettings()
	clickOn(t, m, "settings")
	if m.st.App.Settings == nil {
		t.Fatal("a click on settings opens them")
	}
	m.st.App.Settings = nil
	src.chatStatus = chatOn()
	m.Update(chatStatusMsg{status: &view.ChatStatus{Enabled: true, Parent: "claude", Configured: "claude", Available: []string{"claude", "codex"}}})
	if m.st.App.Welcome {
		t.Fatal("not the first run")
	}
	clickOn(t, m, "claude ")
	if !m.st.App.Welcome {
		t.Fatal("a click on the parent opens the parents to switch to")
	}
}

func TestSlashModelListsEveryModelTheParentOffers(t *testing.T) {
	m, src := appModel(t)
	withConversation(t, m, src)
	withModels(t, src)
	src.parentModels = &view.ParentModels{Parent: "claude"}
	_ = json.Unmarshal([]byte(`{"parent": "claude", "models": [{"name": "opus", "detail": "the newest Opus"},
		{"name": "claude-sonnet-5-5", "detail": ""}, {"name": "claude-haiku-4-5-20251001", "detail": ""}]}`), src.parentModels)
	m.st.ModelsFor = ""
	cmd := m.runPalette(paletteItem{Action: "model"}, "")
	if m.st.ModelsFor != "loading:claude" || !strings.Contains(paletteText(m.st), "asking claude for its models") {
		t.Fatalf("the menu says it is asking: %q %s", m.st.ModelsFor, paletteText(m.st))
	}
	run(cmd, m)
	got := paletteText(m.st)
	for _, want := range []string{"opus · the newest Opus", "claude-sonnet-5-5", "claude-haiku-4-5-20251001", "sonnet", "haiku"} {
		if !strings.Contains(got, want) {
			t.Fatalf("missing %q in %s", want, got)
		}
	}
	if strings.Contains(got, "asking") || strings.Count(got, "| opus ·") != 1 {
		t.Fatalf("each model once, no longer asking: %s", got)
	}
	asks := src.modelAsks
	run(m.runPalette(paletteItem{Action: "model"}, ""), m)
	if src.modelAsks != asks {
		t.Fatal("the list is kept a while, not asked at each /model")
	}
}

func paletteText(st State) string {
	var labels []string
	for _, it := range paletteItems(st) {
		labels = append(labels, it.Label+" · "+it.Detail)
	}
	return "| " + strings.Join(labels, " | ")
}
