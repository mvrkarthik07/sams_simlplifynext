import { execFileSync } from 'node:child_process';
import * as fs from 'node:fs';
import * as path from 'node:path';
import * as cdk from 'aws-cdk-lib';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import { Duration, RemovalPolicy, Stack, type StackProps } from 'aws-cdk-lib';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as events from 'aws-cdk-lib/aws-events';
import * as targets from 'aws-cdk-lib/aws-events-targets';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as s3deploy from 'aws-cdk-lib/aws-s3-deployment';
import * as scheduler from 'aws-cdk-lib/aws-scheduler';
import * as sfn from 'aws-cdk-lib/aws-stepfunctions';
import * as ssm from 'aws-cdk-lib/aws-ssm';
import { Construct } from 'constructs';
import { brokerDefinition } from './broker-definition.js';

const REGION = 'us-east-1';
const CONNECTORS = ['aws-iam', 'github', 'slack', 'notion', 'salesforce', 'workday'] as const;

export class DeadboltStack extends Stack {
  public readonly graphTable: dynamodb.Table;
  public readonly snapshotBucket: s3.Bucket;
  public readonly preimageBucket: s3.Bucket;
  public readonly auditBucket: s3.Bucket;
  public readonly operatorUserPool: cognito.UserPool;
  public readonly operatorUserPoolClient: cognito.UserPoolClient;
  public readonly mcpOAuthClient: cognito.UserPoolClient;
  public readonly operatorUserPoolDomain: cognito.UserPoolDomain;

