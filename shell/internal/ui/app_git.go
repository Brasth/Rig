package ui

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
)

// Git in the code tab, as an IDE has it: blame for each line (b), marks in the gutter for the lines changed since the
// last commit, and a source control view beside the explorer (g): the branch, the staged and unstaged files, stage,
// unstage and discard, a file's diff, a commit, the recent commits, and push through the ship screen. Git runs here,
// in the repository, as the code tab's own file reads do.

// gitFile is one changed file in the source control view.
type gitFile struct {
	path   string
	code   byte // M modified, A added, D deleted, R renamed, U conflict, ? untracked
	staged bool
}

// gitCommit is one of the recent commits.
type gitCommit struct {
	hash, subject, author string
	at                    float64
}

// gitState is what the source control view shows.
type gitState struct {
	branch, upstream string
	ahead, behind    int
	files            []gitFile
	commits          []gitCommit
	err              string
}

// blameLine is who last changed one line of the file, and when.
type blameLine struct {
	hash, author, summary string
	at                    float64
}

// gitDiffView is a file's diff shown in place of the file: its unstaged or its staged changes.
type gitDiffView struct {
	path   string
	staged bool
	file   DiffFile
	off    int
	raw    string // the diff as git wrote it: a hunk of it is staged or unstaged on its own
	hunk   int    // the chosen hunk (from 0)
}

// hunks splits a file's raw diff into its header and each hunk's text, as git apply takes them.
func hunks(raw string) (head string, parts []string) {
	var cur []string
	flush := func() {
		if cur != nil {
			parts = append(parts, strings.Join(cur, "\n")+"\n")
		}
		cur = nil
	}
	var headLines []string
	for _, l := range strings.Split(strings.TrimRight(raw, "\n"), "\n") {
		switch {
		case strings.HasPrefix(l, "@@"):
			flush()
			cur = []string{l}
		case cur != nil:
			cur = append(cur, l)
		default:
			headLines = append(headLines, l)
		}
	}
	flush()
	return strings.Join(headLines, "\n") + "\n", parts
}

// hunkPatch is a patch with one hunk of the file's diff, for git apply.
func hunkPatch(raw string, i int) string {
	head, parts := hunks(raw)
	if i < 0 || i >= len(parts) {
		return ""
	}
	return head + parts[i]
}

// hunkOf is which hunk (from 0) line i of the parsed diff is in.
func hunkOf(lines []DiffLine, i int) int {
	n := -1
	for j := 0; j <= i && j < len(lines); j++ {
		if lines[j].Kind == '@' {
			n++
		}
	}
	return n
}

type gitStatusMsg struct{ state *gitState }

type blameMsg struct {
	file  string
	lines []blameLine
	err   error
}

type gutterMsg struct {
	file  string
	marks map[int]byte // line (from 0) → A added, M modified, D removed after it
}

type gitDoneMsg struct {
	note string
	err  error
}

type gitDiffMsg struct{ view *gitDiffView }

// gitOut runs git in the repository, never asking for a password, with a time limit.
func gitOut(repo string, args ...string) (string, error) {
	return gitOutFor(20*time.Second, repo, args...)
}

// gitOutFor is gitOut with its own time limit: a pull over the network takes longer than a status.
func gitOutFor(limit time.Duration, repo string, args ...string) (string, error) {
	ctx, cancel := context.WithTimeout(context.Background(), limit)
	defer cancel()
	cmd := exec.CommandContext(ctx, "git", append([]string{"-C", repo}, args...)...)
	cmd.Env = append(os.Environ(), "GIT_TERMINAL_PROMPT=0", "GIT_OPTIONAL_LOCKS=0", "GIT_EDITOR=true", "GIT_MERGE_AUTOEDIT=no")
	out, err := cmd.Output()
	if ee, ok := err.(*exec.ExitError); ok && len(ee.Stderr) > 0 {
		return string(out), fmt.Errorf("%s", strings.TrimSpace(string(ee.Stderr)))
	}
	return string(out), err
}

