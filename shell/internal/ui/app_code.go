package ui

import (
	"encoding/csv"
	"fmt"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"

	"github.com/charmbracelet/lipgloss"

	"github.com/Brasth/Rig/shell/internal/view"
)

// The changes, code, search and preview screens of the design's ADE screens.

// DiffFile is one file of a job's unified diff.
type DiffFile struct {
	Path           string
	Added, Deleted int
	Lines          []DiffLine
}

// DiffLine is one line of a hunk: Kind is '+', '-', ' ' or '@' (a hunk's start); Num the line number shown.
type DiffLine struct {
	Kind byte
	Num  int
	Text string
}

var hunkHead = regexp.MustCompile(`^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@`)

// ParseDiff splits a unified diff into files and numbered lines: new numbers for added and kept lines, old ones for
// removed lines, as the design shows them.
func ParseDiff(text string) []DiffFile {
	var out []DiffFile
	var cur *DiffFile
	oldN, newN := 0, 0
	for _, line := range strings.Split(text, "\n") {
		switch {
		case strings.HasPrefix(line, "diff --git "):
			out = append(out, DiffFile{})
			cur = &out[len(out)-1]
			if i := strings.LastIndex(line, " b/"); i >= 0 {
				cur.Path = line[i+3:]
			}
		case cur == nil:
		case strings.HasPrefix(line, "+++ "):
			if p := strings.TrimPrefix(line, "+++ "); p != "/dev/null" {
				cur.Path = strings.TrimPrefix(p, "b/")
			}
		case strings.HasPrefix(line, "--- "), strings.HasPrefix(line, "index "), strings.HasPrefix(line, "new file"),
			strings.HasPrefix(line, "deleted file"), strings.HasPrefix(line, "similarity"), strings.HasPrefix(line, "Binary"):
		case strings.HasPrefix(line, "@@"):
			if m := hunkHead.FindStringSubmatch(line); m != nil {
				oldN, _ = strconv.Atoi(m[1])
				newN, _ = strconv.Atoi(m[2])
			}
			cur.Lines = append(cur.Lines, DiffLine{Kind: '@'})
		case strings.HasPrefix(line, "+"):
			cur.Lines = append(cur.Lines, DiffLine{'+', newN, line[1:]})
			cur.Added++
			newN++
		case strings.HasPrefix(line, "-"):
			cur.Lines = append(cur.Lines, DiffLine{'-', oldN, line[1:]})
			cur.Deleted++
			oldN++
		case strings.HasPrefix(line, " "):
			cur.Lines = append(cur.Lines, DiffLine{' ', newN, line[1:]})
			oldN++
			newN++
		}
	}
	return out
}

// lightGround is true for the light theme, whose tints and syntax colours are darker.
func lightGround() bool { return colGround == "#fafafa" }

func tints() (add, del string) {
	if lightGround() {
		return "#ecfdf3", "#fdecec"
	}
	return "#13301f", "#3a1d20"
}

// splitHints is a footer with keys on the left and on the right, under a thin line, as the code and changes screens have.
func splitHints(st State, left, right [][2]string, w int) string {
	part := func(pairs [][2]string) string {
		var out []string
		for _, p := range pairs {
			out = append(out, st.hint(p))
		}
		return strings.Join(out, "   ")
	}
	return spread("   "+part(left), "", part(right)+"   ", w)
}

