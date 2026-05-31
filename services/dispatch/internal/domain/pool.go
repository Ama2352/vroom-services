package domain

import (
	"errors"
)

var (
	ErrNoDriversAvailable = errors.New("no drivers available in the area")
)

type AvailableDriver struct {
	ID       string  `json:"id"`
	Lat      float64 `json:"lat"`
	Lng      float64 `json:"lng"`
	Distance float64 `json:"distance"`
}

type DriverPool struct {
	Candidates []AvailableDriver
}

func NewDriverPool(candidates []AvailableDriver) *DriverPool {
	return &DriverPool{
		Candidates: candidates,
	}
}

// Nearest returns the closest available candidate from the pre-filtered pool.
// The waterfall retry logic (offer → reject → re-match) is orchestrated by the caller.
func (p *DriverPool) Nearest() (*AvailableDriver, error) {
	if len(p.Candidates) == 0 {
		return nil, ErrNoDriversAvailable
	}
	return &p.Candidates[0], nil
}
