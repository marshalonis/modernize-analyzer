#!/usr/bin/env python3
"""
CDK app — Modernization Analyzer
Deploy with: cdk deploy --all
"""
import os
import aws_cdk as cdk
from stacks.network_stack import NetworkStack
from stacks.ecr_stack import EcrStack
from stacks.ecs_stack import EcsStack

app = cdk.App()

env = cdk.Environment(
    account=os.getenv("CDK_DEFAULT_ACCOUNT") or os.getenv("AWS_ACCOUNT_ID"),
    region=os.getenv("CDK_DEFAULT_REGION", "us-east-1"),
)

# Configuration — override via CDK context or environment
default_model_id = app.node.try_get_context("defaultModelId") or os.getenv(
    "DEFAULT_MODEL_ID",
    "anthropic.claude-3-5-sonnet-20241022-v2:0",
)

network = NetworkStack(app, "ModernizerNetwork", env=env)
ecr = EcrStack(app, "ModernizerEcr", env=env)
ecs = EcsStack(
    app,
    "ModernizerEcs",
    vpc=network.vpc,
    frontend_repo=ecr.frontend_repo,
    backend_repo=ecr.backend_repo,
    frontend_alb_sg=network.frontend_alb_sg,
    frontend_task_sg=network.frontend_task_sg,
    backend_alb_sg=network.backend_alb_sg,
    backend_task_sg=network.backend_task_sg,
    default_model_id=default_model_id,
    env=env,
)
ecs.add_dependency(network)
ecs.add_dependency(ecr)

app.synth()
