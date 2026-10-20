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
