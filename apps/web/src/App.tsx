import { useEffect, useState } from 'react';
import { fetchHealth } from './health.ts';
import './styles.css';

export function App() {
  const [apiState, setApiState] = useState('checking');

  useEffect(() => {
    const controller = new AbortController();
    fetchHealth(controller.signal)
      .then((status) => setApiState(status))
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === 'AbortError') return;
        setApiState('unavailable');
      });
    return () => controller.abort();
  }, []);

  return (
    <main>
      <p className="eyebrow">Development workspace</p>
      <h1>Labelloop</h1>
      <p>Multi-domain image annotation with reproducible dataset compilation.</p>
      <p role="status">API status: {apiState}</p>
      <footer>
        <a href="https://github.com/TogetherWeOwn/labelloop">AGPL-3.0 source code</a>
      </footer>
    </main>
  );
}
