# Deadbolt hackathon deployment

This runbook deploys the fixture-backed dashboard and MCP demo in `us-east-1`. It is designed
for a small budget: schedules are disabled by default, Lambda is on-demand, DynamoDB is
pay-per-request, and logs retain for one day. It does not promote Salesforce, Workday, or
GitHub Enterprise from fixture mode to a live connector. The optional Cognito operator login
is enabled in the deployment command below; provider PATs never enter the browser build.

## 1. Select and verify the hackathon account

Use a dedicated deployment profile or role. Do not use an AWS root profile.

```bash
export AWS_PROFILE=hackathon
export AWS_DEFAULT_REGION=us-east-1
aws sts get-caller-identity
```

Confirm that the returned account ID is the hackathon account before continuing.

## 2. Run the gates and synthesize without deploying

```bash
cd /Users/karthik/sams_simlplifynext/backend
uv sync --all-extras --dev
make verify
make guard

cd infra
npm ci
npm run build
npm test
npx cdk synth
```

The synth must show `SchedulesEnabled: false` and outputs named `ApiBaseUrl`, `McpEndpoint`,
`SpaBucketName`, and `SpaWebsiteUrl`. The six connector credentials are imported as pre-created
SSM SecureStrings; the stack does not put placeholder secrets into CloudFormation.

## 3. Bootstrap and deploy the safe demo stack

Bootstrapping is normally needed once per account and Region:

```bash
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
npx cdk bootstrap "aws://${ACCOUNT_ID}/us-east-1"
npx cdk diff
npx cdk deploy DeadboltStack --require-approval never -c requireAuth=true
```

The default deployment does not enable the hourly snapshot, six-hour budget schedule, or HR event
rule. Do not pass `-c enableSchedules=true` until real connector credentials and the real-provider
Lambda handlers have been reviewed.

## 4. Publish the dashboard against the deployed API

The S3 website asset is built before the API Function URL exists, so publish the final frontend
after the stack returns its outputs:

```bash
API_BASE=$(aws cloudformation describe-stacks --stack-name DeadboltStack \
  --query "Stacks[0].Outputs[?OutputKey=='ApiBaseUrl'].OutputValue" --output text)
MCP_URL=$(aws cloudformation describe-stacks --stack-name DeadboltStack \
  --query "Stacks[0].Outputs[?OutputKey=='McpEndpoint'].OutputValue" --output text)
SPA_BUCKET=$(aws cloudformation describe-stacks --stack-name DeadboltStack \
  --query "Stacks[0].Outputs[?OutputKey=='SpaBucketName'].OutputValue" --output text)
COGNITO_POOL=$(aws cloudformation describe-stacks --stack-name DeadboltStack \
  --query "Stacks[0].Outputs[?OutputKey=='CognitoUserPoolId'].OutputValue" --output text)
COGNITO_CLIENT=$(aws cloudformation describe-stacks --stack-name DeadboltStack \
  --query "Stacks[0].Outputs[?OutputKey=='CognitoClientId'].OutputValue" --output text)

cd ../../frontend
VITE_API_BASE="$API_BASE" \
VITE_AUTH_REQUIRED=1 \
VITE_COGNITO_REGION=us-east-1 \
VITE_COGNITO_USER_POOL_ID="$COGNITO_POOL" \
VITE_COGNITO_CLIENT_ID="$COGNITO_CLIENT" \
npm run build
aws s3 sync dist "s3://${SPA_BUCKET}" --delete

printf 'Dashboard: '
aws cloudformation describe-stacks --stack-name DeadboltStack \
  --query "Stacks[0].Outputs[?OutputKey=='SpaWebsiteUrl'].OutputValue" --output text
printf 'MCP: %s\n' "$MCP_URL"
```

The deployed dashboard and MCP server are fixture-only and intentionally contain no real
connector data. Function URLs remain `NONE` at the AWS transport layer for simple judge access,
but API and MCP requests return `401` unless they carry a valid Cognito ID-token bearer header
when deployed with `-c requireAuth=true`.

The login screen now supports **Create a new account**. New operators enter an email and password,
receive a Cognito confirmation code, and confirm the account in the dashboard. The CLI bootstrap
below is still available for a controlled demo operator:

```bash
aws cognito-idp admin-create-user --user-pool-id "$COGNITO_POOL" \
  --username demo-operator@example.com --temporary-password '<temporary-password>' \
  --message-action SUPPRESS
aws cognito-idp admin-set-user-password --user-pool-id "$COGNITO_POOL" \
  --username demo-operator@example.com --password '<permanent-password>' --permanent
```

Open the dashboard and sign in with that user. For an MCP smoke check, obtain an ID token through
the same Cognito app client and send it as `Authorization: Bearer <ID_TOKEN>`; configure the same
header in the remote MCP client. Do not paste the token into a repository or slide deck.

The authenticated dashboard's **Connections** page accepts GitHub, Salesforce JWT, and Workday
read credentials. Saving writes an operator-scoped SSM SecureString; use **Test read-only** to
verify the provider without any provider mutation. Then use **Scan into dashboard** to explicitly
promote the verified snapshot into the review dataset. Scanning remains read-only.

## 5. Connect and rehearse MCP

Claude Code can connect directly to the HTTPS Function URL:

```bash
claude mcp add --transport http deadbolt "$MCP_URL"
claude mcp list
```

In ChatGPT Developer Mode, add the value of `MCP_URL` as the remote MCP app URL. Confirm that
`list_findings`, `get_plan`, `get_metrics`, and `get_audit_log` work before trying a demo action.
State-changing tools require `confirm=true` and the current plan hash.

## 6. Remove the rehearsal stack

When the demo is finished, destroy the named stack and verify the account has no remaining
Deadbolt resources. Object-Lock records with active retention can prevent bucket deletion until
their retention window ends.

```bash
cd /Users/karthik/sams_simlplifynext/backend/infra
npx cdk destroy DeadboltStack --force
```
