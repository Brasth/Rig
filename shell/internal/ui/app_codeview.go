package ui

import (
	"fmt"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"unicode/utf8"

	"github.com/charmbracelet/lipgloss"
	"github.com/charmbracelet/x/ansi"

	"github.com/Brasth/Rig/shell/internal/view"
)

// The code tab, as the design draws it: an explorer of the repository on the left (its folders open and close, a
// filter, which agent is changing a file and whether it is new or modified in that agent's copy), and on the right the
// open files as tabs, a breadcrumb to the code around the cursor, the file with line numbers, colours and a mark on
// the lines an agent is changing, a status bar, and the keys.

// explorerWidth is the explorer's width, when the window has room for it.
const explorerWidth = 30

// treeRow is one row of the explorer: a folder or a file, how deep it is, and for a folder whether it is open.
type treeRow struct {
	path  string
	dir   bool
	depth int
	open  bool
}

// treeFiles is every file the explorer knows: the repository's, and the ones agents are adding in their copies.
func treeFiles(a *AppState) []string {
	seen := map[string]bool{}
	var out []string
	for _, f := range a.Files {
		if !seen[f] {
			seen[f] = true
			out = append(out, f)
		}
	}
	for f := range a.Changed {
		if !seen[f] {
			seen[f] = true
			out = append(out, f)
		}
	}
	if a.File != "" && !seen[a.File] {
		out = append(out, a.File)
	}
	return out
}

// dirOpen is whether a folder shows its contents: as toggled, else open when it holds the open file or a file an
// agent is changing.
func dirOpen(a *AppState, dir string) bool {
	if open, ok := a.TreeOpen[dir]; ok {
		return open
	}
	if a.File != "" && strings.HasPrefix(a.File, dir+"/") {
		return true
	}
	for f := range a.Changed {
		if strings.HasPrefix(f, dir+"/") {
			return true
		}
	}
	return false
}

// treeRows is the explorer's rows: folders first, then files, each sorted; with a filter, the matching files.
func treeRows(a *AppState) []treeRow {
	files := treeFiles(a)
	if q := strings.ToLower(strings.TrimSpace(a.TreeFilter)); q != "" {
		var rows []treeRow
		for _, f := range files {
			if strings.Contains(strings.ToLower(f), q) {
				rows = append(rows, treeRow{path: f})
			}
		}
		sort.Slice(rows, func(i, j int) bool { return rows[i].path < rows[j].path })
		return rows
	}
	dirs := map[string]map[string]bool{} // a folder's folders
	kids := map[string][]string{}        // a folder's files
	for _, f := range files {
		parent := filepath.Dir(f)
		if parent == "." {
			parent = ""
		}
		kids[parent] = append(kids[parent], f)
		for d := parent; d != ""; {
			up := filepath.Dir(d)
			if up == "." {
				up = ""
			}
			if dirs[up] == nil {
				dirs[up] = map[string]bool{}
			}
			dirs[up][d] = true
			d = up
		}
	}
	var rows []treeRow
	var walk func(dir string, depth int)
	walk = func(dir string, depth int) {
		var sub []string
		for d := range dirs[dir] {
			sub = append(sub, d)
		}
		sort.Strings(sub)
		for _, d := range sub {
			open := dirOpen(a, d)
			rows = append(rows, treeRow{path: d, dir: true, depth: depth, open: open})
			if open {
				walk(d, depth+1)
			}
		}
		fs := kids[dir]
		sort.Strings(fs)
		for _, f := range fs {
			rows = append(rows, treeRow{path: f, depth: depth})
		}
	}
	walk("", 0)
	return rows
}

// sideColour is the explorer's and the status bar's ground: a step off the screen's.
func sideColour() string {
	if lightGround() {
		return "#f4f4f6"
	}
	return colSelBg
}

