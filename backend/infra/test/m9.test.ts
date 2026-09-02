import { Template, Match } from 'aws-cdk-lib/assertions';
import * as cdk from 'aws-cdk-lib';
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { DeadboltStack } from '../lib/deadbolt-stack.js';

const app = new cdk.App();
const template = Template.fromStack(new DeadboltStack(app, 'TestDeadboltStack', { env: { region: 'us-east-1' } }));

test('uses the low-cost state and storage primitives', () => {
  template.hasResourceProperties('AWS::DynamoDB::Table', {
    BillingMode: 'PAY_PER_REQUEST',
    PointInTimeRecoverySpecification: { PointInTimeRecoveryEnabled: false },
    GlobalSecondaryIndexes: [Match.objectLike({ IndexName: 'GSI1', KeySchema: Match.arrayWith([Match.objectLike({ AttributeName: 'GSI1PK' }), Match.objectLike({ AttributeName: 'GSI1SK' })]) })],
  });
  template.resourceCountIs('AWS::S3::Bucket', 4);
  template.resourceCountIs('AWS::Events::EventBus', 1);
  template.hasResourceProperties('AWS::StepFunctions::StateMachine', { StateMachineType: 'STANDARD' });
});

test('enables Object Lock and one-day Lambda logs', () => {
  template.resourcePropertiesCountIs('AWS::S3::Bucket', { ObjectLockEnabled: true }, 3);
  template.resourceCountIs('AWS::Logs::LogGroup', 6);
  template.hasResourceProperties('AWS::Logs::LogGroup', { RetentionInDays: 1 });
});

test('creates six connector credentials as SecureStrings', () => {
  template.resourcePropertiesCountIs('AWS::SSM::Parameter', { Type: 'SecureString' }, 6);
});

test('keeps destructive IAM authority on the executor role only', () => {
  const resources = template.findResources('AWS::IAM::Policy');
  const owners = Object.entries(resources).filter(([, resource]) => JSON.stringify(resource).includes('iam:DetachUserPolicy'));
  assert.equal(owners.length, 1);
  assert.equal(JSON.stringify(resources).includes('iam:*'), false);
  for (const resource of Object.values(resources)) {
    const statements = resource.Properties.PolicyDocument.Statement ?? [];
    for (const statement of statements) {
      assert.notEqual(statement.Action, '*');
    }
  }
});

test('has no API Gateway, ALB, VPC, NAT, or CloudFront resources', () => {
  for (const type of ['AWS::ApiGateway::RestApi', 'AWS::ElasticLoadBalancingV2::LoadBalancer', 'AWS::EC2::VPC', 'AWS::EC2::NatGateway', 'AWS::CloudFront::Distribution']) {
    template.resourceCountIs(type, 0);
  }
  template.resourceCountIs('AWS::Lambda::Url', 1);
});