// parseStatus reads `git status --porcelain=v1 -b -z`.
func parseStatus(out string) *gitState {
	st := &gitState{}
	parts := strings.Split(out, "\x00")
	for i := 0; i < len(parts); i++ {
		p := parts[i]
		if len(p) < 3 {
			continue
		}
		if strings.HasPrefix(p, "## ") {
			head := strings.TrimPrefix(p, "## ")
			if j := strings.Index(head, " ["); j >= 0 {
				for _, kv := range strings.Split(strings.Trim(head[j+2:], "]"), ", ") {
					if n, err := strconv.Atoi(strings.TrimPrefix(strings.TrimPrefix(kv, "ahead "), "behind ")); err == nil {
						if strings.HasPrefix(kv, "ahead") {
							st.ahead = n
						} else {
							st.behind = n
						}
					}
				}
				head = head[:j]
			}
			st.branch, st.upstream, _ = strings.Cut(head, "...")
			st.branch = strings.TrimPrefix(st.branch, "No commits yet on ")
			continue
		}
		x, y, path := p[0], p[1], p[3:]
		if x == 'R' || x == 'C' {
			i++ // the path it came from
		}
		switch {
		case x == '?' && y == '?':
			st.files = append(st.files, gitFile{path: path, code: '?'})
		case x == 'U' || y == 'U' || (x == 'A' && y == 'A') || (x == 'D' && y == 'D'):
			st.files = append(st.files, gitFile{path: path, code: 'U'})
		default:
			if x != ' ' {
				st.files = append(st.files, gitFile{path: path, code: x, staged: true})
			}
			if y != ' ' {
				st.files = append(st.files, gitFile{path: path, code: y})
			}
		}
	}
	return st
}

// fetchGit reads the branch, the changed files and the recent commits.
func (m *Model) fetchGit() tea.Cmd {
	repo := m.st.App.Repo
	return func() tea.Msg {
		out, err := gitOut(repo, "status", "--porcelain=v1", "-b", "-z")
		if err != nil {
			return gitStatusMsg{&gitState{err: err.Error()}}
		}
		st := parseStatus(out)
		if log, err := gitOut(repo, "log", "-8", "--format=%h%x00%s%x00%an%x00%at"); err == nil {
			for _, l := range strings.Split(strings.TrimSpace(log), "\n") {
				if f := strings.Split(l, "\x00"); len(f) == 4 {
					at, _ := strconv.ParseFloat(f[3], 64)
					st.commits = append(st.commits, gitCommit{f[0], f[1], f[2], at})
				}
			}
		}
		return gitStatusMsg{st}
	}
}

// parseBlame reads `git blame --porcelain`: who last changed each line of the file.
func parseBlame(out string) []blameLine {
	type info struct {
		author, summary string
		at              float64
	}
	known := map[string]*info{}
	var lines []blameLine
	cur := ""
	for _, l := range strings.Split(out, "\n") {
		switch {
		case strings.HasPrefix(l, "\t"):
			in := known[cur]
			if in == nil {
				in = &info{}
			}
			lines = append(lines, blameLine{hash: cur, author: in.author, summary: in.summary, at: in.at})
		case len(l) >= 40 && strings.Count(l[:40], " ") == 0 && (len(l) == 40 || l[40] == ' '):
			cur = l[:40]
			if known[cur] == nil {
				known[cur] = &info{}
			}
		case strings.HasPrefix(l, "author "):
			known[cur].author = strings.TrimPrefix(l, "author ")
		case strings.HasPrefix(l, "author-time "):
			known[cur].at, _ = strconv.ParseFloat(strings.TrimPrefix(l, "author-time "), 64)
		case strings.HasPrefix(l, "summary "):
			known[cur].summary = strings.TrimPrefix(l, "summary ")
		}
	}
	return lines
}

func (m *Model) fetchBlame() tea.Cmd {
	repo, file := m.st.App.Repo, m.st.App.File
	if file == "" {
		return nil
	}
	return func() tea.Msg {
		out, err := gitOut(repo, "blame", "--porcelain", "--", file)
		if err != nil {
			return blameMsg{file: file, err: err}
		}
		return blameMsg{file: file, lines: parseBlame(out)}
	}
}

