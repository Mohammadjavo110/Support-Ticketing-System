export default function Header({ user, onLogout }) {
  return (
    <header className="header">
      <div className="header__brand">
        <span className="header__logo" aria-hidden="true">
          ت
        </span>
        <h1 className="header__title">سامانه تیکت پشتیبانی</h1>
      </div>
      {user ? (
        <div className="header__user">
          <span className="header__name">{user.display_name || user.email}</span>
          <span className="header__role">{user.role === 'agent' ? 'کارشناس' : 'مشتری'}</span>
          <button type="button" className="btn btn--ghost btn--sm" onClick={onLogout}>
            خروج
          </button>
        </div>
      ) : null}
    </header>
  );
}
