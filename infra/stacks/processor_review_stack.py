"""AWS CDK stack for the ProcessorReview serverless agent (stage-parameterized).

One stack instance per stage (dev/prod), same account/region, stage-suffixed
resource names. LangGraph Cloud hosting is unaffected — this stack only adds
the AWS deployment target.

Architecture:
- Lambda Function URL = API front door (Platform-compatible FastAPI shim).
- ECS Fargate task = background-run worker for full 0–15 disclosure runs
  (no 15-min ceiling). Same Docker image; command overridden to
  ``python -m api.worker``.
- Streaming ``/runs/stream`` still executes inside Lambda (900s max) — use
  ``POST /threads/{id}/runs`` + poll for long runs.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    aws_dynamodb as dynamodb,
    aws_ec2 as ec2,
    aws_ecr_assets as ecr_assets,
    aws_ecs as ecs,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_logs as logs,
    aws_s3 as s3,
    aws_secretsmanager as secretsmanager,
)
from constructs import Construct

REPO_ROOT = Path(__file__).resolve().parents[2]

# Shared Docker build context exclusions (Lambda + Fargate use one image asset).
IMAGE_EXCLUDE = [
    ".env",
    ".git",
    ".cursor",
    ".langgraph_api",
    "venv",
    ".venv",
    "infra/.venv",
    "infra/cdk.out*",
    "cdk.out*",
    "logs",
    "tmp",
    "docs",
    "tests",
    "scripts",
    "factory",
    "definitions",
    "local",
    "**/__pycache__",
    "*.numbers",
    "*.docx",
    "*.log",
    "*.csv",
    "2604*.json",
    "2604*.txt",
    "output-*.json",
]

WORKER_CONTAINER_NAME = "worker"

# Secret values are pushed out-of-band via `aws secretsmanager put-secret-value`
# (infra/deploy.sh / .github/workflows/aws-deploy.yml) — never through
# CloudFormation, so they never appear in a synthesized template.
SECRET_KEYS = [
    "API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "LANGSMITH_API_KEY",
    "LANGCHAIN_API_KEY",
    "LANGGRAPH_API_KEY",
    # Encompass — test
    "ENCOMPASS_API_BASE_URL",
    "ENCOMPASS_CLIENT_ID",
    "ENCOMPASS_CLIENT_SECRET",
    "ENCOMPASS_INSTANCE_ID",
    "ENCOMPASS_USERNAME",
    "ENCOMPASS_PASSWORD",
    "ENCOMPASS_SUBJECT_USER_ID",
    # Encompass — prod
    "PROD_ENCOMPASS_API_BASE_URL",
    "PROD_ENCOMPASS_CLIENT_ID",
    "PROD_ENCOMPASS_CLIENT_SECRET",
    "PROD_ENCOMPASS_INSTANCE_ID",
    "PROD_ENCOMPASS_USERNAME",
    "PROD_ENCOMPASS_PASSWORD",
    "PROD_ENCOMPASS_SUBJECT_USER_ID",
    "PROD_ENCOMPASS_ACCESS_TOKEN",
    # Document / address services
    "LANDINGAI_API_KEY",
    "USPS_CLIENT_ID",
    "USPS_CLIENT_SECRET",
    "DOCREPO_AUTH_TOKEN",
    "DOCREPO_PUT_API_BASE",
    "DOCREPO_GET_API_BASE",
    "DOCREPO_CREATE_API_BASE",
    "EFOLDER_API_TOKEN",
    "EFOLDER_API_BASE_URL",
    # TaskTile rns_ai_only — prod + dev tenants (selected at runtime by state["env"])
    "TASKTILE_PROD_CLIENT_KEY",
    "TASKTILE_PROD_CLIENT_SECRET",
    "TASKTILE_DEV_CLIENT_KEY",
    "TASKTILE_DEV_CLIENT_SECRET",
    # AWS (Textract) — optional; Lambda role may cover this instead
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_REGION",
]

# Non-secret config that MAY be overridden at deploy time. Only set on the
# Lambda when provided — the tools have working hardcoded defaults for each.
OPTIONAL_PLAIN_ENV = [
    "LANGGRAPH_DEFAULT_RECURSION_LIMIT",
    "AI_DEBUG_MODE",
    "USE_LOCAL_COPILOTAGENT",
    "EFOLDER_API_BASE_URL",
    "DOCREPO_PUT_API_BASE",
    "DOCREPO_GET_API_BASE",
    "DOCREPO_CREATE_API_BASE",
    # TaskTile rns_ai_only feature flags (default off/shadow in code)
    "TASKTILE_AI_ONLY_ENABLED",
    "TASKTILE_AI_ONLY_FETCH",
    "TASKTILE_SHADOW_MODE",
]


def _deploy_env(name: str, default: str = "") -> str:
    """Read deploy-time env (infra/deploy.sh sources .env before cdk deploy)."""
    return os.environ.get(name, default)


class ProcessorReviewStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, *, stage: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        is_prod = stage == "prod"
        function_name = f"processor-assistant-review-{stage}"

        checkpoint_table = dynamodb.Table(
            self,
            "CheckpointTable",
            table_name=f"processor-assistant-review-checkpoints-{stage}",
            partition_key=dynamodb.Attribute(name="PK", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="SK", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN if is_prod else RemovalPolicy.DESTROY,
        )

        # Large checkpoint payloads (>350KB) are offloaded here by
        # langgraph_checkpoint_aws DynamoDBSaver — without this, full 0–15
        # runs fail mid-workflow with DynamoDB ValidationException.
        checkpoint_bucket = s3.Bucket(
            self,
            "CheckpointBucket",
            bucket_name=f"processor-assistant-review-checkpoints-{stage}-{self.account}",
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            versioned=False,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="expire-old-checkpoints",
                    enabled=True,
                    expiration=Duration.days(30 if is_prod else 14),
                )
            ],
            removal_policy=RemovalPolicy.RETAIN if is_prod else RemovalPolicy.DESTROY,
            auto_delete_objects=not is_prod,
        )

        # GenerateSecretString seeds an empty placeholder ONCE at creation and
        # CloudFormation never touches the value again. Never use
        # SecretValue.unsafe_plain_text here — it bakes live keys into the
        # template (readable via cloudformation:GetTemplate).
        agent_secrets = secretsmanager.Secret(
            self,
            "AgentSecrets",
            secret_name=f"processor-assistant-review-secrets-{stage}",
            description=(
                f"ProcessorReview agent credentials ({stage}). Values are managed out-of-band "
                "via `aws secretsmanager put-secret-value` (see infra/deploy.sh), "
                "not by CloudFormation."
            ),
            generate_secret_string=secretsmanager.SecretStringGenerator(
                secret_string_template=json.dumps({key: "" for key in SECRET_KEYS}),
                generate_string_key="_cfn_placeholder",
                exclude_punctuation=True,
            ),
        )

        environment = {
            # Non-secret config only — secrets load from Secrets Manager at
            # cold start (api/secrets.py).
            "CHECKPOINT_TABLE_NAME": checkpoint_table.table_name,
            "CHECKPOINT_S3_BUCKET": checkpoint_bucket.bucket_name,
            "CHECKPOINT_S3_PREFIX": f"processor-assistant-review-{stage}",
            "AGENT_SECRETS_ARN": agent_secrets.secret_arn,
            "REVIEW_STAGE": stage,
            # Stage-specific LangSmith project (LANGSMITH_DEV_PROJECT / LANGSMITH_PROD_PROJECT)
            # so dev and prod traces never land in the same project.
            "LANGSMITH_PROJECT": _deploy_env(
                f"LANGSMITH_{stage.upper()}_PROJECT", f"processor-assistant-review-{stage}"
            ),
            "LANGSMITH_TRACING": _deploy_env("LANGSMITH_TRACING", "true"),
            "CORS_ALLOW_ORIGINS": _deploy_env("CORS_ALLOW_ORIGINS", "*"),
            "REVIEW_LOG_DIR": "/tmp/review-logs",
            "AWS_LWA_INVOKE_MODE": "response_stream",
            "AWS_LWA_READINESS_CHECK_PATH": "/health",
            "PORT": "8080",
        }
        for name in OPTIONAL_PLAIN_ENV:
            value = _deploy_env(name)
            if value:
                environment[name] = value

        

        # One image for Lambda API + Fargate worker (command overridden per target).
        agent_image = ecr_assets.DockerImageAsset(
            self,
            "AgentImage",
            directory=str(REPO_ROOT),
            file="api/Dockerfile",
            exclude=IMAGE_EXCLUDE,
            platform=ecr_assets.Platform.LINUX_ARM64,
        )

        # ── Fargate worker (long background runs) ────────────────────────────
        # us-west-1 allows only 5 VPCs by default and the account is at that
        # limit, so a stage can be pointed at an existing VPC via WORKER_VPC_ID.
        existing_vpc_id = _deploy_env("WORKER_VPC_ID")
        if existing_vpc_id:
            vpc = ec2.Vpc.from_lookup(self, "WorkerVpc", vpc_id=existing_vpc_id)
            worker_subnets = list(vpc.public_subnets)
            if not worker_subnets:
                raise ValueError(
                    f"WORKER_VPC_ID={existing_vpc_id} has no public subnets. "
                    "run_task uses assignPublicIp=ENABLED, so the worker subnets "
                    "need an internet gateway route."
                )
        else:
            vpc = ec2.Vpc(
                self,
                "WorkerVpc",
                vpc_name=f"processor-assistant-review-worker-{stage}",
                max_azs=2,
                nat_gateways=0,
                subnet_configuration=[
                    ec2.SubnetConfiguration(
                        name="Public",
                        subnet_type=ec2.SubnetType.PUBLIC,
                        cidr_mask=24,
                    ),
                ],
            )
            worker_subnets = list(vpc.public_subnets)

        worker_sg = ec2.SecurityGroup(
            self,
            "WorkerSecurityGroup",
            vpc=vpc,
            security_group_name=f"processor-assistant-review-worker-{stage}",
            description="ProcessorReview Fargate worker - egress only (public IP for outbound APIs)",
            allow_all_outbound=True,
        )

        cluster = ecs.Cluster(
            self,
            "WorkerCluster",
            cluster_name=f"processor-assistant-review-worker-{stage}",
            vpc=vpc,
            container_insights_v2=(
                ecs.ContainerInsights.ENABLED if is_prod else ecs.ContainerInsights.DISABLED
            ),
        )

        worker_log_group = logs.LogGroup(
            self,
            "WorkerLogGroup",
            log_group_name=f"/ecs/processor-assistant-review-worker-{stage}",
            retention=logs.RetentionDays.THREE_MONTHS if is_prod else logs.RetentionDays.ONE_MONTH,
            removal_policy=RemovalPolicy.RETAIN if is_prod else RemovalPolicy.DESTROY,
        )

        task_def = ecs.FargateTaskDefinition(
            self,
            "WorkerTask",
            family=f"processor-assistant-review-worker-{stage}",
            cpu=2048,
            memory_limit_mib=8192,
            runtime_platform=ecs.RuntimePlatform(
                cpu_architecture=ecs.CpuArchitecture.ARM64,
                operating_system_family=ecs.OperatingSystemFamily.LINUX,
            ),
        )

        # Worker env mirrors Lambda (minus LWA-specific vars). Run IDs come from
        # container overrides at ecs.run_task time.
        worker_environment = {
            "CHECKPOINT_TABLE_NAME": checkpoint_table.table_name,
            "CHECKPOINT_S3_BUCKET": checkpoint_bucket.bucket_name,
            "CHECKPOINT_S3_PREFIX": environment["CHECKPOINT_S3_PREFIX"],
            "AGENT_SECRETS_ARN": agent_secrets.secret_arn,
            "REVIEW_STAGE": stage,
            "LANGSMITH_PROJECT": environment["LANGSMITH_PROJECT"],
            "LANGSMITH_TRACING": environment["LANGSMITH_TRACING"],
            "REVIEW_LOG_DIR": "/tmp/review-logs",
            # 3h wall-clock guard inside api/worker.py (Fargate itself has no cap).
            "REVIEW_MAX_RUN_SECONDS": _deploy_env("REVIEW_MAX_RUN_SECONDS", "10800"),
        }
        for name in OPTIONAL_PLAIN_ENV:
            if name in environment:
                worker_environment[name] = environment[name]

        task_def.add_container(
            WORKER_CONTAINER_NAME,
            container_name=WORKER_CONTAINER_NAME,
            image=ecs.ContainerImage.from_docker_image_asset(agent_image),
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="worker",
                log_group=worker_log_group,
            ),
            environment=worker_environment,
            command=["python", "-m", "api.worker"],
            essential=True,
        )

        checkpoint_table.grant_read_write_data(task_def.task_role)
        agent_secrets.grant_read(task_def.task_role)
        checkpoint_bucket.grant_read_write(task_def.task_role)

        # ── Lambda API front door ────────────────────────────────────────────
        agent_fn = lambda_.DockerImageFunction(
            self,
            "AgentFunction",
            function_name=function_name,
            description=f"Processor-assistant-review LangGraph Platform API ({stage})",
            code=lambda_.DockerImageCode.from_ecr(
                repository=agent_image.repository,
                tag_or_digest=agent_image.image_tag,
            ),
            memory_size=3008,
            timeout=Duration.seconds(900),
            architecture=lambda_.Architecture.ARM_64,
            environment={
                **environment,
                "WORKER_TASK_DEFINITION_ARN": task_def.task_definition_arn,
                "WORKER_CLUSTER_ARN": cluster.cluster_arn,
                "WORKER_SUBNET_IDS": ",".join(s.subnet_id for s in worker_subnets),
                "WORKER_SECURITY_GROUP_ID": worker_sg.security_group_id,
                "WORKER_CONTAINER_NAME": WORKER_CONTAINER_NAME,
            },
        )
        agent_fn.node.add_dependency(agent_image)

        checkpoint_table.grant_read_write_data(agent_fn)
        agent_secrets.grant_read(agent_fn)
        checkpoint_bucket.grant_read_write(agent_fn)

        agent_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["lambda:InvokeFunction"],
                resources=[
                    f"arn:{self.partition}:lambda:{self.region}:{self.account}:function:{function_name}"
                ],
            )
        )
        agent_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["ecs:RunTask"],
                resources=[task_def.task_definition_arn],
            )
        )
        agent_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["ecs:StopTask"],
                resources=[
                    f"arn:{self.partition}:ecs:{self.region}:{self.account}:task/{cluster.cluster_name}/*"
                ],
            )
        )
        agent_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["iam:PassRole"],
                resources=[
                    task_def.task_role.role_arn,
                    task_def.execution_role.role_arn,
                ],
            )
        )
        # Do not retry crashed workers — re-running a partial review against a live loan is unsafe.
        agent_fn.configure_async_invoke(retry_attempts=0)

        _cors_origins = [
            o.strip()
            for o in _deploy_env("CORS_ALLOW_ORIGINS", "*").split(",")
            if o.strip()
        ] or ["*"]
        function_url = agent_fn.add_function_url(
            auth_type=lambda_.FunctionUrlAuthType.NONE,
            invoke_mode=lambda_.InvokeMode.RESPONSE_STREAM,
            cors=lambda_.FunctionUrlCorsOptions(
                allowed_origins=_cors_origins,
                allowed_methods=[lambda_.HttpMethod.ALL],
                allowed_headers=[
                    "content-type",
                    "accept",
                    "authorization",
                    "x-api-key",
                    "x-auth-scheme",
                ],
                allow_credentials=False,
            ),
        )

        CfnOutput(self, "ApiUrl", value=function_url.url)
        CfnOutput(self, "CheckpointTableName", value=checkpoint_table.table_name)
        CfnOutput(self, "CheckpointBucketName", value=checkpoint_bucket.bucket_name)
        CfnOutput(self, "AgentSecretsArn", value=agent_secrets.secret_arn)
        CfnOutput(self, "LambdaFunctionName", value=agent_fn.function_name)
        CfnOutput(self, "WorkerClusterName", value=cluster.cluster_name)
        CfnOutput(self, "Stage", value=stage)