// fileMark is the explorer's mark for a file an agent is changing: a dot in its colour, and A (new in its copy) or M.
func fileMark(st State, path string) string {
	who := st.App.Changed[path]
	if who == "" {
		return ""
	}
	letter := st.paint("#b45309", "M")
	if st.App.Added[path] {
		letter = st.paint("#15803d", "A")
	}
	return st.paint(agentColor(who), "●") + " " + letter
}

// explorer is the left column.
func explorer(st State, w, h int) []string {
	a := st.App
	if a.GitView {
		return gitPane(st, w, h)
	}
	repo := st.faint(filepath.Base(a.Repo))
	lines := []string{spread(sideHeads(st, w-lipgloss.Width(repo)-1), "", repo+" ", w)}
	filter := st.faint("/ filter files")
	switch {
	case a.TreeFiltering:
		filter = st.faint("/ ") + a.TreeFilter + "▏"
	case a.TreeFilter != "":
		filter = st.faint("/ ") + a.TreeFilter
	}
	box := " " + fit(filter, w-4) + " "
	if st.Styled {
		box = tint(box, colRaised)
	}
	lines = append(lines, " "+box+" ")
	rows := treeRows(a)
	room := h - len(lines) - 2
	sel := a.TreeSel
	if !a.TreeFocus {
		sel = -1
		for i, r := range rows {
			if r.path == a.File {
				sel = i
			}
		}
	}
	from := 0
	if sel >= room {
		from = sel - room + 1
	}
	for i := from; i < len(rows) && len(lines) < h-2; i++ {
		r := rows[i]
		indent := strings.Repeat("  ", r.depth)
		var left, right string
		switch {
		case r.dir:
			arrow := "▸"
			if r.open {
				arrow = "▾"
			}
			left = " " + indent + st.dim(arrow+" "+filepath.Base(r.path))
		case a.TreeFilter != "":
			left = "   " + r.path
		default:
			left = " " + indent + "  " + filepath.Base(r.path)
			right = fileMark(st, r.path)
		}
		current := r.path == a.File && !r.dir
		if current {
			left = " " + indent + "  " + st.bold(filepath.Base(r.path))
			if a.TreeFilter != "" {
				left = "   " + st.bold(r.path)
			}
		}
		row := fit(left, w-lipgloss.Width(right)-2) + right + "  "
		if (current || (a.TreeFocus && i == a.TreeSel)) && st.Styled {
			row = tint(fit(row, w), "#ececf0")
			if !lightGround() {
				row = tint(fit(row, w), colRaised)
			}
		} else if a.TreeFocus && i == a.TreeSel {
			row = ">" + fit(row, w)[1:]
		}
		lines = append(lines, st.zone("row:tree:"+r.path, fit(row, w)))
	}
	for len(lines) < h-2 {
		lines = append(lines, "")
	}
	legend := " " + st.paint(colAccent, "●") + " " + st.faint("agent copy") + "  " + st.paint("#b45309", "M") + " " + st.paint("#15803d", "A")
	lines = append(lines, legend, "")
	for i := range lines {
		lines[i] = fit(lines[i], w)
		if st.Styled {
			lines[i] = tint(lines[i], sideColour())
		}
	}
	return lines
}

var (
	symFunc  = regexp.MustCompile(`^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:function|def|class|fn)\s+([A-Za-z_]\w*)`)
	symGo    = regexp.MustCompile(`^func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)`)
	symCall  = regexp.MustCompile(`^\s*([A-Za-z_][\w.]*)\(\s*('[^']*'|"[^"]*")`)
	symConst = regexp.MustCompile(`^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_]\w*)\s*=\s*(?:async\s*)?(?:function\b|\()`)
)