// changesScreen is the design's review of one helper's changes: its files, the diff with numbered lines, your comments,
// and how it was checked.
func changesScreen(v *view.View, st State, w, h int) ([]string, [][2]string) {
	a := st.App
	rows := v.Rows("Jobs")
	foot := [][2]string{{"]", "next file"}, {"c", "comment"}, {"esc", "back"}}
	if len(rows) == 0 {
		lines := []string{"", center(st.dim("No changes yet. When a helper finishes, what it changed shows here."), w)}
		for len(lines) < h {
			lines = append(lines, fit("", w))
		}
		return lines[:h], foot
	}
	r := rows[clampSel(st.Selected["Jobs"], len(rows))]
	worker := r.Worker
	if worker == "" {
		worker = "parent"
	}
	files := []DiffFile{}
	if a.Diff != nil && a.DiffID == r.ID {
		files = ParseDiff(a.Diff.Diff)
		if len(files) == 0 {
			for _, f := range a.Diff.Files {
				files = append(files, DiffFile{Path: f.Path})
			}
		}
	}
	cur := clampSel(a.DiffFile, len(files))
	var names []string
	for i, f := range files {
		name := filepath.Base(f.Path)
		if i == cur {
			name = st.bold(name)
		} else {
			name = st.dim(name)
		}
		names = append(names, st.zone(fmt.Sprintf("row:difffile:%d", i), name))
	}
	left := "   " + st.tile(worker) + "  " + st.bold(clean(r.Title)) + "  " + st.faint(fmt.Sprintf("%s · %d files", worker, len(files)))
	lines := []string{spread(left, "", strings.Join(names, "   ")+"   ", w), fit("", w)}
	cw := min(96, w-8)
	x := (w - cw) / 2
	var body []string
	switch {
	case a.Diff == nil || a.DiffID != r.ID:
		body = append(body, st.faint("loading the diff…"))
	case len(files) == 0:
		body = append(body, st.dim("No changes recorded for this job."))
	default:
		f := files[cur]
		dir, name := filepath.Dir(f.Path)+"/", filepath.Base(f.Path)
		if dir == "./" {
			dir = ""
		}
		head := st.faint(dir) + st.bold(name)
		if f.Added+f.Deleted > 0 {
			head += "   " + st.paint(colGreen, fmt.Sprintf("+%d", f.Added)) + " " + st.paint(colRed, fmt.Sprintf("−%d", f.Deleted))
		}
		body = append(body, head, "")
		add, del := tints()
		for i, l := range f.Lines {
			if l.Kind == '@' {
				if i > 0 {
					body = append(body, st.faint("   ⋯")) // between hunks; the file's first one needs no mark
				}
				continue
			}
			mark, colour, bg := " ", colText, ""
			switch l.Kind {
			case '+':
				mark, colour, bg = "+", colGreen, add
			case '-':
				mark, colour, bg = "−", colRed, del
			}
			text := fmt.Sprintf("%4d %s %s", l.Num, mark, sanitize(l.Text))
			text = fit(text, cw)
			if st.Styled {
				style := lipgloss.NewStyle().Foreground(lipgloss.Color(colour))
				if bg != "" {
					style = style.Background(lipgloss.Color(bg))
				}
				num := lipgloss.NewStyle().Foreground(lipgloss.Color(colFaint))
				if bg != "" {
					num = num.Background(lipgloss.Color(bg))
				}
				text = num.Render(text[:5]) + style.Render(text[5:])
			}
			body = append(body, text)
		}
		if a.Diff.Truncated {
			body = append(body, st.faint("   … the diff is longer than this view shows"))
		}
	}
	// what you said to this helper, and the checks under it
	var tail []string
	for _, c := range a.Comments[r.ID] {
		tail = append(tail, "    "+st.paint(colBorder, "│")+" "+st.faint(fmt.Sprintf("%-7s", "you"))+" "+c)
	}
	if len(a.Comments[r.ID]) > 0 {
		tail = append(tail, "    "+st.paint(colBorder, "│")+" "+st.faint(fmt.Sprintf("%-7s", worker))+" "+st.paint(colAccent, "● gets it with its next step"))
	}
	if a.Commenting {
		tail = append(tail, "", st.faint("comment to "+worker+" › ")+st.Prompt+"▏")
	}
	if r.Explanation != "" {
		tail = append(tail, "", st.dim(fit(clean(r.Explanation), cw)))
	}
	if st.DockEvidence != nil && st.DockID == r.ID {
		for _, sec := range st.DockEvidence.Sections {
			if sec.Title != "Checks" {
				continue
			}
			var parts []string
			for _, l := range sec.Lines {
				if part := checkWord(st, l.Text); part != "" {
					parts = append(parts, part)
				}
			}
			if len(parts) > 0 {
				tail = append(tail, "", strings.Join(parts, "   ")+"   "+st.dim(appParent(st)+" applies it after"))
			}
		}
	}
	room := h - len(lines) - len(tail) - 1
	off := min(a.DiffOff, max(0, len(body)-room))
	if off < 0 {
		off = 0
	}
	body = body[off:]
	if len(body) > room {
		body = body[:max(0, room)]
	}
	for len(body) < room {
		body = append(body, "")
	}
	lines = append(lines, indentAll(append(body, tail...), x, w)...)
	for len(lines) < h {
		lines = append(lines, fit("", w))
	}
	return lines[:h], foot
}

