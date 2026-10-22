package ui

import (
	"strings"

	"github.com/Brasth/Rig/shell/internal/view"
)

// plainError says an error in words anyone can act on, for the ones people actually meet. The original text stays
// under it, faint, for whoever needs it (and for a bug report). It returns "" for an error it has no better words for.
func plainError(text, parent string) string {
	t := strings.ToLower(text)
	has := func(words ...string) bool {
		for _, w := range words {
			if strings.Contains(t, w) {
				return true
			}
		}
		return false
	}
	switch {
	case has("invalid api key", "please run /login", "not logged in", "not signed in", "unauthorized", "authentication failed", "401"):
		return parent + " is not signed in. Sign in in " + parent + "'s own screen once (/login there), or pick another assistant with /parent."
	case has("rate limit", "rate_limit", "429", "overloaded", "too many requests", "usage limit", "quota"):
		return "The model is busy, or you reached a usage limit. Wait a minute and send it again."
	case has("context length", "context window", "prompt is too long", "maximum context", "too many tokens"):
		return "This conversation has grown too long for the model. /compact keeps a short summary and goes on from it."
	case has("is not on path", "command not found", "executable file not found"):
		return parent + " is not installed on this computer. /doctor shows how to get it."
	case has("econnrefused", "enotfound", "connection reset", "unable to connect", "network is unreachable", "could not resolve", "getaddrinfo"):
		return parent + " could not reach the internet. Check your connection and send it again."
	case has("(indexerror at", "(keyerror at", "(typeerror at", "(valueerror at", "(attributeerror at"):
		return "Rig hit an internal error. /doctor checks the project; the details are in .rig/mcp-errors.log."
	case has("launch setup failed", "never started"):
		return "A helper could not start. /doctor can check the project and clean up what it left."
	case has("the parent exited with status", "exited with status"):
		return parent + " stopped unexpectedly. Send the message again; if it keeps happening, /doctor looks for the cause."
	}
	return ""
}

// errParent is who an error is about: the parent that was talking then, else the one in use.
func errParent(st State, e view.ChatEntry) string {
	if e.Parent != "" {
		return e.Parent
	}
	return appParent(st)
}
