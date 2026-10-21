package ui

import (
	"encoding/json"
	"os"
	"path"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"time"
	"unicode"

	tea "github.com/charmbracelet/bubbletea"
)

// Go to definition, as an IDE does without a language server: F12 or a ⌃/⌥ click on a name opens where it is defined,
// on an import path opens that file, and ⌃t goes back. A name imported from a file of the repository is looked up in
// that file; any other name in this file, then its folder, then the whole repository. A name from a package outside
// the repository says so.

// jumpTo is a place in a file: where a definition is, or where a jump came from.
type jumpTo struct {
	file string
	at   pos
}

type definitionMsg struct {
	name  string
	found *jumpTo
	note  string
}

// identAt is the name the cursor is on (letters, digits, _ and $).
func identAt(line string, c int) string {
	r := []rune(line)
	word := func(x rune) bool { return unicode.IsLetter(x) || unicode.IsDigit(x) || x == '_' || x == '$' }
	from, to := min(c, len(r)), min(c, len(r))
	for from > 0 && word(r[from-1]) {
		from--
	}
	for to < len(r) && word(r[to]) {
		to++
	}
	return string(r[from:to])
}

// quotedAt is the string the cursor is in, when it is one: an import's path.
func quotedAt(line string, c int) string {
	r := []rune(line)
	for _, q := range []rune{'\'', '"', '`'} {
		open := -1
		for i := 0; i < len(r); i++ {
			if r[i] != q {
				continue
			}
			if open < 0 {
				open = i
				continue
			}
			if c > open && c <= i {
				return string(r[open+1 : i])
			}
			open = -1
		}
	}
	return ""
}

var sourceExts = []string{"", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".vue", ".svelte", ".py", ".go", ".rs", ".rb", ".css", ".scss", ".json"}

// resolveImport is the repository file an import path names, from the file it is in; "" when it is not in the repository.
func resolveImport(repo, from, spec string) string {
	var bases []string
	switch {
	case strings.HasPrefix(spec, "./") || strings.HasPrefix(spec, "../"):
		bases = []string{path.Join(path.Dir(from), spec)}
	case len(aliasBases(repo, spec)) > 0:
		bases = aliasBases(repo, spec) // tsconfig's paths and baseUrl
	case strings.HasPrefix(spec, "@/") || strings.HasPrefix(spec, "~/"):
		bases = []string{path.Join("src", spec[2:]), spec[2:]} // the usual alias for src
	case strings.HasPrefix(spec, "/"):
		bases = []string{strings.TrimPrefix(spec, "/")}
	case strings.HasPrefix(spec, "."):
		// a Python relative module: from .models import User
		dots := len(spec) - len(strings.TrimLeft(spec, "."))
		dir := path.Dir(from)
		for i := 1; i < dots; i++ {
			dir = path.Dir(dir)
		}
		bases = []string{path.Join(dir, strings.ReplaceAll(spec[dots:], ".", "/"))}
	default:
		bases = []string{spec, strings.ReplaceAll(spec, ".", "/"), path.Join("src", spec)}
	}
	for _, base := range bases {
		if strings.HasPrefix(base, "..") {
			continue
		}
		for _, ext := range sourceExts {
			for _, cand := range []string{base + ext, path.Join(base, "index"+ext), path.Join(base, "__init__"+ext)} {
				if ext == "" && cand != base {
					continue
				}
				if info, err := os.Stat(filepath.Join(repo, filepath.FromSlash(cand))); err == nil && !info.IsDir() {
					return cand
				}
			}
		}
	}
	return ""
}

var (
	jsImport = regexp.MustCompile(`(?:import|export)\s+(?:type\s+)?([^'"]*?)\s+from\s+['"]([^'"]+)['"]`)
	pyImport = regexp.MustCompile(`^\s*from\s+([\w.]+)\s+import\s+(.+)$`)
)

