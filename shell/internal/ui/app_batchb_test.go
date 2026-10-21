package ui

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
)

func TestTsconfigPathsAndReExportsAreFollowed(t *testing.T) {
	repo := t.TempDir()
	for p, body := range map[string]string{
		"tsconfig.json":    "{\n  // comments allowed\n  \"compilerOptions\": {\"baseUrl\": \".\", \"paths\": {\"@app/*\": [\"src/app/*\"], \"@lib\": [\"src/lib/index.ts\"],},},\n}\n",
		"src/app/user.ts":  "export function loadUser() {}\n",
		"src/lib/index.ts": "export * from './store'\nexport { helper as h } from './util'\n",
		"src/lib/store.ts": "export class Store {}\n",
		"src/lib/util.ts":  "export function helper() {}\n",
		"src/main.ts":      "import { loadUser } from '@app/user'\nimport { Store, h } from '@lib'\n",
	} {
		_ = os.MkdirAll(filepath.Join(repo, filepath.Dir(p)), 0o755)
		_ = os.WriteFile(filepath.Join(repo, p), []byte(body), 0o644)
	}
	if got := resolveImport(repo, "src/main.ts", "@app/user"); got != "src/app/user.ts" {
		t.Fatalf("a tsconfig wildcard path: %q", got)
	}
	if got := resolveImport(repo, "src/main.ts", "@lib"); got != "src/lib/index.ts" {
		t.Fatalf("an exact path: %q", got)
	}
	re := definitionRe("Store")
	if found := throughReExports(repo, "src/lib/index.ts", "Store", re, 0); found == nil || found.file != "src/lib/store.ts" {
		t.Fatalf("export * from is followed: %+v", found)
	}
	if found := throughReExports(repo, "src/lib/index.ts", "helper", definitionRe("helper"), 0); found == nil || found.file != "src/lib/util.ts" {
		t.Fatalf("export { x } from is followed: %+v", found)
	}
	if found := throughReExports(repo, "src/lib/index.ts", "nothing", definitionRe("nothing"), 0); found != nil {
		t.Fatal("a name not re-exported is not found")
	}
}

func TestF2OffersTheRestOfTheRepositoryThroughSearch(t *testing.T) {
	m, _ := editing(t, 0, 0)
	a := m.st.App
	a.FileLines = []string{"const user = 1", "use(user)"}
	a.setCursor(pos{0, 7})
	keyMsg(m, tea.KeyF2)
	a.CodeAsk.text = ""
	typeText(m, "account")
	keyMsg(m, tea.KeyEnter)
	if a.CodeAsk == nil || a.CodeAsk.kind != "rename-where" || !strings.Contains(a.CodeAsk.label, "user → account") {
		t.Fatalf("after the rename here, a question about the rest: %+v", a.CodeAsk)
	}
	follow(m, func() tea.Msg { return tea.KeyMsg{Type: tea.KeyEnter} }, 0)
	s := a.Search
	if s == nil || s.Query != "user" || !s.Word || s.Replace == nil || *s.Replace != "account" {
		t.Fatalf("⏎ opens the search on the old name, whole word, the replace filled in: %+v", s)
	}
}

func TestTheOutlineFollowsTheCursorAndGoesToASymbol(t *testing.T) {
	m, _ := codeRepo(t)
	a := m.st.App
	a.FileLines = []string{"import x", "", "export function first() {", "  return 1", "}", "", "export function second() {", "  return 2", "}"}
	m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("O")})
	if !a.Outline || !a.TreeFocus {
		t.Fatal("O shows the outline with the keys")
	}
	out := appScreen(m)
	for _, want := range []string{"OUTLINE", "first", "second"} {
		if !strings.Contains(out, want) {
			t.Fatalf("missing %q:\n%s", want, out)
		}
	}
	press(m, "down")
	follow(m, press(m, "enter"), 0)
	if a.FileOff != 3 || a.TreeFocus {
		t.Fatalf("⏎ scrolls to the symbol's line (7, three above it) and leaves the outline: off %d", a.FileOff)
	}
	press(m, "e")
	a.setCursor(pos{7, 0})
	if at := outlineAt(outlineRows(a), 7); at != 1 {
		t.Fatalf("the cursor in second lights it: %d", at)
	}
}
