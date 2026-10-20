// Package runtimedir locates a repository's private API runtime directory.
//
// It must agree byte for byte with scripts/ui_store.py runtime_directory:
// /tmp/rig-ui-<uid>/<first 32 hex of sha256(resolved repo path)>.
package runtimedir

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"os"
	"path/filepath"
)

// Resolve returns the absolute, symlink-resolved path the Python side hashes.
func Resolve(repo string) (string, error) {
	abs, err := filepath.Abs(repo)
	if err != nil {
		return "", err
	}
	if real, err := filepath.EvalSymlinks(abs); err == nil {
		return real, nil
	}
	return abs, nil
}

// Token is the directory name for a resolved repository path.
func Token(resolved string) string {
	sum := sha256.Sum256([]byte(resolved))
	return hex.EncodeToString(sum[:])[:32]
}

// Dir is the runtime directory for repo. It does not create it: the Python
// server owns creation and the 0700 mode.
func Dir(repo string) (string, error) {
	resolved, err := Resolve(repo)
	if err != nil {
		return "", err
	}
	return filepath.Join("/tmp", fmt.Sprintf("rig-ui-%d", os.Getuid()), Token(resolved)), nil
}