// parseGutter reads `git diff -U0`: the lines added, the lines changed, and where lines were removed.
func parseGutter(out string) map[int]byte {
	marks := map[int]byte{}
	for _, l := range strings.Split(out, "\n") {
		m := hunkHead.FindStringSubmatch(l)
		if m == nil || !strings.HasPrefix(l, "@@") {
			continue
		}
		oldN, newStart, newN := 1, 0, 1
		if f := strings.Fields(l); len(f) >= 3 {
			oldN = spanLen(f[1])
			newN = spanLen(f[2])
		}
		newStart, _ = strconv.Atoi(m[2])
		switch {
		case newN == 0:
			marks[max(0, newStart-1)] = 'D' // removed after this line
		case oldN == 0:
			for i := 0; i < newN; i++ {
				marks[newStart-1+i] = 'A'
			}
		default:
			for i := 0; i < newN; i++ {
				marks[newStart-1+i] = 'M'
			}
		}
	}
	return marks
}

// spanLen is the count in a hunk's -a,b or +c,d (1 when it is left out).
func spanLen(s string) int {
	if _, n, ok := strings.Cut(s, ","); ok {
		v, _ := strconv.Atoi(n)
		return v
	}
	return 1
}

func (m *Model) fetchGutter() tea.Cmd {
	repo, file := m.st.App.Repo, m.st.App.File
	if file == "" || previewKind(file) == "binary" {
		return nil
	}
	return func() tea.Msg {
		if _, err := gitOut(repo, "ls-files", "--error-unmatch", "--", file); err != nil {
			// not in git yet: every line is new
			data, err := os.ReadFile(filepath.Join(repo, filepath.Clean("/"+file)))
			if err != nil {
				return gutterMsg{file: file}
			}
			marks := map[int]byte{}
			for i := range strings.Split(strings.TrimSuffix(string(data), "\n"), "\n") {
				marks[i] = 'A'
			}
			return gutterMsg{file: file, marks: marks}
		}
		out, _ := gitOut(repo, "diff", "-U0", "--no-color", "--no-ext-diff", "HEAD", "--", file)
		return gutterMsg{file: file, marks: parseGutter(out)}
	}
}

// gitAct runs one change to the repository's git state, then the view, the gutter and the blame are read again.
func (m *Model) gitAct(note string, run func(repo string) error) tea.Cmd {
	repo := m.st.App.Repo
	return func() tea.Msg {
		return gitDoneMsg{note: note, err: run(repo)}
	}
}

func (m *Model) afterGit(msg gitDoneMsg) tea.Cmd {
	a := m.st.App
	if msg.err != nil {
		m.st.Message = "git: " + clean(msg.err.Error())
	} else {
		m.st.Message = msg.note
	}
	cmds := []tea.Cmd{m.fetchGit(), m.fetchGutter(), m.fetchBlame()}
	if a.GitDiff != nil {
		cmds = append(cmds, m.fetchGitDiff(a.GitDiff.path, a.GitDiff.staged))
	}
	if a.File != "" && !a.Dirty {
		cmds = append(cmds, m.openFile(a.File)) // a discard, a pull or a switch changes the file under the editor
	}
	if a.reloadFiles || strings.HasPrefix(msg.note, "pulled") {
		a.reloadFiles, a.Files = false, nil // another branch, or pulled work: the explorer reads the files again
		cmds = append(cmds, m.loadFiles())
	}
	return tea.Batch(cmds...)
}

func (m *Model) fetchGitDiff(path string, staged bool) tea.Cmd {
	repo := m.st.App.Repo
	return func() tea.Msg {
		args := []string{"diff", "--no-color", "--no-ext-diff"}
		if staged {
			args = append(args, "--cached")
		}
		out, _ := gitOut(repo, append(args, "--", path)...)
		if strings.TrimSpace(out) == "" && !staged {
			// a new file: all of it
			out, _ = gitOut(repo, "diff", "--no-color", "--no-index", "--", "/dev/null", path)
		}
		view := &gitDiffView{path: path, staged: staged, raw: out}
		if files := ParseDiff(out); len(files) > 0 {
			view.file = files[0]
		}
		view.file.Path = path
		return gitDiffMsg{view}
	}
}