// symbolAt is the function or block around line n, for the breadcrumb: router.post('/login'), a function's name.
func symbolAt(lines []string, n int) string {
	if n < 0 || n >= len(lines) {
		return ""
	}
	indent := func(l string) int { return len(l) - len(strings.TrimLeft(l, " \t")) }
	limit := indent(lines[n])
	for i := n; i >= 0 && i > n-400; i-- {
		l := lines[i]
		if strings.TrimSpace(l) == "" || (i < n && indent(l) > limit) {
			continue
		}
		if m := symGo.FindStringSubmatch(l); m != nil {
			return m[1]
		}
		if m := symFunc.FindStringSubmatch(l); m != nil {
			return m[1]
		}
		if m := symConst.FindStringSubmatch(l); m != nil {
			return m[1]
		}
		if m := symCall.FindStringSubmatch(l); m != nil && opensBlock(l) {
			return m[1] + "(" + m[2] + ")"
		}
		if indent(l) < limit {
			limit = indent(l)
		}
	}
	return ""
}

// opensBlock: a line that starts a block of code, as router.post('/login', (req, res) => { does.
func opensBlock(l string) bool {
	t := strings.TrimSpace(l)
	return strings.HasSuffix(t, "{") || strings.HasSuffix(t, ":") || strings.HasSuffix(t, "=>")
}

// languages names a file's language from its extension, for the status bar.
var languages = map[string]string{".ts": "TypeScript", ".tsx": "TypeScript JSX", ".js": "JavaScript", ".jsx": "JavaScript JSX",
	".mjs": "JavaScript", ".go": "Go", ".py": "Python", ".rb": "Ruby", ".rs": "Rust", ".java": "Java", ".kt": "Kotlin",
	".swift": "Swift", ".c": "C", ".h": "C", ".cc": "C++", ".cpp": "C++", ".cs": "C#", ".php": "PHP", ".sh": "Shell",
	".bash": "Shell", ".zsh": "Shell", ".md": "Markdown", ".json": "JSON", ".yml": "YAML", ".yaml": "YAML", ".toml": "TOML",
	".html": "HTML", ".css": "CSS", ".scss": "SCSS", ".sql": "SQL", ".prisma": "Prisma", ".vue": "Vue", ".svelte": "Svelte",
	".lua": "Lua", ".ex": "Elixir", ".exs": "Elixir", ".dart": "Dart", ".scala": "Scala", ".r": "R", ".txt": "Plain Text"}

// indentName is how a file indents, as the status bar says it: Spaces: 2, or Tab Size: 4.
func indentName(st State, lines []string) string {
	least := 0
	for i, l := range lines {
		if i == 400 {
			break
		}
		if strings.HasPrefix(l, "\t") {
			return fmt.Sprintf("Tab Size: %d", tabSize(st))
		}
		if n := len(l) - len(strings.TrimLeft(l, " ")); n > 0 && strings.TrimSpace(l) != "" && (least == 0 || n < least) {
			least = n
		}
	}
	if least == 0 {
		return ""
	}
	return fmt.Sprintf("Spaces: %d", least)
}

// cursorLine is the line the status bar and breadcrumb are about: the cursor's while editing, the selection's end,
// else the first line shown.
func cursorLine(a *AppState) (line, col int) {
	switch {
	case a.Editing:
		return a.CurLine, a.CurCol
	case a.Selecting:
		return a.SelTo, 0
	}
	return a.FileOff, 0
}

