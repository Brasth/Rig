package main

import (
	"bytes"
	"errors"
	"strings"
	"testing"

	"github.com/Brasth/Rig/shell/internal/api"
)

func TestParseSize(t *testing.T) {
	for in, ok := range map[string]bool{"120x36": true, "80X24": true, "0x5": false, "x": false, "10": false, "2000x5": false, "ax5": false} {
		_, _, err := parseSize(in)
		if (err == nil) != ok {
			t.Errorf("%q: err=%v want ok=%v", in, err, ok)
		}
	}
}

func TestWantASCII(t *testing.T) {
	env := func(m map[string]string) func(string) string { return func(k string) string { return m[k] } }
	cases := []struct {
		name string
		flag bool
		env  map[string]string
		want bool
	}{
		{"default", false, nil, false},
		{"flag", true, nil, true},
		{"forced", false, map[string]string{"RIG_TUI_ASCII": "1"}, true},
		{"cjk keeps ascii ui", false, map[string]string{"RIG_TUI_CJK": "1"}, true},
		{"utf8 locale", false, map[string]string{"LANG": "en_US.UTF-8"}, false},
		{"utf-8 spelled with dash", false, map[string]string{"LC_ALL": "C.utf-8"}, false},
		{"non-utf locale", false, map[string]string{"LANG": "C"}, true},
		{"LC_ALL wins over LANG", false, map[string]string{"LC_ALL": "C", "LANG": "en_US.UTF-8"}, true},
	}
	for _, c := range cases {
		if got := wantASCII(c.flag, env(c.env)); got != c.want {
			t.Errorf("%s: got %v want %v", c.name, got, c.want)
		}
	}
}

func TestOnceFromFixture(t *testing.T) {
	var out, errb bytes.Buffer
	code := run([]string{"--once", "--fixture", "../../testdata/snapshot.json", "--size", "120x36", "--ascii"}, &out, &errb)
	if code != 0 {
		t.Fatalf("exit %d: %s", code, errb.String())
	}
	text := out.String()
	for _, want := range []string{"Rig . my-repo", "Needs input", "job-7f2"} {
		if !strings.Contains(text, want) {
			t.Errorf("missing %q in\n%s", want, text)
		}
	}
	if lines := strings.Count(strings.TrimRight(text, "\n"), "\n") + 1; lines != 36 {
		t.Errorf("got %d lines, want 36", lines)
	}
}

func TestOnceRejectsBadArguments(t *testing.T) {
	for _, args := range [][]string{
		{"--once", "--fixture", "../../testdata/snapshot.json", "--size", "nope"},
		{"--once", "--fixture", "../../testdata/snapshot.json", "--tab", "Nowhere"},
		{"--once", "--fixture", "/no/such/file.json"},
	} {
		var out, errb bytes.Buffer
		if code := run(args, &out, &errb); code == 0 {
			t.Errorf("%v should fail", args)
		}
	}
}

func TestVersion(t *testing.T) {
	var out, errb bytes.Buffer
	if run([]string{"--version"}, &out, &errb) != 0 || !strings.HasPrefix(out.String(), "rig-shell ") {
		t.Fatalf("got %q", out.String())
	}
}

func TestOnceWorkspaceFromFixtures(t *testing.T) {
	var out, errb bytes.Buffer
	code := run([]string{"--once", "--tab", "Workspace", "--fixture", "../../testdata/snapshot.json",
		"--workspace-fixture", "../../testdata/workspace.json", "--size", "100x24", "--ascii"}, &out, &errb)
	if code != 0 {
		t.Fatalf("exit %d: %s", code, errb.String())
	}
	text := out.String()
	for _, want := range []string{"Rig . my-repo", "[Env]", "! 9229  held by node"} {
		if !strings.Contains(text, want) {
			t.Errorf("missing %q in\n%s", want, text)
		}
	}
	if run([]string{"--once", "--tab", "Workspace", "--workspace-fixture", "/no/such.json"}, &out, &errb) == 0 {
		t.Error("a missing workspace fixture must fail")
	}
}

func TestOnceSessionRendersTheMockupsThreeColumnsFromFixtures(t *testing.T) {
	var out, errb bytes.Buffer
	code := run([]string{"--once", "--tab", "Term", "--fixture", "../../testdata/snapshot.json",
		"--workspace-fixture", "../../testdata/workspace.json", "--evidence-fixture", "../../testdata/evidence.json",
		"--size", "120x36", "--ascii"}, &out, &errb)
	if code != 0 {
		t.Fatalf("exit %d: %s", code, errb.String())
	}
	text := out.String()
	for _, want := range []string{"[Session]", "- Workspace", "- Worktrees", "- Memory", "Jobs 1-7/7 . attention first", "Queue 2 pending", "- Evidence . job-7f2", "diff "} {
		if !strings.Contains(text, want) {
			t.Errorf("missing %q in\n%s", want, text)
		}
	}
}

func TestRetryingStartsTheServerOnceAndOnlyWhenItWasNotRunning(t *testing.T) {
	e := &ensuringSource{} // not enabled: revive does nothing, so only the second call is observed
	calls := 0
	out, err := retrying(e, func() (string, error) {
		calls++
		if calls == 1 {
			return "", api.ErrNotRunning
		}
		return "sent", nil
	})
	if out != "sent" || err != nil || calls != 2 {
		t.Fatalf("a refused connection is tried again: %q %v %d", out, err, calls)
	}
	calls = 0
	_, err = retrying(e, func() (string, error) { calls++; return "", errors.New("the server said no") })
	if err == nil || calls != 1 {
		t.Fatalf("any other error is not retried (the request may have run): %v %d", err, calls)
	}
	calls = 0
	_, err = retrying(e, func() (string, error) { calls++; return "", api.ErrNotRunning })
	if !errors.Is(err, api.ErrNotRunning) || calls != 2 {
		t.Fatalf("still not running after one retry: it is reported, not looped: %v %d", err, calls)
	}
}