// afterSave reads git again for the saved file: its gutter, its blame, the source control view.
func (m *Model) afterSave() tea.Cmd {
	a := m.st.App
	if a.Dirty {
		return nil // not saved
	}
	cmds := []tea.Cmd{m.fetchGutter(), m.fetchBlame()}
	if a.Git != nil {
		cmds = append(cmds, m.fetchGit())
	}
	return tea.Batch(cmds...)
}

// gitRow is one row of the source control view.
type gitRow struct {
	kind string // staged, change, commit
	file gitFile
	c    gitCommit
}

func gitRows(a *AppState) []gitRow {
	var rows []gitRow
	if a.Git == nil {
		return nil
	}
	for _, f := range a.Git.files {
		if f.staged {
			rows = append(rows, gitRow{kind: "staged", file: f})
		}
	}
	for _, f := range a.Git.files {
		if !f.staged {
			rows = append(rows, gitRow{kind: "change", file: f})
		}
	}
	for _, c := range a.Git.commits {
		rows = append(rows, gitRow{kind: "commit", c: c})
	}
	return rows
}

func gitCodeColour(code byte) string {
	switch code {
	case 'A', '?':
		return "#15803d"
	case 'D':
		return colRed
	case 'U':
		return colAmber
	}
	return "#b45309"
}

// gitPane is the source control view, in place of the explorer.
func gitPane(st State, w, h int) []string {
	a := st.App
	lines := []string{sideHeads(st, w)}
	g := a.Git
	switch {
	case g == nil:
		lines = append(lines, "", " "+st.faint("reading git…"))
	case g.err != "":
		lines = append(lines, "", " "+st.paint(colAmber, fit(clean(g.err), w-2)))
	default:
		// the branch, a click switches it; the message box, a click commits; the buttons
		branch := " ⎇ " + st.bold(g.branch) + st.faint(" ▾")
		if g.ahead > 0 {
			branch += st.faint(fmt.Sprintf("  ↑%d", g.ahead))
		}
		if g.behind > 0 {
			branch += st.faint(fmt.Sprintf("  ↓%d", g.behind))
		}
		box := " " + fit(st.faint("Message (c to commit)"), w-4) + " "
		if st.Styled {
			box = tint(box, colRaised)
		}
		lines = append(lines, st.zone("row:gitbtn:branch", branch), " "+st.zone("row:gitbtn:commit", box),
			" "+gitButton(st, "commit", "✓ Commit")+" "+gitButton(st, "pull", "↓ Pull")+" "+gitButton(st, "push", "↑ Push"), "")
	}
	rows := gitRows(a)
	sel := -1
	if a.TreeFocus {
		sel = clampSel(a.GitSel, len(rows))
	}
	staged, changes := 0, 0
	for _, r := range rows {
		switch r.kind {
		case "staged":
			staged++
		case "change":
			changes++
		}
	}
	section, selLine := "", -1
	for i, r := range rows {
		if r.kind != section {
			section = r.kind
			title := map[string]string{"staged": fmt.Sprintf("STAGED %d", staged), "change": fmt.Sprintf("CHANGES %d", changes),
				"commit": "RECENT"}[r.kind]
			if len(lines) > gitHeadRows {
				lines = append(lines, "")
			}
			lines = append(lines, " "+st.faint(title))
		}
		var row string
		if r.kind == "commit" {
			row = "   " + st.faint(r.c.hash) + " " + fit(clean(r.c.subject), max(4, w-18)) + " " + st.faint(ago(st.Now, r.c.at))
		} else {
			code := string(r.file.code)
			if code == "?" {
				code = "U" // untracked, as editors mark it
			}
			if r.file.code == 'U' {
				code = "!"
			}
			name, dir := filepath.Base(r.file.path), filepath.Dir(strings.TrimSuffix(r.file.path, "/"))
			if strings.HasSuffix(r.file.path, "/") {
				name = filepath.Base(strings.TrimSuffix(r.file.path, "/")) + "/" // an untracked folder
			}
			if dir == "." {
				dir = ""
			}
			right := st.paint(gitCodeColour(r.file.code), code) + "  "
			row = fit("   "+name+"  "+st.faint(dir), w-lipgloss.Width(right)) + right
		}
		row = fit(row, w)
		if i == sel && st.Styled {
			row = tint(row, colRaised)
		} else if i == sel {
			row = ">" + row[1:]
		}
		if i == sel {
			selLine = len(lines)
		}
		lines = append(lines, st.zone(fmt.Sprintf("row:git:%d", i), row))
	}
	if g != nil && g.err == "" && staged+changes == 0 {
		at := min(len(lines), gitHeadRows)
		lines = append(lines[:at], append([]string{" " + st.faint("nothing to commit"), ""}, lines[at:]...)...)
	}
	if selLine >= h-1 {
		// a long list: the rows scroll under the heads so the chosen one stays in view
		lines = append(lines[:1], lines[selLine-(h-3):]...)
	}
	for len(lines) < h {
		lines = append(lines, "")
	}
	lines = lines[:h] // the keys are in the footer
	for i := range lines {
		lines[i] = fit(lines[i], w)
		if st.Styled {
			lines[i] = tint(lines[i], sideColour())
		}
	}
	return lines
}

