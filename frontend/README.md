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

## Backend transport

The dashboard always uses the HTTP client in \`src/lib/api.ts\`; there is no in-memory mock fallback.
Set \`VITE_API_BASE\` to the API Function URL (including \`/api\`). The client sends the Cognito
ID-token bearer from the current browser session when authenticated, applies an eight-second timeout,
and retries network failures once. Backend responses must use the stable \`data\`/\`error\` envelope.

## Core Metaphors Implemented
- **Pipeline View**: The finding detail screen shows drift as a CI/CD-style pipeline (Detected → Scored → Planned → Approval → Executing → Verified/Rolled back).
- **Deterministic Drift Review**: Clicking "Re-run Engine" in the Finding Detail screen simulates the "same input, same plan, every time" workflow by showing the exact same plan hash being produced.
- **Audit Trail**: The \`/audit\` route provides a dense, terminal-style view representing the OpenTelemetry trace logs. 
- **Security-Ops Dashboard**: High contrast, dark-mode first design adhering to the provided design system tokens.
