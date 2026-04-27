/**
 * ApiInspector.jsx – Shows the last API request/response for technical demo.
 */
import { useState } from 'react';
import { Terminal, ChevronDown, ChevronUp } from 'lucide-react';
import { useDemo } from '../store/demoStore';
import './ApiInspector.css';

const METHOD_COLOR = {
  GET:    '#22C55E',
  POST:   '#6C63FF',
  PUT:    '#F59E0B',
  DELETE: '#EF4444',
  PATCH:  '#06B6D4',
};

function StatusBadge({ code }) {
  if (!code) return null;
  const color = code >= 200 && code < 300 ? '#22C55E'
              : code >= 400               ? '#EF4444'
              : '#F59E0B';
  return (
    <span className="status-code" style={{ color, borderColor: `${color}44`, background: `${color}11` }}>
      {code}
    </span>
  );
}

export default function ApiInspector() {
  const { state } = useDemo();
  const [open, setOpen] = useState(true);
  const log = state.apiLog;

  return (
    <div className="api-inspector">
      <button
        id="api-inspector-toggle"
        className="api-header"
        onClick={() => setOpen(o => !o)}
      >
        <Terminal size={13} />
        <span className="api-title">API Inspector</span>
        {log && <StatusBadge code={log.status} />}
        {log && (
          <span className="api-method" style={{ color: METHOD_COLOR[log.method] ?? '#94A3B8' }}>
            {log.method}
          </span>
        )}
        {log && <span className="api-url mono">{log.url}</span>}
        <span className="api-chevron">
          {open ? <ChevronDown size={13} /> : <ChevronUp size={13} />}
        </span>
      </button>

      {open && (
        <div className="api-body">
          {!log && (
            <p className="api-empty">No API calls yet. Start the demo to see requests here.</p>
          )}
          {log && (
            <div className="api-detail">
              <div className="api-meta-row">
                <span className="api-method-big" style={{ color: METHOD_COLOR[log.method] ?? '#94A3B8' }}>
                  {log.method}
                </span>
                <span className="api-url-big mono">{log.url}</span>
                <StatusBadge code={log.status} />
                <span className="api-time">{log.ts.toLocaleTimeString('vi-VN')}</span>
              </div>
              <div className="api-cols">
                {log.payload && Object.keys(log.payload).length > 0 && (
                  <div className="api-col">
                    <div className="api-col-label">Request Payload</div>
                    <pre className="api-pre">{JSON.stringify(log.payload, null, 2)}</pre>
                  </div>
                )}
                <div className="api-col">
                  <div className="api-col-label">Response</div>
                  <pre className="api-pre">
                    {typeof log.response === 'string'
                      ? log.response
                      : JSON.stringify(log.response, null, 2)}
                  </pre>
                </div>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
