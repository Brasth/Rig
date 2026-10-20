package runtimedir

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

// The directory must equal what scripts/ui_store.py computes, or the shell can
// never find the server's socket.
func TestDirMatchesPython(t *testing.T) {
	py, err := exec.LookPath("python3")
	if err != nil {
		t.Skip("python3 not available")
	}
	scripts, err := filepath.Abs("../../../scripts")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(scripts, "ui_store.py")); err != nil {
		t.Skip("scripts/ui_store.py not found")
	}
	real := t.TempDir()
	link := filepath.Join(t.TempDir(), "via-link")
	if err := os.Symlink(real, link); err != nil {
		t.Fatal(err)
	}
	for _, repo := range []string{real, link, real + "/./sub/.."} {
		_ = os.MkdirAll(filepath.Join(real, "sub"), 0o755)
		out, err := exec.Command(py, "-I", "-c",
			"import sys; sys.path.insert(0, sys.argv[1]); from ui_store import runtime_directory; print(runtime_directory(sys.argv[2]))",
			scripts, repo).CombinedOutput()
		if err != nil {
			t.Fatalf("python failed: %v: %s", err, out)
		}
		want := strings.TrimSpace(string(out))
		got, err := Dir(repo)
		if err != nil {
			t.Fatal(err)
		}
		if got != want {
			t.Errorf("repo %q: go %q, python %q", repo, got, want)
		}
	}
}

func TestTokenIsStableAndShort(t *testing.T) {
	a, b := Token("/x/y"), Token("/x/y")
	if a != b || len(a) != 32 || Token("/x/z") == a {
		t.Fatalf("bad token %q %q", a, b)
	}
}
