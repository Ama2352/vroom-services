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

// WaterfallMatch implements the matching logic. 
// In a real waterfall, this might involve multiple steps or criteria.
func (p *DriverPool) WaterfallMatch() (*AvailableDriver, error) {
	if len(p.Candidates) == 0 {
		return nil, ErrNoDriversAvailable
	}
	
	// For now, the "waterfall" just picks the first valid candidate (nearest)
	// We can extend this logic later (e.g., checking driver rating, preference, etc.)
	return &p.Candidates[0], nil
}
