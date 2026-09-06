// Lucide ships individual ESM files without matching per-file declarations.
// All per-icon modules share the package's public, fully typed icon contract.
declare module 'lucide-react/dist/esm/icons/*.mjs' {
  import type { LucideIcon } from 'lucide-react';
  const icon: LucideIcon;
  export default icon;
}
