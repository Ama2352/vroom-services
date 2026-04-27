/**
 * App.jsx – Root layout for the Vroom Ride Hailing Demo.
 *
 * Layout:
 *   TopBar (fixed header)
 *   ┌──────────────────────────────────────┐
 *   │ PassengerPanel │ MapView │ RightCol  │
 *   │  (left)        │ (center)│ Driver    │
 *   │                │         │ Timeline  │
 *   └──────────────────────────────────────┘
 *   ControlBar (fixed footer)
 *   ApiInspector (collapsible footer)
 */
import { DemoStoreProvider } from './store/demoStore';
import TopBar         from './components/TopBar';
import PassengerPanel from './components/PassengerPanel';
import MapView        from './components/MapView';
import DriverPanel    from './components/DriverPanel';
import EventTimeline  from './components/EventTimeline';
import ControlBar     from './components/ControlBar';
import ApiInspector   from './components/ApiInspector';
import './App.css';

export default function App() {
  return (
    <DemoStoreProvider>
      <div className="app-shell">
        <TopBar />

        <main className="app-main">
          {/* Left: Passenger */}
          <aside className="col-left">
            <PassengerPanel />
          </aside>

          {/* Center: Map */}
          <section className="col-center">
            <MapView />
          </section>

          {/* Right: Driver + Timeline */}
          <aside className="col-right">
            <div className="right-top">
              <DriverPanel />
            </div>
            <div className="right-bottom">
              <EventTimeline />
            </div>
          </aside>
        </main>

        <footer className="app-footer">
          <ControlBar />
          <ApiInspector />
        </footer>
      </div>
    </DemoStoreProvider>
  );
}