// statusBar is the line under the code: the branch and the agents at work; where the cursor is and what the file is.
func statusBar(v *view.View, st State, w int) string {
	a := st.App
	left := "   "
	if a.Branch != "" {
		left += "⎇ " + a.Branch
		if g := a.Git; g != nil && g.branch == a.Branch {
			if g.ahead > 0 {
				left += fmt.Sprintf(" ↑%d", g.ahead)
			}
			if g.behind > 0 {
				left += fmt.Sprintf(" ↓%d", g.behind)
			}
		}
		left += "   "
	}
	if pc := problemCounts(st); pc != "" {
		left += pc + "   "
	}
	if a.File != "" && a.FileLines != nil {
		// blame on: who last changed the cursor's line, as an IDE's status bar says
		line, _ := cursorLine(a)
		if b := blameAt(a, line); b != nil && a.Blame {
			left += fit(clean(blameText(st, b)), max(10, w/3)) + "   "
		}
	}
	if n := len(activeJobs(v)); n > 0 {
		word := "agents"
		if n == 1 {
			word = "agent"
		}
		left += st.paint(colAccent, "●") + fmt.Sprintf(" %d %s", n, word)
	}
	var parts []string
	if a.File != "" && a.FileLines != nil {
		line, col := cursorLine(a)
		pos := fmt.Sprintf("Ln %d, Col %d", line+1, col+1)
		if a.Selecting {
			pos += fmt.Sprintf(" (%d selected)", max(a.SelFrom, a.SelTo)-min(a.SelFrom, a.SelTo)+1)
		}
		parts = append(parts, pos)
		if in := indentName(st, a.FileLines); in != "" {
			parts = append(parts, in)
		}
		enc := "UTF-8"
		for _, l := range a.FileLines {
			if !utf8.ValidString(l) {
				enc = "binary"
				break
			}
		}
		eol := "LF"
		if a.FileCRLF {
			eol = "CRLF"
		}
		lang := languages[strings.ToLower(filepath.Ext(a.File))]
		if lang == "" {
			lang = "Plain Text"
		}
		parts = append(parts, enc, eol, lang)
	}
	line := spread(left, "", strings.Join(parts, "   ")+"   ", w)
	if st.Styled {
		return tint(st.dim(line), "#f0f0f3")
	}
	return line
}

// codeTabs is the row of open files, the open one lit, each with × to close it. When they do not all fit, the row
// shows the ones around the open file, with ‹ › for those left out on either side.
func codeTabs(st State, w int) string {
	a := st.App
	if len(a.Tabs) == 0 {
		return ""
	}
	parts := make([]string, len(a.Tabs))
	at := 0
	for i, f := range a.Tabs {
		name := " " + filepath.Base(f) + " "
		if who := a.Changed[f]; who != "" {
			name += st.paint(agentColor(who), "●") + " "
		}
		if (f == a.File && a.Dirty) || a.Bufs[f] != nil {
			name = strings.TrimSuffix(name, " ") + " " + st.paint(colText, "◆") + " " // not saved
		}
		if f == a.File {
			at = i
			if st.Styled {
				name = tint(lipgloss.NewStyle().Bold(true).Underline(true).Render(" "+filepath.Base(f))+" "+
					strings.TrimPrefix(name, " "+filepath.Base(f)+" "), colRaised)
			} else {
				name = "[" + strings.TrimSpace(name) + "]"
			}
		} else {
			name = st.dim(name)
		}
		x := st.faint("×")
		if f == a.File {
			x = st.dim("×")
		}
		parts[i] = st.zone("row:tabfile:"+f, " "+name) + st.zone("row:tabclose:"+f, x) + " "
	}
	close := st.zone("key:⌃w", st.faint("⌃w close")) + "  "
	room := w - lipgloss.Width(close)
	if room < 30 {
		close, room = "", w
	}
	// the open tab first, then its neighbours while they fit, keeping room for ‹ and ›
	from, to := at, at+1
	used := lipgloss.Width(parts[at])
	for grew := true; grew; {
		grew = false
		if from > 0 && used+lipgloss.Width(parts[from-1])+6 <= room {
			from--
			used += lipgloss.Width(parts[from])
			grew = true
		}
		if to < len(parts) && used+lipgloss.Width(parts[to])+6 <= room {
			used += lipgloss.Width(parts[to])
			to++
			grew = true
		}
	}
	row := strings.Join(parts[from:to], "")
	if from > 0 {
		row = st.zone("row:tabfile:"+a.Tabs[from-1], st.faint(fmt.Sprintf("‹%d", from))) + row
	}
	if to < len(parts) {
		row += st.zone("row:tabfile:"+a.Tabs[to], st.faint(fmt.Sprintf("%d›", len(parts)-to)))
	}
	return spread(row, "", close, w)
}

