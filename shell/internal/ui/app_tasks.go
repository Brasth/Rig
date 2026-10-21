package ui

import (
	"encoding/json"
	"os"
	"path"
	"path/filepath"
	"regexp"
	"sort"
	"strings"

	tea "github.com/charmbracelet/bubbletea"
)

// Running things from the editor, as an IDE's tasks and test runner: ⌃k › run a task lists the repository's
// scripts (package.json), make and just targets, and its test suites; F9 runs the test the cursor is in (or the
// open file's tests). Each runs in the terminal split, in your own shell, so you see it as it runs.

// taskItem is one thing to run: its name, what it runs, and where it comes from.
type taskItem struct {
	name, command, source string
}

var (
	makeTargetRe = regexp.MustCompile(`^([A-Za-z0-9][A-Za-z0-9_./-]*)\s*:(?:[^=]|$)`)
	justRecipeRe = regexp.MustCompile(`^([A-Za-z_][A-Za-z0-9_-]*)(?:\s+[^:]*)?:\s*(?:$|[^=])`)
)

// repoTasks are the tasks the repository offers, read from its files.
func repoTasks(repo string) []taskItem {
	var out []taskItem
	read := func(name string) string {
		b, err := os.ReadFile(filepath.Join(repo, name))
		if err != nil || len(b) > 1<<20 {
			return ""
		}
		return string(b)
	}
	if pkg := read("package.json"); pkg != "" {
		var p struct {
			Scripts map[string]string `json:"scripts"`
		}
		if json.Unmarshal([]byte(pkg), &p) == nil && len(p.Scripts) > 0 {
			runner := "npm run"
			switch {
			case read("pnpm-lock.yaml") != "":
				runner = "pnpm run"
			case read("yarn.lock") != "":
				runner = "yarn"
			case read("bun.lockb") != "" || read("bun.lock") != "":
				runner = "bun run"
			}
			names := make([]string, 0, len(p.Scripts))
			for n := range p.Scripts {
				names = append(names, n)
			}
			sort.Strings(names)
			for _, n := range names {
				out = append(out, taskItem{n, runner + " " + n, "package.json · " + p.Scripts[n]})
			}
		}
	}
	for _, mk := range []string{"Makefile", "makefile", "GNUmakefile"} {
		text := read(mk)
		if text == "" {
			continue
		}
		for _, l := range strings.Split(text, "\n") {
			if m := makeTargetRe.FindStringSubmatch(l); m != nil && !strings.HasPrefix(m[1], ".") && !strings.Contains(m[1], "%") && !strings.Contains(m[1], "$") {
				out = append(out, taskItem{m[1], "make " + m[1], mk})
			}
		}
		break
	}
	for _, jf := range []string{"justfile", "Justfile", ".justfile"} {
		text := read(jf)
		if text == "" {
			continue
		}
		for _, l := range strings.Split(text, "\n") {
			if m := justRecipeRe.FindStringSubmatch(l); m != nil && !strings.HasPrefix(l, " ") && !strings.HasPrefix(l, "\t") && !strings.HasPrefix(m[1], "_") {
				out = append(out, taskItem{m[1], "just " + m[1], jf})
			}
		}
		break
	}
	if read("go.mod") != "" {
		out = append(out, taskItem{"go test", "go test ./...", "go.mod"})
	}
	if read("Cargo.toml") != "" {
		out = append(out, taskItem{"cargo test", "cargo test", "Cargo.toml"})
	}
	if read("pytest.ini") != "" || read("pyproject.toml") != "" || read("setup.cfg") != "" || read("tests/__init__.py") != "" || read("tox.ini") != "" {
		out = append(out, taskItem{"pytest", "pytest", "python"})
	}
	return out
}

// taskPaletteItems are the palette's rows in ⌃k › run a task: the tasks that match.
func taskPaletteItems(st State, q string) []paletteItem {
	var out []paletteItem
	for _, t := range st.App.Tasks {
		if q != "" && !strings.Contains(strings.ToLower(t.name+" "+t.command), q) {
			continue
		}
		detail := t.command + " · " + clean(t.source)
		if r := []rune(detail); len(r) > 48 {
			detail = string(r[:47]) + "…"
		}
		out = append(out, paletteItem{"run a task", t.name, detail, "run:" + t.command})
	}
	if len(out) == 0 {
		detail := "no package.json scripts, Makefile, justfile, go.mod, Cargo.toml or pytest here"
		if q != "" {
			detail = "no task matches " + q + "; ⏎ runs it as typed"
			out = append(out, paletteItem{"run a task", st.App.Palette.Query, detail, "run:" + st.App.Palette.Query})
		} else {
			out = append(out, paletteItem{"run a task", "nothing to run", detail, ""})
		}
	}
	return out
}