// tsPaths is a repository's tsconfig (or jsconfig) aliases: baseUrl and paths, read once and kept a minute.
type tsPaths struct {
	base  string
	paths map[string][]string // "@app/*" → ["src/app/*"]
	at    time.Time
}

var (
	tsCache   = map[string]*tsPaths{}
	tsCacheMu sync.Mutex
)

func readTsPaths(repo string) *tsPaths {
	tsCacheMu.Lock()
	defer tsCacheMu.Unlock()
	if c := tsCache[repo]; c != nil && time.Since(c.at) < time.Minute {
		return c
	}
	out := &tsPaths{paths: map[string][]string{}, at: time.Now()}
	for _, name := range []string{"tsconfig.json", "jsconfig.json"} {
		data, err := os.ReadFile(filepath.Join(repo, name))
		if err != nil {
			continue
		}
		var cfg struct {
			CompilerOptions struct {
				BaseURL string              `json:"baseUrl"`
				Paths   map[string][]string `json:"paths"`
			} `json:"compilerOptions"`
		}
		// tsconfig allows comments and trailing commas: strip the ones that break encoding/json
		if err := json.Unmarshal(stripJSONComments(data), &cfg); err != nil {
			continue
		}
		out.base = strings.TrimPrefix(path.Clean(cfg.CompilerOptions.BaseURL), "./")
		for k, v := range cfg.CompilerOptions.Paths {
			out.paths[k] = v
		}
		break
	}
	tsCache[repo] = out
	return out
}

// stripJSONComments takes // and /* */ comments and trailing commas out of a tsconfig.
func stripJSONComments(b []byte) []byte {
	s := regexp.MustCompile(`(?m)^\s*//.*$|/\*[\s\S]*?\*/`).ReplaceAllString(string(b), "")
	s = regexp.MustCompile(`,(\s*[}\]])`).ReplaceAllString(s, "$1")
	return []byte(s)
}

// aliasBases is where a tsconfig alias points: "@app/x" with "@app/*": ["src/app/*"] gives src/app/x, under baseUrl.
func aliasBases(repo, spec string) []string {
	ts := readTsPaths(repo)
	var out []string
	add := func(target string) {
		target = strings.TrimPrefix(path.Clean(target), "./")
		if ts.base != "" && ts.base != "." {
			target = path.Join(ts.base, target)
		}
		out = append(out, target)
	}
	for pattern, targets := range ts.paths {
		if i := strings.Index(pattern, "*"); i >= 0 {
			pre, post := pattern[:i], pattern[i+1:]
			if strings.HasPrefix(spec, pre) && strings.HasSuffix(spec, post) && len(spec) >= len(pre)+len(post) {
				mid := spec[len(pre) : len(spec)-len(post)]
				for _, t := range targets {
					add(strings.Replace(t, "*", mid, 1))
				}
			}
		} else if pattern == spec {
			for _, t := range targets {
				add(t)
			}
		}
	}
	if len(out) == 0 && ts.base != "" && ts.base != "." && !strings.HasPrefix(spec, ".") {
		add(spec) // a bare module under baseUrl
	}
	return out
}

var reExport = regexp.MustCompile(`^\s*export\s+(?:\*|\{([^}]*)\})\s+from\s+['"]([^'"]+)['"]`)

// throughReExports follows export * from and export { x } from in a file, a few hops, to where name is defined.
func throughReExports(repo, file, name string, re *regexp.Regexp, depth int) *jumpTo {
	if depth > 4 {
		return nil
	}
	data, err := os.ReadFile(filepath.Join(repo, filepath.FromSlash(file)))
	if err != nil || len(data) > 2<<20 {
		return nil
	}
	for _, l := range strings.Split(string(data), "\n") {
		m := reExport.FindStringSubmatch(l)
		if m == nil {
			continue
		}
		if m[1] != "" && !regexp.MustCompile(`(^|[^\w$])`+regexp.QuoteMeta(name)+`($|[^\w$])`).MatchString(m[1]) {
			continue // export { a, b } from: only those names
		}
		target := resolveImport(repo, file, m[2])
		if target == "" || target == file {
			continue
		}
		if found := findDefinition(repo, target, name, re); found != nil {
			return found
		}
		if found := throughReExports(repo, target, name, re, depth+1); found != nil {
			return found
		}
	}
	return nil
}