// breadcrumb is where the cursor is: the file's folders, its name and the code around the cursor; and which agent is
// changing it in its copy.
func breadcrumb(st State, w int) string {
	a := st.App
	segs := strings.Split(a.File, "/")
	var parts []string
	for i, s := range segs {
		if i == len(segs)-1 {
			parts = append(parts, st.dim(s))
		} else {
			parts = append(parts, st.faint(s))
		}
	}
	line, _ := cursorLine(a)
	if sym := symbolAt(a.FileLines, line); sym != "" {
		parts = append(parts, st.dim(sym))
	}
	right := ""
	if who := a.Changed[a.File]; who != "" {
		right = st.paint(agentColor(who), "● "+who+" editing its copy") + "  "
	}
	return spread("  "+strings.Join(parts, st.faint(" › ")), "", right, w)
}

// codeRows is the file itself: a mark on the lines an agent is changing, the line numbers, the code in colour, the
// selection lit and, while editing, the cursor.
func codeRows(st State, w, room int) []string {
	a := st.App
	lo, hi := min(a.SelFrom, a.SelTo), max(a.SelFrom, a.SelTo)
	off := min(a.FileOff, max(0, len(a.FileLines)-room))
	marks := a.ChangedLines[a.File]
	who := a.Changed[a.File]
	word, wordBg := "", "#e5e7eb"
	if !lightGround() {
		wordBg = "#3a3d4a"
	}
	var brackets []pos
	if a.Editing && a.CurLine < len(a.FileLines) {
		if _, _, ok := a.selRange(); !ok {
			word = wordAt(a.FileLines[a.CurLine], a.CurCol)
		}
		if p, q, ok := matchBracket(a.FileLines, a.cursor()); ok {
			brackets = []pos{p, q}
		}
	}
	var out []string
	last := len(a.FileLines)
	if !a.Editing && last > 1 && a.FileLines[last-1] == "" {
		last-- // the file's final newline ends its last line; it starts no line of its own
	}
	for i := off; i < last && len(out) < room; i++ {
		raw := strings.ReplaceAll(a.FileLines[i], "\t", strings.Repeat(" ", tabSize(st)))
		text := sanitize(raw)
		if a.Find != "" {
			text = mark(st, text, a.Find)
		} else {
			text = highlight(st, text)
		}
		if a.Editing && a.Find == "" {
			// the word under the cursor where else it is, and the bracket that pairs with the one at the cursor
			for _, s := range wordSpans(a.FileLines[i], word) {
				text = paintCells(st, text, displayCol(a.FileLines[i], s[0], tabSize(st)), displayCol(a.FileLines[i], s[1], tabSize(st)), wordBg)
			}
			for _, b := range brackets {
				if b.l == i {
					text = paintCells(st, text, displayCol(a.FileLines[i], b.c, tabSize(st)), displayCol(a.FileLines[i], b.c+1, tabSize(st)), wordBg)
				}
			}
		}
		if a.Editing && len(a.Extra) > 0 {
			text = extraSelected(st, text, i)
		}
		if from, to, ok := a.selRange(); ok && a.Editing && i >= from.l && i <= to.l {
			// the selection: from its start on its first line, to its end on its last, a cell past a line it runs on from
			d0, d1 := 0, ansi.StringWidth(text)+1
			if i == from.l {
				d0 = displayCol(a.FileLines[i], from.c, tabSize(st))
			}
			if i == to.l {
				d1 = displayCol(a.FileLines[i], to.c, tabSize(st))
			}
			text = selectCells(st, text, d0, d1)
		}
		if a.Editing && i == a.CurLine {
			text = withCursor(st, text, displayCol(a.FileLines[i], a.CurCol, tabSize(st)))
		}
		gutter := " "
		switch {
		case marks[i+1]:
			gutter = st.paint(agentColor(who), "▎")
		case a.GutterFor == a.File && a.Gutter[i] == 'A':
			gutter = st.paint(colGreen, "▎") // added since the last commit
		case a.GutterFor == a.File && a.Gutter[i] == 'M':
			gutter = st.paint(colAccent, "▎") // changed
		case a.GutterFor == a.File && a.Gutter[i] == 'D':
			gutter = st.paint(colRed, "▁") // lines removed after this one
		}
		if cell := blameCell(st, i); cell != "" {
			gutter += cell
		}
		if blame := inlineBlame(st, i, w-8-lipgloss.Width(gutter)-ansi.StringWidth(text)); blame != "" {
			text += blame
		}
		if a.Editing || canEdit(a) {
			// a click in the text puts the cursor there, and starts editing
			text = st.zone(fmt.Sprintf("text:code:%d", i), text+strings.Repeat(" ", max(0, w-6-lipgloss.Width(gutter)+1-ansi.StringWidth(text))))
		}
		row := gutter + st.paint("#c3c5ce", fmt.Sprintf("%3d  ", i+1)) + text
		row = fit(row, w)
		if a.Selecting && i >= lo && i <= hi {
			if st.Styled {
				sel := "#eeedfc"
				if !lightGround() {
					sel = colSelBg
				}
				row = tint(row, sel)
			} else {
				row = ">" + row[1:]
			}
		}
		out = append(out, row)
	}
	return out
}

