package ui

import (
	"strings"
	"testing"

	"github.com/charmbracelet/lipgloss"
	"github.com/muesli/termenv"
)

func forceColour(t *testing.T) {
	t.Helper()
	old := lipgloss.ColorProfile()
	lipgloss.SetColorProfile(termenv.ANSI256)
	t.Cleanup(func() { lipgloss.SetColorProfile(old) })
}

func TestStyledFramesKeepEveryLineTheSameWidthAsPlain(t *testing.T) {
	forceColour(t)
	v := loadView(t)
	for _, size := range [][2]int{{120, 36}, {80, 30}, {60, 20}} {
		plain := NewState()
		styled := NewState()
		styled.Styled = true
		a := RenderLines(v, plain, size[0], size[1])
		b := Decorate(RenderLines(v, styled, size[0], size[1]))
		if len(a) != len(b) {
			t.Fatalf("%v: %d lines plain, %d styled", size, len(a), len(b))
		}
		for i := range a {
			if lipgloss.Width(a[i]) != lipgloss.Width(b[i]) {
				t.Fatalf("%v line %d changed width: %q vs %q", size, i, a[i], b[i])
			}
		}
	}
}

func TestSelectedRowIsADarkBarAndStatesKeepTheirTone(t *testing.T) {
	forceColour(t)
	v := loadView(t)
	st := NewState()
	st.Styled = true
	out := strings.Join(Decorate(RenderLines(v, st, 120, 36)), "\n")
	if !strings.Contains(out, "48;5;235") {
		t.Fatal("the selected row has no dark background")
	}
	if !strings.Contains(out, "38;5;179") { // amber: Needs input
		t.Fatal("attention keeps its amber tone")
	}
	if !strings.Contains(out, "38;5;114") { // green: Verified
		t.Fatal("verified keeps its green tone")
	}
	if !strings.Contains(out, "38;5;60") {
		t.Fatal("rules use the border colour")
	}
}