// The code screen's syntax colours: a few kinds a terminal can show, enough to read by.
var (
	codeKeywords = regexp.MustCompile(`\b(import|from|export|const|let|var|function|return|if|else|for|while|async|await|class|new|def|package|func|type|struct|interface|true|false|null|nil|None|True|False|try|catch|except|raise|throw|switch|case|break|continue|in|of|with|as|yield|lambda|go|defer|range|map|public|private|static|void|int|string|bool)\b`)
	codeStrings  = regexp.MustCompile("(\"[^\"]*\"|'[^']*'|`[^`]*`)")
	codeNumbers  = regexp.MustCompile(`\b\d[\d_]*\b`)
	codeCalls    = regexp.MustCompile(`\b([A-Za-z_]\w*)\(`)
)

func syntaxColours() (kw, str, fn, num string) {
	if lightGround() {
		return "#7c3aed", "#0f766e", "#1d4ed8", "#c2410c"
	}
	return "#c4a7ff", "#5eead4", "#93c5fd", "#fdba74"
}

// highlight colours one line of code; comments take the whole rest of the line.
func highlight(st State, line string) string {
	if !st.Styled {
		return line
	}
	trim := strings.TrimSpace(line)
	if strings.HasPrefix(trim, "//") || strings.HasPrefix(trim, "#") || strings.HasPrefix(trim, "--") || strings.HasPrefix(trim, "/*") || strings.HasPrefix(trim, "*") {
		return lipgloss.NewStyle().Foreground(lipgloss.Color(colFaint)).Italic(true).Render(line)
	}
	kw, str, fn, num := syntaxColours()
	type span struct {
		a, b int
		c    string
	}
	var spans []span
	taken := make([]bool, len(line)+1)
	add := func(re *regexp.Regexp, c string, group int) {
		for _, m := range re.FindAllStringSubmatchIndex(line, -1) {
			a, b := m[2*group], m[2*group+1]
			if a < 0 {
				continue
			}
			free := true
			for i := a; i < b; i++ {
				if taken[i] {
					free = false
				}
			}
			if !free {
				continue
			}
			for i := a; i < b; i++ {
				taken[i] = true
			}
			spans = append(spans, span{a, b, c})
		}
	}
	add(codeStrings, str, 0)
	add(codeKeywords, kw, 0)
	add(codeCalls, fn, 1)
	add(codeNumbers, num, 0)
	if len(spans) == 0 {
		return line
	}
	var b strings.Builder
	at := 0
	for i := 0; i < len(line); {
		found := false
		for _, s := range spans {
			if s.a == i {
				b.WriteString(line[at:i])
				b.WriteString(lipgloss.NewStyle().Foreground(lipgloss.Color(s.c)).Render(line[s.a:s.b]))
				i, at, found = s.b, s.b, true
				break
			}
		}
		if !found {
			i++
		}
	}
	b.WriteString(line[at:])
	return b.String()
}

func agentColor(name string) string {
	if c, ok := agentColors[name]; ok {
		return c
	}
	return colAccent
}

// mark highlights a search term in a line.
func mark(st State, line, term string) string {
	if term == "" {
		return line
	}
	i := strings.Index(strings.ToLower(line), strings.ToLower(term))
	if i < 0 {
		return line
	}
	hit := line[i : i+len(term)]
	if st.Styled {
		bg := "#fde68a"
		if !lightGround() {
			bg = "#6b5a1e"
		}
		hit = lipgloss.NewStyle().Background(lipgloss.Color(bg)).Render(hit)
	} else {
		hit = "[" + hit + "]"
	}
	return line[:i] + hit + mark(st, line[i+len(term):], term)
}

// previewKind is how a file is previewed instead of shown as source: markdown rendered, csv as a table, and files a
// terminal cannot show handed to the system's own viewer.
func previewKind(path string) string {
	switch strings.ToLower(filepath.Ext(path)) {
	case ".md", ".markdown":
		return "markdown"
	case ".csv", ".tsv":
		return "table"
	case ".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".mp4", ".mov", ".webm", ".zip":
		return "binary"
	}
	return ""
}