// displayCol is where rune col of a line sits on screen, with its tabs as spaces.
func displayCol(line string, col, tab int) int {
	r := []rune(line)
	col = min(col, len(r))
	return ansi.StringWidth(strings.ReplaceAll(string(r[:col]), "\t", strings.Repeat(" ", tab)))
}

// selectCells lights cells d0 to d1 of a coloured line as the editor's selection.
func selectCells(st State, line string, d0, d1 int) string {
	bg := "#c7d2fe"
	if !lightGround() {
		bg = "#3f3f8f"
	}
	return paintCells(st, line, d0, d1, bg)
}

// paintCells puts a background behind cells d0 to d1 of a coloured line.
func paintCells(st State, line string, d0, d1 int, bg string) string {
	if d1 <= d0 {
		return line
	}
	if pad := d1 - ansi.StringWidth(line); pad > 0 {
		line += strings.Repeat(" ", pad)
	}
	if !st.Styled {
		return line
	}
	return ansi.Truncate(line, d0, "") + tint(ansi.Cut(line, d0, d1), bg) + "\x1b[49m" + ansi.TruncateLeft(line, d1, "")
}

// withCursor draws the editing cursor, a block, at cell col of a coloured line.
func withCursor(st State, line string, col int) string {
	if !st.Styled {
		r := []rune(ansi.Strip(line))
		col = min(col, len(r))
		return string(r[:col]) + "▏" + string(r[col:])
	}
	under := ansi.Strip(ansi.Cut(line, col, col+1))
	if under == "" {
		under = " "
	}
	return ansi.Truncate(line, col, "") + "\x1b[7m" + under + "\x1b[27m" + ansi.TruncateLeft(line, col+1, "")
}

