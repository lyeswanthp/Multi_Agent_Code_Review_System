import React, { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Activity, Cpu, AlertTriangle, PlayCircle, CheckCircle2, Server, GitMerge } from 'lucide-react';

// Clean soft-white light palette
const T = {
  pageBg: '#f6f7f9',          // soft near-white with a hint of cool
  cardBg: 'linear-gradient(145deg, #ffffff 0%, #fafbfc 100%)',
  cardBorder: 'rgba(15, 23, 42, 0.08)',
  headerBarBg: 'rgba(255, 255, 255, 0.85)',
  innerPanel: '#ffffff',
  innerBorder: 'rgba(15, 23, 42, 0.06)',
  text: '#1f2937',             // slate-800
  textMuted: '#64748b',        // slate-500
  textFaint: '#94a3b8',        // slate-400
  accent: '#0891b2',           // cyan-600
  accentSoft: 'rgba(8, 145, 178, 0.10)',
  success: '#16a34a',          // green-600
  warn: '#d97706',             // amber-600
  danger: '#dc2626',           // red-600
  cardTitle: '#475569',        // slate-600
};

function formatDuration(startTs: number, endTs: number | null) {
  const s = ((endTs || Date.now()) - startTs) / 1000;
  if (s < 60) return s.toFixed(1) + 's';
  return Math.floor(s/60) + 'm ' + Math.floor(s%60) + 's';
}

function Card({ title, icon: Icon, children, className = '', style = {} }: any) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      whileHover={{ y: -4 }}
      transition={{ duration: 0.4 }}
      style={{
        background: T.cardBg,
        border: `1px solid ${T.cardBorder}`,
        borderRadius: '20px',
        boxShadow: '0 6px 18px rgba(15, 23, 42, 0.04)',
        overflow: 'hidden',
        display: 'flex',
        flexDirection: 'column',
        ...style
      }}
      className={className}
    >
      <div style={{ padding: '18px 24px', borderBottom: `1px solid ${T.innerBorder}`, background: '#fafbfc', display: 'flex', alignItems: 'center', gap: '8px' }}>
        {Icon && <Icon size={18} style={{ color: T.accent }} />}
        <h3 style={{ margin: 0, fontSize: '13px', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.1em', color: T.cardTitle }}>{title}</h3>
      </div>
      <div style={{ padding: '16px 20px', flex: 1, overflow: 'auto', display: 'flex', flexDirection: 'column', gap: '8px', maxHeight: '400px' }}>
        {children}
      </div>
    </motion.div>
  );
}

class ErrorBoundary extends React.Component<{children: React.ReactNode}, {hasError: boolean, error: string}> {
  constructor(props: any) {
    super(props);
    this.state = { hasError: false, error: '' };
  }

  static getDerivedStateFromError(error: Error | any) {
    return { hasError: true, error: String(error?.message || error) };
  }

  componentDidCatch(error: Error | any, info: any) {
    console.error('[App] Error caught:', error, info);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div style={{ padding: '40px', color: '#ef4444', fontFamily: 'monospace' }}>
          <h2>App Error:</h2>
          <pre>{this.state.error}</pre>
        </div>
      );
    }
    return this.props.children;
  }
}

export default function App() {
  return (
    <ErrorBoundary>
      <AppContent />
    </ErrorBoundary>
  );
}

