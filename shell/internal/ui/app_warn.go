package ui

import "strings"

// warnRows are the warnings Rig already knows about the next turn, in amber above the reply box: a server that is not
// signed in, a config the parent ignores. At most two, each on its own line, so they never crowd the conversation.
func warnRows(st State, cw int) []string {
	var out []string
	for _, w := range st.ChatWarnings {
		if len(out) == 2 {
			break
		}
		for i, l := range wrap(w, cw-6, "") {
			prefix := "  "
			if i == 0 {
				prefix += st.paint(colAmber, "⚠ ")
			} else {
				prefix += "  "
			}
			out = append(out, prefix+st.dim(l))
		}
	}
	if len(out) > 0 && strings.TrimSpace(strings.Join(out, "")) != "" {
		return out
	}
	return nil
}