// previewLines is the design's file preview: rendered markdown, a table, or how to open what a terminal cannot show.
func previewLines(st State, a *AppState, w, h int) []string {
	out := []string{""}
	name := st.bold(filepath.Base(a.File))
	switch previewKind(a.File) {
	case "markdown":
		out = append(out, name+"  "+st.faint("rendered · s source"), "")
		for i, raw := range a.FileLines {
			line := strings.TrimRight(raw, " ")
			switch {
			case strings.HasPrefix(line, "# "):
				out = append(out, st.bold(strings.TrimSpace(line[2:])))
			case strings.HasPrefix(line, "#"):
				out = append(out, st.bold(line)) // the design keeps the marks of a sub-heading
			case strings.HasPrefix(strings.TrimSpace(line), "- "), strings.HasPrefix(strings.TrimSpace(line), "* "):
				out = append(out, "  • "+inlineCode(st, strings.TrimSpace(strings.TrimSpace(line)[2:])))
			case strings.HasPrefix(line, "```"):
				out = append(out, st.faint("  ────"))
			case line == "" && i > 0 && strings.HasPrefix(a.FileLines[i-1], "# "):
				// the design runs a title's first paragraph on under it
			default:
				for _, l := range wrap(line, w-2, "") {
					out = append(out, inlineCode(st, l))
				}
			}
		}
	case "table":
		sep := ','
		if strings.HasSuffix(strings.ToLower(a.File), ".tsv") {
			sep = '\t'
		}
		r := csv.NewReader(strings.NewReader(strings.Join(a.FileLines, "\n")))
		r.Comma, r.FieldsPerRecord, r.LazyQuotes = sep, -1, true
		rows, _ := r.ReadAll()
		head := name + "  " + st.faint(fmt.Sprintf("%s rows · table", thousands(max(0, len(rows)-1))))
		if a.Find != "" {
			head += st.faint("  · rows with ") + a.Find
		}
		out = append(out, head, "")
		widths, numeric := []int{}, []bool{}
		for n, row := range rows[:min(len(rows), 200)] {
			for i, c := range row {
				for len(widths) <= i {
					widths, numeric = append(widths, 0), append(numeric, n > 0)
				}
				widths[i] = min(24, max(widths[i], lipgloss.Width(c)))
				if n > 0 && strings.Trim(c, "0123456789.,-−+ ") != "" {
					numeric[i] = false
				}
			}
		}
		for n, row := range rows {
			if n > 0 && a.Find != "" && !strings.Contains(strings.ToLower(strings.Join(row, " ")), strings.ToLower(a.Find)) {
				continue
			}
			var cells []string
			for i, c := range row {
				if numeric[i] && n > 0 {
					cells = append(cells, strings.Repeat(" ", max(0, widths[i]-lipgloss.Width(c)))+fit(c, widths[i]))
				} else {
					cells = append(cells, fit(c, widths[i]))
				}
			}
			line := strings.Join(cells, "  ")
			if n == 0 {
				line = st.faint(line)
			}
			out = append(out, line)
		}
	case "binary":
		if a.ImageCols > 0 {
			out = append(out, name+"  "+st.faint("shown inline · o opens it in your default app"), "")
			out = append(out, imageRows(previewImageID, a.ImageCols, a.ImageRows)...)
		} else {
			out = append(out, name+"  "+st.faint("o opens it in your default app"), "",
				st.dim("Ghostty and kitty show pictures here; this terminal cannot."))
		}
	}
	if off := min(a.FileOff, max(0, len(out)-h)); off > 0 {
		out = out[off:]
	}
	return out
}

var codeSpan = regexp.MustCompile("`([^`]+)`")

// inlineCode shows a markdown line's `code` on the raised grey, without its backticks.
func inlineCode(st State, line string) string {
	return codeSpan.ReplaceAllStringFunc(line, func(m string) string {
		return tintIf(st, " "+m[1:len(m)-1]+" ", colSelBg)
	})
}

// thousands writes n with commas: 12,480.
func thousands(n int) string {
	s := fmt.Sprint(n)
	for i := len(s) - 3; i > 0; i -= 3 {
		s = s[:i] + "," + s[i:]
	}
	return s
}

// SearchState is the design's search: the query, its results grouped by file, and the chosen one.
type SearchState struct {
	Query   string
	Results []SearchHit
	Sel     int
	Ran     string // the query and options the results are for
	// the design's aa ab .* src/** in the box: match case, whole words, a regular expression, and the files searched
	Case, Word, Regex bool
	Scope             string
	EditScope         bool    // typing goes to the files searched
	Replace           *string // ⌃r: what to put in place of the matches, while it is being typed
}

// key is what the results must be for: the query with its options.
func (s *SearchState) key() string {
	return fmt.Sprintf("%s\x00%v%v%v\x00%s", s.Query, s.Case, s.Word, s.Regex, s.Scope)
}

// SearchHit is one matching line.
type SearchHit struct {
	Path string
	Line int
	Text string
}

