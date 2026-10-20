// Package view holds the presentation payload served by board.snapshot.
// The Python server derives every label, tone, glyph, ordering and count; this
// package only decodes it. Unknown fields are ignored so the server can add.
package view

// Version is the board_view payload version this shell understands.
const Version = 1

type Header struct {
	Project   string `json:"project"`
	Attention int    `json:"attention"`
	Running   int    `json:"running"`
	Slots     int    `json:"slots"`
	Cap       int    `json:"cap"`
}

type Tab struct {
	Name      string `json:"name"`
	Count     int    `json:"count"`
	Attention int    `json:"attention"`
}

type Row struct {
	ID          string   `json:"id"`
	Title       string   `json:"title"`
	Label       string   `json:"label"`
	Tone        string   `json:"tone"`
	Glyph       string   `json:"glyph"`
	Second      string   `json:"second"`
	Explanation string   `json:"explanation"`
	Worker      string   `json:"worker"`
	Summary     string   `json:"summary"`
	Detail      []string `json:"detail"`
	Ask         bool     `json:"ask"`
	Attention   bool     `json:"attention"`
}

type View struct {
	Version   int    `json:"version"`
	Header    Header `json:"header"`
	Tabs      []Tab  `json:"tabs"`
	Jobs      []Row  `json:"jobs"`
	Queue     []Row  `json:"queue"`
	Workflows []Row  `json:"workflows"`
}

// Rows returns the rows for a board tab name.
func (v *View) Rows(tab string) []Row {
	if v == nil {
		return nil
	}
	switch tab {
	case "Jobs":
		return v.Jobs
	case "Queue":
		return v.Queue
	case "Workflows":
		return v.Workflows
	}
	return nil
}

// Snapshot is the board.snapshot result: the view plus the server clock.
type Snapshot struct {
	View         View    `json:"view"`
	CapturedWall float64 `json:"captured_wall"`
}
