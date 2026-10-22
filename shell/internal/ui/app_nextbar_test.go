package ui

import (
	"strings"
	"testing"

	"github.com/Brasth/Rig/shell/internal/view"
)

func turnThatChanged(state string, turns, markTurn int, paths ...string) *view.ChatTranscript {
	tr := transcript(state, ce("user", "give the user a role"), ce("assistant", "I added a role constant. Tests pass."))
	tr.Turns = turns
	var files []view.ChatFile
	for _, p := range paths {
		files = append(files, view.ChatFile{Path: p, Added: 2})
	}
	tr.Marks = []view.ChatMark{{Turn: markTurn, Title: "give the user a role", Files: files}}
	return tr
}

func TestAfterATurnThatChangedFilesABarOffersReviewCommitAndUndo(t *testing.T) {
	m := gitRepo(t)
	a := m.st.App
	a.Tab, a.File, a.Editing, a.TreeFocus, a.GitView = 1, "", false, false, false
	m.st.Chat = turnThatChanged("idle", 1, 1, "src/routes/login.ts")
	out := appScreen(m)
	for _, want := range []string{"src/routes/login.ts changed", "g Review", "c Commit", "u Undo", "g review"} {
		if !strings.Contains(out, want) {
			t.Fatalf("missing %q:\n%s", want, out)
		}
	}
	m.st.Chat = turnThatChanged("idle", 1, 1, "a.py", "b.py", "c.py")
	if out := appScreen(m); !strings.Contains(out, "3 files changed") {
		t.Fatalf("several files are counted:\n%s", out)
	}
	// u asks what to put back, all or one by one, and nothing is undone until ⏎
	m.st.Chat = turnThatChanged("idle", 1, 1, "src/routes/login.ts")
	src := m.src.(*fakeSource)
	src.rewound = &view.ChatRewound{Turn: 1, Paths: []string{"src/routes/login.ts"}, Dry: true}
	follow(m, press(m, "u"), 0)
	if p := a.Palette; p == nil || !p.Rewind || p.RewindTurn != 1 || len(src.rewinds) != 1 || !strings.Contains(src.rewinds[0], "dry=true") {
		t.Fatalf("u shows what would be put back first: %+v %v", a.Palette, src.rewinds)
	}
	a.Palette = nil
	// c goes to the commit message, g to the review, with the same bar's own button
	follow(m, m.nextAction("review"), 0)
	if a.Tab != 3 || !a.GitView || len(a.GitScope) != 1 {
		t.Fatalf("Review opens this turn's files: tab %d scope %v", a.Tab, a.GitScope)
	}
	a.Tab, a.GitView, a.GitScope = 1, false, nil
	follow(m, press(m, "c"), 0)
	if a.CodeAsk == nil || a.CodeAsk.kind != "commit-turn" {
		t.Fatalf("c asks for the commit message: %+v", a.CodeAsk)
	}
}

func TestTheBarIsOnlyThereWhenItMeansSomething(t *testing.T) {
	m, src := appModel(t)
	withConversation(t, m, src)
	cases := map[string]*view.ChatTranscript{
		"the parent is still working":  turnThatChanged("running", 1, 1, "a.py"),
		"a later turn changed nothing": turnThatChanged("idle", 2, 1, "a.py"),
		"the turn changed no files":    transcript("idle", ce("user", "hi"), ce("assistant", "hello")),
		"the turn failed":              turnThatChanged("failed", 1, 1, "a.py"),
	}
	for why, tr := range cases {
		m.st.Chat = tr
		if nextBarOn(m.st) {
			t.Errorf("no bar when %s", why)
		}
	}
	m.st.Chat = turnThatChanged("idle", 1, 1, "a.py")
	if !nextBarOn(m.st) {
		t.Fatal("the bar shows after a turn that changed files")
	}
	typeText(m, "and also")
	if nextBarOn(m.st) || strings.Contains(appScreen(m), "g Review") {
		t.Fatal("typing a reply hides it")
	}
	// the letters are typed, not taken as commands, while the box has text
	if m.st.Prompt != "and also" {
		t.Fatalf("g, c and u are text once you type: %q", m.st.Prompt)
	}
}