// importedFrom is where this file imports a name from: the import's path, when the file names it in one.
func importedFrom(lines []string, name string) string {
	word := regexp.MustCompile(`(^|[^\w$])` + regexp.QuoteMeta(name) + `($|[^\w$])`)
	for _, l := range lines {
		if m := jsImport.FindStringSubmatch(l); m != nil && word.MatchString(m[1]) {
			return m[2]
		}
		if m := pyImport.FindStringSubmatch(l); m != nil && word.MatchString(m[2]) {
			return m[1]
		}
	}
	return ""
}

// definitionRe finds where a name is defined, in the languages the code tab knows.
func definitionRe(name string) *regexp.Regexp {
	n := regexp.QuoteMeta(name)
	return regexp.MustCompile(`^\s*(?:export\s+)?(?:default\s+)?(?:declare\s+)?(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:abstract\s+)?` +
		`(?:function\*?|class|interface|type|enum|const|let|var|def|fn|struct|trait|module|func(?:\s*\([^)]*\))?)\s+` + n + `\b` +
		`|^\s*(?:export\s+)?(?:const|let|var)\s+\{[^}]*\b` + n + `\b[^}]*\}\s*=` + // destructured
		`|^\s*(?:(?:public|private|protected|static|async|override|readonly)\s+)*` + n + `\s*(?:<[^>]*>)?\s*\([^)]*\)\s*(?::[^{]*)?\{` + // a method
		`|^\s*` + n + `\s*[:=]\s*(?:async\s*)?(?:function\b|\([^)]*\)\s*=>|\w+\s*=>)`) // an object's function
}

// findDefinition looks for where name is defined in one file: the cursor goes on the name.
func findDefinition(repo, file, name string, re *regexp.Regexp) *jumpTo {
	data, err := os.ReadFile(filepath.Join(repo, filepath.FromSlash(file)))
	if err != nil || len(data) > 2<<20 {
		return nil
	}
	for i, l := range strings.Split(string(data), "\n") {
		if at := definedAt(l, name, re); at >= 0 {
			return &jumpTo{file: file, at: pos{i, at}}
		}
	}
	return nil
}

// definedAt is where name sits on a line that defines it, in runes; -1 when the line does not define it.
func definedAt(l, name string, re *regexp.Regexp) int {
	if notMethod[name] {
		return -1 // a keyword: if (ok) { looks like a method
	}
	loc := re.FindStringIndex(l)
	if loc == nil {
		return -1
	}
	if j := strings.Index(l[loc[0]:loc[1]], name); j >= 0 {
		return len([]rune(l[:loc[0]+j]))
	}
	return len([]rune(l[:loc[0]]))
}

