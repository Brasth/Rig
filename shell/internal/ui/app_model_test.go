package ui

import (
	"encoding/json"
	"fmt"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"

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

func withEfforts(src *fakeSource) {
	_ = json.Unmarshal([]byte(`{"claude": {"effort": "", "scope": "default", "levels": ["low", "medium", "high", "xhigh", "max"]}}`),
		&src.appSettings.ParentEfforts)
}

func TestModelAlsoControlsHowMuchTheParentReasons(t *testing.T) {
	m, src := appModel(t)
	withConversation(t, m, src)
	withModels(t, src)
	withEfforts(src)
	typeText(m, "/model")
	run(press(m, "enter"), m)
	items := paletteItems(m.st)
	if items[0].Action != "model:default" || items[1].Label != "reasoning effort ▸" || items[1].Action != "effort-menu" || items[1].Detail != "its own setting" {
		t.Fatalf("/model has a reasoning row right under the default model: %+v", items[:3])
	}
	if out := appScreen(m); !strings.Contains(out, "reasoning effort ▸") {
		t.Fatalf("it is on the screen at once:\n%s", out)
	}
	keyMsg(m, tea.KeyDown)
	keyMsg(m, tea.KeyEnter)
	if p := m.st.App.Palette; p == nil || !p.Effort || p.Model {
		t.Fatalf("⏎ on it opens the levels: %+v", p)
	}
	var got []string
	for _, it := range paletteItems(m.st) {
		got = append(got, it.Label+"|"+it.Detail+"|"+it.Action)
	}
	list := strings.Join(got, "\n")
	for _, want := range []string{"default|its own setting · in use|effort:default", "low||effort:low", "xhigh||effort:xhigh", "max||effort:max"} {
		if !strings.Contains(list, want) {
			t.Fatalf("the levels the CLI documents; missing %q:\n%s", want, list)
		}
	}
	m.st.App.Palette.Query, m.st.App.Palette.Sel = "high", 0
	_, cmd := m.Update(press(m, "enter")())
	run(cmd, m)
	if n := len(src.settingsSaved); n == 0 || src.settingsSaved[n-1] != [3]string{"parent_effort.claude", "high", "all"} {
		t.Fatalf("a level is kept for all repos: %v", src.settingsSaved)
	}
	if !strings.Contains(m.st.Message, "reasons at high") {
		t.Fatalf("it says so: %q", m.st.Message)
	}
	m.st.App.Palette = &Palette{Effort: true, Query: "ultra"}
	if items := paletteItems(m.st); items[len(items)-1].Action != "effort:ultra" {
		t.Fatalf("a level the CLI added since can be typed: %+v", items)
	}
	m.st.App.Palette = &Palette{Model: true, Query: "high"}
	if items := paletteItems(m.st); items[0].Action != "effort:high" {
		t.Fatalf("a level typed in /model sets the level, not a model called high: %+v", items)
	}
	for _, it := range paletteItems(m.st) {
		if strings.HasPrefix(it.Action, "model:") {
			t.Fatalf("a level is not a model: %+v", it)
		}
	}
	m.st.App.Palette = nil
	run(m.appSend("/effort max"), m)
	if last := src.settingsSaved[len(src.settingsSaved)-1]; last != [3]string{"parent_effort.claude", "max", "all"} {
		t.Fatalf("/effort LEVEL sets it at once: %v", src.settingsSaved)
	}
	run(m.appSend("/model sonnet low"), m)
	n := len(src.settingsSaved)
	if src.settingsSaved[n-2] != [3]string{"parent_model.claude", "sonnet", "all"} || src.settingsSaved[n-1] != [3]string{"parent_effort.claude", "low", "all"} {
		t.Fatalf("/model NAME LEVEL sets both: %v", src.settingsSaved)
	}
	run(m.appSend("/effort default"), m)
	if last := src.settingsSaved[len(src.settingsSaved)-1]; last != [3]string{"parent_effort.claude", "default", "all"} {
		t.Fatalf("default goes back to the CLI's own setting: %v", last)
	}
	run(m.appSend("/effort"), m)
	if p := m.st.App.Palette; p == nil || !p.Effort {
		t.Fatal("/effort alone opens the levels")
	}
}

func TestAPaletteScrollsToTheRowChosenPastTheTwelveShown(t *testing.T) {
	m, src := appModel(t)
	withConversation(t, m, src)
	withModels(t, src)
	m.st.ModelsFor = "claude"
	for i := 0; i < 20; i++ {
		m.st.ModelList = append(m.st.ModelList, ModelChoice{Name: fmt.Sprintf("model-%02d", i)})
	}
	typeText(m, "/model")
	run(press(m, "enter"), m)
	if out := appScreen(m); strings.Contains(out, "model-19") {
		t.Fatal("twelve rows show at first")
	}
	for i := 0; i < 24; i++ {
		keyMsg(m, tea.KeyDown)
	}
	out := appScreen(m)
	if !strings.Contains(out, "model-19") || strings.Contains(out, "reasoning effort") {
		t.Fatalf("moving down scrolls the list so the chosen row shows:\n%s", out)
	}
}

func TestTheReasoningLevelShowsInSettingsAndTheTitle(t *testing.T) {
	m, src := appModel(t)
	withConversation(t, m, src)
	withModels(t, src)
	withEfforts(src)
	src.appSettings.ParentEfforts["claude"] = struct {
		Effort string   `json:"effort"`
		Scope  string   `json:"scope"`
		Levels []string `json:"levels"`
	}{Effort: "high", Scope: "all", Levels: []string{"low", "medium", "high"}}
	m.st.ChatEffort = "high"
	run(m.runPalette(paletteItem{Action: "settings"}, ""), m)
	if screen := appScreen(m); !strings.Contains(screen, "claude's reasoning") || !strings.Contains(screen, "high · all repos  ⏎ change") {
		t.Fatalf("the Settings row:\n%s", screen)
	}
	m.st.ChatParent = "claude"
	if title := strings.Join(chatColumn(m.view, m.st, 100, 24), "\n"); !strings.Contains(title, "Parent · claude") || !strings.Contains(title, "· high") {
		t.Fatalf("the board's title names the level:\n%s", title)
	}
}