// codeScreen is the code tab: the explorer, and the open file in its tabs; its keys for the footer.
func codeScreen(v *view.View, st State, w, h int) ([]string, [][2]string) {
	a := st.App
	keys := [][2]string{{"⌃b", "tree"}, {"g", "git"}, {"b", "blame"}, {"/", "find"}, {"v", "select"}, {"e", "edit"}, {"E", "$EDITOR"}}
	if a.GitDiff != nil {
		keys = [][2]string{{"] [", "hunk"}, {"h", "stage hunk"}, {"g", "git"}, {"esc", "close diff"}}
	}
	if a.Conflict != nil && !a.TreeFocus {
		keys = [][2]string{{"] [", "conflict"}, {"o", "ours"}, {"t", "theirs"}, {"b", "both"}, {"e", "edit"}, {"esc", "file"}}
	}
	switch {
	case a.CodeAsk != nil || a.Editing:
		keys = editKeys(st, a)
	case a.TreeFocus && a.GitView:
		keys = [][2]string{{"space", "stage"}, {"⏎", "diff"}, {"u", "discard"}, {"c", "commit"}, {"P", "pull"}, {"p", "push"}, {"b", "branch"}, {"g", "files"}, {"esc", "back"}}
	case a.TreeFocus:
		keys = [][2]string{{"↑↓", "move"}, {"⏎", "open"}, {"←→", "fold"}, {"/", "filter"}, {"n", "new"}, {"r", "rename"}, {"d", "delete"}, {"g", "git"}, {"esc", "back"}}
	}
	treeW := 0
	if w >= 90 {
		treeW = explorerWidth
	}
	ew := w - treeW
	var ed []string
	switch {
	case a.File == "":
		ed = []string{codeTabs(st, ew), "", "  " + st.dim("Open a file from the explorer (⌃b), or with ⌃k: type part of its name.")}
	case a.FileErr != "":
		ed = []string{codeTabs(st, ew), breadcrumb(st, ew), "", "  " + st.paint(colAmber, "cannot open: "+clean(a.FileErr))}
	case a.FileLines == nil:
		ed = []string{codeTabs(st, ew), breadcrumb(st, ew), "", "  " + st.faint("loading…")}
	case a.Conflict != nil && a.Conflict.path == a.File:
		ed = []string{codeTabs(st, ew)}
		ed = append(ed, conflictRows(st, ew, h-1-len(ed)-1)...)
	case a.GitDiff != nil:
		ed = []string{codeTabs(st, ew)}
		ed = append(ed, gitDiffRows(st, ew, h-1-len(ed)-1)...)
	default:
		ed = []string{codeTabs(st, ew), breadcrumb(st, ew)}
		ed = append(ed, codeRows(st, ew, h-1-len(ed)-1)...)
	}
	for len(ed) < h-2 {
		ed = append(ed, "")
	}
	ed = ed[:h-2]
	bottom := ""
	switch {
	case a.CodeAsk != nil:
		bottom = "      " + st.dim(a.CodeAsk.label+" ›") + " " + a.CodeAsk.text + "▏"
		switch a.CodeAsk.kind {
		case "find", "replace":
			find := a.CodeAsk.text
			if a.CodeAsk.kind == "replace" {
				find = a.CodeAsk.find
			}
			if n, at := matchCount(a, find); find != "" && n == 0 {
				bottom += "   " + st.paint(colAmber, "no results")
			} else if find != "" {
				bottom += "   " + st.faint(fmt.Sprintf("%d of %d", at, n))
			}
		}
	case a.Finding:
		bottom = "      " + st.dim("/") + " " + a.Find + "▏"
	case a.Selecting:
		chip := " a  ask " + appParent(st) + " "
		if st.Styled {
			chip = lipgloss.NewStyle().Background(lipgloss.Color(colText)).Foreground(lipgloss.Color(colGround)).Render(chip)
		} else {
			chip = "[" + strings.TrimSpace(chip) + "]"
		}
		bottom = "      " + st.zone("key:a", chip) + "   " + st.hint([2]string{"@", "add to chat"}) + "   " + st.hint([2]string{"x", "explain"})
	case a.Editing && a.Dirty:
		bottom = "      " + st.paint(colAmber, "● unsaved") + "   " + st.hint([2]string{"⌃s", "save"})
	case a.Editing:
		bottom = "      " + st.faint("editing "+filepath.Base(a.File))
	}
	ed = append(ed, bottom)
	var lines []string
	var side []string
	if treeW > 0 {
		side = explorer(st, treeW, h-1)
	}
	for i := 0; i < h-1; i++ {
		row := fit(ed[i], ew)
		if treeW > 0 {
			row = side[i] + row
		}
		lines = append(lines, row)
	}
	lines = append(lines, statusBar(v, st, w))
	return lines, keys
}
