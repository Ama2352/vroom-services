import { useState } from 'react';
import { DemoStoreProvider } from './store/demoStore';
import TopBar         from './components/TopBar';
import PassengerPanel from './components/PassengerPanel';
import DriverPanel    from './components/DriverPanel';
import MapView        from './components/MapView';
import TripProgress   from './components/TripProgress';
import ControlBar     from './components/ControlBar';
import EventFeed      from './components/EventFeed';
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
          {/* Left: Passenger perspective */}
          <aside className="col-left">
            <PassengerPanel />
          </aside>

          {/* Center: Map + trip progress strip */}
          <section className="col-center">
            <div className="center-map">
              <MapView />
            </div>
            <TripProgress />
          </section>

          {/* Right: Driver perspective */}
          <aside className="col-right">
            <DriverPanel />
          </aside>
        </main>

        <footer className="app-footer">
          <ControlBar />
          <EventFeed />
          <ApiInspector />
        </footer>

        <SystemMonitor open={monitorOpen} onClose={() => setMonitorOpen(false)} />
      </div>
    </DemoStoreProvider>
  );
}
