package ui

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func writeScene(t *testing.T, body string) string {
	t.Helper()
	dir := t.TempDir()
	path := filepath.Join(dir, "scene.json")
	if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
	return path
}

func TestASceneDrawsTheConversationWithItsDiffAndNextBar(t *testing.T) {
	t.Cleanup(func() { ApplyTheme("rig") })
	snap, _ := filepath.Abs("../../testdata/snapshot.json")
	path := writeScene(t, `{"screen":"conversation","theme":"dark","repo":"my-repo","branch":"main","snapshot":"`+snap+`",
	 "status":{"parent":"claude","model":"opus"},
	 "chat":{"session":"c-1","parent":"claude","state":"idle","turns":1,"entries":[
	   {"role":"user","text":"give the user a role"},{"role":"tool","text":"Read src/user.ts"},
	   {"role":"assistant","text":"Done."},{"role":"diff","text":"src/user.ts +1 −0\n+const role = 'admin'"}],
	   "marks":[{"turn":1,"title":"give the user a role","files":[{"path":"src/user.ts","added":1}]}]}}`)
	sc, v, err := LoadScene(path)
	if err != nil {
		t.Fatal(err)
	}
	if len(v.Rows("Jobs")) == 0 {
		t.Fatal("the snapshot's jobs are the helpers on screen")
	}
	lines, err := RenderScene(sc, v, 120, 36, false)
	if err != nil {
		t.Fatal(err)
	}
	out := strings.Join(lines, "\n")
	for _, want := range []string{"my-repo / main", "give the user a role", "● Read src/user.ts", "src/user.ts  +1 −0", "+ const role = 'admin'", "[g Review] [c Commit] [u Undo]", "Reply to claude"} {
		if !strings.Contains(out, want) {
			t.Errorf("missing %q:\n%s", want, out)
		}
	}
	if len(lines) != 36 {
		t.Fatalf("a frame is the size asked for: %d lines", len(lines))
	}
	coloured, err := RenderScene(sc, v, 120, 36, true)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(coloured[1], "\x1b[38;2;") {
		t.Fatalf("with colour the lines carry true-colour SGR:\n%q", coloured[1])
	}
}

func TestEveryScreenKindAndTheThemeFlag(t *testing.T) {
	t.Cleanup(func() { ApplyTheme("rig") })
	for screen, want := range map[string]string{
		"welcome":  "Who do you want to talk to?",
		"code":     "EXPLORER",
		"settings": "Settings",
		"doctor":   "claude is signed out",
	} {
		path := writeScene(t, `{"screen":"`+screen+`","repo":"my-repo","status":{"parent":"claude"},
		 "parents":[{"Name":"claude","Installed":true}],
		 "file":{"path":"a.py","lines":["x = 1"]},"files":["a.py"],
		 "findings":{"problems":1,"findings":[{"level":"problem","title":"claude is signed out","fix":"login","fix_label":"/login"}]},
		 "app_settings":{"theme":"light","themes":["light","dark"],"parent":"claude"}}`)
		sc, v, err := LoadScene(path)
		if err != nil {
			t.Fatal(err)
		}
		lines, err := RenderScene(sc, v, 100, 30, false)
		if err != nil {
			t.Fatalf("%s: %v", screen, err)
		}
		if out := strings.Join(lines, "\n"); !strings.Contains(out, want) {
			t.Errorf("%s: missing %q:\n%s", screen, want, out)
		}
	}
	sc, v, _ := LoadScene(writeScene(t, `{"screen":"nowhere"}`))
	if _, err := RenderScene(sc, v, 100, 30, false); err == nil {
		t.Fatal("an unknown screen is refused")
	}
	sc, v, _ = LoadScene(writeScene(t, `{"theme":"sepia"}`))
	if _, err := RenderScene(sc, v, 100, 30, false); err == nil {
		t.Fatal("an unknown theme is refused")
	}
}
