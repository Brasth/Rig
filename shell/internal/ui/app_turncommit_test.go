package ui

import (
	"strings"
	"testing"

	"github.com/Brasth/Rig/shell/internal/view"
)

func TestAfterATurnGShowsItsFilesAndCCommitsJustThose(t *testing.T) {
	m := gitRepo(t)
	a := m.st.App
	a.Tab, a.File, a.Editing, a.TreeFocus, a.GitView = 1, "", false, false, false
	tr := transcript("idle", ce("user", "give the user a role"), ce("assistant", "I added a `role` constant to the login route. The tests still pass."))
	if got := commitMessage(State{Chat: transcript("idle", ce("user", "x"), ce("assistant", "I appended print(4) to a.py so it still starts with print('hi')."))}); got != "I appended print(4) to a.py so it still starts with print('hi')" {
		t.Fatalf("a dot inside a file name does not end the sentence: %q", got)
	}
	tr.Marks = []view.ChatMark{{Turn: 1, Title: "give the user a role", Files: []view.ChatFile{{Path: "src/routes/login.ts", Added: 2, Deleted: 1}}}}
	m.st.Chat = tr
	if got := commitMessage(m.st); got != "I added a role constant to the login route" {
		t.Fatalf("the message comes from the answer's first sentence, plain: %q", got)
	}
	follow(m, press(m, "g"), 0)
	if a.Tab != 3 || !a.GitView || len(a.GitScope) != 1 {
		t.Fatalf("g opens the git view scoped to the turn: tab %d view %v scope %v", a.Tab, a.GitView, a.GitScope)
	}
	var paths []string
	for _, r := range gitRows(a) {
		if r.kind != "commit" {
			paths = append(paths, r.file.path)
		}
	}
	if len(paths) != 1 || paths[0] != "src/routes/login.ts" || !strings.Contains(appScreen(m), "this turn's files") {
		t.Fatalf("only the turn's files show (notes.md is another change): %v\n%s", paths, appScreen(m))
	}
	follow(m, press(m, "t"), 0)
	if len(a.GitScope) != 0 || len(gitRows(a)) < 3 {
		t.Fatalf("t shows every change: %v", gitRows(a))
	}
	follow(m, press(m, "t"), 0)
	follow(m, press(m, "c"), 0)
	if a.CodeAsk == nil || a.CodeAsk.kind != "commit-turn" || a.CodeAsk.text != "I added a role constant to the login route" {
		t.Fatalf("c asks for the message, prefilled: %+v", a.CodeAsk)
	}
	typeText(m, " (by claude)")
	follow(m, press(m, "enter"), 0)
	log, _ := gitOut(a.Repo, "log", "-1", "--format=%s")
	if strings.TrimSpace(log) != "I added a role constant to the login route (by claude)" {
		t.Fatalf("the commit is made with the message: %q %q", log, m.st.Message)
	}
	status, _ := gitOut(a.Repo, "status", "--porcelain")
	if !strings.Contains(status, "notes.md") || strings.Contains(status, "login.ts") {
		t.Fatalf("only the turn's file is in the commit; the other change stays: %q", status)
	}
	// from the conversation, c goes straight to the message
	a.Tab, a.GitView, a.TreeFocus = 1, false, false
	follow(m, press(m, "c"), 0)
	if a.CodeAsk == nil || a.CodeAsk.kind != "commit-turn" || a.Tab != 3 {
		t.Fatal("c in the conversation asks for the message too")
	}
}
