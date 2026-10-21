package ui

import (
	"os"
	"path/filepath"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
)

func TestImportsAndDefinitionsAreFound(t *testing.T) {
	repo := t.TempDir()
	for p, body := range map[string]string{"src/App.tsx": "export default function App() {}\n", "src/lib/index.ts": "export const x = 1\n",
		"app/models.py": "class User:\n    pass\n"} {
		_ = os.MkdirAll(filepath.Join(repo, filepath.Dir(p)), 0o755)
		_ = os.WriteFile(filepath.Join(repo, p), []byte(body), 0o644)
	}
	for spec, want := range map[string]string{"./App.tsx": "src/App.tsx", "./App": "src/App.tsx", "./lib": "src/lib/index.ts",
		"@/App": "src/App.tsx", "react": ""} {
		if got := resolveImport(repo, "src/main.tsx", spec); got != want {
			t.Errorf("%s from src/main.tsx: %q, want %q", spec, got, want)
		}
	}
	if got := resolveImport(repo, "app/views.py", ".models"); got != "app/models.py" {
		t.Errorf("a Python relative module: %q", got)
	}
	lines := []string{"import { StrictMode } from 'react'", "import { createRoot } from 'react-dom/client'", "import App, { helper as h } from './App.tsx'"}
	if importedFrom(lines, "createRoot") != "react-dom/client" || importedFrom(lines, "App") != "./App.tsx" || importedFrom(lines, "root") != "" {
		t.Fatal("where a name is imported from")
	}
	for line, name := range map[string]string{"export default function App() {": "App", "const helper = async (a) => {": "helper",
		"def run(self):": "run", "func (s *Server) Start() error {": "Start", "export interface Props {": "Props", "  render() {": "render",
		"type Row struct {": "Row"} {
		if definedAt(line, name, definitionRe(name)) < 0 {
			t.Errorf("%q defines %s", line, name)
		}
	}
	if definedAt("  return App()", "App", definitionRe("App")) >= 0 || definedAt("if (ok) {", "if", definitionRe("if")) >= 0 {
		t.Error("a use is not a definition")
	}
}

func TestF12GoesToTheDefinitionAndCtrlTComesBack(t *testing.T) {
	m, _ := codeRepo(t)
	repo := m.st.App.Repo
	_ = os.WriteFile(filepath.Join(repo, "src/App.tsx"), []byte("import './index.css'\n\nexport default function App() {\n  return null\n}\n"), 0o644)
	_ = os.WriteFile(filepath.Join(repo, "src/util.ts"), []byte("// helpers\nexport function slug(s: string) {\n  return s\n}\n"), 0o644)
	_ = os.WriteFile(filepath.Join(repo, "src/main.tsx"), []byte("import { createRoot } from 'react-dom/client'\nimport App from './App.tsx'\n\ncreateRoot(App)\nslug('x')\n"), 0o644)
	m.st.App.Files = append(m.st.App.Files, "src/App.tsx", "src/util.ts", "src/main.tsx")
	follow(m, m.runPalette(paletteItem{Action: "file:src/main.tsx"}, ""), 0)
	a := m.st.App
	press(m, "e")
	a.setCursor(pos{3, 12}) // on App in createRoot(App)
	follow(m, func() tea.Msg { return tea.KeyMsg{Type: tea.KeyF12} }, 0)
	if a.File != "src/App.tsx" || a.cursor() != (pos{2, 24}) {
		t.Fatalf("F12 opens the imported file at the definition: %q %v", a.File, a.cursor())
	}
	follow(m, func() tea.Msg { return tea.KeyMsg{Type: tea.KeyCtrlT} }, 0)
	if a.File != "src/main.tsx" || a.cursor() != (pos{3, 12}) {
		t.Fatalf("⌃t comes back: %q %v", a.File, a.cursor())
	}
	a.setCursor(pos{4, 1}) // slug, not imported: looked up in the repository
	follow(m, func() tea.Msg { return tea.KeyMsg{Type: tea.KeyF12} }, 0)
	if a.File != "src/util.ts" || a.cursor() != (pos{1, 16}) {
		t.Fatalf("a name found in the repository: %q %v", a.File, a.cursor())
	}
	follow(m, func() tea.Msg { return tea.KeyMsg{Type: tea.KeyCtrlT} }, 0)
	a.setCursor(pos{0, 10}) // createRoot comes from a package
	follow(m, func() tea.Msg { return tea.KeyMsg{Type: tea.KeyF12} }, 0)
	if a.File != "src/main.tsx" || m.st.Message != "createRoot comes from react-dom/client, outside the repository" {
		t.Fatalf("a package's name says so: %q", m.st.Message)
	}
	a.setCursor(pos{1, 20}) // in './App.tsx'
	follow(m, func() tea.Msg { return tea.KeyMsg{Type: tea.KeyF12} }, 0)
	if a.File != "src/App.tsx" {
		t.Fatalf("an import's path opens the file: %q", a.File)
	}
}

func TestACtrlClickOnANameGoesToItsDefinition(t *testing.T) {
	m, _ := codeRepo(t)
	m.SetMouse(true)
	repo := m.st.App.Repo
	_ = os.WriteFile(filepath.Join(repo, "src/routes/util.ts"), []byte("export function check(u) {\n  return u\n}\n"), 0o644)
	_ = os.WriteFile(filepath.Join(repo, "src/routes/use.ts"), []byte("import { check } from './util'\ncheck(1)\n"), 0o644)
	m.st.App.Files = append(m.st.App.Files, "src/routes/util.ts", "src/routes/use.ts")
	follow(m, m.runPalette(paletteItem{Action: "file:src/routes/use.ts"}, ""), 0)
	x, y := findOnScreen(t, m, "check(1)")
	follow(m, func() tea.Msg {
		return tea.MouseMsg{X: x + 2, Y: y, Button: tea.MouseButtonLeft, Action: tea.MouseActionPress, Ctrl: true}
	}, 0)
	if a := m.st.App; a.File != "src/routes/util.ts" || a.cursor() != (pos{0, 16}) {
		t.Fatalf("⌃ click goes to the definition: %q %v", a.File, a.cursor())
	}
}
