/**
 * App.jsx – Root layout for the Vroom Ride Hailing Demo.
 *
 * Layout:
 *   TopBar (fixed header)
 *   ┌──────────────────────────────────┐
 *   │ Left col          │  Center      │
 *   │  PassengerPanel   │  MapView     │
 *   │  DriverPanel      │  (full)      │
 *   └──────────────────────────────────┘
 *   ControlBar (fixed footer)
 *   ApiInspector (collapsible footer)
 *
 *   SystemMonitor – slide-in overlay (toggle via TopBar)
 */
import { useState } from 'react';
import { DemoStoreProvider } from './store/demoStore';
import TopBar         from './components/TopBar';
import PassengerPanel from './components/PassengerPanel';
import MapView        from './components/MapView';
import DriverPanel    from './components/DriverPanel';
import ControlBar     from './components/ControlBar';
import ApiInspector   from './components/ApiInspector';
import SystemMonitor  from './components/SystemMonitor';
import './App.css';

export default function App() {
  const [monitorOpen, setMonitorOpen] = useState(false);

  return (
    <DemoStoreProvider>
      <div className="app-shell">
        <TopBar
          monitorOpen={monitorOpen}
          onToggleMonitor={() => setMonitorOpen(v => !v)}
        />

        <main className="app-main">
          {/* Left: Passenger + Driver stacked */}
          <aside className="col-left">
            <div className="left-top">
              <PassengerPanel />
            </div>
            <div className="left-bottom">
              <DriverPanel />
            </div>
          </aside>

          {/* Center: Map (full height) */}
          <section className="col-center">
            <MapView />
          </section>
        </main>

        <footer className="app-footer">
          <ControlBar />
          <ApiInspector />
        </footer>

        {/* System Monitor – slide-in overlay */}
        <SystemMonitor open={monitorOpen} onClose={() => setMonitorOpen(false)} />
      </div>
    </DemoStoreProvider>
  );
}
