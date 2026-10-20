package view

// Panel is a ready-to-render list of sections: the Workspace shape, shared by the Fleet tab. The
// server decides every line and tone; the shell never parses dc-cli output.
type Panel = Workspace

// FleetReply is the fleet.status result.
type FleetReply struct {
	View Panel `json:"view"`
}

// Evidence is the job.evidence result: the drawer for one job.
type Evidence struct {
	JobID    string    `json:"job_id"`
	Sections []Section `json:"sections"`
}

// RecoveryPlan is dc-cli's one proposed next step for an environment fault. Safe is the
// server's decision that Rig itself will apply it once the owner confirms.
type RecoveryPlan struct {
	Ready   bool   `json:"ready"`
	ID      string `json:"id"`
	Summary string `json:"summary"`
	Command string `json:"command"`
	Apply   string `json:"apply"`
	Safe    bool   `json:"safe"`
}

// RecoverReply is the env.recover result.
type RecoverReply struct {
	Applied string `json:"applied"`
	Exit    int    `json:"exit"`
	After   struct {
		ID    string `json:"id"`
		Ready bool   `json:"ready"`
	} `json:"after"`
}

// SectionsOrNil lets a nil panel render as "loading".
func (p *Panel) SectionsOrNil() *[]Section {
	if p == nil {
		return nil
	}
	return &p.Sections
}

// LogReply is the log.follow result: a job's log tail as plain text.
type LogReply struct {
	Job *struct {
		ID     string `json:"id"`
		Label  string `json:"label"`
		Worker string `json:"worker"`
	} `json:"job"`
	Text  string `json:"text"`
	Error bool   `json:"error"`
}

// ShellPlan is the env.shell result: the server's decision whether, and how, to open a shell in a job's container.
type ShellPlan struct {
	Available bool     `json:"available"`
	Reason    string   `json:"reason"`
	Argv      []string `json:"argv"`
	Workspace string   `json:"workspace"`
	Dir       string   `json:"dir"`   // working directory for a pane
	Env       []string `json:"env"`   // extra KEY=VALUE entries for a pane
	Label     string   `json:"label"` // what to call the pane
}

// ChatStatus is chat.status: whether the headless-parent chat experiment is on, and its recent sessions.
type ChatStatus struct {
	Enabled   bool     `json:"enabled"`
	Available []string `json:"available"`
	Sessions  []struct {
		ID     string `json:"id"`
		Parent string `json:"parent"`
		State  string `json:"state"`
		Turns  int    `json:"turns"`
	} `json:"sessions"`
}

// ChatEntry is one line of a chat transcript.
type ChatEntry struct {
	Role string `json:"role"`
	Text string `json:"text"`
	At   string `json:"at"`
}

// ChatTranscript is chat.transcript.
type ChatTranscript struct {
	Session string      `json:"session"`
	Parent  string      `json:"parent"`
	State   string      `json:"state"`
	Turns   int         `json:"turns"`
	Note    string      `json:"note"`
	Entries []ChatEntry `json:"entries"`
}

// ChatStarted is the chat.send result.
type ChatStarted struct {
	Session string `json:"session"`
	Parent  string `json:"parent"`
	State   string `json:"state"`
}
