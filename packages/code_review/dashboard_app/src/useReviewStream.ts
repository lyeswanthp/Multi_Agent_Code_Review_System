import { useState, useEffect, useRef } from 'react';

export interface DashboardState {
  connected: boolean;
  startTime: number;
  phases: Record<string, { status: string; detail: string; startTime: number; endTime: number | null }>;
  agents: Record<string, { status: string; filesReviewed: number; filesTotal: number; charsProcessed: number; startTime: number; endTime: number | null }>;
  files: Array<{ path: string; chars: number }>;
  diffFiles: Array<{ path: string; oldLines: number; newLines: number; oldChars: number; newChars: number; oldCode: string; newCode: string }>;
  agentFiles: Record<string, Array<{ path: string; chars: number }>>;
  activeFile: Record<string, { path: string; index: number; total: number; error?: string }>;
  fileErrors: Array<{ agent: string; path: string; error: string }>;
  llmCalls: Array<{ id: string; agent: string; model: string; promptChars: number; prompt: string; responseChars: number; response?: string; status: string; error?: string; startTime: number; endTime: number | null }>;
  findings: Array<any>;
}

const initialState: DashboardState = {
  connected: false,
  startTime: Date.now(),
  phases: {},
  agents: {},
  files: [],
  diffFiles: [],
  agentFiles: {},
  activeFile: {},
  fileErrors: [],
  llmCalls: [],
  findings: []
};

