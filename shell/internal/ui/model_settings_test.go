package ui

import (
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/Brasth/Rig/shell/internal/view"
)

func TestSettingsLoadOnlyWhileTheirTabShowsAndPollNoFasterThanTheInterval(t *testing.T) {
	m, src := newReady(t)
	src.settings = loadSettings(t)
	clock := time.Unix(2000, 0)
	m.now = func() time.Time { return clock }
	if m.fetchSettings(true) != nil || src.settingsCalls != 0 {
		t.Fatal("no settings fetch while another tab shows")
	}
	press(m, "tab", "tab", "tab", "tab") // Jobs -> Queue -> Workflows -> Env -> Settings
	if m.st.Tab != SettingsTab {
		t.Fatalf("tab %q", m.st.Tab)
	}
	m.stBusy = false
	run(m.fetchSettings(true), m)
	if m.st.Settings == nil || m.st.SettingsE != "" {
		t.Fatalf("settings not stored: %+v %q", m.st.Settings, m.st.SettingsE)
	}
	clock = clock.Add(time.Second)
	if m.fetchSettings(false) != nil {
		t.Fatal("a fresh panel is not refetched before the interval")
	}
	clock = clock.Add(workspaceEvery)
	if m.fetchSettings(false) == nil {
		t.Fatal("the panel refreshes after the interval")
	}
}

func TestAFailedSettingsRefreshKeepsTheLastPanelAndSaysWhy(t *testing.T) {
	m, src := newReady(t)
	press(m, "tab", "tab", "tab", "tab")
	src.settings = loadSettings(t)
	m.stBusy = false
	run(m.fetchSettings(true), m)
	src.settings, src.settingsErr = nil, errors.New("api gone")
	m.stBusy = false
	run(m.fetchSettings(true), m)
	if m.st.Settings == nil || m.st.SettingsE != "api gone" {
		t.Fatalf("stale panel and reason expected: %+v %q", m.st.Settings, m.st.SettingsE)
	}
}

func TestTheSettingsTabIsReadOnlyAndScrolls(t *testing.T) {
	m, src := newReady(t)
	press(m, "tab", "tab", "tab", "tab")
	src.settings = loadSettings(t)
	m.stBusy = false
	run(m.fetchSettings(true), m)
	press(m, "a", "d", "x")
	if len(src.calls) != 0 || m.st.Mode != ModeNormal {
		t.Fatalf("settings take no job action: calls %v mode %v", src.calls, m.st.Mode)
	}
	press(m, "j", "j")
	if m.st.DetailOff != 2 {
		t.Fatalf("j scrolls the panel, offset %d", m.st.DetailOff)
	}
	out := Render(m.view, m.st, 100, 30)
	for _, banned := range []string{"│", "Enter details", "x stop", "e enqueue"} {
		if strings.Contains(out, banned) {
			t.Errorf("settings must not show %q", banned)
		}
	}
	if !strings.Contains(out, "read-only: edit .rig/harness.toml") {
		t.Errorf("the footer says where to change settings:\n%s", out)
	}
}

func TestSettingsShowTheEffectiveValuesAndFlagAProblemOnTheTab(t *testing.T) {
	st := NewState()
	st.Tab = SettingsTab
	st.Settings = loadSettings(t)
	out := Render(loadView(t), st, 120, 36)
	for _, want := range []string{"This project", "Who does what", "Sandbox", "provider   devcontainer", "container  per-job"} {
		if !strings.Contains(out, want) {
			t.Errorf("missing %q:\n%s", want, out)
		}
	}
	st.Tab = "Jobs"
	st.Settings = &view.Panel{Attention: true, Sections: st.Settings.Sections}
	if !strings.Contains(strings.Split(Render(loadView(t), st, 120, 36), "\n")[1], "Settings !") {
		t.Error("a settings problem is flagged on the tab")
	}
}
