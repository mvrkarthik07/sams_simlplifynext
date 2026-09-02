# Deadbolt Frontend

This is the frontend component for Deadbolt, an autonomous entitlement-drift detection and brokered-revocation system for SaaS estates. Built for the SimplifyNext IGNITE Agentic AI Hackathon 2026.

## Stack
- React 18
- Vite
- TypeScript
- Tailwind CSS
- Lucide Icons
- React Router

## Development Setup

1. Install dependencies:
   \`\`\`bash
   npm install
   \`\`\`

2. Start the development server:
   \`\`\`bash
   npm run dev
   \`\`\`

## Swapping the Mock Data Layer for Real APIs

The frontend currently uses a deterministic mock data layer located at \`src/lib/api.ts\`. This isolates the frontend from backend changes during development. 

To swap to the live backend:
1. Update \`src/lib/api.ts\` to perform real \`fetch()\` or \`axios\` calls to your AWS API Gateway / Lambda Function URLs instead of using the local \`generateSeedData()\` mock.
2. Ensure the backend returns JSON that identically matches the \`Entitlement\`, \`Finding\`, \`Plan\`, and \`AuditLogEntry\` interfaces defined in \`src/lib/types.ts\`.

Because the entire application reads from the \`api\` object exported from \`src/lib/api.ts\`, you only need to modify this single file to connect the real backend. The UI components will function normally as long as the data contract is respected.

## Core Metaphors Implemented
- **Pipeline View**: The finding detail screen shows drift as a CI/CD-style pipeline (Detected → Scored → Planned → Approval → Executing → Verified/Rolled back).
- **Deterministic Drift Demo**: Clicking "Re-run Engine" in the Finding Detail screen simulates the "same input, same plan, every time" demo beat by showing the exact same plan hash being produced.
- **Audit Trail**: The \`/audit\` route provides a dense, terminal-style view representing the OpenTelemetry trace logs. 
- **Security-Ops Dashboard**: High contrast, dark-mode first design adhering to the provided design system tokens.