// searchScreen is the design's search: the box, how many results in how many files, and each file's matches.
func searchScreen(st State, w, h int) ([]string, string) {
	s := st.App.Search
	cw := min(appColumn, w-8)
	foot := keyHints(st, [][2]string{{"↑↓", "result"}, {"⏎", "open"}, {"⇥", "ask " + appParent(st)}, {"⌃r", "replace"}, {"esc", "back"}}, w)
	opt := func(label string, on bool) string {
		if on {
			return st.bold(label)
		}
		return st.faint(label)
	}
	scope := s.Scope
	if s.EditScope {
		scope += "▏"
	} else if scope == "" {
		scope = "**"
	}
	query := s.Query
	if !s.EditScope {
		query += "▏"
	}
	left := " " + st.faint("/") + " " + query
	right := opt("aa", s.Case) + "   " + opt("ab", s.Word) + "   " + opt(".*", s.Regex) + "   " + st.paint(colFaint, scope) + "  "
	if s.EditScope {
		right = opt("aa", s.Case) + "   " + opt("ab", s.Word) + "   " + opt(".*", s.Regex) + "   " + scope + "  "
	}
	box := roundBox(st, []string{left + strings.Repeat(" ", max(2, cw-2-lipgloss.Width(left)-lipgloss.Width(right))) + right}, cw, true)
	lines := append([]string{""}, box...)
	files := map[string]int{}
	var order []string
	for _, r := range s.Results {
		if files[r.Path] == 0 {
			order = append(order, r.Path)
		}
		files[r.Path]++
	}
	switch {
	case s.Replace != nil:
		lines = append(lines, st.paint(colAccent, "replace "+s.Query+" with › ")+*s.Replace+"▏"+st.faint("   ⏎ replaces in "+fmt.Sprint(len(order))+" files · esc cancels"))
	case s.Query == "":
		lines = append(lines, st.faint("type to search the repository · tab: ask "+appParent(st)+" instead"))
	case s.Ran != s.key():
		lines = append(lines, st.faint("searching…"))
	default:
		lines = append(lines, st.faint(fmt.Sprintf("%d results in %d files · tab: ask %s instead", len(s.Results), len(order), appParent(st))))
	}
	lines = append(lines, "")
	sel := clampSel(s.Sel, len(s.Results))
	// the design shows the first files' lines and folds the rest to a count; the chosen result's file is always open
	open := map[string]bool{}
	for i, f := range order {
		if i < 2 {
			open[f] = true
		}
	}
	if len(s.Results) > 0 {
		open[s.Results[sel].Path] = true
	}
	prev := ""
	for i, r := range s.Results {
		if len(lines) >= h-1 {
			break
		}
		if r.Path != prev {
			if prev != "" && open[prev] {
				lines = append(lines, "")
			}
			who := ""
			if c := st.App.Changed[r.Path]; c != "" {
				who = "  " + st.paint(agentColor(c), "● "+c)
			}
			head := st.bold(r.Path) + who
			if !open[r.Path] {
				count := fmt.Sprintf("%d matches", files[r.Path])
				if files[r.Path] == 1 {
					count = "1 match"
				}
				head += "  " + st.faint(count)
			}
			lines = append(lines, st.zone(fmt.Sprintf("row:search:%d", i), head))
			prev = r.Path
		}
		if !open[r.Path] {
			continue
		}
		row := "   " + st.faint(fmt.Sprintf("%-4d", r.Line)) + " " + mark(st, fit(sanitize(strings.TrimSpace(r.Text)), cw-10), s.Query)
		if i == sel {
			if st.Styled {
				row = tint(st.bold(fit(row, cw)), colSelBg)
			} else {
				row = ">" + row[1:]
			}
		}
		lines = append(lines, st.zone(fmt.Sprintf("row:search:%d", i), row))
	}
	for len(lines) < h {
		lines = append(lines, "")
	}
	return indentAll(lines[:h], (w-cw)/2, w), foot
}

var checkLine = regexp.MustCompile(`^[●!]?\s*(\S+)\s+exit\s+(\S+)`)

// checkWord is one check as the design words it: "● tests pass", "● lint running", "● unit failed".
func checkWord(st State, text string) string {
	m := checkLine.FindStringSubmatch(strings.TrimSpace(sanitize(text)))
	if m == nil {
		return ""
	}
	switch m[2] {
	case "0":
		return st.paint(colGreen, "● "+m[1]+" pass")
	case "running":
		return st.paint(colAccent, "● "+m[1]+" running")
	}
	return st.paint(colRed, "● "+m[1]+" failed")
}

// tabSize is the code view's tab width from Settings › Editor; 4 until it is known.
func tabSize(st State) int {
	if st.AppSettings != nil && st.AppSettings.TabSize > 0 {
		return st.AppSettings.TabSize
	}
	return 4
}
