import * as fs from 'node:fs';
import * as path from 'node:path';
import * as cdk from 'aws-cdk-lib';
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

  public constructor(scope: Construct, id: string, props?: StackProps) {
    super(scope, id, props);
    if (this.region !== REGION) {
      throw new Error(`Deadbolt must be deployed in ${REGION}`);
    }

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
    this.deploySpa(spaBucket);

    const credentials = CONNECTORS.map((system) => new ssm.StringParameter(this, `${this.idFor(system)}Credential`, {
      parameterName: `/deadbolt/connectors/${system}/credential`,
      stringValue: 'configure-before-deploy',
      type: ssm.ParameterType.SECURE_STRING,
      description: `SecureString credential for the ${system} connector`,
    }));

    const connectors = this.function('Connectors', 'connectors', 'deadbolt.broker.handler.lambda_handler');
    const driftEngine = this.function('DriftEngine', 'drift-engine', 'deadbolt.broker.handler.lambda_handler');
    const planBuilder = this.function('PlanBuilder', 'plan-builder', 'deadbolt.broker.handler.lambda_handler');
    const executor = this.function('Executor', 'executor', 'deadbolt.broker.handler.lambda_handler');
    const brokerHandler = this.function('BrokerHandler', 'broker-handler', 'deadbolt.broker.handler.lambda_handler');
    const budgetGuard = this.function('BudgetGuard', 'budget-guard', 'budget_guard.handler.lambda_handler');

    this.addLogPolicy(connectors, 'connectors');
    this.addLogPolicy(driftEngine, 'drift-engine');
    this.addLogPolicy(planBuilder, 'plan-builder');
    this.addLogPolicy(executor, 'executor');
    this.addLogPolicy(brokerHandler, 'broker-handler');
    this.addLogPolicy(budgetGuard, 'budget-guard');

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

    const hrBus = new events.EventBus(this, 'HrEventBus', { eventBusName: 'deadbolt-hr-events' });
    const hrRule = new events.Rule(this, 'HrEventRefreshRule', {
      eventBus: hrBus,
      eventPattern: { source: ['deadbolt.hr'], detailType: ['EmployeeChanged'] },
    });
    hrRule.addTarget(new targets.LambdaFunction(connectors, { event: events.RuleTargetInput.fromObject({ trigger: 'hr-event', detail: events.EventField.fromPath('$.detail') }) }));

    const budgetRule = new events.Rule(this, 'BudgetGuardSchedule', { schedule: events.Schedule.expression('rate(6 hours)') });
    budgetRule.addTarget(new targets.LambdaFunction(budgetGuard));
    new cdk.CfnOutput(this, 'BrokerFunctionUrl', { value: brokerUrl.url });
    new cdk.CfnOutput(this, 'ApprovalBrokerStateMachineArn', { value: stateMachine.stateMachineArn });
    new cdk.CfnOutput(this, 'HrEventBusArn', { value: hrBus.eventBusArn });
    new cdk.CfnOutput(this, 'SpaWebsiteUrl', { value: spaBucket.bucketWebsiteUrl });
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
    const candidates = [path.join(repoRoot, 'frontend', 'dist'), path.join(repoRoot, 'Frontend', 'dist')];
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

  private function(id: string, name: string, handler: string): lambda.Function {
    const backend = path.resolve(__dirname, '../..');
    return new lambda.Function(this, id, {
      functionName: `deadbolt-${name}`,
      runtime: lambda.Runtime.PYTHON_3_12,
      architecture: lambda.Architecture.ARM_64,
      memorySize: 512,
      timeout: Duration.seconds(30),
      code: lambda.Code.fromAsset(backend, {
        exclude: [
          'infra',
          'infra/**',
          '.venv',
          '.venv/**',
          '.pytest_cache',
          '.pytest_cache/**',
          'artifacts',
          'artifacts/**',
        ],
      }),
      handler,
      environment: { PYTHONPATH: 'src' },
      logGroup: new logs.LogGroup(this, `${id}LogGroup`, {
        logGroupName: `/aws/lambda/deadbolt-${name}`,
        retention: logs.RetentionDays.ONE_DAY,
        removalPolicy: RemovalPolicy.DESTROY,
      }),
      role: this.lambdaRole(`${id}Role`),
    });
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

  private grantConnectorAccess(fn: lambda.Function, credentials: ssm.StringParameter[], snapshots: s3.Bucket): void {
    fn.addToRolePolicy(new iam.PolicyStatement({ actions: ['ssm:GetParameter', 'ssm:GetParameters'], resources: credentials.map((item) => item.parameterArn) }));
    fn.addToRolePolicy(new iam.PolicyStatement({ actions: ['dynamodb:BatchWriteItem', 'dynamodb:PutItem'], resources: [this.graphTable.tableArn] }));
    snapshots.grantPut(fn);
    fn.addToRolePolicy(new iam.PolicyStatement({ actions: ['iam:ListUsers', 'iam:ListAttachedUserPolicies', 'iam:ListUserPolicies', 'iam:GetUserPolicy', 'iam:GenerateServiceLastAccessedDetails', 'iam:GetServiceLastAccessedDetails'], resources: [`arn:aws:iam::${this.account}:user/*`] }));
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
