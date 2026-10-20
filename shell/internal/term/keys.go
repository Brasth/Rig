package term

import (
	"strings"

	tea "github.com/charmbracelet/bubbletea"
)

type cursorKey struct {
	letter string // final byte of the CSI/SS3 form
	mod    string // "1;<modifier>" prefix value for modified forms
}

var arrows = map[tea.KeyType]cursorKey{
	tea.KeyUp: {"A", "2"}, tea.KeyDown: {"B", "2"}, tea.KeyRight: {"C", "2"}, tea.KeyLeft: {"D", "2"},
	tea.KeyHome: {"H", "2"}, tea.KeyEnd: {"F", "2"},
}

// Modified arrows and home/end that Bubble Tea reports as their own key types: shift 2, ctrl 5, ctrl+shift 6.
var modified = map[tea.KeyType]struct {
	base tea.KeyType
	mod  string
}{
	tea.KeyShiftUp: {tea.KeyUp, "2"}, tea.KeyShiftDown: {tea.KeyDown, "2"}, tea.KeyShiftRight: {tea.KeyRight, "2"}, tea.KeyShiftLeft: {tea.KeyLeft, "2"},
	tea.KeyShiftHome: {tea.KeyHome, "2"}, tea.KeyShiftEnd: {tea.KeyEnd, "2"},
	tea.KeyCtrlUp: {tea.KeyUp, "5"}, tea.KeyCtrlDown: {tea.KeyDown, "5"}, tea.KeyCtrlRight: {tea.KeyRight, "5"}, tea.KeyCtrlLeft: {tea.KeyLeft, "5"},
	tea.KeyCtrlHome: {tea.KeyHome, "5"}, tea.KeyCtrlEnd: {tea.KeyEnd, "5"},
	tea.KeyCtrlShiftUp: {tea.KeyUp, "6"}, tea.KeyCtrlShiftDown: {tea.KeyDown, "6"}, tea.KeyCtrlShiftRight: {tea.KeyRight, "6"}, tea.KeyCtrlShiftLeft: {tea.KeyLeft, "6"},
}

var tilde = map[tea.KeyType]string{
	tea.KeyInsert: "2", tea.KeyDelete: "3", tea.KeyPgUp: "5", tea.KeyPgDown: "6",
	tea.KeyF5: "15", tea.KeyF6: "17", tea.KeyF7: "18", tea.KeyF8: "19", tea.KeyF9: "20", tea.KeyF10: "21", tea.KeyF11: "23", tea.KeyF12: "24",
}

var ss3 = map[tea.KeyType]string{tea.KeyF1: "P", tea.KeyF2: "Q", tea.KeyF3: "R", tea.KeyF4: "S"}

// KeyBytes is what a terminal would send for k. appCursor selects the application cursor-key form the
// program may have asked for. It returns nil for keys that have no byte form.
func KeyBytes(k tea.KeyMsg, appCursor bool) []byte {
	out := keyBytes(k, appCursor)
	if out == nil {
		return nil
	}
	if k.Alt && k.Type != tea.KeyEsc {
		return append([]byte{0x1b}, out...)
	}
	return out
}

func keyBytes(k tea.KeyMsg, appCursor bool) []byte {
	if k.Paste {
		return []byte(strings.ReplaceAll(strings.ReplaceAll(string(k.Runes), "\r\n", "\r"), "\n", "\r"))
	}
	switch k.Type {
	case tea.KeyRunes:
		return []byte(string(k.Runes))
	case tea.KeySpace:
		return []byte(" ")
	case tea.KeyEnter:
		return []byte("\r")
	case tea.KeyBackspace:
		return []byte{0x7f}
	case tea.KeyTab:
		return []byte("\t")
	case tea.KeyShiftTab:
		return []byte("\x1b[Z")
	case tea.KeyEsc:
		return []byte{0x1b}
	}
	if c, ok := arrows[k.Type]; ok {
		if appCursor {
			return []byte("\x1bO" + c.letter)
		}
		return []byte("\x1b[" + c.letter)
	}
	if m, ok := modified[k.Type]; ok {
		return []byte("\x1b[1;" + m.mod + arrows[m.base].letter)
	}
	if code, ok := tilde[k.Type]; ok {
		return []byte("\x1b[" + code + "~")
	}
	if letter, ok := ss3[k.Type]; ok {
		return []byte("\x1bO" + letter)
	}
	// Control keys are their control code (Ctrl+A is 1 ... Ctrl+Z is 26, Ctrl+\ is 28, Ctrl+] is 29).
	if k.Type >= tea.KeyCtrlAt && k.Type <= tea.KeyCtrlUnderscore {
		return []byte{byte(k.Type)}
	}
	return nil
}