  public constructor(scope: Construct, id: string, props?: StackProps) {
    super(scope, id, props);
    if (this.region !== REGION) {
      throw new Error(`Deadbolt must be deployed in ${REGION}`);
    }
    const pythonCode = this.pythonCode();
    const schedulesEnabled = this.schedulesEnabled();
    const authRequired = this.authRequired();
    this.operatorUserPool = new cognito.UserPool(this, 'OperatorUserPool', {
      userPoolName: 'deadbolt-operators',
      selfSignUpEnabled: true,
      autoVerify: { email: true },
      signInAliases: { email: true },
      removalPolicy: RemovalPolicy.DESTROY,
    });
    this.graphTable = new dynamodb.Table(this, 'GraphTable', {
      tableName: 'deadbolt-graph',
      partitionKey: { name: 'PK', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'SK', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      pointInTimeRecovery: false,
      removalPolicy: RemovalPolicy.DESTROY,
    });
    this.graphTable.addGlobalSecondaryIndex({
      indexName: 'GSI1',
      partitionKey: { name: 'GSI1PK', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'GSI1SK', type: dynamodb.AttributeType.STRING },
      projectionType: dynamodb.ProjectionType.ALL,
    });

    this.snapshotBucket = this.lockedBucket('SnapshotsBucket');
    this.preimageBucket = this.lockedBucket('PreimagesBucket');
    this.auditBucket = this.lockedBucket('AuditBucket');
    const spaBucket = new s3.Bucket(this, 'SpaBucket', {
      websiteIndexDocument: 'index.html',
      websiteErrorDocument: 'index.html',
      blockPublicAccess: new s3.BlockPublicAccess({
        blockPublicAcls: false,
        ignorePublicAcls: false,
        blockPublicPolicy: false,
        restrictPublicBuckets: false,
      }),
      removalPolicy: RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
    });
    spaBucket.addToResourcePolicy(new iam.PolicyStatement({
      principals: [new iam.AnyPrincipal()],
      actions: ['s3:GetObject'],
      resources: [spaBucket.arnForObjects('*')],
    }));

    this.operatorUserPoolDomain = this.operatorUserPool.addDomain('OperatorDomain', {
      cognitoDomain: { domainPrefix: `deadbolt-${this.account}` },
    });
    this.operatorUserPoolClient = this.operatorUserPool.addClient('OperatorWebClient', {
      authFlows: { userPassword: true },
      preventUserExistenceErrors: true,
    });
    const mcpCallbackPort = this.mcpOAuthCallbackPort();
    this.mcpOAuthClient = this.operatorUserPool.addClient('McpOAuthClient', {
      generateSecret: false,
      preventUserExistenceErrors: true,
      oAuth: {
        flows: { authorizationCodeGrant: true },
        scopes: [cognito.OAuthScope.OPENID, cognito.OAuthScope.EMAIL],
        callbackUrls: [`http://localhost:${mcpCallbackPort}/callback`],
      },
    });
    this.deploySpa(spaBucket);

    const credentials = CONNECTORS.map((system) => ssm.StringParameter.fromSecureStringParameterAttributes(
      this,
      `${this.idFor(system)}Credential`,
      { parameterName: `/deadbolt/connectors/${system}/credential` },
    ));

    const connectors = this.function('Connectors', 'connectors', 'deadbolt.broker.handler.lambda_handler', pythonCode);
    const driftEngine = this.function('DriftEngine', 'drift-engine', 'deadbolt.broker.handler.lambda_handler', pythonCode);
    const planBuilder = this.function('PlanBuilder', 'plan-builder', 'deadbolt.broker.handler.lambda_handler', pythonCode);
    const executor = this.function('Executor', 'executor', 'deadbolt.broker.handler.lambda_handler', pythonCode);
    const brokerHandler = this.function('BrokerHandler', 'broker-handler', 'deadbolt.broker.handler.lambda_handler', pythonCode);
    const budgetGuard = this.function('BudgetGuard', 'budget-guard', 'budget_guard.handler.lambda_handler', pythonCode);
    const apiHandler = this.function('ApiHandler', 'api', 'deadbolt.handlers.api.lambda_handler', pythonCode);
    const mcpHandler = this.function('McpHandler', 'mcp', 'deadbolt.mcp_lambda.lambda_handler', pythonCode);
    apiHandler.addEnvironment('DEADBOLT_LLM_MODE', 'bedrock');

    this.addLogPolicy(connectors, 'connectors');
    this.addLogPolicy(driftEngine, 'drift-engine');
    this.addLogPolicy(planBuilder, 'plan-builder');
    this.addLogPolicy(executor, 'executor');
    this.addLogPolicy(brokerHandler, 'broker-handler');
    this.addLogPolicy(budgetGuard, 'budget-guard');
    this.addLogPolicy(apiHandler, 'api');
    this.addLogPolicy(mcpHandler, 'mcp');
    this.grantConnectionAccess(apiHandler);
    this.grantRegisterStateAccess(apiHandler);
    this.grantBedrockAccess(apiHandler);
    this.grantConnectionAccess(mcpHandler);

    this.grantConnectorAccess(connectors, credentials, this.snapshotBucket);
    this.grantDriftAccess(driftEngine);
    this.grantPlanAccess(planBuilder);
    this.grantExecutorAccess(executor);
    this.grantBrokerAccess(brokerHandler);
    this.grantBudgetAccess(budgetGuard);

    const brokerUrl = brokerHandler.addFunctionUrl({
      authType: lambda.FunctionUrlAuthType.NONE,
      cors: { allowedOrigins: ['*'], allowedMethods: [lambda.HttpMethod.ALL], allowedHeaders: ['*'] },
    });
    const apiUrl = apiHandler.addFunctionUrl({
      authType: lambda.FunctionUrlAuthType.NONE,
      cors: {
        allowedOrigins: [spaBucket.bucketWebsiteUrl, 'http://localhost:5173', 'http://127.0.0.1:5173'],
        // Lambda Function URLs handle OPTIONS preflight automatically; CloudFormation only
        // accepts the actual methods here.
        allowedMethods: [lambda.HttpMethod.GET, lambda.HttpMethod.POST],
        allowedHeaders: ['content-type', 'authorization'],
      },
    });
    const mcpUrl = mcpHandler.addFunctionUrl({
      authType: lambda.FunctionUrlAuthType.NONE,
      cors: { allowedOrigins: ['*'], allowedMethods: [lambda.HttpMethod.ALL], allowedHeaders: ['content-type', 'authorization'] },
    });

    const stateMachineRole = new iam.Role(this, 'BrokerStateMachineRole', {
      assumedBy: new iam.ServicePrincipal('states.amazonaws.com'),
    });
    stateMachineRole.addToPolicy(new iam.PolicyStatement({
      actions: ['lambda:InvokeFunction'],
      resources: [connectors.functionArn, planBuilder.functionArn, executor.functionArn, brokerHandler.functionArn],
    }));
    const stateMachine = new sfn.StateMachine(this, 'ApprovalBrokerStateMachine', {
      stateMachineType: sfn.StateMachineType.STANDARD,
      role: stateMachineRole,
      definitionBody: sfn.DefinitionBody.fromString(JSON.stringify(brokerDefinition({
        notify: brokerHandler,
        audit: planBuilder,
        executor,
        negotiator: planBuilder,
        pager: brokerHandler,
      }))),
    });

    const hrBus = new events.EventBus(this, 'HrEventBus', { eventBusName: 'deadbolt-hr-events' });
    if (schedulesEnabled) {
      const schedulerRole = new iam.Role(this, 'SnapshotSchedulerRole', {
        assumedBy: new iam.ServicePrincipal('scheduler.amazonaws.com'),
      });
      schedulerRole.addToPolicy(new iam.PolicyStatement({
        actions: ['lambda:InvokeFunction'],
        resources: [connectors.functionArn],
      }));
      new scheduler.CfnSchedule(this, 'HourlySnapshotSchedule', {
        flexibleTimeWindow: { mode: 'OFF' },
        scheduleExpression: 'rate(1 hour)',
        scheduleExpressionTimezone: 'UTC',
        target: { arn: connectors.functionArn, roleArn: schedulerRole.roleArn, input: JSON.stringify({ trigger: 'hourly-snapshot' }) },
      });

      const budgetRule = new events.Rule(this, 'BudgetGuardSchedule', { schedule: events.Schedule.expression('rate(6 hours)') });
      budgetRule.addTarget(new targets.LambdaFunction(budgetGuard));
      const hrRule = new events.Rule(this, 'HrEventRefreshRule', {
        eventBus: hrBus,
        eventPattern: { source: ['deadbolt.hr'], detailType: ['EmployeeChanged'] },
      });
      hrRule.addTarget(new targets.LambdaFunction(connectors, { event: events.RuleTargetInput.fromObject({ trigger: 'hr-event', detail: events.EventField.fromPath('$.detail') }) }));
    }
    new cdk.CfnOutput(this, 'BrokerFunctionUrl', { value: brokerUrl.url });
    new cdk.CfnOutput(this, 'ApiFunctionUrl', { value: apiUrl.url });
    new cdk.CfnOutput(this, 'ApiBaseUrl', { value: `${apiUrl.url}api` });
    new cdk.CfnOutput(this, 'McpEndpoint', { value: `${mcpUrl.url}mcp` });
    new cdk.CfnOutput(this, 'ApprovalBrokerStateMachineArn', { value: stateMachine.stateMachineArn });
    new cdk.CfnOutput(this, 'HrEventBusArn', { value: hrBus.eventBusArn });
    new cdk.CfnOutput(this, 'SpaWebsiteUrl', { value: spaBucket.bucketWebsiteUrl });
    new cdk.CfnOutput(this, 'SpaBucketName', { value: spaBucket.bucketName });
    new cdk.CfnOutput(this, 'SchedulesEnabled', { value: String(schedulesEnabled) });
    new cdk.CfnOutput(this, 'CognitoUserPoolId', { value: this.operatorUserPool.userPoolId });
    new cdk.CfnOutput(this, 'CognitoClientId', { value: this.operatorUserPoolClient.userPoolClientId });
    new cdk.CfnOutput(this, 'CognitoOAuthDomain', { value: this.operatorUserPoolDomain.domainName });
    new cdk.CfnOutput(this, 'McpOAuthClientId', { value: this.mcpOAuthClient.userPoolClientId });
    new cdk.CfnOutput(this, 'McpOAuthCallbackUrl', { value: `http://localhost:${this.mcpOAuthCallbackPort()}/callback` });
    new cdk.CfnOutput(this, 'CognitoRegion', { value: REGION });
    new cdk.CfnOutput(this, 'AuthRequired', { value: String(authRequired) });
  }

  private lockedBucket(id: string): s3.Bucket {
    return new s3.Bucket(this, id, {
      objectLockEnabled: true,
      versioned: true,
      removalPolicy: RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
    });
  }

  private deploySpa(bucket: s3.Bucket): void {
    const repoRoot = path.resolve(__dirname, '../../..');
    const candidates = [path.join(repoRoot, 'frontend', 'dist'), path.join(repoRoot, 'frontend', 'dist')];
    const buildOutput = candidates.find((candidate) => fs.existsSync(candidate));
    const source = buildOutput
      ? s3deploy.Source.asset(buildOutput)
      : s3deploy.Source.data('index.html', '<!doctype html><html><body>Deadbolt</body></html>');
    new s3deploy.BucketDeployment(this, 'SpaDeployment', {
      destinationBucket: bucket,
      sources: [source],
      retainOnDelete: false,
    });
  }

  private function(id: string, name: string, handler: string, code: lambda.Code): lambda.Function {
    return new lambda.Function(this, id, {
      functionName: `deadbolt-${name}`,
      runtime: lambda.Runtime.PYTHON_3_12,
      architecture: lambda.Architecture.ARM_64,
      memorySize: 512,
      timeout: Duration.seconds(30),
      code,
      handler,
      environment: {
        PYTHONPATH: 'src',
        // The handler falls back to fixtures when this optional artifact is absent.
        DEADBOLT_CAPTURE_DIR: 'artifacts/captures',
        DEADBOLT_GRAPH_TABLE_NAME: this.graphTable.tableName,
        DEADBOLT_AUTH_REQUIRED: String(this.authRequired()),
        COGNITO_REGION: REGION,
        COGNITO_USER_POOL_ID: this.operatorUserPool.userPoolId,
        COGNITO_CLIENT_ID: this.operatorUserPoolClient.userPoolClientId,
        COGNITO_OAUTH_DOMAIN: this.operatorUserPoolDomain.domainName.replace(/\.auth\..+$/, ''),
        COGNITO_MCP_CLIENT_ID: this.mcpOAuthClient.userPoolClientId,
      },
      logGroup: new logs.LogGroup(this, `${id}LogGroup`, {
        logGroupName: `/aws/lambda/deadbolt-${name}`,
        retention: logs.RetentionDays.ONE_DAY,
        removalPolicy: RemovalPolicy.DESTROY,
      }),
      role: this.lambdaRole(`${id}Role`),
    });
  }

  private pythonCode(): lambda.Code {
    const backend = path.resolve(__dirname, '../..');
    return lambda.Code.fromAsset(backend, {
      bundling: {
        image: lambda.Runtime.PYTHON_3_12.bundlingImage,
        local: {
          tryBundle: (outputDir: string): boolean => {
            execFileSync('uv', [
              'pip', 'install', '--target', outputDir,
              '--python-platform', 'aarch64-manylinux2014',
              '--python-version', '3.12', '.',
            ], { cwd: backend, stdio: 'inherit' });
            for (const packageName of ['scenarios', 'budget_guard']) {
              fs.cpSync(path.join(backend, packageName), path.join(outputDir, packageName), { recursive: true });
            }
            fs.cpSync(
              path.join(backend, 'tests', 'fixtures', 'scenario'),
              path.join(outputDir, 'tests', 'fixtures', 'scenario'),
              { recursive: true },
            );
            const captures = path.join(backend, 'artifacts', 'captures');
            if (fs.existsSync(captures)) {
              fs.mkdirSync(path.join(outputDir, 'artifacts'), { recursive: true });
              fs.cpSync(captures, path.join(outputDir, 'artifacts', 'captures'), { recursive: true });
            }
            return true;
          },
        },
      },
    });
  }

  private schedulesEnabled(): boolean {
    const contextValue = this.node.tryGetContext('enableSchedules');
    return contextValue === true
      || contextValue === 'true'
      || process.env.DEADBOLT_ENABLE_SCHEDULES === 'true';
  }

  private authRequired(): boolean {
    const contextValue = this.node.tryGetContext('requireAuth');
    return contextValue === true
      || contextValue === 'true'
      || process.env.DEADBOLT_REQUIRE_AUTH === 'true';
  }

  private mcpOAuthCallbackPort(): number {
    const contextValue = this.node.tryGetContext('mcpOAuthCallbackPort');
    const raw = contextValue ?? process.env.DEADBOLT_MCP_OAUTH_CALLBACK_PORT ?? '6274';
    const port = Number(raw);
    if (!Number.isInteger(port) || port < 1024 || port > 65535) {
      throw new Error('mcpOAuthCallbackPort must be an integer between 1024 and 65535');
    }
    return port;
  }

  private lambdaRole(id: string): iam.Role {
    return new iam.Role(this, id, {
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
      description: 'Dedicated least-privilege execution role for one Deadbolt Lambda',
    });
  }

  private idFor(value: string): string {
    return value.split('-').map((part) => part[0].toUpperCase() + part.slice(1)).join('');
  }

  private addLogPolicy(fn: lambda.Function, name: string): void {
    fn.addToRolePolicy(new iam.PolicyStatement({
      actions: ['logs:CreateLogGroup', 'logs:CreateLogStream', 'logs:PutLogEvents'],
      resources: [`arn:aws:logs:${REGION}:${this.account}:log-group:/aws/lambda/deadbolt-${name}:*`],
    }));
  }

  private grantConnectorAccess(fn: lambda.Function, credentials: ssm.IStringParameter[], snapshots: s3.Bucket): void {
    fn.addToRolePolicy(new iam.PolicyStatement({ actions: ['ssm:GetParameter', 'ssm:GetParameters'], resources: credentials.map((item) => item.parameterArn) }));
    fn.addToRolePolicy(new iam.PolicyStatement({ actions: ['dynamodb:BatchWriteItem', 'dynamodb:PutItem'], resources: [this.graphTable.tableArn] }));
    snapshots.grantPut(fn);
    fn.addToRolePolicy(new iam.PolicyStatement({ actions: ['iam:ListUsers', 'iam:ListAttachedUserPolicies', 'iam:ListUserPolicies', 'iam:GetUserPolicy', 'iam:GenerateServiceLastAccessedDetails', 'iam:GetServiceLastAccessedDetails'], resources: [`arn:aws:iam::${this.account}:user/*`] }));
  }

  private grantConnectionAccess(fn: lambda.Function): void {
    fn.addToRolePolicy(new iam.PolicyStatement({
      actions: ['ssm:GetParameter', 'ssm:PutParameter', 'ssm:DeleteParameter'],
      resources: [`arn:aws:ssm:${REGION}:${this.account}:parameter/deadbolt/connections/*`],
    }));
  }

  private grantRegisterStateAccess(fn: lambda.Function): void {
    fn.addToRolePolicy(new iam.PolicyStatement({
      actions: ['dynamodb:GetItem', 'dynamodb:PutItem'],
      resources: [this.graphTable.tableArn],
    }));
  }

  private grantBedrockAccess(fn: lambda.Function): void {
    fn.addToRolePolicy(new iam.PolicyStatement({
      actions: ['bedrock:InvokeModel'],
      resources: [
        `arn:aws:bedrock:${REGION}::foundation-model/amazon.nova-lite-v1:0`,
        `arn:aws:bedrock:${REGION}::foundation-model/anthropic.claude-3-haiku-20240307-v1:0`,
      ],
    }));
  }

  private grantDriftAccess(fn: lambda.Function): void {
    fn.addToRolePolicy(new iam.PolicyStatement({ actions: ['dynamodb:Query', 'dynamodb:GetItem', 'dynamodb:PutItem', 'dynamodb:BatchWriteItem'], resources: [this.graphTable.tableArn, this.graphTable.tableArn + '/index/*'] }));
  }

  private grantPlanAccess(fn: lambda.Function): void {
    fn.addToRolePolicy(new iam.PolicyStatement({ actions: ['dynamodb:Query', 'dynamodb:GetItem', 'dynamodb:PutItem', 'dynamodb:BatchWriteItem'], resources: [this.graphTable.tableArn] }));
    this.preimageBucket.grantPut(fn);
  }

  private grantExecutorAccess(fn: lambda.Function): void {
    fn.addToRolePolicy(new iam.PolicyStatement({ actions: ['dynamodb:GetItem', 'dynamodb:PutItem', 'dynamodb:UpdateItem'], resources: [this.graphTable.tableArn] }));
    this.preimageBucket.grantReadWrite(fn);
    this.auditBucket.grantPut(fn);
    const userArn = `arn:aws:iam::${this.account}:user/*`;
    const policyArn = `arn:aws:iam::${this.account}:policy/*`;
    fn.addToRolePolicy(new iam.PolicyStatement({ actions: ['iam:DetachUserPolicy', 'iam:AttachUserPolicy'], resources: [policyArn] }));
    fn.addToRolePolicy(new iam.PolicyStatement({ actions: ['iam:DeleteUserPolicy', 'iam:PutUserPolicy', 'iam:ListUserPolicies', 'iam:GetUserPolicy', 'iam:ListAttachedUserPolicies'], resources: [userArn] }));
  }

  private grantBrokerAccess(fn: lambda.Function): void {
    this.auditBucket.grantPut(fn);
    fn.addToRolePolicy(new iam.PolicyStatement({ actions: ['states:SendTaskSuccess', 'states:SendTaskFailure'], resources: ['arn:aws:states:us-east-1:' + this.account + ':execution:*'] }));
  }

  private grantBudgetAccess(fn: lambda.Function): void {
    fn.addToRolePolicy(new iam.PolicyStatement({ actions: ['ce:GetCostAndUsage'], resources: ['*'] }));
  }
}
