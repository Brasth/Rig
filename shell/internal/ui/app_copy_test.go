package ui

import (
	"strings"
	"testing"

	"github.com/Brasth/Rig/shell/internal/view"
)

func TestCopyingTakesTheTextNotTheDrawing(t *testing.T) {
	rows := []string{"  ╭ go", "  │ func main() {", "  │   run()", "  │ }", "  ╰", "  ▎ careful", "  │ a │ b │", "plain"}
	got := strings.Join(undecorate(rows), "\n")
	want := "```go\nfunc main() {\n  run()\n}\n```\ncareful\n  │ a │ b │\nplain"
	if got != want {
		t.Fatalf("a code block copies as markdown, a quote without its bar, a table as drawn:\n%s", got)
	}
}

func TestADragStaysInTheColumnItBeganIn(t *testing.T) {
	plain := []string{"LEFT   one two three   RIGHT", "LEFT   four five six   RIGHT"}
	sel := textSel{ax: 7, ay: 0, bx: 20, by: 1, cx0: 7, cx1: 22}
	if got := selectedText(plain, sel); strings.Contains(got, "LEFT") || strings.Contains(got, "RIGHT") || !strings.Contains(got, "one two three") || !strings.Contains(got, "four five six") {
		t.Fatalf("only the column's cells: %q", got)
	}
	free := textSel{ax: 7, ay: 0, bx: 20, by: 1}
	if got := selectedText(plain, free); !strings.Contains(got, "RIGHT") {
		t.Fatalf("a drag that began outside any column is as before: %q", got)
	}
}

func TestADoubleClickTakesTheWordAndATripleClickTheLine(t *testing.T) {
	m := &Model{plain: []string{"LEFT   Use `go test`, then ship it.   RIGHT"}}
	at := func(x int) *textSel { return &textSel{ax: x, ay: 0, bx: x, by: 0, cx0: 7, cx1: 35} }
	if got := m.wordOrLineAt(at(11), 2); got != "`go" {
		t.Fatalf("the word under the pointer: %q", got)
	}
	if got := m.wordOrLineAt(at(28), 2); got != "ship" {
		t.Fatalf("a word: %q", got)
	}
	if got := m.wordOrLineAt(at(32), 2); got != "it" {
		t.Fatalf("the full stop is not part of the word: %q", got)
	}
	if got := m.wordOrLineAt(at(11), 3); got != "Use `go test`, then ship it." {
		t.Fatalf("the whole line, within the column: %q", got)
	}
	if got := m.wordOrLineAt(&textSel{ax: 1, ay: 0, cx0: 7, cx1: 35}, 2); got != "" {
		t.Fatalf("outside the column nothing is taken: %q", got)
	}
}

func TestCopyLastGivesTheAnswerOrItsLastCodeBlock(t *testing.T) {
	entries := []view.ChatEntry{{Role: "user", Text: "q"}, {Role: "tool", Text: "ran x"},
		{Role: "assistant", Text: "First:\n```sh\nnpm i\n```\nthen\n```go\nfmt.Println(1)\n```"}, {Role: "assistant", Text: "Done."}}
	if got := lastAnswer(entries); !strings.HasPrefix(got, "First:") || !strings.HasSuffix(got, "Done.") {
		t.Fatalf("the answer after the last thing you said: %q", got)
	}
	if got, ok := lastCodeBlock(lastAnswer(entries)); !ok || got != "fmt.Println(1)" {
		t.Fatalf("the last block: %q %v", got, ok)
	}
	if _, ok := lastCodeBlock("no code here"); ok {
		t.Fatal("no block")
	}
	if got, ok := lastCodeBlock("```py\nprint(1)"); !ok || got != "print(1)" {
		t.Fatalf("one still streaming in: %q", got)
	}
	st := State{Chat: &view.ChatTranscript{Entries: []view.ChatEntry{{Role: "user", Text: "q"}, {Role: "assistant", Text: "plain text"}}}}
	if got := copyLast(st, "code"); got != "the last answer has no code block" {
		t.Fatalf("it says so: %q", got)
	}
}