export function useReviewStream() {
  const [state, setState] = useState<DashboardState>(initialState);
  const esRef = useRef<EventSource | null>(null);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reconnectAttempts = useRef(0);
  const maxReconnectDelay = 5000;
  const listenersRef = useRef<Array<{ type: string; listener: (e: any) => void }>>([]);

  useEffect(() => {
    console.log('[useReviewStream] Mounting, connecting to SSE...');

    const handleEvent = (kind: string, data: any) => {
      console.log('[useReviewStream] Event:', kind, data);
      setState(s => {
        const next = { ...s };
        next.phases = { ...s.phases };
        next.agents = { ...s.agents };
        next.activeFile = { ...s.activeFile };

        const ts = Date.now();

        switch(kind) {
          case 'phase.start':
            next.phases[data.phase] = { status: 'running', detail: '', startTime: ts, endTime: null };
            break;
          case 'phase.done':
            if (next.phases[data.phase]) {
              next.phases[data.phase] = { ...next.phases[data.phase], status: 'done', detail: data.detail || '', endTime: ts };
            }
            break;
          case 'phase.fail':
            if (next.phases[data.phase]) {
              next.phases[data.phase] = { ...next.phases[data.phase], status: 'failed', detail: data.detail || '', endTime: ts };
            }
            break;
          case 'agent.set':
            (data.agents || []).forEach((a: string) => {
              if (!next.agents[a]) {
                next.agents[a] = { status: 'pending', filesReviewed: 0, filesTotal: 0, charsProcessed: 0, startTime: 0, endTime: null };
              }
            });
            break;
          case 'agent.start':
            next.agents[data.agent] = { status: 'running', filesReviewed: 0, filesTotal: 0, charsProcessed: 0, startTime: ts, endTime: null };
            break;
          case 'agent.done':
            if (next.agents[data.agent]) {
              next.agents[data.agent] = { ...next.agents[data.agent], status: 'done', endTime: ts };
            }
            break;
          case 'agent.fail':
            if (next.agents[data.agent]) {
              next.agents[data.agent] = { ...next.agents[data.agent], status: 'failed', endTime: ts };
            }
            break;
          case 'agent.files':
            next.agentFiles = { ...s.agentFiles, [data.agent]: (data.files || []).map((f: string) => ({
              path: f,
              chars: (data.chars || {})[f] || 0
            }))};
            if (next.agents[data.agent]) {
              next.agents[data.agent] = { ...next.agents[data.agent], filesTotal: data.files.length };
            }
            break;
          case 'agent.file.start':
            next.activeFile[data.agent] = { path: data.file, index: data.index, total: data.total };
            break;
          case 'agent.file.done':
          case 'agent.file.skip':
            if (next.activeFile[data.agent]?.path === data.file) {
              delete next.activeFile[data.agent];
            }
            if (next.agents[data.agent]) {
              next.agents[data.agent] = { ...next.agents[data.agent], filesReviewed: next.agents[data.agent].filesReviewed + 1 };
            }
            if (data.error) {
              next.fileErrors = [...s.fileErrors, { agent: data.agent, path: data.file, error: data.error }];
            }
            break;
          case 'llm.request':
            next.llmCalls = [...s.llmCalls, {
              id: data.id, agent: data.agent, model: data.model,
              promptChars: data.prompt_chars || 0,
              prompt: data.prompt || '',
              responseChars: 0, status: 'pending',
              startTime: ts, endTime: null
            }];
            break;
          case 'llm.response':
            next.llmCalls = s.llmCalls.map(c =>
              c.id === data.id ? { ...c, status: 'done', responseChars: data.response_chars, response: data.response, endTime: ts } : c
            );
            break;
          case 'llm.error':
            next.llmCalls = s.llmCalls.map(c =>
              c.id === data.id ? { ...c, status: 'failed', error: data.error, endTime: ts } : c
            );
            break;
          case 'diff.file':
            next.diffFiles = [...s.diffFiles, {
              path: data.path, oldLines: data.old_lines, newLines: data.new_lines,
              oldChars: data.old_chars, newChars: data.new_chars,
              oldCode: data.old_code, newCode: data.new_code
            }];
            break;
          case 'file.loaded':
            next.files = [...s.files, { path: data.path, chars: data.chars }];
            break;
          case 'findings':
            next.findings = data.findings || [];
            break;
          case 'agent.finding':
            if (data.finding) {
               next.findings = [...s.findings, data.finding];
            }
            break;
          case 'review.done':
            if (data.findings) next.findings = data.findings;
            break;
        }
        return next;
      });
    };

    const eventTypes = [
      'phase.start', 'phase.done', 'phase.fail',
      'agent.set', 'agent.start', 'agent.done', 'agent.fail',
      'agent.files', 'agent.file.start', 'agent.file.done', 'agent.file.skip',
      'llm.request', 'llm.response', 'llm.error',
      'diff.file', 'file.loaded', 'findings', 'agent.finding', 'review.done'
    ];

    const connectSSE = () => {
      console.log('[useReviewStream] Creating SSE connection...');

      // Clean up previous connection
      if (esRef.current) {
        esRef.current.close();
        esRef.current = null;
      }

      // Remove old listeners
      listenersRef.current.forEach(({ type, listener }) => {
        if (esRef.current) {
          esRef.current.removeEventListener(type, listener);
        }
      });
      listenersRef.current = [];

      const es = new EventSource('/events');
      esRef.current = es;

      es.onopen = () => {
        console.log('[useReviewStream] SSE connected!');
        setState(s => ({ ...s, connected: true, startTime: s.startTime || Date.now() }));
        reconnectAttempts.current = 0;
      };

      es.onerror = () => {
        console.log('[useReviewStream] SSE error, state:', es.readyState);
        setState(s => ({ ...s, connected: false }));

        // Only reconnect if not closed and we haven't exceeded max attempts in a short time
        if (es.readyState !== EventSource.CLOSED) {
          const delay = Math.min(1000 * Math.pow(2, reconnectAttempts.current), maxReconnectDelay);
          reconnectAttempts.current++;

          console.log('[useReviewStream] Reconnecting in', delay, 'ms (attempt', reconnectAttempts.current, ')');

          if (reconnectTimeoutRef.current) {
            clearTimeout(reconnectTimeoutRef.current);
          }

          reconnectTimeoutRef.current = setTimeout(() => {
            if (esRef.current?.readyState !== EventSource.OPEN) {
              connectSSE();
            }
          }, delay);
        }
      };

      // Add event listeners
      eventTypes.forEach(type => {
        const listener = (e: any) => {
          try {
            const data = JSON.parse(e.data);
            handleEvent(type, data);
          } catch(err) {
            console.error('[useReviewStream] Parse error:', err);
          }
        };
        es.addEventListener(type, listener);
        listenersRef.current.push({ type, listener });
      });
    };

    connectSSE();

    // Replay historical events for late-connecting clients
    console.log('[useReviewStream] Fetching history...');
    fetch('/api/history')
      .then(res => {
        console.log('[useReviewStream] History response status:', res.status);
        return res.json();
      })
      .then(events => {
        console.log('[useReviewStream] Got', events.length, 'historical events');
        events.forEach((e: any) => {
          if (e.kind) handleEvent(e.kind, e.data || {});
        });
      })
      .catch(err => {
        console.error('[useReviewStream] History fetch failed:', err);
      });

    return () => {
      console.log('[useReviewStream] Unmounting, cleaning up...');
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
      }
      if (esRef.current) {
        listenersRef.current.forEach(({ type, listener }) => {
          esRef.current?.removeEventListener(type, listener);
        });
        esRef.current.close();
        esRef.current = null;
      }
    };
  }, []);

  return state;
}