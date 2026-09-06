import { useEffect, useState } from 'react';
import { NavLink } from 'react-router-dom';
import Cable from 'lucide-react/dist/esm/icons/cable.mjs';
import LayoutDashboard from 'lucide-react/dist/esm/icons/layout-dashboard.mjs';
import Moon from 'lucide-react/dist/esm/icons/moon.mjs';
import ScrollText from 'lucide-react/dist/esm/icons/scroll-text.mjs';
import ShieldAlert from 'lucide-react/dist/esm/icons/shield-alert.mjs';
import Sun from 'lucide-react/dist/esm/icons/sun.mjs';
import Menu from 'lucide-react/dist/esm/icons/menu.mjs';
import X from 'lucide-react/dist/esm/icons/x.mjs';
import PanelLeftClose from 'lucide-react/dist/esm/icons/panel-left-close.mjs';
import PanelLeftOpen from 'lucide-react/dist/esm/icons/panel-left-open.mjs';
import LockKeyhole from 'lucide-react/dist/esm/icons/lock-keyhole.mjs';
import { clearSession, isAuthConfigured } from '../lib/auth';
import { applyTheme, getInitialTheme, type Theme } from '../lib/theme';
const navItems = [
  { name: 'Overview', to: '/', icon: LayoutDashboard },
  { name: 'Audit', to: '/audit', icon: ScrollText },
  { name: 'Policy', to: '/policy', icon: ShieldAlert },
  { name: 'Connections', to: '/connections', icon: Cable },
];
export function Sidebar() {
  const [theme, setTheme] = useState<Theme>(getInitialTheme);
  const [collapsed, setCollapsed] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  useEffect(() => {
    applyTheme(theme);
  }, [theme]);
  return (
    <aside
      className={`app-sidebar register-sidebar${collapsed ? ' sidebar-collapsed' : ''}${menuOpen ? ' menu-open' : ''}`}
      onKeyDown={(event) => {
        if (event.key === 'Escape') {
          setMenuOpen(false);
          document.getElementById('mobile-menu-button')?.focus();
        }
      }}
    >
      <div className="register-brand">
        <button
          id="mobile-menu-button"
          className="register-icon-button mobile-menu-button"
          onClick={() => setMenuOpen(!menuOpen)}
          aria-label={menuOpen ? 'Close navigation' : 'Open navigation'}
          aria-expanded={menuOpen}
          aria-controls="primary-nav"
        >
          {menuOpen ? (
            <X size={16} aria-hidden="true" />
          ) : (
            <Menu size={16} aria-hidden="true" />
          )}
        </button>
        <LockKeyhole size={18} className="brand-symbol" aria-hidden="true" />
        <span className="sidebar-label">Deadbolt</span>
        <button
          className="register-icon-button collapse-button"
          onClick={() => setCollapsed(!collapsed)}
          aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        >
          {collapsed ? (
            <PanelLeftOpen size={16} aria-hidden="true" />
          ) : (
            <PanelLeftClose size={16} aria-hidden="true" />
          )}
        </button>
      </div>
      <nav
        id="primary-nav"
        className="register-nav"
        aria-label="Primary navigation"
      >
        {navItems.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.to === '/'}
            className="register-nav-link"
            title={item.name}
            onClick={() => setMenuOpen(false)}
          >
            <item.icon size={16} aria-hidden="true" />
            <span className="sidebar-label">{item.name}</span>
          </NavLink>
        ))}
      </nav>
      <div className="register-sidebar-footer">
        <button
          className="register-icon-button theme-preference"
          onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
          aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}
          title={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}
        >
          {theme === 'dark' ? (
            <Sun size={16} aria-hidden="true" />
          ) : (
            <Moon size={16} aria-hidden="true" />
          )}
        </button>
        {isAuthConfigured ? (
          <button
            className="register-button signout"
            onClick={() => {
              clearSession();
              window.location.reload();
            }}
          >
            Sign out
          </button>
        ) : null}
      </div>
    </aside>
  );
}
