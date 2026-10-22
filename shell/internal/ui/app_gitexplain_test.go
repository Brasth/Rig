package ui

import (
	"strings"
	"testing"
)

func TestGitFailuresAreSaidInWordsWithWhatToDo(t *testing.T) {
	for in, want := range map[string]string{
		"Author identity unknown\n\n*** Please tell me who you are.\n\nRun\n  git config --global user.email": "git config --global user.name",
		"husky - pre-commit hook exited with code 1 (error)":                                                  "a git hook stopped the commit",
		"nothing to commit, working tree clean":                                                               "space stages",
		"There is no tracking information for the current branch.":                                            "ship it first (p)",
		"Automatic merge failed; fix conflicts and then commit the result.":                                   "left conflicts",
		"error: Your local changes to the following files would be overwritten by merge:":                     "commit or discard them first",
		"fatal: Authentication failed for 'https://github.com/x/y'":                                           "gh auth login",
		" ! [rejected] main -> main (non-fast-forward)":                                                       "pull first (P)",
		"fatal: Could not resolve host: github.com":                                                           "check the connection",
		"fatal: Unable to create '.git/index.lock': File exists.":                                             "another git command",
		"fatal: something new\nhint: do this\nand that":                                                       "fatal: something new and that",
	} {
		if got := explainGit(in); !strings.Contains(got, want) {
			t.Errorf("%q → %q, want it to say %q", in, got, want)
		}
	}
}
