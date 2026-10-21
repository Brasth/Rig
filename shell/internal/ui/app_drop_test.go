package ui

import (
	"bytes"
	"image"
	"image/png"
	"os"
	"path/filepath"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
)

func TestADroppedFilesPathsAreReadAsTerminalsPasteThem(t *testing.T) {
	dir := t.TempDir()
	spaced, plain := filepath.Join(dir, "My Shot.png"), filepath.Join(dir, "notes.md")
	for _, p := range []string{spaced, plain} {
		_ = os.WriteFile(p, []byte("x"), 0o644)
	}
	escaped := strings.ReplaceAll(spaced, " ", `\ `)
	for paste, want := range map[string][]string{
		escaped:                     {spaced},
		"'" + spaced + "' " + plain: {spaced, plain},
		`"` + spaced + `"`:          {spaced},
		"file://" + plain + "\n":    {plain},
		"file://" + strings.ReplaceAll(spaced, " ", "%20"): {spaced},
		plain + " " + dir + "/missing":                     nil,
		"fix the bug in " + plain:                          nil,
		"notes.md":                                         nil,
		"'" + plain:                                        nil,
		"file://elsewhere" + plain:                         nil,
	} {
		got := droppedFiles(paste)
		if strings.Join(got, "|") != strings.Join(want, "|") {
			t.Errorf("%q: %q, want %q", paste, got, want)
		}
	}
}

func TestDroppingFilesOnTheWindowAddsThemToTheBox(t *testing.T) {
	m, src := appModel(t)
	withConversation(t, m, src)
	repo := m.st.App.Repo
	_ = os.MkdirAll(filepath.Join(repo, "src"), 0o755)
	inside := filepath.Join(repo, "src", "login.ts")
	outside := filepath.Join(t.TempDir(), "Screen Shot.png")
	_ = os.WriteFile(inside, []byte("x"), 0o644)
	_ = os.WriteFile(outside, []byte("x"), 0o644)
	typeText(m, "look at")
	m.st.App.Tab = 3
	m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune(inside + " " + strings.ReplaceAll(outside, " ", `\ `)), Paste: true})
	want := `look at @src/login.ts @"` + outside + `" `
	if m.st.Prompt != want || m.st.App.Tab != 1 {
		t.Fatalf("the box has the two files, inside the repo by its own path: %q (tab %d)", m.st.Prompt, m.st.App.Tab)
	}
	m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("plain words"), Paste: true})
	if !strings.HasSuffix(m.st.Prompt, " plain words") {
		t.Fatalf("a paste that is not files is text: %q", m.st.Prompt)
	}
}

func TestADroppedPictureShowsAboveTheBox(t *testing.T) {
	m, src := appModel(t)
	withConversation(t, m, src)
	dir := t.TempDir()
	shot := filepath.Join(dir, "Screen Shot.png")
	img := image.NewRGBA(image.Rect(0, 0, 40, 20))
	var buf bytes.Buffer
	_ = png.Encode(&buf, img)
	_ = os.WriteFile(shot, buf.Bytes(), 0o644)
	notes := filepath.Join(dir, "notes.md")
	_ = os.WriteFile(notes, []byte("x"), 0o644)
	m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune(strings.ReplaceAll(shot, " ", `\ `) + " " + notes), Paste: true})
	out := appScreen(m)
	for _, want := range []string{"▣ Screen Shot.png", "40×20 · ", " B", `@"` + shot + `"`} {
		if !strings.Contains(out, want) {
			t.Fatalf("the picture is named above the box; missing %q:\n%s", want, out)
		}
	}
	if strings.Contains(out, "▣ notes.md") {
		t.Fatal("only pictures get a line")
	}
	if cols, rows := thumbCells(40, 20); cols != 20 || rows != 5 {
		t.Fatalf("a 2:1 picture's thumbnail: %d×%d", cols, rows)
	}
	if cols, _ := thumbCells(1000, 10); cols != 40 {
		t.Fatal("never wider than 40 cells")
	}
	if cols, _ := thumbCells(1, 1000); cols != 4 {
		t.Fatal("never narrower than 4 cells")
	}
	m.setPrompt("", 0) // sent or cleared: the line goes with the reference
	if out := appScreen(m); strings.Contains(out, "▣ Screen Shot.png") {
		t.Fatalf("it goes with the reference:\n%s", out)
	}
	if sizeWord(214*1024) != "214 KB" || sizeWord(1<<20+1<<19) != "1.5 MB" || sizeWord(12) != "12 B" {
		t.Fatal("sizes read as people say them")
	}
}