// gitHeadRows is how many rows the git view's head takes: the view's name, the branch, the message box, the buttons.
const gitHeadRows = 5

// gitButton is one of the git view's buttons: a click does what its key does.
func gitButton(st State, id, label string) string {
	text := " " + label + " "
	if st.Styled {
		text = tint(text, colRaised)
	} else {
		text = "[" + label + "]"
	}
	return st.zone("row:gitbtn:"+id, text)
}

// sideHeads are the side bar's two views, the open one lit: the files and git.
func sideHeads(st State, w int) string {
	a := st.App
	files, git, outline := st.faint("EXPLORER"), st.faint("GIT"), st.faint("OUTLINE")
	switch {
	case a.GitView:
		git = st.bold("GIT")
	case a.Outline:
		outline = st.bold("OUTLINE")
	default:
		files = st.bold("EXPLORER")
	}
	n := ""
	if g := a.Git; g != nil && len(g.files) > 0 {
		n = st.faint(fmt.Sprintf(" %d", len(g.files)))
	}
	return fit(" "+st.zone("row:side:files", files)+"  "+st.zone("row:side:git", git+n)+"  "+st.zone("row:side:outline", outline), w)
}

// gitKey is the source control view's keys.
func (m *Model) gitKey(k tea.KeyMsg) (tea.Model, tea.Cmd) {
	a := m.st.App
	if a.CodeAsk != nil {
		return m.askKey(k)
	}
	rows := gitRows(a)
	sel := clampSel(a.GitSel, len(rows))
	var r *gitRow
	if sel < len(rows) {
		r = &rows[sel]
	}
	switch k.String() {
	case "]", "[":
		if d := a.GitDiff; d != nil {
			_, parts := hunks(d.raw)
			if k.String() == "]" {
				d.hunk = min(d.hunk+1, max(0, len(parts)-1))
			} else {
				d.hunk = max(0, d.hunk-1)
			}
			d.off = hunkRow(d) // the chosen hunk in view
		}
	case "h":
		if d := a.GitDiff; d != nil {
			return m, m.stageHunk(d)
		}
	case "pgdown":
		if a.GitDiff != nil {
			a.GitDiff.off += 15
		}
	case "pgup":
		if a.GitDiff != nil {
			a.GitDiff.off = max(0, a.GitDiff.off-15)
		}
	case "up", "k":
		a.GitSel = clampSel(sel-1, len(rows))
	case "down", "j":
		a.GitSel = clampSel(sel+1, len(rows))
	case " ", "s":
		if r != nil && r.kind != "commit" {
			return m, m.toggleStage(*r)
		}
	case "a":
		return m, m.gitAct("staged every change", func(repo string) error { _, err := gitOut(repo, "add", "-A"); return err })
	case "enter", "d":
		if r != nil && r.kind != "commit" {
			if r.file.code == 'U' {
				a.TreeFocus = false
				return m, m.openConflicts(r.file.path) // a conflict: its sides, not a diff
			}
			return m, m.fetchGitDiff(r.file.path, r.kind == "staged")
		}
	case "o":
		if r != nil && r.kind != "commit" && r.file.code != 'D' {
			a.TreeFocus = false
			return m, m.runPalette(paletteItem{Action: "file:" + r.file.path}, "")
		}
	case "u":
		if r != nil && r.kind == "change" {
			m.pendingX, m.pendingI = "git.discard", string(r.file.code)+r.file.path
			what := "Discard the changes to " + r.file.path + "? They are lost."
			if r.file.code == '?' {
				what = "Delete " + r.file.path + "? It is not in git, so it cannot be brought back."
			}
			m.st.Mode, m.st.Prompt = ModeConfirm, what
		}
	case "c":
		staged := false
		for _, f := range a.Git.filesOr() {
			staged = staged || f.staged
		}
		if !staged {
			m.st.Message = "nothing staged: space stages a file, a stages every change"
			return m, nil
		}
		a.CodeAsk = &codeAsk{kind: "commit", label: "commit message"}
	case "p":
		a.TreeFocus = false
		return m, m.runPalette(paletteItem{Action: "ship"}, "")
	case "P":
		return m, m.pull()
	case "b":
		return m, m.openBranches()
	case "r":
		return m, m.fetchGit()
	case "esc":
		if a.GitDiff != nil {
			a.GitDiff = nil // the diff first, then the view
			return m, nil
		}
		a.TreeFocus = false
	case "g", "ctrl+b":
		a.TreeFocus = false
		if k.String() == "g" {
			a.GitView, a.GitDiff = false, nil
		}
	}
	return m, nil
}

