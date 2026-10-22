package ui

import (
	"strings"
	"testing"
)

func md(text string, cw int) string {
	return strings.Join(mdLines(State{}, text, cw), "\n")
}

func TestMarkdownIsDrawnNotShownRaw(t *testing.T) {
	out := md("# Plan\n\nDo **this** first, then `go test`.\n\n- one\n- two with a very long line that has to wrap around the edge of the screen somewhere\n1. step\n> careful\n---", 40)
	for _, want := range []string{"Plan", "Do this first, then go test.", "  • one", "  • two with", "    ", "  1. step", "  ▎ careful", "────"} {
		if !strings.Contains(out, want) {
			t.Errorf("missing %q:\n%s", want, out)
		}
	}
	for _, raw := range []string{"# Plan", "**this**", "`go test`", "- one", "> careful"} {
		if strings.Contains(out, raw) {
			t.Errorf("the marker %q should be drawn, not shown:\n%s", raw, out)
		}
	}
	for _, l := range strings.Split(out, "\n") {
		if len([]rune(l)) > 40 {
			t.Errorf("line wider than the screen: %q", l)
		}
	}
}

func TestACodeBlockKeepsItsLinesAndIsMarked(t *testing.T) {
	out := md("Run:\n\n```go\nfunc main() {\n\tfmt.Println(\"a    b\")\n}\n```\nDone.", 60)
	for _, want := range []string{"╭ go", "│ func main() {", "│     fmt.Println(\"a    b\")", "╰", "Done."} {
		if !strings.Contains(out, want) {
			t.Errorf("missing %q:\n%s", want, out)
		}
	}
	// half a block, as it streams in, still reads as code
	half := md("```py\nprint(1)", 60)
	if !strings.Contains(half, "│ print(1)") || strings.Contains(half, "```") {
		t.Errorf("an unclosed block is code until it closes:\n%s", half)
	}
	// a long code line is cut into pieces, not lost
	long := md("```\n"+strings.Repeat("x", 100)+"\n```", 40)
	if strings.Count(long, "x") != 100 {
		t.Errorf("no code is lost to wrapping:\n%s", long)
	}
}

func TestATableIsDrawnInColumns(t *testing.T) {
	out := md("| name | role |\n|------|------|\n| asana | **mcp** server |\n| clerk | app |", 60)
	for _, want := range []string{"│ name  │ role", "├", "│ asana │ mcp server", "│ clerk │ app"} {
		if !strings.Contains(out, want) {
			t.Errorf("missing %q:\n%s", want, out)
		}
	}
	if strings.Contains(out, "---") {
		t.Errorf("the separator row is the rule:\n%s", out)
	}
	for _, l := range strings.Split(md("| a | "+strings.Repeat("b", 80)+" |\n|---|---|\n| 1 | 2 |", 40), "\n") {
		if len([]rune(l)) > 40 {
			t.Errorf("a wide table is shortened to the screen: %q", l)
		}
	}
}

func TestAStyledPhraseKeepsItsStyleAcrossALineEnd(t *testing.T) {
	st := State{Styled: true}
	lines := mdLines(st, "see **one two three four five six** done", 16)
	if len(lines) < 2 {
		t.Fatalf("it wraps: %q", lines)
	}
	for _, l := range lines {
		if strings.Count(l, "\x1b[0m") < strings.Count(l, "\x1b[")/2 {
			t.Errorf("each styled word closes its own style so none bleeds past the line: %q", l)
		}
	}
}
