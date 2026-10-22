package ui

import "strings"

// explainGit puts a git failure in words with what to do. git's own text is for people who already know git: "Author
// identity unknown *** Please tell me who you are" says nothing about what to type. A failure it does not know keeps
// git's own last lines, so nothing is hidden.
func explainGit(err string) string {
	low := strings.ToLower(err)
	has := func(words ...string) bool {
		for _, w := range words {
			if strings.Contains(low, w) {
				return true
			}
		}
		return false
	}
	switch {
	case has("author identity unknown", "unable to auto-detect email", "please tell me who you are"):
		return `git does not know who you are yet. In your terminal: git config --global user.name "Your Name" and git config --global user.email you@example.com`
	case has("pre-commit hook", "commit-msg hook", "husky", "hook declined", "hook failed"):
		return "a git hook stopped the commit: " + gitTail(err, 2) + " (fix what it says, then commit again)"
	case has("nothing to commit", "no changes added to commit"):
		return "nothing is staged: space stages the file under the cursor"
	case has("no tracking information", "no upstream", "has no upstream"):
		return "this branch has not been pushed yet, so there is nothing to pull: ship it first (p) to publish it"
	case has("automatic merge failed", "merge conflict", "conflict (content)", "fix conflicts"):
		return "the pull left conflicts: open the files marked in the git view and choose, then commit"
	case has("would be overwritten by merge", "would be overwritten by checkout", "please commit your changes or stash"):
		return "you have uncommitted changes in files this would change: commit or discard them first"
	case has("authentication failed", "could not read username", "terminal prompts disabled", "permission denied (publickey)", "could not read password"):
		return "git is not signed in. Sign in once in your terminal (GitHub: gh auth login, or run git pull there), then try again"
	case has("non-fast-forward", "fetch first", "tip of your current branch is behind"):
		return "the remote has commits you do not have: pull first (P), then push again"
	case has("could not resolve host", "connection timed out", "network is unreachable", "connection refused"):
		return "could not reach the remote: check the connection and try again"
	case has("index.lock", "another git process"):
		return "another git command is running (or one stopped early): wait for it, or remove .git/index.lock if none is"
	}
	return gitTail(err, 2)
}

// gitTail is the last n non-empty lines of git's text on one line, without git's hint: lines.
func gitTail(s string, n int) string {
	var keep []string
	for _, l := range strings.Split(s, "\n") {
		l = strings.TrimSpace(l)
		if l != "" && !strings.HasPrefix(l, "hint:") {
			keep = append(keep, l)
		}
	}
	if len(keep) > n {
		keep = keep[len(keep)-n:]
	}
	return strings.Join(keep, " ")
}