func (g *gitState) filesOr() []gitFile {
	if g == nil {
		return nil
	}
	return g.files
}

// pull brings the branch's upstream in: a fast-forward, or a merge without asking for its message.
func (m *Model) pull() tea.Cmd {
	m.st.Message = "pulling…"
	return m.gitAct("pulled", func(repo string) error {
		_, err := gitOutFor(2*time.Minute, repo, "pull", "--no-edit")
		return err
	})
}

// openBranches opens the palette on the branches: the local ones, the remote ones without a local branch, or a new one.
func (m *Model) openBranches() tea.Cmd {
	a := m.st.App
	a.Palette = &Palette{Branch: true}
	repo := a.Repo
	return func() tea.Msg {
		out, err := gitOut(repo, "for-each-ref", "--sort=-committerdate", "--format=%(refname)", "refs/heads", "refs/remotes")
		if err != nil {
			return branchesMsg{}
		}
		var local, remote []string
		for _, ref := range strings.Fields(out) {
			switch {
			case strings.HasPrefix(ref, "refs/heads/"):
				local = append(local, strings.TrimPrefix(ref, "refs/heads/"))
			case strings.HasSuffix(ref, "/HEAD"):
			case strings.HasPrefix(ref, "refs/remotes/"):
				remote = append(remote, strings.TrimPrefix(ref, "refs/remotes/"))
			}
		}
		return branchesMsg{local: local, remote: remote}
	}
}

type branchesMsg struct{ local, remote []string }

