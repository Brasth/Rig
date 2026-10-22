package ui

import (
	"strings"
	"testing"
	"time"

	"github.com/Brasth/Rig/shell/internal/view"
)

func TestSelftestStartsOnRequestShowsChecksAsTheyFinishAndPointsToTheReport(t *testing.T) {
	selftestEvery = time.Millisecond
	defer func() { selftestEvery = 2 * time.Second }()
	m, src := appModel(t)
	withConversation(t, m, src)
	follow(m, m.appSend("/selftest"), 0)
	if p := m.st.App.Palette; p == nil || !p.SelfTest || src.selftestPolls < 1 || len(src.selftestStarts) != 0 {
		t.Fatalf("/selftest opens the screen and reads the report, and starts nothing: %v polls %d starts %v", m.st.App.Palette, src.selftestPolls, src.selftestStarts)
	}
	items := paletteItems(m.st)
	if len(items) != 1 || items[0].Action != "selftest-run" || !strings.Contains(items[0].Detail, "costs a few cents") {
		t.Fatalf("one row, and it says what it costs: %+v", items)
	}
	src.selftest = &view.SelfTest{State: "running", Running: true, Parents: []view.SelfTestParent{{Name: "claude", Checks: []view.SelfTestCheck{
		{ID: "basic", Label: "answers a message", Status: "pass", Seconds: 4},
		{ID: "effort", Label: "accepts a reasoning level", Status: "running"},
		{ID: "model", Label: "accepts a listed model", Status: "waiting"}}}}}
	follow(m, press(m, "enter"), 0)
	if len(src.selftestStarts) != 1 {
		t.Fatalf("⏎ starts it: %v", src.selftestStarts)
	}
	var rows []string
	for _, it := range paletteItems(m.st) {
		rows = append(rows, it.Section+"|"+it.Label+"|"+it.Detail+"|"+it.Action)
	}
	list := strings.Join(rows, "\n")
	for _, want := range []string{"self-test|running…", "claude|✓ answers a message|(4s)", "claude|… accepts a reasoning level", "claude|· accepts a listed model"} {
		if !strings.Contains(list, want) {
			t.Fatalf("missing %q:\n%s", want, list)
		}
	}
	if strings.Contains(list, "selftest-run") {
		t.Fatal("no second start while it runs")
	}
	// the tick asks again while the screen is open
	polls := src.selftestPolls
	follow(m, m.onSelftestTick(), 0)
	if src.selftestPolls <= polls {
		t.Fatalf("the screen refreshes while the test runs: %d → %d", polls, src.selftestPolls)
	}
	// finished with one failure: the report row says where it is
	src.selftest = &view.SelfTest{State: "done", Parents: []view.SelfTestParent{{Name: "codex", Checks: []view.SelfTestCheck{
		{ID: "basic", Label: "answers a message", Status: "pass"},
		{ID: "plan", Label: "plan mode changes nothing", Status: "fail", Detail: "it wrote a file although plan mode was on"}}}}}
	follow(m, m.askSelftest(), 0)
	rows = nil
	for _, it := range paletteItems(m.st) {
		rows = append(rows, it.Label+"|"+it.Detail+"|"+it.Action)
	}
	list = strings.Join(rows, "\n")
	for _, want := range []string{"▶ run it again", "✕ plan mode changes nothing|it wrote a file although plan mode was on", "report|1 check failed: the report is in .rig/selftest.txt, paste it in a bug report|file:.rig/selftest.txt"} {
		if !strings.Contains(list, want) {
			t.Fatalf("missing %q:\n%s", want, list)
		}
	}
	// the screen no longer polls once it is closed
	m.st.App.Palette = nil
	polls = src.selftestPolls
	follow(m, m.onSelftestTick(), 0)
	if src.selftestPolls != polls {
		t.Fatal("a closed screen is not refreshed")
	}
}
