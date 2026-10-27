import { useCallback, useEffect, useState } from 'react';
import AuthPage from './components/AuthPage.jsx';
import Header from './components/Header.jsx';
import CustomerView from './components/CustomerView.jsx';
import AgentInbox from './components/AgentInbox.jsx';
import TicketThread from './components/TicketThread.jsx';
import { ErrorBanner } from './components/Ui.jsx';
import { api } from './api.js';

// Only non-sensitive display data is cached (never a token: the session lives
// in an HttpOnly cookie the page cannot read).
const STORAGE_KEY = 'ticket_web_user';

function readStoredUser() {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || !parsed.email) return null;
    return {
      email: parsed.email,
      display_name: parsed.display_name || '',
      role: parsed.role === 'agent' ? 'agent' : 'customer',
    };
  } catch (err) {
    return null;
  }
}

function storeUser(user) {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(user));
  } catch (err) {
    // Storage may be unavailable; the session still works for this page load.
  }
}

function dropStoredUser() {
  try {
    window.localStorage.removeItem(STORAGE_KEY);
  } catch (err) {
    // Ignore: nothing sensitive is kept there anyway.
  }
}

export default function App() {
  const [user, setUser] = useState(readStoredUser);
  const [route, setRoute] = useState({ name: 'home' });
  const [healthError, setHealthError] = useState('');
  const [sessionMessage, setSessionMessage] = useState('');

  const checkHealth = useCallback(async () => {
    setHealthError('');
    try {
      await api.health();
    } catch (err) {
      setHealthError(err.message);
    }
  }, []);

  useEffect(() => {
    checkHealth();
  }, [checkHealth]);

  // A 401 from any request drops the local session and returns to the login page.
  useEffect(() => {
    function handleExpired() {
      dropStoredUser();
      setUser(null);
      setRoute({ name: 'home' });
      setSessionMessage('نشست شما منقضی شده است؛ لطفاً دوباره وارد شوید.');
    }
    window.addEventListener('auth:expired', handleExpired);
    return () => window.removeEventListener('auth:expired', handleExpired);
  }, []);

  const handleAuth = useCallback(
    (nextUser) => {
      storeUser(nextUser);
      setUser(nextUser);
      setRoute({ name: 'home' });
      setSessionMessage('');
      checkHealth();
    },
    [checkHealth]
  );

  const handleLogout = useCallback(async () => {
    try {
      await api.logout();
    } catch (err) {
      // Even if the server is unreachable, drop the local session.
    }
    dropStoredUser();
    setUser(null);
    setRoute({ name: 'home' });
    setSessionMessage('');
    setHealthError('');
  }, []);

  const openTicket = useCallback((id) => setRoute({ name: 'ticket', id }), []);
  const goHome = useCallback(() => setRoute({ name: 'home' }), []);

  let content;
  if (!user) {
    content = <AuthPage onAuth={handleAuth} />;
  } else if (route.name === 'ticket' && route.id) {
    content = <TicketThread ticketId={route.id} role={user.role} onBack={goHome} />;
  } else if (user.role === 'agent') {
    content = <AgentInbox onOpenTicket={openTicket} />;
  } else {
    content = <CustomerView onOpenTicket={openTicket} />;
  }

  return (
    <div className="app">
      <Header user={user} onLogout={handleLogout} />
      <main className="main">
        <ErrorBanner message={healthError} onRetry={checkHealth} />
        <ErrorBanner message={sessionMessage} />
        {content}
      </main>
      <footer className="footer">سامانه تیکت پشتیبانی — محیط توسعه</footer>
    </div>
  );
}
