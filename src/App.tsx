import React from 'react';
import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom';
import LoginPage from './LoginPage.tsx';
import Dashboard from './Dashboard.tsx';

const getAuthState = () => {
  if (typeof window === 'undefined') {
    return { isLoggedIn: false, username: '' };
  }

  try {
    const stored = localStorage.getItem('stockAuth');
    if (!stored) {
      return { isLoggedIn: false, username: '' };
    }

    const parsed = JSON.parse(stored);
    return {
      isLoggedIn: parsed?.isLoggedIn === true,
      username: typeof parsed?.username === 'string' ? parsed.username : '',
    };
  } catch {
    return { isLoggedIn: false, username: '' };
  }
};

const ProtectedRoute: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { isLoggedIn } = getAuthState();
  return isLoggedIn ? <>{children}</> : <Navigate to="/login" replace />;
};

const PublicRoute: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { isLoggedIn } = getAuthState();
  return isLoggedIn ? <Navigate to="/dashboard" replace /> : <>{children}</>;
};

const App: React.FC = () => {
  const { isLoggedIn } = getAuthState();

  return (
    <Router>
      <Routes>
        <Route path="/" element={<Navigate to={isLoggedIn ? '/dashboard' : '/login'} replace />} />
        <Route path="/login" element={<PublicRoute><LoginPage /></PublicRoute>} />
        <Route path="/dashboard" element={<ProtectedRoute><Dashboard /></ProtectedRoute>} />
        <Route path="*" element={<Navigate to={isLoggedIn ? '/dashboard' : '/login'} replace />} />
      </Routes>
    </Router>
  );
};

export default App;