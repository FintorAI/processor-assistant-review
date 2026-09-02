#!/usr/bin/env python3
"""CDK app entry point — processor-assistant-review."""

import os

import aws_cdk as cdk

from stacks.processor_review_stack import ProcessorReviewStack

app = cdk.App()

stage = os.environ.get("STAGE") or os.environ.get("REVIEW_STAGE") or "dev"
account = os.environ.get("PAR_AWS_ACCOUNT_ID") or os.environ.get("CDK_DEFAULT_ACCOUNT")
region = (
    os.environ.get("PAR_AWS_REGION")
    or os.environ.get("CDK_DEFAULT_REGION")
    or "us-west-1"
)

ProcessorReviewStack(
    app,
    f"ProcessorReviewStack-{stage}",
    stage=stage,
    env=cdk.Environment(account=account, region=region),
)

app.synth()