// branchItems are the palette in the branch list: what fits the query, and the query as a new branch.
func branchItems(st State, q string) []paletteItem {
	a := st.App
	current := ""
	if a.Git != nil {
		current = a.Git.branch
	}
	var out []paletteItem
	have := map[string]bool{}
	for _, b := range a.Branches {
		have[b] = true
		if q == "" || strings.Contains(strings.ToLower(b), q) {
			detail := ""
			if b == current {
				detail = "current"
			}
			out = append(out, paletteItem{"branches", b, detail, "branch:" + b})
		}
	}
	for _, r := range a.RemoteBranches {
		_, name, _ := strings.Cut(r, "/")
		if have[name] || !(q == "" || strings.Contains(strings.ToLower(r), q)) {
			continue
		}
		out = append(out, paletteItem{"remote", r, "a local branch follows it", "branch:" + name})
	}
	if typed := strings.TrimSpace(st.App.Palette.Query); typed != "" && !have[typed] {
		out = append(out, paletteItem{"new", "create " + typed, "from " + current, "branch-new:" + typed})
	}
	if len(out) == 0 {
		out = append(out, paletteItem{"branches", "type a name", "to create a branch", ""})
	}
	return out
}

// switchBranch moves the checkout to a branch, or makes a new one there; git refuses when changes would be lost.
func (m *Model) switchBranch(name string, create bool) tea.Cmd {
	m.st.App.reloadFiles = true
	if create {
		return m.gitAct("on the new branch "+name, func(repo string) error { _, err := gitOut(repo, "switch", "-c", name); return err })
	}
	return m.gitAct("on "+name, func(repo string) error { _, err := gitOut(repo, "switch", name); return err })
}

// hunkRow is the row of the diff's body the chosen hunk starts on, to scroll it into view.
func hunkRow(d *gitDiffView) int {
	row, n := 0, -1
	for i, l := range d.file.Lines {
		if l.Kind == '@' {
			n++
			if n == d.hunk {
				return max(0, row-1)
			}
			if i > 0 {
				row++ // the ⋯ between hunks
			}
			continue
		}
		row++
	}
	return 0
}

// stageHunk stages the chosen hunk of a file's changes, or unstages it from the staged ones (h in the diff).
func (m *Model) stageHunk(d *gitDiffView) tea.Cmd {
	patch := hunkPatch(d.raw, d.hunk)
	if patch == "" {
		m.st.Message = "no hunk chosen: ] and [ move between them"
		return nil
	}
	if strings.Contains(d.raw, "--- /dev/null") {
		return m.toggleStage(gitRow{kind: map[bool]string{true: "staged", false: "change"}[d.staged], file: gitFile{path: d.path}}) // a new file: all of it
	}
	note := fmt.Sprintf("staged hunk %d of %s", d.hunk+1, d.path)
	args := []string{"apply", "--cached", "--unidiff-zero", "--recount"}
	if d.staged {
		note = fmt.Sprintf("unstaged hunk %d of %s", d.hunk+1, d.path)
		args = append(args, "--reverse")
	}
	return m.gitAct(note, func(repo string) error {
		ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
		defer cancel()
		cmd := exec.CommandContext(ctx, "git", append([]string{"-C", repo}, args...)...)
		cmd.Env = append(os.Environ(), "GIT_TERMINAL_PROMPT=0")
		cmd.Stdin = strings.NewReader(patch)
		if out, err := cmd.CombinedOutput(); err != nil {
			return fmt.Errorf("%s", strings.TrimSpace(string(out)))
		}
		return nil
	})
}

// toggleStage stages a change, or unstages a staged one.
func (m *Model) toggleStage(r gitRow) tea.Cmd {
	path := r.file.path
	if r.kind == "staged" {
		return m.gitAct("unstaged "+path, func(repo string) error {
			if _, err := gitOut(repo, "restore", "--staged", "--", path); err != nil {
				_, err = gitOut(repo, "reset", "-q", "--", path) // a repository with no commit yet
				return err
			}
			return nil
		})
	}
	return m.gitAct("staged "+path, func(repo string) error { _, err := gitOut(repo, "add", "-A", "--", path); return err })
}

// discard throws away a file's unstaged changes, or deletes a file git does not have.
func (m *Model) discard(codePath string) tea.Cmd {
	code, path := codePath[0], codePath[1:]
	return m.gitAct("discarded "+path, func(repo string) error {
		if code == '?' {
			full, ok := inRepo(path)
			if !ok {
				return fmt.Errorf("%s is outside the repository", path)
			}
			return os.Remove(filepath.Join(repo, full))
		}
		_, err := gitOut(repo, "restore", "--", path)
		return err
	})
}

