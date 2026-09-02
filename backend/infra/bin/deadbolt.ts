#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { DeadboltStack } from '../lib/deadbolt-stack.js';

const app = new cdk.App();

new DeadboltStack(app, 'DeadboltStack', {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: 'us-east-1',
  },
  description: 'Deadbolt deterministic entitlement drift broker infrastructure',
});
