package testkit

import (
	"fmt"
	"strings"
	"testing"
)

// Logger wraps *testing.T and produces structured, readable test output.
type Logger struct {
	t *testing.T
}

// New creates a new Logger for the given test.
func New(t *testing.T) *Logger {
	t.Helper()
	return &Logger{t: t}
}

// Begin prints the opening banner with test name and proof statement.
// Box width is 68 characters (inner content width = 66 after "║ " and " ║").
func (l *Logger) Begin(testName, proves string) {
	l.t.Helper()
	const width = 68
	const inner = width - 2 // 66 chars between ║ and ║

	top := "╔" + strings.Repeat("═", width-2) + "╗"
	bot := "╚" + strings.Repeat("═", width-2) + "╝"

	nameContent := fmt.Sprintf("  TEST    %s", testName)
	provesContent := fmt.Sprintf("  PROVING %s", proves)

	nameRow := "║" + padRight(nameContent, inner) + "║"
	provesRow := "║" + padRight(provesContent, inner) + "║"

	l.t.Log("\n" + top + "\n" + nameRow + "\n" + provesRow + "\n" + bot + "\n")
}

// Step prints a step header: [STEP N/M] label
func (l *Logger) Step(n, total int, label string) {
	l.t.Helper()
	l.t.Logf("[STEP %d/%d] %s", n, total, label)
}

// Detail prints a key-value detail line, key right-padded to 10 chars.
func (l *Logger) Detail(key, value string) {
	l.t.Helper()
	l.t.Logf("           %-10s: %s", key, value)
}

// Arrow prints an action arrow line.
func (l *Logger) Arrow(action string) {
	l.t.Helper()
	l.t.Logf("           → %s", action)
}

// Result prints a blank line, [RESULT], then each check indented by 9 spaces.
// Callers pass pre-formatted strings like "✓ Trip status      : COMPLETED".
func (l *Logger) Result(checks ...string) {
	l.t.Helper()
	l.t.Log("")
	l.t.Log("[RESULT]")
	for _, check := range checks {
		l.t.Logf("         %s", check)
	}
}

// padRight pads s with spaces on the right to reach length n.
// If s is already longer than n it is returned unchanged.
func padRight(s string, n int) string {
	runeLen := len([]rune(s))
	if runeLen >= n {
		return s
	}
	return s + strings.Repeat(" ", n-runeLen)
}