// goToDefinition opens where the name or the import path at the cursor is defined.
func (m *Model) goToDefinition() tea.Cmd {
	a := m.st.App
	if a.File == "" || len(a.FileLines) == 0 {
		return nil
	}
	line := a.FileLines[min(a.CurLine, len(a.FileLines)-1)]
	repo, file, lines, files := a.Repo, a.File, a.FileLines, a.Files
	from := jumpTo{file: a.File, at: a.cursor()}
	if spec := quotedAt(line, a.CurCol); spec != "" {
		// an import's path: the file it names
		if target := resolveImport(repo, file, spec); target != "" {
			m.jumpBack = append(m.jumpBack, from)
			return m.openAt(jumpTo{file: target})
		}
		if !strings.Contains(spec, " ") && (strings.Contains(spec, "/") || strings.HasPrefix(spec, ".")) {
			m.st.Message = spec + " is not a file of the repository (a package?)"
			return nil
		}
	}
	name := identAt(line, a.CurCol)
	if name == "" {
		m.st.Message = "no name at the cursor"
		return nil
	}
	m.st.Message = "looking for " + name + "…"
	return func() tea.Msg {
		re := definitionRe(name)
		if spec := importedFrom(lines, name); spec != "" {
			target := resolveImport(repo, file, spec)
			if target == "" {
				return definitionMsg{name: name, note: name + " comes from " + spec + ", outside the repository"}
			}
			if found := findDefinition(repo, target, name, re); found != nil {
				return definitionMsg{name: name, found: found}
			}
			if found := throughReExports(repo, target, name, re, 0); found != nil {
				return definitionMsg{name: name, found: found} // an index that re-exports it
			}
			return definitionMsg{name: name, found: &jumpTo{file: target}} // a default export: the file itself
		}
		for i, l := range lines {
			if at := definedAt(l, name, re); at >= 0 {
				return definitionMsg{name: name, found: &jumpTo{file: file, at: pos{i, at}}}
			}
		}
		// its folder first, then the whole repository: source files only
		if len(files) == 0 {
			if out, err := gitOut(repo, "ls-files", "--cached", "--others", "--exclude-standard"); err == nil {
				files = strings.Split(strings.TrimSpace(out), "\n")
			}
		}
		dir := path.Dir(file)
		order := make([]string, 0, len(files))
		for _, f := range files {
			if path.Dir(f) == dir && f != file {
				order = append(order, f)
			}
		}
		for _, f := range files {
			if path.Dir(f) != dir {
				order = append(order, f)
			}
		}
		for _, f := range order {
			if languages[strings.ToLower(filepath.Ext(f))] == "" || strings.Contains(f, "node_modules/") {
				continue
			}
			if found := findDefinition(repo, f, name, re); found != nil {
				return definitionMsg{name: name, found: found}
			}
		}
		return definitionMsg{name: name, note: "no definition of " + name + " in the repository"}
	}
}

// openAt shows a place: in this file the cursor goes there; another file opens there.
func (m *Model) openAt(to jumpTo) tea.Cmd {
	a := m.st.App
	if to.file == a.File && a.FileLines != nil {
		m.goTo(to.at)
		return nil
	}
	a.pendingAt = &to
	return m.runPalette(paletteItem{Action: "file:" + to.file}, "")
}

// goTo puts the cursor at p in the open file, editing it, with the line in view.
func (m *Model) goTo(p pos) {
	a := m.st.App
	if !a.Editing {
		m.startEdit()
	}
	if a.Editing {
		m.moveTo(p, false)
		a.FileOff = max(0, a.CurLine-5)
		m.keepCursorShown()
	} else {
		a.FileOff = max(0, p.l-5)
	}
}

func (m *Model) afterDefinition(msg definitionMsg) tea.Cmd {
	a := m.st.App
	if msg.found == nil {
		m.st.Message = msg.note
		return nil
	}
	m.st.Message = msg.name + " · " + msg.found.file + ":" + strconv.Itoa(msg.found.at.l+1) + "   ⌃t back"
	m.jumpBack = append(m.jumpBack, jumpTo{file: a.File, at: a.cursor()})
	if len(m.jumpBack) > 50 {
		m.jumpBack = m.jumpBack[len(m.jumpBack)-50:]
	}
	return m.openAt(*msg.found)
}

// jumpBackCmd goes back to where the last jump came from (⌃t).
func (m *Model) jumpBackCmd() tea.Cmd {
	if len(m.jumpBack) == 0 {
		m.st.Message = "nowhere to go back to"
		return nil
	}
	to := m.jumpBack[len(m.jumpBack)-1]
	m.jumpBack = m.jumpBack[:len(m.jumpBack)-1]
	return m.openAt(to)
}