// openMemory opens the file the parent reads for its standing instructions: CLAUDE.md for claude, AGENTS.md for the
// others, in the code tab. The parent's own /memory does the same in its screen.
func (m *Model) openMemory() tea.Cmd {
	a := m.st.App
	name := "AGENTS.md"
	if appParent(m.st) == "claude" {
		name = "CLAUDE.md"
	}
	if _, err := os.Stat(filepath.Join(a.Repo, name)); err != nil {
		m.st.Message = "no " + name + " in this repository yet: /init asks " + appParent(m.st) + " to write one"
		return nil
	}
	return m.runPalette(paletteItem{Action: "file:" + name}, "")
}

// runInTerminal types a command into the terminal split, opening it first when it is not there.
func (m *Model) runInTerminal(command string) tea.Cmd {
	a := m.st.App
	command = strings.TrimSpace(command)
	if command == "" {
		return nil
	}
	var cmd tea.Cmd
	if !a.Term || m.paneDone() {
		a.Term = false
		cmd = m.toggleTerm()
	}
	a.TermFocus, a.TermUp = true, 0
	if m.pane != nil {
		m.pane.Write([]byte(command + "\n"))
	}
	m.st.Message = "running: " + command
	return cmd
}

var (
	goTestRe   = regexp.MustCompile(`^func (Test\w+)\(`)
	pyTestRe   = regexp.MustCompile(`^\s*(?:async\s+)?def (test\w*)\(`)
	pyClassRe  = regexp.MustCompile(`^class (\w+)`)
	jsTestRe   = regexp.MustCompile(`^\s*(?:it|test)(?:\.only|\.skip)?\(\s*['"` + "`" + `](.+?)['"` + "`" + `]`)
	rustTestRe = regexp.MustCompile(`^\s*(?:pub\s+)?(?:async\s+)?fn (\w+)\s*\(`)
)

// testAtCursor is the command that runs the test the cursor is in, or the file's tests when it is in none; "" when
// the file is not one that has tests.
func testAtCursor(repo, file string, lines []string, line int) string {
	ext := strings.ToLower(path.Ext(file))
	dir := path.Dir(file)
	if dir == "." {
		dir = ""
	}
	above := func(re *regexp.Regexp) string {
		for i := min(line, len(lines)-1); i >= 0; i-- {
			if m := re.FindStringSubmatch(lines[i]); m != nil {
				return m[1]
			}
		}
		return ""
	}
	switch ext {
	case ".go":
		if !strings.HasSuffix(file, "_test.go") {
			return ""
		}
		pkg := "./" + dir
		if dir == "" {
			pkg = "."
		}
		if name := above(goTestRe); name != "" {
			return "go test " + pkg + " -run '^" + name + "$'"
		}
		return "go test " + pkg
	case ".py":
		base := path.Base(file)
		if !strings.HasPrefix(base, "test_") && !strings.HasSuffix(base, "_test.py") && !strings.Contains(file, "tests/") {
			return ""
		}
		if name := above(pyTestRe); name != "" {
			indented := false
			for i := min(line, len(lines)-1); i >= 0; i-- {
				if pyTestRe.MatchString(lines[i]) {
					indented = strings.HasPrefix(lines[i], " ") || strings.HasPrefix(lines[i], "\t")
					break
				}
			}
			if class := above(pyClassRe); class != "" && indented {
				return "pytest " + file + "::" + class + "::" + name // a method of a test class
			}
			return "pytest " + file + "::" + name
		}
		return "pytest " + file
	case ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs":
		base := path.Base(file)
		if !strings.Contains(base, ".test.") && !strings.Contains(base, ".spec.") && !strings.Contains(file, "__tests__/") {
			return ""
		}
		runner := "npx jest"
		if pkg, err := os.ReadFile(filepath.Join(repo, "package.json")); err == nil && strings.Contains(string(pkg), `"vitest"`) {
			runner = "npx vitest run"
		}
		if name := above(jsTestRe); name != "" {
			return runner + " " + file + " -t " + shellQuote(name)
		}
		return runner + " " + file
	case ".rs":
		if name := above(rustTestRe); name != "" && strings.Contains(strings.Join(lines, "\n"), "#[test]") {
			return "cargo test " + name
		}
		if strings.Contains(strings.Join(lines, "\n"), "#[test]") {
			return "cargo test"
		}
	}
	return ""
}

// shellQuote quotes a test's name for the shell.
func shellQuote(s string) string {
	return "'" + strings.ReplaceAll(s, "'", `'\''`) + "'"
}

// runTestAtCursor runs the test the cursor is in, in the terminal split.
func (m *Model) runTestAtCursor() tea.Cmd {
	a := m.st.App
	if a.File == "" || a.FileLines == nil {
		return nil
	}
	line, _ := cursorLine(a)
	command := testAtCursor(a.Repo, a.File, a.FileLines, line)
	if command == "" {
		m.st.Message = path.Base(a.File) + " has no tests rig shell knows how to run (go, pytest, jest, vitest, cargo)"
		return nil
	}
	if a.Dirty {
		m.saveFile() // the test runs what is on disk
	}
	return m.runInTerminal(command)
}

// openTasks lists the repository's tasks in the palette.
func (m *Model) openTasks() tea.Cmd {
	a := m.st.App
	a.Tasks = repoTasks(a.Repo)
	a.Palette = &Palette{Tasks: true}
	return nil
}
