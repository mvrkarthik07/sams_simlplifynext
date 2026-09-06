import { useEffect, useState } from 'react';
import { NavLink } from 'react-router-dom';
import { Cable, LayoutDashboard, Moon, ScrollText, ShieldAlert, Sun } from 'lucide-react';
import { clsx } from 'clsx';
import { twMerge } from 'tailwind-merge';
import { clearSession, isAuthConfigured } from '../lib/auth';
import { applyTheme, getInitialTheme, type Theme } from '../lib/theme';

const navItems = [
  { name: 'Overview', to: '/', icon: LayoutDashboard },
  { name: 'Audit Trail', to: '/audit', icon: ScrollText },
  { name: 'Policy Tiers', to: '/policy', icon: ShieldAlert },
  { name: 'Connections', to: '/connections', icon: Cable },
];

export function Sidebar() {
  const [theme, setTheme] = useState<Theme>(() => getInitialTheme());

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  return (
    <aside className="flex h-screen w-16 md:w-64 shrink-0 flex-col overflow-y-auto border-r border-border bg-card">
      <div className="brand-lockup">
        <img src="/brand/logo-tile.svg" alt="Deadbolt" className="brand-logo" />
        <div className="sidebar-label">
          <h1 className="font-semibold text-lg leading-tight">Deadbolt</h1>
          <p className="text-xs text-muted-foreground">Access governance</p>
        </div>
      </div>

      <nav className="flex-1 space-y-1 p-2 md:p-4" aria-label="Primary navigation">
        {navItems.map((item) => (
          <NavLink
            key={item.name}
            to={item.to}
            className={({ isActive }) =>
              twMerge(
                clsx(
                  "flex items-center gap-3 px-3 py-2 text-sm font-medium transition-colors focus:outline-none focus:ring-2 focus:ring-ring",
                  isActive
                    ? "bg-secondary text-foreground"
                    : "text-muted-foreground hover:bg-muted hover:text-foreground"
                )
              )
            }
          >
            <item.icon className="w-5 h-5 shrink-0" aria-hidden="true" />
            <span className="sidebar-label">{item.name}</span>
          </NavLink>
        ))}
      </nav>

      <div className="border-t border-border p-2 md:p-4 text-xs text-muted-foreground">
        <p className="font-mono sidebar-label">System active</p>
        <p className="mt-1 sidebar-label">Owner-configured source</p>
        <button
          type="button"
          className="theme-toggle"
          onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
          aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}
        >
          {theme === 'dark' ? <Sun size={14} aria-hidden="true" /> : <Moon size={14} aria-hidden="true" />}
          <span className="sidebar-label">{theme === 'dark' ? 'Light mode' : 'Dark mode'}</span>
        </button>
        {isAuthConfigured && (
          <button type="button" className="mt-4 text-left font-semibold text-foreground underline sidebar-label" onClick={() => { clearSession(); window.location.reload(); }}>
            Sign out
          </button>
        )}
      </div>
    </aside>
  );
}