func (m *Model) commit(message string) tea.Cmd {
	message = strings.TrimSpace(message)
	if message == "" {
		m.st.Message = "a commit needs a message"
		return nil
	}
	return m.gitAct("committed: "+message, func(repo string) error {
		_, err := gitOut(repo, "commit", "-q", "-m", message)
		return err
	})
}

// openGit shows the source control view and moves the keys into it.
func (m *Model) openGit() tea.Cmd {
	a := m.st.App
	a.GitView, a.TreeFocus, a.GitSel = true, true, 0
	return m.fetchGit()
}

// blameAt is who last changed line i of the open file, while the file is as saved (an edit moves the lines).
func blameAt(a *AppState, i int) *blameLine {
	if a.BlameFor != a.File || a.Dirty || i < 0 || i >= len(a.BlameLines) {
		return nil
	}
	return &a.BlameLines[i]
}

// blameText is a line's blame in words: "Huy, 3d · Add the role", or that it is not committed yet.
func blameText(st State, b *blameLine) string {
	if strings.Trim(b.hash, "0") == "" {
		return "You · not committed yet"
	}
	return b.author + ", " + ago(st.Now, b.at) + " · " + b.summary
}

// inlineBlame is the cursor line's blame at its end, faint, as GitLens shows it.
func inlineBlame(st State, i, room int) string {
	a := st.App
	if !a.Editing || i != a.CurLine || room < 12 {
		return ""
	}
	b := blameAt(a, i)
	if b == nil {
		return ""
	}
	return "     " + st.faint(fit(clean(blameText(st, b)), room-5))
}

const blameWidth = 24

// blameCell is the blame column for line i: who and when on the first line of each run of one commit.
func blameCell(st State, i int) string {
	a := st.App
	b := blameAt(a, i)
	if !a.Blame || b == nil {
		return ""
	}
	if i > 0 && a.BlameLines[i-1].hash == b.hash {
		return strings.Repeat(" ", blameWidth)
	}
	text := b.author + " · " + ago(st.Now, b.at)
	if strings.Trim(b.hash, "0") == "" {
		text = "not committed"
	}
	return st.faint(fit(clean(text), blameWidth-2)) + "  "
}

// gitDiffRows draws a file's diff in place of the file.
func gitDiffRows(st State, w, room int) []string {
	a := st.App
	d := a.GitDiff
	what := "changes"
	if d.staged {
		what = "staged changes"
	}
	out := []string{"  " + st.bold(d.path) + "  " + st.faint(what) + "   " + st.paint(colGreen, fmt.Sprintf("+%d", d.file.Added)) + " " +
		st.paint(colRed, fmt.Sprintf("−%d", d.file.Deleted)), ""}
	if len(d.file.Lines) == 0 {
		return append(out, "  "+st.faint("no changes"))
	}
	add, del := tints()
	var body []string
	_, parts := hunks(d.raw)
	if len(parts) > 1 {
		verb := "stages"
		if d.staged {
			verb = "unstages"
		}
		out[0] += "   " + st.faint(fmt.Sprintf("hunk %d of %d · ] [ choose · h %s it", d.hunk+1, len(parts), verb))
	}
	in, n := -1, -1
	for i, l := range d.file.Lines {
		if l.Kind == '@' {
			n++
			in = n
			if i > 0 {
				body = append(body, st.faint("   ⋯"))
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
		text := fit(fmt.Sprintf("%4d %s %s", l.Num, mark, sanitize(l.Text)), w-2)
		if st.Styled && bg != "" {
			text = tint(st.paint(colour, text), bg)
		} else if st.Styled {
			text = st.dim(text)
		}
		edge := "  "
		if in == d.hunk && len(parts) > 1 {
			edge = st.paint(colAccent, "▎") + " " // the chosen hunk
		}
		body = append(body, edge+text)
	}
	off := max(0, min(d.off, len(body)-(room-len(out))))
	body = body[off:]
	return append(out, body...)
}
