import { NavLink } from 'react-router-dom';
import { LayoutDashboard, ScrollText, ShieldAlert } from 'lucide-react';
import { clsx } from 'clsx';
import { twMerge } from 'tailwind-merge';

const navItems = [
  { name: 'Overview', to: '/', icon: LayoutDashboard },
  { name: 'Audit Trail', to: '/audit', icon: ScrollText },
  { name: 'Policy Tiers', to: '/policy', icon: ShieldAlert },
];

export function Sidebar() {
  return (
    <aside className="w-64 border-r border-border bg-card flex flex-col h-screen overflow-y-auto">
      <div className="p-6 border-b border-border flex items-center gap-3">
        <div className="w-8 h-8 rounded bg-primary flex items-center justify-center font-bold text-on-primary">
          DB
        </div>
        <div>
          <h1 className="font-bold text-lg leading-tight">Deadbolt</h1>
          <p className="text-xs text-muted-foreground uppercase tracking-wider">Ops Dashboard</p>
        </div>
      </div>
      
      <nav className="flex-1 p-4 space-y-2">
        {navItems.map((item) => (
          <NavLink
            key={item.name}
            to={item.to}
            className={({ isActive }) =>
              twMerge(
                clsx(
                  "flex items-center gap-3 px-3 py-2 rounded-md text-sm font-medium transition-colors focus:outline-none focus:ring-2 focus:ring-ring",
                  isActive 
                    ? "bg-secondary text-foreground" 
                    : "text-muted-foreground hover:bg-muted hover:text-foreground"
                )
              )
            }
          >
            <item.icon className="w-5 h-5" />
            {item.name}
          </NavLink>
        ))}
      </nav>
      
      <div className="p-4 border-t border-border text-xs text-muted-foreground font-mono">
        System: ACTIVE <br/>
        Last sync: 2m ago
      </div>
    </aside>
  );
}
