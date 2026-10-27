import { useState } from 'react';
import { api } from '../api.js';
import { ErrorBanner } from './Ui.jsx';

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function pickUser(data, fallback) {
  const src = (data && (data.user || data.account || data)) || {};
  return {
    email: src.email || fallback.email || '',
    display_name: src.display_name || src.displayName || fallback.display_name || '',
    role: src.role || fallback.role || null,
  };
}

/**
 * The login endpoint only sets an HttpOnly cookie, so the role may be missing
 * from the response. In that case probe the agent-only inbox: a 2xx answer
 * means the session belongs to an agent, a 4xx means it belongs to a customer.
 */
async function resolveUser(data, fallback) {
  const user = pickUser(data, fallback);
  if (user.role === 'agent' || user.role === 'customer') return user;
  try {
    await api.inbox();
    user.role = 'agent';
  } catch (err) {
    const httpStatus = typeof err.status === 'number' ? err.status : 0;
    user.role = httpStatus >= 400 && httpStatus < 500 ? 'customer' : (fallback.role || 'customer');
  }
  return user;
}

export default function AuthPage({ onAuth }) {
  const [mode, setMode] = useState('login');
  const [form, setForm] = useState({
    email: '',
    password: '',
    display_name: '',
    role: 'customer',
  });
  const [error, setError] = useState('');
  const [fieldError, setFieldError] = useState('');
  const [busy, setBusy] = useState(false);

  function update(field) {
    return (event) => setForm((prev) => ({ ...prev, [field]: event.target.value }));
  }

  function switchMode(next) {
    setMode(next);
    setError('');
    setFieldError('');
  }

  function validate() {
    const email = form.email.trim();
    if (!email) return 'ایمیل الزامی است.';
    if (!EMAIL_RE.test(email)) return 'قالب ایمیل معتبر نیست.';
    if (!form.password) return 'رمز عبور الزامی است.';
    if (form.password.length < 6) return 'رمز عبور باید حداقل ۶ نویسه باشد.';
    if (mode === 'register' && !form.display_name.trim()) return 'نام نمایشی الزامی است.';
    return '';
  }

  async function submit(event) {
    event.preventDefault();
    const problem = validate();
    if (problem) {
      setFieldError(problem);
      return;
    }
    setFieldError('');
    setError('');
    setBusy(true);

    const email = form.email.trim();
    const displayName = form.display_name.trim();

    try {
      let data;
      if (mode === 'login') {
        data = await api.login({ email, password: form.password });
      } else {
        data = await api.register({
          email,
          password: form.password,
          display_name: displayName,
          role: form.role,
        });
      }
      const user = await resolveUser(data, {
        email,
        display_name: displayName,
        role: mode === 'register' ? form.role : undefined,
      });
      onAuth(user);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth">
      <section className="card auth__card" aria-labelledby="auth-title">
        <h2 className="auth__title" id="auth-title">
          سامانه تیکت پشتیبانی
        </h2>
        <p className="auth__subtitle">
          برای ادامه وارد شوید یا در سامانه ثبت‌نام کنید
        </p>

        <div className="tabs" role="group" aria-label="حالت حساب">
          <button
            type="button"
            className={mode === 'login' ? 'tab tab--active' : 'tab'}
            aria-pressed={mode === 'login'}
            onClick={() => switchMode('login')}
          >
            ورود
          </button>
          <button
            type="button"
            className={mode === 'register' ? 'tab tab--active' : 'tab'}
            aria-pressed={mode === 'register'}
            onClick={() => switchMode('register')}
          >
            ثبت‌نام
          </button>
        </div>

        <ErrorBanner message={error} />

        <form onSubmit={submit} noValidate>
          <div className="field">
            <label htmlFor="auth-email">ایمیل</label>
            <input
              id="auth-email"
              name="email"
              type="email"
              dir="ltr"
              autoComplete="email"
              value={form.email}
              onChange={update('email')}
              placeholder="you@example.com"
            />
          </div>

          <div className="field">
            <label htmlFor="auth-password">رمز عبور</label>
            <input
              id="auth-password"
              name="password"
              type="password"
              dir="ltr"
              autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
              value={form.password}
              onChange={update('password')}
              placeholder="••••••"
            />
          </div>

          {mode === 'register' ? (
            <div className="field">
              <label htmlFor="auth-name">نام نمایشی</label>
              <input
                id="auth-name"
                name="display_name"
                type="text"
                autoComplete="name"
                value={form.display_name}
                onChange={update('display_name')}
                placeholder="مثلاً سارا محمدی"
              />
            </div>
          ) : null}

          <fieldset className="field">
            <legend>نقش شما</legend>
            <div className="role-toggle">
              <label className={form.role === 'customer' ? 'seg seg--active' : 'seg'}>
                <input
                  type="radio"
                  name="role"
                  value="customer"
                  checked={form.role === 'customer'}
                  onChange={update('role')}
                />
                مشتری
              </label>
              <label className={form.role === 'agent' ? 'seg seg--active' : 'seg'}>
                <input
                  type="radio"
                  name="role"
                  value="agent"
                  checked={form.role === 'agent'}
                  onChange={update('role')}
                />
                کارشناس
              </label>
            </div>
          </fieldset>

          {fieldError ? (
            <p className="field-error" role="alert">
              {fieldError}
            </p>
          ) : null}

          <button type="submit" className="btn btn--primary btn--block" disabled={busy}>
            {busy ? 'در حال ارسال…' : mode === 'login' ? 'ورود' : 'ثبت‌نام'}
          </button>
        </form>
      </section>
    </div>
  );
}
