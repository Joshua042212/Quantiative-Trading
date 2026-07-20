import { useEffect, useState } from 'react';
import type { CSSProperties, FC, FormEvent } from 'react';
import { useNavigate } from 'react-router-dom';

const getStoredUsername = () => {
  if (typeof window === 'undefined') return '';

  try {
    const stored = localStorage.getItem('stockAuth');
    if (!stored) return '';
    const parsed = JSON.parse(stored);
    return typeof parsed?.username === 'string' ? parsed.username : '';
  } catch {
    return '';
  }
};

const LoginPage: FC = () => {
  const [username, setUsername] = useState(getStoredUsername);
  const [password, setPassword] = useState('');
  const [errorMsg, setErrorMsg] = useState('');
  const navigate = useNavigate();

  useEffect(() => {
    const storedUsername = getStoredUsername();
    if (storedUsername) {
      setUsername(storedUsername);
    }
  }, []);

  const handleLogin = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();

    if (username === 'account' && password === 'password') {
      setErrorMsg('');
      localStorage.setItem('stockAuth', JSON.stringify({ isLoggedIn: true, username }));
      navigate('/dashboard');
    } else {
      setErrorMsg('帳號或密碼錯誤，請重試！');
    }
  };

  return (
    <div style={styles.container}>
      <div style={styles.card}>
        <h2>量化交易系統登入</h2>
        <form onSubmit={handleLogin} style={styles.form}>
          <div style={styles.inputGroup}>
            <label>帳號：</label>
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="輸入 account"
              style={styles.input}
            />
          </div>
          <div style={styles.inputGroup}>
            <label>密碼：</label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="輸入 password"
              style={styles.input}
            />
          </div>
          {errorMsg && <p style={{ color: 'red' }}>{errorMsg}</p>}
          <button type="submit" style={styles.button}>登入</button>
        </form>
      </div>
    </div>
  );
};

const styles = {
  container: { display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100vh', backgroundColor: '#f0f2f5' },
  card: { padding: '40px', backgroundColor: 'white', borderRadius: '8px', boxShadow: '0 4px 12px rgba(0,0,0,0.1)', width: '300px' },
  form: { display: 'flex', flexDirection: 'column', gap: '15px', marginTop: '20px' },
  inputGroup: { display: 'flex', flexDirection: 'column', gap: '5px' },
  input: { padding: '8px', borderRadius: '4px', border: '1px solid #ccc' },
  button: { padding: '10px', backgroundColor: '#1890ff', color: 'white', border: 'none', borderRadius: '4px', cursor: 'pointer', fontSize: '16px' }
} satisfies Record<string, CSSProperties>;

export default LoginPage;