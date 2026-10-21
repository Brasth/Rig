package ui

import (
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
	"github.com/muesli/termenv"

	"github.com/Brasth/Rig/shell/internal/view"
)

func themeModel(t *testing.T) (*Model, *fakeSource) {
	t.Helper()
	t.Cleanup(func() { ApplyTheme("rig") }) // the palette is shared by every test
	m, src := newReady(t)
	src.settings = loadSettings(t)
	return m, src
}

func TestTheSavedThemeIsLoadedAndDrawn(t *testing.T) {
	m, src := themeModel(t)
	src.theme = &view.ThemeReply{Theme: "dracula", Scope: "repo"}
	run(m.fetchTheme(), m)
	if m.st.Theme != "dracula" || m.st.ThemeScope != "repo" || colGround != "#282a36" || colText != "#f8f8f2" {
		t.Fatalf("theme %q %q ground %q", m.st.Theme, m.st.ThemeScope, colGround)
	}
	old := lipgloss.ColorProfile()
	lipgloss.SetColorProfile(termenv.TrueColor)
	defer lipgloss.SetColorProfile(old)
	if out := m.View(); !strings.Contains(out, "48;2;40;42;54") {
		t.Fatal("a true-colour terminal gets the theme's own ground, #282a36")
	}
	lipgloss.SetColorProfile(termenv.ANSI256)
	if got := sgrColor("#282a36", true); !strings.HasPrefix(got, "48;5;") {
		t.Fatalf("a 256-colour terminal gets the nearest colour: %q", got)
	}
}

func TestTheSettingsTabChangesTheThemeAndWhereItIsKept(t *testing.T) {
	m, src := themeModel(t)
	runAll(m, m.goTab(SettingsTab))
	plain := m.st
	plain.Styled = false
	out := Render(m.view, plain, 120, 36)
	if !strings.Contains(out, "› [rig]   light    dark    material    dracula") || !strings.Contains(out, "←→ change") {
		t.Fatalf("the Theme row:\n%s", out)
	}
	run(press(m, "l"), m)
	if m.st.Theme != "light" || colGround != "#fafafa" || len(src.themeSaved) != 1 || src.themeSaved[0] != [2]string{"light", "all"} {
		t.Fatalf("→ applies the next theme at once and keeps it for all repos: %q %v", m.st.Theme, src.themeSaved)
	}
	if !strings.Contains(m.st.Message, "theme light for all repos") {
		t.Fatalf("message %q", m.st.Message)
	}
	run(press(m, "g"), m)
	if src.themeSaved[1] != [2]string{"light", "repo"} || m.st.ThemeScope != "repo" {
		t.Fatalf("g keeps it for this repo instead: %v", src.themeSaved)
	}
	run(press(m, "h"), m)
	if src.themeSaved[2] != [2]string{"rig", "repo"} {
		t.Fatalf("← goes back, still for this repo: %v", src.themeSaved)
	}
}

func TestSlashThemeAndTheFlagForOneBoard(t *testing.T) {
	m, src, _ := chatSession(t)
	t.Cleanup(func() { ApplyTheme("rig") })
	typeText(m, "/theme material")
	run(press(m, "enter"), m)
	if colAccent != "#d0bcff" || len(src.themeSaved) != 1 || src.themeSaved[0][0] != "material" {
		t.Fatalf("/theme NAME switches and saves: %v", src.themeSaved)
	}
	typeText(m, "/theme nope")
	run(press(m, "enter"), m)
	if !strings.Contains(m.st.Message, "no theme called nope") || colAccent != "#d0bcff" {
		t.Fatalf("an unknown theme changes nothing: %q", m.st.Message)
	}
	m2, src2 := themeModel(t)
	if !m2.SetTheme("dark") || m2.SetTheme("nope") {
		t.Fatal("--theme takes a known name only")
	}
	src2.theme = &view.ThemeReply{Theme: "light", Scope: "all"}
	if m2.fetchTheme() != nil {
		t.Fatal("--theme wins over the saved theme")
	}
	m2.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("x")})
	if colGround != "#15161a" {
		t.Fatal("the dark ground")
	}
}
