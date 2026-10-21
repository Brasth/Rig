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

func TestPaintPutsTheMockupGroundUnderEveryCellAfterEveryReset(t *testing.T) {
	cases := map[string]string{
		"plain":                    baseSGR() + "plain\x1b[0m",
		"a\x1b[0mb":                baseSGR() + "a\x1b[0m\x1b[38;5;255m\x1b[48;5;233mb\x1b[0m",
		"a\x1b[mb":                 baseSGR() + "a\x1b[m\x1b[38;5;255m\x1b[48;5;233mb\x1b[0m",
		"\x1b[0;1;31mred":          baseSGR() + "\x1b[0;1;31m\x1b[48;5;233mred\x1b[0m",
		"\x1b[0;31;44mboth":        baseSGR() + "\x1b[0;31;44mboth\x1b[0m",
		"\x1b[39mfg":               baseSGR() + "\x1b[39m\x1b[38;5;255mfg\x1b[0m",
		"\x1b[49mbg":               baseSGR() + "\x1b[49m\x1b[48;5;233mbg\x1b[0m",
		"\x1b[38;5;179mamber":      baseSGR() + "\x1b[38;5;179mamber\x1b[0m",
		"\x1b[0;38;2;1;2;3mtrue":   baseSGR() + "\x1b[0;38;2;1;2;3m\x1b[48;5;233mtrue\x1b[0m",
		"\x1b[48;5;235mbar\x1b[0m": baseSGR() + "\x1b[48;5;235mbar\x1b[0m\x1b[38;5;255m\x1b[48;5;233m\x1b[0m",
	}
	for in, want := range cases {
		if got := Paint([]string{in})[0]; got != want {
			t.Errorf("%q\n got %q\nwant %q", in, got, want)
		}
	}
}

func TestAPaintedFrameKeepsItsWidthAndHasNoUnpaintedReset(t *testing.T) {
	forceColour(t)
	st := NewState()
	st.Styled, st.Tab, st.Workspace = true, SessionTab, loadWorkspace(t)
	st.PaneLines = []string{"\x1b[0;1;32mok\x1b[0m plain"}
	frame := RenderLines(loadView(t), st, 120, 36)
	painted := Paint(Decorate(frame))
	for i := range frame {
		if lipgloss.Width(painted[i]) != 120 {
			t.Fatalf("line %d is %d wide", i, lipgloss.Width(painted[i]))
		}
		body := strings.TrimSuffix(painted[i], "\x1b[0m")
		for _, m := range sgrSeq.FindAllStringIndex(body, -1) {
			seq := body[m[0]:m[1]]
			if seq == "\x1b[0m" && !strings.HasPrefix(body[m[1]:], "\x1b[38;5;255m\x1b[48;5;233m") {
				t.Fatalf("line %d: a reset is not followed by the ground: %q", i, body)
			}
		}
	}
}