function AppContent() {
  const [elapsed, setElapsed] = useState(0);
  const [connected, setConnected] = useState(false);
  const [startTime, setStartTime] = useState<number>(0);

  // Dashboard state with safe defaults
  const [phases, setPhases] = useState<Record<string, any>>({});
  const [agents, setAgents] = useState<Record<string, any>>({});
  const [findings, setFindings] = useState<any[]>([]);
  const [diffFiles, setDiffFiles] = useState<any[]>([]);
  const [llmCalls, setLlmCalls] = useState<any[]>([]);
  const [expandedCalls, setExpandedCalls] = useState<Record<string, boolean>>({});

  const toggleExpand = (id: string) => {
    setExpandedCalls(prev => ({ ...prev, [id]: !prev[id] }));
  };

  useEffect(() => {
    console.log('[App] Setting up SSE connection...');

    let es: EventSource;
    try {
      es = new EventSource('/events');
    } catch (err) {
      console.error('[App] Failed to create EventSource:', err);
      return;
    }

    es.onopen = () => {
      console.log('[App] SSE connected');
      setConnected(true);
      setStartTime(Date.now());
    };

    es.onerror = (err) => {
      console.error('[App] SSE error:', err);
      setConnected(false);
    };

    const eventTypes = [
      'phase.start', 'phase.done', 'phase.fail',
      'agent.set', 'agent.start', 'agent.done', 'agent.fail',
      'agent.files', 'agent.file.start', 'agent.file.done', 'agent.file.skip',
      'llm.request', 'llm.response', 'llm.error',
      'diff.file', 'file.loaded', 'findings', 'agent.finding', 'review.done'
    ];

    eventTypes.forEach(type => {
      es.addEventListener(type, (e: any) => {
        try {
          // Backend sends: {"kind": "event.type", "data": {...actual data...}, "ts": 123}
          // We need to extract actual data from the nested "data" field
          const parsed = JSON.parse(e.data);
          const eventData = parsed.data || {}; // Extract nested data
          console.log('[App] Event:', type, eventData);

          switch(type) {
            case 'phase.start':
              setPhases(prev => ({ ...prev, [eventData.phase]: { status: 'running', detail: '', startTime: Date.now() } }));
              break;
            case 'phase.done':
              setPhases(prev => {
                const p = prev[eventData.phase] || {};
                const updated = { ...p, status: 'done', detail: eventData.detail || '' };
                return { ...prev, [eventData.phase]: updated };
              });
              break;
            case 'phase.fail':
              setPhases(prev => {
                const p = prev[eventData.phase] || {};
                const updated = { ...p, status: 'failed', detail: eventData.detail || '' };
                return { ...prev, [eventData.phase]: updated };
              });
              break;
            case 'agent.set':
              setAgents(prev => {
                const next = { ...prev };
                (eventData.agents || []).forEach((a: string) => {
                  if (!next[a]) next[a] = { status: 'pending', filesReviewed: 0, filesTotal: 0 };
                });
                return next;
              });
              break;
            case 'agent.start':
              setAgents(prev => ({ ...prev, [eventData.agent]: { status: 'running', filesReviewed: 0, filesTotal: 0 } }));
              break;
            case 'agent.files':
              setAgents(prev => {
                const a = prev[eventData.agent];
                return a ? { ...prev, [eventData.agent]: { ...a, filesTotal: eventData.files?.length || 0 } } : prev;
              });
              break;
            case 'agent.done':
              setAgents(prev => {
                const a = prev[eventData.agent];
                return a ? { ...prev, [eventData.agent]: { ...a, status: 'done' } } : prev;
              });
              break;
            case 'agent.fail':
              setAgents(prev => {
                const a = prev[eventData.agent];
                return a ? { ...prev, [eventData.agent]: { ...a, status: 'failed' } } : prev;
              });
              break;
            case 'findings':
              if (Array.isArray(eventData.findings)) setFindings(eventData.findings);
              break;
            case 'agent.finding':
              if (eventData.finding) setFindings(prev => [...prev, eventData.finding]);
              break;
            case 'review.done':
              if (eventData.findings && Array.isArray(eventData.findings)) setFindings(eventData.findings);
              break;
            case 'diff.file':
              setDiffFiles(prev => [...prev, eventData]);
              break;
            case 'llm.request':
              setLlmCalls(prev => [...prev, { ...eventData, status: 'pending' }]);
              break;
            case 'llm.response':
              setLlmCalls(prev => prev.map(c => c.id === eventData.id ? { ...c, status: 'done', ...eventData } : c));
              break;
            case 'llm.error':
              setLlmCalls(prev => prev.map(c => c.id === eventData.id ? { ...c, status: 'failed', error: eventData.error } : c));
              break;
            case 'agent.file.done':
            case 'agent.file.skip':
              setAgents(prev => {
                const a = prev[eventData.agent];
                return a ? { ...prev, [eventData.agent]: { ...a, filesReviewed: (a.filesReviewed || 0) + 1 } } : prev;
              });
              break;
          }
        } catch (err) {
          console.error('[App] Parse error:', err);
        }
      });
    });

    // NOTE: history is already replayed by the SSE server on connect
    // (see web_dashboard.py — it writes bus.history before subscribing).
    // Don't refetch /api/history; that double-dispatches every event with a
    // broken envelope (eventData ends up empty), which manifests as a phantom
    // "undefined" agent, blank diff rows, and ghost pending LLM calls.

    return () => {
      if (es) es.close();
    };
  }, []);

  useEffect(() => {
    if (connected && startTime) {
      const interval = setInterval(() => {
        setElapsed(Date.now() - startTime);
      }, 1000);
      return () => clearInterval(interval);
    }
  }, [connected, startTime]);

  const findingsList = findings ? [...findings].reverse() : [];

  return (
    <div style={{ minHeight: '100vh', paddingBottom: '40px', backgroundColor: T.pageBg, color: T.text }}>
      {/* Header */}
      <header style={{
        position: 'sticky', top: 0, zIndex: 50, padding: '16px 32px',
        background: T.headerBarBg, backdropFilter: 'blur(16px)',
        borderBottom: `1px solid ${T.cardBorder}`,
        display: 'flex', justifyContent: 'space-between', alignItems: 'center'
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
          <h1 style={{ margin: 0, fontSize: '20px', fontWeight: 600, color: T.text }}>
            CodeNexus Dashboard
          </h1>
          <span style={{
            fontSize: '10px', padding: '2px 8px', borderRadius: '12px',
            background: T.accentSoft, color: T.accent,
            border: `1px solid ${T.accent}`, textTransform: 'uppercase', letterSpacing: '1px'
          }}>
            Live
          </span>
        </div>
        <div style={{
          display: 'flex', alignItems: 'center', gap: '12px',
          padding: '6px 14px', background: '#ffffff',
          borderRadius: '20px', border: `1px solid ${T.cardBorder}`
        }}>
          <div style={{
            width: '8px', height: '8px', borderRadius: '50%',
            background: connected ? T.success : T.danger,
            boxShadow: `0 0 10px ${connected ? T.success : T.danger}`
          }} />
          <span style={{ fontSize: '13px', color: T.textMuted, fontWeight: 500 }}>
            {connected ? 'Connected' : 'Disconnected'}
          </span>
          <span style={{
            borderLeft: `1px solid ${T.cardBorder}`,
            paddingLeft: '12px', fontFamily: 'monospace', color: T.text, fontSize: '14px'
          }}>
            {Math.floor(elapsed / 60000).toString().padStart(2, '0')}:
            {Math.floor((elapsed % 60000) / 1000).toString().padStart(2, '0')}
          </span>
        </div>
      </header>

      {/* Main Content */}
      <main style={{ padding: '24px', maxWidth: '98%', margin: '0 auto' }}>
        {/* Debug info */}
        <div style={{
          padding: '8px 16px', marginBottom: '24px',
          background: '#ffffff', borderRadius: '8px',
          border: `1px solid ${T.innerBorder}`,
          fontFamily: 'monospace', fontSize: '12px', color: T.textMuted
        }}>
          Debug: {Object.keys(phases || {}).length} phases, {Object.keys(agents || {}).length} agents, {(findings || []).length} findings
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(12, 1fr)', gap: '32px' }}>
          {/* Left Column */}
          <div style={{ gridColumn: 'span 4', display: 'flex', flexDirection: 'column', gap: '32px' }}>
            <Card title="Pipeline Status" icon={Activity}>
              {['static_analysis', 'context', 'agents'].map(phaseName => {
                const p = (phases || {})[phaseName];
                const status = p?.status || 'pending';
                return (
                  <div key={phaseName} style={{
                    display: 'flex', alignItems: 'center', gap: '16px',
                    padding: '12px 16px', background: T.innerPanel,
                    border: `1px solid ${T.innerBorder}`, borderRadius: '12px'
                  }}>
                    <div style={{ width: '24px', height: '24px', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                      {status === 'running' ? <PlayCircle size={16} color={T.accent} /> :
                       status === 'done' ? <CheckCircle2 size={16} color={T.success} /> :
                       <div style={{ width: '8px', height: '8px', borderRadius: '50%', background: T.cardBorder }} />}
                    </div>
                    <div style={{ flex: 1 }}>
                      <div style={{ fontSize: '14px', fontWeight: 500, textTransform: 'capitalize', color: T.text }}>
                        {phaseName.replace('_', ' ')}
                      </div>
                      <div style={{ fontSize: '12px', color: T.textMuted }}>{p?.detail || 'Waiting...'}</div>
                    </div>
                    <div style={{ fontSize: '12px', color: T.textMuted, fontFamily: 'monospace' }}>
                      {p?.startTime ? formatDuration(p.startTime, p.endTime) : ''}
                    </div>
                  </div>
                );
              })}
            </Card>

            <Card title="AI Specialists" icon={Cpu}>
              {Object.entries(agents || {}).map(([name, agent]: [string, any]) => (
                <div key={name} style={{
                  display: 'flex', alignItems: 'center', gap: '16px',
                  padding: '12px 16px', background: T.innerPanel,
                  border: `1px solid ${T.innerBorder}`, borderRadius: '12px'
                }}>
                  <div style={{ flex: 1, fontWeight: 500, fontSize: '14px', textTransform: 'capitalize', color: T.text }}>
                    {name.replace('_', ' ')}
                  </div>
                  <div style={{ fontSize: '12px', fontFamily: 'monospace', color: T.textMuted }}>
                    {(agent?.filesReviewed || 0)} / {(agent?.filesTotal || 0)} files
                  </div>
                  <div style={{
                    fontSize: '11px', padding: '4px 10px', borderRadius: '12px', fontWeight: 600, textTransform: 'uppercase',
                    background: agent?.status === 'running' ? T.accentSoft :
                                agent?.status === 'done' ? 'rgba(22, 163, 74, 0.12)' : 'rgba(15, 23, 42, 0.04)',
                    color: agent?.status === 'running' ? T.accent :
                          agent?.status === 'done' ? T.success : T.textFaint
                  }}>
                    {agent?.status || 'pending'}
                  </div>
                </div>
              ))}
              {Object.keys(agents || {}).length === 0 && (
                <div style={{ color: T.textMuted, fontSize: '13px', textAlign: 'center', padding: '20px' }}>
                  Waiting for agents to initialize...
                </div>
              )}
            </Card>

            <Card title="Diff Analysis" icon={GitMerge}>
              {(diffFiles || []).length > 0 ? (
                (diffFiles || []).map((f: any, i: number) => (
                  <div key={i} style={{
                    display: 'flex', justifyContent: 'space-between',
                    padding: '10px 16px', background: T.innerPanel,
                    border: `1px solid ${T.innerBorder}`, borderRadius: '8px', fontSize: '13px'
                  }}>
                    <span style={{ fontFamily: 'monospace', color: T.accent }}>{f.path}</span>
                    <span style={{ display: 'flex', gap: '8px' }}>
                      <span style={{ color: T.success }}>+{f.new_lines ?? f.newLines ?? 0}</span>
                      <span style={{ color: T.danger }}>-{f.old_lines ?? f.oldLines ?? 0}</span>
                    </span>
                  </div>
                ))
              ) : (
                <div style={{ padding: '20px', textAlign: 'center', color: T.textMuted, fontSize: '13px' }}>
                  Waiting for diffs...
                </div>
              )}
            </Card>
          </div>

          {/* Right Column (Full Width - Findings + LLM Trace) */}
          <div style={{ gridColumn: 'span 8', display: 'flex', flexDirection: 'column', gap: '32px' }}>
            <Card title="Review Findings" icon={AlertTriangle} style={{ flex: 1 }}>
              <AnimatePresence>
                {findingsList.length === 0 ? (
                  <div style={{ padding: '40px', textAlign: 'center', color: T.textMuted, fontSize: '13px' }}>
                    No issues found yet...
                  </div>
                ) : (
                  findingsList.map((f: any, i: number) => {
                    const severityColor = f?.severity === 'high' ? T.danger :
                                         f?.severity === 'low' ? T.textFaint : T.warn;
                    return (
                      <motion.div
                        key={i}
                        initial={{ opacity: 0, scale: 0.95 }}
                        animate={{ opacity: 1, scale: 1 }}
                        layout
                        style={{
                          padding: '16px', background: T.innerPanel,
                          border: `1px solid ${T.innerBorder}`, borderRadius: '12px',
                          borderLeft: `4px solid ${severityColor}`, marginBottom: '12px'
                        }}
                      >
                        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '8px' }}>
                          <div style={{ fontFamily: 'monospace', color: T.accent, fontSize: '12px' }}>
                            {f?.file?.split('/').pop()}:{f?.line}
                          </div>
                          <div style={{
                            fontSize: '10px', textTransform: 'uppercase', padding: '2px 8px',
                            borderRadius: '10px', background: 'rgba(15, 23, 42, 0.04)', color: T.textMuted, letterSpacing: '0.5px'
                          }}>
                            {f?.category}
                          </div>
                        </div>
                        <div style={{ fontSize: '13px', marginBottom: '10px', lineHeight: '1.5', color: T.text }}>
                          {f?.message}
                        </div>
                        {f?.suggestion && (
                          <div style={{
                            fontSize: '12px', color: T.text, background: '#f8fafc',
                            padding: '10px', borderRadius: '8px', borderLeft: `2px solid ${T.cardBorder}`
                          }}>
                            {f.suggestion}
                          </div>
                        )}
                      </motion.div>
                    );
                  })
                )}
              </AnimatePresence>
            </Card>

            <Card title="LLM Network Trace" icon={Server}>
              {(llmCalls || []).slice(-50).reverse().map((call: any, i: number) => {
                const id = call.id || String(i);
                const isExpanded = !!expandedCalls[id];
                const statusColor = call.status === 'failed' ? T.danger : call.status === 'done' ? T.success : T.textFaint;
                return (
                  <div key={id} style={{
                    background: T.innerPanel,
                    border: `1px solid ${T.innerBorder}`,
                    borderRadius: '8px',
                    fontSize: '12px', marginBottom: '8px', overflow: 'hidden'
                  }}>
                    <div
                      onClick={() => toggleExpand(id)}
                      style={{
                        padding: '10px 14px',
                        cursor: 'pointer',
                        userSelect: 'none',
                        transition: 'background 0.15s',
                      }}
                      onMouseEnter={(e) => { (e.currentTarget as HTMLDivElement).style.background = 'rgba(15, 23, 42, 0.03)'; }}
                      onMouseLeave={(e) => { (e.currentTarget as HTMLDivElement).style.background = 'transparent'; }}
                    >
                      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '4px', alignItems: 'center' }}>
                        <span style={{ fontWeight: 500, color: T.text, display: 'flex', alignItems: 'center', gap: '6px' }}>
                          <span style={{
                            display: 'inline-block',
                            transform: isExpanded ? 'rotate(90deg)' : 'rotate(0deg)',
                            transition: 'transform 0.15s',
                            color: T.textFaint,
                            fontSize: '10px',
                          }}>▶</span>
                          {call.agent || 'unknown'}
                        </span>
                        <span style={{ fontFamily: 'monospace', color: statusColor }}>
                          {call.status}
                        </span>
                      </div>
                      <div style={{ display: 'flex', justifyContent: 'space-between', color: T.textMuted, fontSize: '11px', paddingLeft: '16px' }}>
                        <span>{call.model || 'unknown'}</span>
                        <span>{(call.promptChars || 0)} in → {(call.responseChars || 0)} out</span>
                      </div>
                    </div>
                    <AnimatePresence>
                      {isExpanded && (
                        <motion.div
                          initial={{ height: 0, opacity: 0 }}
                          animate={{ height: 'auto', opacity: 1 }}
                          exit={{ height: 0, opacity: 0 }}
                          transition={{ duration: 0.2 }}
                          style={{ overflow: 'hidden', borderTop: `1px solid ${T.innerBorder}` }}
                        >
                          <div style={{ padding: '12px 14px', display: 'flex', flexDirection: 'column', gap: '10px' }}>
                            <div>
                              <div style={{
                                fontSize: '10px', textTransform: 'uppercase', letterSpacing: '0.1em',
                                color: T.accent, fontWeight: 600, marginBottom: '4px'
                              }}>
                                Prompt
                              </div>
                              <pre style={{
                                margin: 0, padding: '10px', background: '#f8fafc',
                                border: `1px solid ${T.innerBorder}`,
                                borderRadius: '6px', fontSize: '11px', color: T.text,
                                fontFamily: 'monospace', whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                                maxHeight: '240px', overflow: 'auto',
                              }}>
                                {call.prompt || '(no prompt captured)'}
                              </pre>
                            </div>
                            <div>
                              <div style={{
                                fontSize: '10px', textTransform: 'uppercase', letterSpacing: '0.1em',
                                color: call.status === 'failed' ? T.danger : T.success,
                                fontWeight: 600, marginBottom: '4px'
                              }}>
                                {call.status === 'failed' ? 'Error' : 'Response'}
                              </div>
                              <pre style={{
                                margin: 0, padding: '10px', background: '#f8fafc',
                                border: `1px solid ${T.innerBorder}`,
                                borderRadius: '6px', fontSize: '11px', color: T.text,
                                fontFamily: 'monospace', whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                                maxHeight: '240px', overflow: 'auto',
                              }}>
                                {call.status === 'failed'
                                  ? (call.error || '(no error message)')
                                  : (call.response || (call.status === 'pending' ? '(awaiting response...)' : '(empty response)'))}
                              </pre>
                            </div>
                          </div>
                        </motion.div>
                      )}
                    </AnimatePresence>
                  </div>
                );
              })}
              {(llmCalls || []).length === 0 && (
                <div style={{ textAlign: 'center', color: T.textMuted, fontSize: '13px', padding: '20px' }}>
                  Waiting for network activity...
                </div>
              )}
            </Card>
          </div>
        </div>
      </main>
    </div>
  );
}