package view

// Panel is a ready-to-render list of sections: the Workspace shape, shared by the Fleet tab. The
// server decides every line and tone; the shell never parses dc-cli output.
type Panel = Workspace

// FleetReply is the fleet.status result.
type FleetReply struct {
	View Panel `json:"view"`
}

// SettingsReply is the settings.view result: read-only sections in the Workspace shape.
type SettingsReply struct {
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
	Parent    string   `json:"parent"` // who answers a new conversation; empty when no parent CLI is installed
	// Configured is the project's own parent (.rig/harness.toml); empty means none was chosen, so the board offers its picker.
	Configured string   `json:"configured"`
	Model      string   `json:"model"`   // the parent CLI's own model setting, short ("opus"); empty when it names none
	Parents    []string `json:"parents"` // every parent the chat can run
	Sessions   []struct {
		ID     string `json:"id"`
		Parent string `json:"parent"`
		State  string `json:"state"`
		Turns  int    `json:"turns"`
	} `json:"sessions"`
}

// JobDiff is job.diff: what one job changed, as a unified diff with per-file counts.
type JobDiff struct {
	JobID  string `json:"job_id"`
	Source string `json:"source"` // "git", or "recorded" when only the paths are known
	Files  []struct {
		Path    string `json:"path"`
		Added   string `json:"added"`
		Deleted string `json:"deleted"`
	} `json:"files"`
	Diff      string `json:"diff"`
	Truncated bool   `json:"truncated"`
}

// AppSettings is settings.app: what the Settings screen can change, and where each value is kept.
type AppSettings struct {
	Theme             string          `json:"theme"`
	ThemeScope        string          `json:"theme_scope"`
	Themes            []string        `json:"themes"`
	Bell              bool            `json:"bell"`
	BellScope         string          `json:"bell_scope"`
	Parent            string          `json:"parent"`
	Enabled           bool            `json:"enabled"`
	Workers           map[string]bool `json:"workers"`
	Installed         []string        `json:"installed"`
	MaxRunning        int             `json:"max_running"`
	Objective         string          `json:"objective"`
	RoutingMode       string          `json:"routing_mode"`
	Sandbox           string          `json:"sandbox"`
	Worktrees         string          `json:"worktrees"`
	WorktreeContainer string          `json:"worktree_container"`
	Harness           string          `json:"harness"`
	HarnessText       string          `json:"harness_text"`
	Problems          []string        `json:"problems"`
	Global            string          `json:"global"`
	Editor            string          `json:"editor"`
	Shell             string          `json:"shell"`
}

// Today is applies.today: the helpers' work applied, undone or held, and commands allowed, newest first.
type Today struct {
	Rows []struct {
		JobID   string  `json:"job_id"`
		Title   string  `json:"title"`
		Worker  string  `json:"worker"`
		Branch  string  `json:"branch"`
		State   string  `json:"state"` // applied, undone, held or command
		At      float64 `json:"at"`
		Commit  string  `json:"commit"`
		Files   int     `json:"files"`
		Added   int     `json:"added"`
		Deleted int     `json:"deleted"`
		Checks  []Check `json:"checks"`
		Review  string  `json:"review"` // the worker of a finished review of this job
		By      string  `json:"by"`     // who allowed a command: "parent" or "you"
	} `json:"rows"`
}

// Check is one check a job ran and whether it passed.
type Check struct {
	Name string `json:"name"`
	OK   bool   `json:"ok"`
}

// ShipStatus is ship.status: what is ahead of the upstream branch.
type ShipStatus struct {
	Branch   string `json:"branch"`
	Upstream string `json:"upstream"`
	Ahead    []struct {
		Commit  string `json:"commit"`
		Subject string `json:"subject"`
	} `json:"ahead"`
	Title     string  `json:"title"`
	TitleFrom string  `json:"title_from"` // "tasks" (the parent's) or "commits"
	Checks    []Check `json:"checks"`
	PRBranch  string  `json:"pr_branch"`
	GH        bool    `json:"gh"`
}

// ThemeReply is ui.theme and ui.set_theme: the board's theme and where it is kept ("all", "repo" or "default").
type ThemeReply struct {
	Theme  string   `json:"theme"`
	Scope  string   `json:"scope"`
	Themes []string `json:"themes"`
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
	Ask     *ChatAsk    `json:"ask"`     // the parent's permission request waiting for the owner, if any
	Asks    bool        `json:"asks"`    // this parent's headless mode can ask for permission at all
	Partial string      `json:"partial"` // the answer streaming in, before it joins the entries
}

// ChatAsk is a permission request the parent is blocked on; chat.answer allows or denies it by ID.
type ChatAsk struct {
	ID      string `json:"id"`
	Tool    string `json:"tool"`
	Preview string `json:"preview"`
}

// ChatStarted is the chat.send result.
type ChatStarted struct {
	Session string `json:"session"`
	Parent  string `json:"parent"`
	State   string `json:"state"`
}
