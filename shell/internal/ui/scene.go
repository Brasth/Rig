package ui

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/charmbracelet/lipgloss"
	"github.com/muesli/termenv"

	"github.com/Brasth/Rig/shell/internal/view"
)

// A Scene is one of the app's screens described in a JSON file, so that `rig-shell --scene FILE` can draw it with
// no project, API server or parent CLI: documentation screenshots and the recorded walkthrough come from these
// files (scripts/media). It sets the same State the running app sets; nothing here draws anything itself.
type Scene struct {
	Screen string `json:"screen"` // conversation (the default), welcome, code, settings, doctor
	Theme  string `json:"theme"`  // rig, light, dark, material or dracula; light is the app's default
	Repo   string `json:"repo"`   // the repository's name in the top bar
	Branch string `json:"branch"`
	Facts  string `json:"facts"` // what the first-conversation screen says about the repository
	Now    string `json:"now"`   // the clock, RFC 3339: the working spinner's elapsed time counts from the last user entry
	// Snapshot is the board snapshot (board.snapshot) whose Jobs rows are the helpers in the panel and the top bar;
	// a path relative to the scene file.
	Snapshot string               `json:"snapshot"`
	Status   *view.ChatStatus     `json:"status"` // who answers, their model and reasoning level
	Chat     *view.ChatTranscript `json:"chat"`
	Prompt   string               `json:"prompt"` // what is typed in the reply box
	Panel    bool                 `json:"panel"`  // the agents panel is open
	PanelSel int                  `json:"panel_sel"`
	Parents  []PickItem           `json:"parents"` // the welcome screen's choices
	Sel      int                  `json:"sel"`     // the chosen row of the welcome screen or the settings section
	Row      int                  `json:"row"`     // the settings row
	Advanced bool                 `json:"advanced"`
	File     *SceneFile           `json:"file"` // the code tab's open file
	Files    []string             `json:"files"`
	Problems *view.JobProblems    `json:"problems"`
	Findings *view.ShellDoctor    `json:"findings"`
	Settings *view.AppSettings    `json:"app_settings"`
	Message  string               `json:"message"` // a status-bar notice
}

// SceneFile is the code tab's open file in a scene: its lines and the gutter marks git would give them.
type SceneFile struct {
	Path   string            `json:"path"`
	Lines  []string          `json:"lines"`
	Gutter map[string]string `json:"gutter"` // line number (1-based) to M, A or D
	Line   int               `json:"line"`   // the cursor's line, 1-based
	Tabs   []string          `json:"tabs"`   // the open tabs; the file itself is added when missing
}

// LoadScene reads a scene file and the snapshot it names.
func LoadScene(path string) (*Scene, *view.View, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, nil, err
	}
	var sc Scene
	if err := json.Unmarshal(raw, &sc); err != nil {
		return nil, nil, fmt.Errorf("%s: %w", path, err)
	}
	v := &view.View{Version: view.Version}
	if sc.Snapshot != "" {
		snapPath := sc.Snapshot
		if !filepath.IsAbs(snapPath) {
			snapPath = filepath.Join(filepath.Dir(path), snapPath)
		}
		raw, err := os.ReadFile(snapPath)
		if err != nil {
			return nil, nil, err
		}
		var snap view.Snapshot
		if err := json.Unmarshal(raw, &snap); err != nil {
			return nil, nil, fmt.Errorf("%s: %w", snapPath, err)
		}
		v = &snap.View
	}
	return &sc, v, nil
}

// SceneState is the State the app would be in for the scene.
func SceneState(sc *Scene, styled bool) (State, error) {
	st := NewState()
	st.Styled = styled
	st.Tab = SessionTab
	st.ChatOn = true
	theme := sc.Theme
	if theme == "" {
		theme = "light"
	}
	if !ApplyTheme(theme) {
		return st, fmt.Errorf("theme must be one of %s", strings.Join(ThemeNames(), ", "))
	}
	st.Theme = theme
	a := &AppState{Repo: sc.Repo, Branch: sc.Branch, Facts: sc.Facts, Tab: 1}
	st.App = a
	if sc.Now != "" {
		now, err := time.Parse(time.RFC3339, sc.Now)
		if err != nil {
			return st, fmt.Errorf("now: %w", err)
		}
		st.Now = now
	}
	if sc.Status != nil {
		st.ChatParent, st.ChatModel, st.ChatEffort = sc.Status.Parent, sc.Status.Model, sc.Status.Effort
		st.ChatSessions = sc.Status.Sessions
	} else if sc.Chat != nil {
		st.ChatParent = sc.Chat.Parent
	}
	st.Chat = sc.Chat
	st.Prompt = sc.Prompt
	a.Panel, a.PanelSel = sc.Panel, sc.PanelSel
	a.Files, a.Problems = sc.Files, sc.Problems
	st.AppSettings = sc.Settings
	st.Message = sc.Message
	switch sc.Screen {
	case "", "conversation":
	case "welcome":
		a.Welcome, a.WelcomeSel = true, sc.Sel
		st.Picker = &Picker{Items: sc.Parents, Sel: sc.Sel, Current: st.ChatParent}
	case "code":
		a.Tab = 3
		if f := sc.File; f != nil {
			a.File, a.FileLines = f.Path, append([]string(nil), f.Lines...)
			a.Tabs = f.Tabs
			if !contains(a.Tabs, f.Path) {
				a.Tabs = append(a.Tabs, f.Path)
			}
			if f.Line > 0 {
				a.CurLine = f.Line - 1
			}
			if len(f.Gutter) > 0 {
				a.Gutter, a.GutterFor = map[int]byte{}, f.Path
				for k, mark := range f.Gutter {
					var n int
					if _, err := fmt.Sscanf(k, "%d", &n); err == nil && mark != "" {
						a.Gutter[n-1] = mark[0]
					}
				}
			}
		}
	case "settings":
		a.Settings = &SettingsScreen{Section: sc.Sel, Row: sc.Row, Advanced: sc.Advanced}
	case "doctor":
		a.Palette = &Palette{Doctor: true}
		a.Findings = sc.Findings
	default:
		return st, fmt.Errorf("screen must be conversation, welcome, code, settings or doctor")
	}
	return st, nil
}

// RenderScene draws the scene at w by h. With colour, the lines carry the app's SGR colours in true colour, as a
// terminal would get them; without, they are plain text as the golden tests see it.
func RenderScene(sc *Scene, v *view.View, w, h int, colour bool) ([]string, error) {
	st, err := SceneState(sc, colour)
	if err != nil {
		return nil, err
	}
	if colour {
		ForceTrueColour()
		return Paint(AppFrame(v, st, w, h)), nil
	}
	return AppFrame(v, st, w, h), nil
}

// ForceTrueColour draws for a file rather than a terminal: every colour in true colour, whatever stdout is. It must
// run before anything is rendered, since lipgloss strips colours at render time when it thinks there is no terminal.
func ForceTrueColour() {
	lipgloss.SetColorProfile(termenv.TrueColor)
}

func contains(list []string, s string) bool {
	for _, x := range list {
		if x == s {
			return true
		}
	}
	return false
}
