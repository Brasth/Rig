package view

// WorkspaceVersion is the workspace view payload version this shell understands.
const WorkspaceVersion = 1

type Line struct {
	Text string `json:"text"`
	Tone string `json:"tone"`
}

type Section struct {
	Title string `json:"title"`
	Lines []Line `json:"lines"`
}

// Workspace is the ready-to-render workspace panel. The server parses dc-cli
// output; this type only decodes the result.
type Workspace struct {
	Version   int       `json:"version"`
	Attention bool      `json:"attention"`
	Context   string    `json:"context"` // the header's container identity, "devcontainer a3f9c1"; empty without a running one
	Sections  []Section `json:"sections"`
	// Recovery is copied from the reply's raw status by the client; nil when dc-cli reports no fault.
	Recovery *RecoveryPlan `json:"-"`
}

// WorkspaceReply is the workspace.status result.
type WorkspaceReply struct {
	View   Workspace `json:"view"`
	Status struct {
		Recovery *RecoveryPlan `json:"recovery"`
	} `json:"status"`
}
